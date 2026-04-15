"""
func/sys_agent_recording.py — Agent Session Recorder & Self-Debugger

Records every tool call, result, timing, and token usage during a session.
When something fails, the agent reviews its own trace to distinguish:
  - Logic errors    (wrong tool, wrong args, wrong plan)
  - Environment errors (missing file, network down, permission denied)
  - Cascade failures (step 3 failed because step 2 gave bad output)

Four tools:
  recording_start      begin a new recording session
  recording_stop       stop and save the full session trace
  recording_snapshot   capture current state mid-session (checkpointing)
  recording_analyze    analyze a session trace for failures and patterns

Output:
  recordings/
    session_<id>_<timestamp>.jsonl   → streaming NDJSON event log
    session_<id>_<timestamp>.html    → human-readable replay viewer
    session_<id>_analysis.json       → failure analysis report

Self-debug report includes:
  - Full tool call chain with inputs/outputs
  - First failure point with root cause classification
  - Cascade analysis (which steps were poisoned by the failure)
  - Recommended fix (logic change vs env fix vs retry)
  - Token cost per step
  - Slowest steps
  - Repeated/redundant tool calls (efficiency audit)
"""

from __future__ import annotations

import json
import os
import re
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any


# ── Constants ─────────────────────────────────────────────────────────────────

RECORDINGS_DIR = "recordings"
MAX_VALUE_PREVIEW = 300   # chars shown in summaries

# Error classification patterns
_LOGIC_PATTERNS = [
    r"Unknown function",
    r"unexpected keyword argument",
    r"AttributeError",
    r"KeyError",
    r"TypeError",
    r"wrong number of arguments",
    r"not in \[",
    r"invalid.*argument",
]

_ENV_PATTERNS = [
    r"No such file or directory",
    r"Permission denied",
    r"Connection refused",
    r"ENOENT",
    r"Network.*unreachable",
    r"Timeout",
    r"quota.*exceeded",
    r"RESOURCE_EXHAUSTED",
    r"ModuleNotFoundError",
    r"command not found",
    r"🔒",
]

_RETRY_PATTERNS = [
    r"rate.?limit",
    r"429",
    r"503",
    r"temporarily unavailable",
    r"try again",
]


# ── Schemas ───────────────────────────────────────────────────────────────────

schema_recording_start = {
    "name": "recording_start",
    "description": (
        "Begin recording this agent session. Captures every tool call, "
        "result, timing, and token usage. Call at the very start of a complex task. "
        "Returns a session_id to use with recording_stop and recording_analyze."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": (
                    "Human-readable session name. E.g. 'add_rate_limiting', "
                    "'fix_login_bug'. Auto-generated if omitted."
                )
            },
            "task_description": {
                "type": "string",
                "description": "The task being attempted in this session."
            },
            "metadata": {
                "type": "object",
                "description": "Any extra context to attach (plan_file, task_id, etc.)."
            }
        },
        "required": []
    }
}

schema_recording_stop = {
    "name": "recording_stop",
    "description": (
        "Stop the current recording and save the full session trace. "
        "Call at the end of a task (success or failure). "
        "Automatically runs a failure analysis if any errors were recorded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session ID from recording_start. Uses active session if omitted."
            },
            "outcome": {
                "type": "string",
                "enum": ["success", "failure", "partial", "interrupted"],
                "description": "Final outcome of the task.",
                "default": "success"
            },
            "notes": {
                "type": "string",
                "description": "Any final notes or observations about the session."
            }
        },
        "required": []
    }
}

schema_recording_snapshot = {
    "name": "recording_snapshot",
    "description": (
        "Capture a named checkpoint mid-session. "
        "Use after completing each major subtask so partial progress is preserved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Checkpoint name. E.g. 'after_discovery', 'patch_applied'."
            },
            "notes": {
                "type": "string",
                "description": "What was achieved at this checkpoint."
            }
        },
        "required": ["label"]
    }
}

schema_recording_analyze = {
    "name": "recording_analyze",
    "description": (
        "Analyze a recorded session to identify failures, root causes, "
        "and inefficiencies. The agent reads its own trace to self-debug. "
        "Returns: first failure point, error classification, cascade analysis, "
        "redundant calls, slowest steps, and recommended fix."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session to analyze. Uses most recent session if omitted."
            },
            "focus": {
                "type": "string",
                "enum": ["failures", "performance", "redundancy", "full"],
                "description": (
                    "failures: focus on what went wrong. "
                    "performance: slowest steps, token waste. "
                    "redundancy: repeated/unnecessary tool calls. "
                    "full: complete analysis (default)."
                ),
                "default": "full"
            }
        },
        "required": []
    }
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ToolEvent:
    event_type:   str        # tool_call | tool_result | snapshot | session_start | session_end
    timestamp:    str
    elapsed_ms:   float
    tool_name:    str        = ""
    args:         dict       = field(default_factory=dict)
    result:       str        = ""
    success:      bool       = True
    error:        str        = ""
    tokens_used:  int        = 0
    duration_ms:  float      = 0.0
    label:        str        = ""   # for snapshots
    notes:        str        = ""
    call_index:   int        = 0


@dataclass
class SessionMeta:
    session_id:       str
    task_description: str
    started_at:       str
    ended_at:         str        = ""
    outcome:          str        = "unknown"
    total_events:     int        = 0
    total_tool_calls: int        = 0
    total_tokens:     int        = 0
    total_duration_ms: float     = 0.0
    metadata:         dict       = field(default_factory=dict)
    notes:            str        = ""


# ── Global session registry ───────────────────────────────────────────────────

class _SessionRegistry:
    """Thread-safe singleton that holds active recording sessions."""

    def __init__(self):
        self._lock     = threading.Lock()
        self._sessions: dict[str, "_RecordingSession"] = {}
        self._active:   Optional[str] = None

    def start(self, session: "_RecordingSession"):
        with self._lock:
            self._sessions[session.session_id] = session
            self._active = session.session_id

    def get(self, session_id: Optional[str] = None) -> Optional["_RecordingSession"]:
        with self._lock:
            sid = session_id or self._active
            return self._sessions.get(sid) if sid else None

    def stop(self, session_id: Optional[str] = None):
        with self._lock:
            sid = session_id or self._active
            if sid and sid in self._sessions:
                del self._sessions[sid]
            if self._active == sid:
                self._active = None

    def active_id(self) -> Optional[str]:
        return self._active


_registry = _SessionRegistry()


class _RecordingSession:
    """Holds the live event stream for one recording session."""

    def __init__(self, session_id: str, task_description: str,
                 metadata: dict, cwd: str):
        self.session_id       = session_id
        self.task_description = task_description
        self.metadata         = metadata
        self.cwd              = cwd
        self.started_at       = datetime.now().isoformat()
        self.start_epoch      = time.time()
        self.events:  list[ToolEvent] = []
        self.call_index = 0
        self._lock = threading.Lock()

        # JSONL log file (streaming)
        rec_dir = Path(cwd) / RECORDINGS_DIR
        rec_dir.mkdir(exist_ok=True)
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = rec_dir / f"session_{session_id}_{ts}.jsonl"
        self._log_fh  = open(self.log_path, "w", buffering=1)  # line-buffered

        # Write header
        self._write_event(ToolEvent(
            event_type   = "session_start",
            timestamp    = self.started_at,
            elapsed_ms   = 0,
            label        = session_id,
            notes        = task_description,
        ))

    def record_tool_call(self, tool_name: str, args: dict) -> int:
        with self._lock:
            self.call_index += 1
            idx = self.call_index
        ev = ToolEvent(
            event_type  = "tool_call",
            timestamp   = datetime.now().isoformat(),
            elapsed_ms  = (time.time() - self.start_epoch) * 1000,
            tool_name   = tool_name,
            args        = _sanitize_args(args),
            call_index  = idx,
        )
        self._append(ev)
        return idx

    def record_tool_result(self, call_index: int, tool_name: str,
                           result: str, duration_ms: float,
                           tokens: int = 0):
        success = not _is_error(result)
        error   = result if not success else ""
        ev = ToolEvent(
            event_type  = "tool_result",
            timestamp   = datetime.now().isoformat(),
            elapsed_ms  = (time.time() - self.start_epoch) * 1000,
            tool_name   = tool_name,
            result      = result[:MAX_VALUE_PREVIEW],
            success     = success,
            error       = error[:200] if error else "",
            duration_ms = duration_ms,
            tokens_used = tokens,
            call_index  = call_index,
        )
        self._append(ev)

    def record_snapshot(self, label: str, notes: str):
        ev = ToolEvent(
            event_type = "snapshot",
            timestamp  = datetime.now().isoformat(),
            elapsed_ms = (time.time() - self.start_epoch) * 1000,
            label      = label,
            notes      = notes,
        )
        self._append(ev)

    def _append(self, ev: ToolEvent):
        with self._lock:
            self.events.append(ev)
        self._write_event(ev)

    def _write_event(self, ev: ToolEvent):
        try:
            self._log_fh.write(json.dumps(asdict(ev)) + "\n")
        except Exception:
            pass

    def close(self, outcome: str, notes: str) -> SessionMeta:
        ended_at   = datetime.now().isoformat()
        total_ms   = (time.time() - self.start_epoch) * 1000
        tool_calls = [e for e in self.events if e.event_type == "tool_call"]
        total_tok  = sum(e.tokens_used for e in self.events)

        meta = SessionMeta(
            session_id        = self.session_id,
            task_description  = self.task_description,
            started_at        = self.started_at,
            ended_at          = ended_at,
            outcome           = outcome,
            total_events      = len(self.events),
            total_tool_calls  = len(tool_calls),
            total_tokens      = total_tok,
            total_duration_ms = total_ms,
            metadata          = self.metadata,
            notes             = notes,
        )

        # Write session_end event
        ev = ToolEvent(
            event_type = "session_end",
            timestamp  = ended_at,
            elapsed_ms = total_ms,
            label      = outcome,
            notes      = notes,
        )
        self._write_event(ev)
        self._log_fh.close()

        return meta


# ── Public API ────────────────────────────────────────────────────────────────

def recording_start(
    working_directory: str,
    session_id: Optional[str] = None,
    task_description: str = "",
    metadata: Optional[dict] = None,
) -> str:
    ts  = datetime.now().strftime("%H%M%S")
    sid = _slugify(session_id) if session_id else f"session_{ts}"

    session = _RecordingSession(
        session_id       = sid,
        task_description = task_description,
        metadata         = metadata or {},
        cwd              = working_directory,
    )
    _registry.start(session)

    return (
        f"✓  Recording started\n"
        f"   Session ID   {sid}\n"
        f"   Task         {task_description or '(no description)'}\n"
        f"   Log          {RECORDINGS_DIR}/session_{sid}_*.jsonl\n"
        f"   Use recording_snapshot(label) to mark checkpoints.\n"
        f"   Use recording_stop() when done.\n"
        f"   Use recording_analyze() if something goes wrong."
    )


def recording_stop(
    working_directory: str,
    session_id: Optional[str] = None,
    outcome: str = "success",
    notes: str = "",
) -> str:
    session = _registry.get(session_id)
    if not session:
        return _stop_no_session(session_id)

    meta = session.close(outcome, notes)
    _registry.stop(session.session_id)

    # Auto-analyze on failure
    analysis_note = ""
    failures = [e for e in session.events
                if e.event_type == "tool_result" and not e.success]
    if failures or outcome in ("failure", "partial"):
        analysis = _analyze_session(session.events, meta, "full")
        _save_analysis(analysis, session.session_id, working_directory)
        analysis_note = (
            f"\n\n  ⚠  {len(failures)} failure(s) detected — "
            f"auto-analysis saved.\n"
            f"  Call recording_analyze() to review."
        )

    return (
        f"✓  Recording stopped\n"
        f"   Session      {meta.session_id}\n"
        f"   Outcome      {outcome}\n"
        f"   Tool calls   {meta.total_tool_calls}\n"
        f"   Duration     {meta.total_duration_ms/1000:.1f}s\n"
        f"   Tokens used  {meta.total_tokens:,}\n"
        f"   Log saved    {session.log_path.name}"
        f"{analysis_note}"
    )


def recording_snapshot(
    working_directory: str,
    label: str,
    notes: str = "",
) -> str:
    session = _registry.get()
    if not session:
        return "No active recording session. Call recording_start() first."

    session.record_snapshot(label, notes)
    elapsed = (time.time() - session.start_epoch)

    return (
        f"📍 Snapshot: {label}\n"
        f"   Elapsed  {elapsed:.1f}s  ·  "
        f"Events so far: {len(session.events)}"
    )


def recording_analyze(
    working_directory: str,
    session_id: Optional[str] = None,
    focus: str = "full",
) -> str:
    # Try active session first
    session = _registry.get(session_id)
    if session:
        events = session.events
        meta   = SessionMeta(
            session_id       = session.session_id,
            task_description = session.task_description,
            started_at       = session.started_at,
        )
        meta.total_tool_calls = len([e for e in events if e.event_type == "tool_call"])
        meta.total_tokens     = sum(e.tokens_used for e in events)
        meta.total_duration_ms = (time.time() - session.start_epoch) * 1000
    else:
        # Load from file
        events, meta = _load_session(session_id, working_directory)
        if not events:
            return (
                f"Session {session_id!r} not found.\n"
                f"Active session: {_registry.active_id() or 'none'}\n"
                f"Check {RECORDINGS_DIR}/ for saved sessions."
            )

    analysis = _analyze_session(events, meta, focus)
    return _format_analysis(analysis, focus)


# ── Core analysis engine ──────────────────────────────────────────────────────

def _analyze_session(
    events: list[ToolEvent],
    meta:   SessionMeta,
    focus:  str,
) -> dict:
    tool_calls   = [e for e in events if e.event_type == "tool_call"]
    tool_results = [e for e in events if e.event_type == "tool_result"]
    snapshots    = [e for e in events if e.event_type == "snapshot"]
    failures     = [e for e in tool_results if not e.success]

    # ── Pair calls with results ───────────────────────────────────────────────
    pairs: list[dict] = []
    result_map = {e.call_index: e for e in tool_results}
    for call in tool_calls:
        result = result_map.get(call.call_index)
        pairs.append({
            "index":      call.call_index,
            "tool":       call.tool_name,
            "args":       call.args,
            "result":     result.result if result else "(no result)",
            "success":    result.success if result else None,
            "error":      result.error if result else "",
            "duration_ms": result.duration_ms if result else 0,
            "tokens":     result.tokens_used if result else 0,
            "elapsed_ms": call.elapsed_ms,
        })

    # ── First failure ─────────────────────────────────────────────────────────
    first_failure = None
    cascade_steps: list[int] = []

    if failures:
        first_fail_ev = min(failures, key=lambda e: e.call_index)
        first_failure = {
            "call_index": first_fail_ev.call_index,
            "tool":       first_fail_ev.tool_name,
            "error":      first_fail_ev.error,
            "type":       _classify_error(first_fail_ev.error),
            "elapsed_ms": first_fail_ev.elapsed_ms,
        }
        # Cascade: steps that ran AFTER the first failure
        fail_idx = first_fail_ev.call_index
        cascade_steps = [
            p["index"] for p in pairs
            if p["index"] > fail_idx and p["success"] is False
        ]

    # ── Redundancy audit ──────────────────────────────────────────────────────
    tool_freq: dict[str, int]      = defaultdict(int)
    arg_freq:  dict[str, int]      = defaultdict(int)
    redundant: list[dict]          = []

    seen_calls: dict[str, list[int]] = defaultdict(list)
    for p in pairs:
        tool_freq[p["tool"]] += 1
        sig = f"{p['tool']}:{json.dumps(p['args'], sort_keys=True)}"
        seen_calls[sig].append(p["index"])
        arg_freq[sig] += 1

    for sig, indices in seen_calls.items():
        if len(indices) > 1:
            tool_name = sig.split(":")[0]
            redundant.append({
                "tool":    tool_name,
                "count":   len(indices),
                "indices": indices,
                "waste":   f"Called {len(indices)}x with identical args",
            })

    # ── Performance ───────────────────────────────────────────────────────────
    slowest = sorted(pairs, key=lambda p: p["duration_ms"], reverse=True)[:5]
    token_by_step = sorted(pairs, key=lambda p: p["tokens"], reverse=True)[:5]
    total_tokens  = sum(p["tokens"] for p in pairs)

    # ── Pattern detection ─────────────────────────────────────────────────────
    patterns: list[str] = []

    # Read-heavy pattern
    reads = [p for p in pairs if p["tool"] in ("get_file_content", "get_files_info")]
    if len(reads) > 5:
        patterns.append(
            f"Read-heavy: {len(reads)} file reads. "
            "Consider using search_code to locate code before reading."
        )

    # No search before read
    read_tools  = {"get_file_content"}
    search_tools = {"search_code", "web_search"}
    if reads and not any(p["tool"] in search_tools for p in pairs[:3]):
        patterns.append("No search before file reads — likely reading blindly.")

    # Patch after write_file
    writes  = [p for p in pairs if p["tool"] == "write_file"]
    patches = [p for p in pairs if p["tool"] == "patch_file"]
    if writes and patches:
        for w in writes:
            for pt in patches:
                if pt["index"] > w["index"] and pt["args"].get("file_path") == w["args"].get("file_path"):
                    patterns.append(
                        f"write_file then patch_file on same file (index {w['index']}→{pt['index']}). "
                        "Use patch_file throughout for existing files."
                    )

    # Tool loop (same tool >3x in a row)
    for i in range(len(pairs) - 2):
        if (pairs[i]["tool"] == pairs[i+1]["tool"] == pairs[i+2]["tool"]):
            patterns.append(
                f"Tool loop: {pairs[i]['tool']} called 3+ times consecutively "
                f"(indices {pairs[i]['index']}-{pairs[i+2]['index']}). "
                "May indicate an infinite retry loop."
            )
            break

    # ── Recommended fix ───────────────────────────────────────────────────────
    recommendations: list[str] = []
    if first_failure:
        err_type = first_failure["type"]
        if err_type == "logic":
            recommendations += [
                f"Root cause: LOGIC ERROR at step {first_failure['call_index']}",
                f"Tool: {first_failure['tool']}",
                "Fix: Review the arguments passed to this tool.",
                "Check: Is the file path correct? Is the pattern valid?",
            ]
        elif err_type == "environment":
            recommendations += [
                f"Root cause: ENVIRONMENT ERROR at step {first_failure['call_index']}",
                f"Tool: {first_failure['tool']}",
                "Fix: Check the environment — file exists? permissions? network?",
                "Do not retry the same call without fixing the environment first.",
            ]
        elif err_type == "retry":
            recommendations += [
                f"Root cause: TRANSIENT ERROR (rate limit / 503) at step {first_failure['call_index']}",
                "Fix: Wait and retry. Do not change the logic.",
            ]
        elif err_type == "cascade":
            recommendations += [
                f"Root cause: CASCADE — step {first_failure['call_index']} failed,",
                f"  poisoning {len(cascade_steps)} subsequent steps.",
                "Fix: Resolve step {first_failure['call_index']} first, then re-run from there.",
            ]

    if redundant:
        recommendations.append(
            f"Efficiency: {len(redundant)} redundant call group(s) found. "
            "Cache results instead of re-calling."
        )

    return {
        "meta":            asdict(meta),
        "total_calls":     len(tool_calls),
        "total_failures":  len(failures),
        "first_failure":   first_failure,
        "cascade_steps":   cascade_steps,
        "redundant_calls": redundant,
        "slowest_steps":   slowest,
        "token_by_step":   token_by_step,
        "total_tokens":    total_tokens,
        "tool_frequency":  dict(tool_freq),
        "patterns":        patterns,
        "recommendations": recommendations,
        "snapshots":       [asdict(s) for s in snapshots],
        "call_chain":      pairs,
    }


def _format_analysis(analysis: dict, focus: str) -> str:
    meta   = analysis["meta"]
    lines  = [
        f"SESSION ANALYSIS  —  {meta['session_id']}",
        f"{'─' * 60}",
        f"  Task        {meta.get('task_description', '?')[:60]}",
        f"  Duration    {meta.get('total_duration_ms', 0)/1000:.1f}s",
        f"  Tool calls  {analysis['total_calls']}",
        f"  Failures    {analysis['total_failures']}",
        f"  Tokens      {analysis['total_tokens']:,}",
        "",
    ]

    # ── Failures ──────────────────────────────────────────────────────────────
    if focus in ("failures", "full"):
        ff = analysis.get("first_failure")
        if ff:
            lines += [
                "FIRST FAILURE",
                "─" * 60,
                f"  Step        #{ff['call_index']}  ({ff['elapsed_ms']/1000:.1f}s into session)",
                f"  Tool        {ff['tool']}",
                f"  Error type  {ff['type'].upper()}",
                f"  Error       {ff['error'][:120]}",
            ]
            if analysis["cascade_steps"]:
                lines.append(
                    f"  Cascade     Steps {analysis['cascade_steps']} also failed as a result"
                )
            lines.append("")
        else:
            lines += ["FAILURES", "─" * 60, "  No failures detected. ✓", ""]

    # ── Recommendations ───────────────────────────────────────────────────────
    if analysis["recommendations"] and focus in ("failures", "full"):
        lines += ["RECOMMENDED FIX", "─" * 60]
        for r in analysis["recommendations"]:
            lines.append(f"  {r}")
        lines.append("")

    # ── Patterns ──────────────────────────────────────────────────────────────
    if analysis["patterns"] and focus in ("full", "redundancy"):
        lines += ["PATTERNS DETECTED", "─" * 60]
        for p in analysis["patterns"]:
            lines.append(f"  ⚠  {p}")
        lines.append("")

    # ── Redundancy ────────────────────────────────────────────────────────────
    if focus in ("redundancy", "full") and analysis["redundant_calls"]:
        lines += ["REDUNDANT CALLS", "─" * 60]
        for r in analysis["redundant_calls"]:
            lines.append(
                f"  {r['tool']:<25} called {r['count']}× identically "
                f"(steps {r['indices']})"
            )
        lines.append("")

    # ── Performance ──────────────────────────────────────────────────────────
    if focus in ("performance", "full"):
        lines += ["SLOWEST STEPS", "─" * 60]
        for p in analysis["slowest_steps"]:
            lines.append(
                f"  #{p['index']:<3} {p['tool']:<25} "
                f"{p['duration_ms']:>8.0f}ms"
                f"{'  ✗' if not p['success'] else ''}"
            )
        lines.append("")

        lines += ["TOOL FREQUENCY", "─" * 60]
        freq = sorted(analysis["tool_frequency"].items(), key=lambda x: x[1], reverse=True)
        for tool, count in freq:
            bar = "█" * min(count, 20)
            lines.append(f"  {tool:<28} {bar} {count}")
        lines.append("")

    # ── Call chain summary ────────────────────────────────────────────────────
    if focus == "full":
        lines += ["CALL CHAIN", "─" * 60]
        for p in analysis["call_chain"]:
            status = "✓" if p["success"] else "✗"
            lines.append(
                f"  {status} #{p['index']:<3} "
                f"{p['tool']:<25} "
                f"{p['duration_ms']:>7.0f}ms  "
                f"{p['result'][:40]}{'…' if len(p['result'])>40 else ''}"
            )
        lines.append("")

    # ── Snapshots ─────────────────────────────────────────────────────────────
    if analysis["snapshots"]:
        lines += ["CHECKPOINTS", "─" * 60]
        for s in analysis["snapshots"]:
            lines.append(
                f"  📍 {s['label']:<25} "
                f"+{s['elapsed_ms']/1000:.1f}s  "
                f"{s['notes'][:50]}"
            )

    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify_error(error: str) -> str:
    if not error:
        return "unknown"
    for p in _RETRY_PATTERNS:
        if re.search(p, error, re.I):
            return "retry"
    for p in _ENV_PATTERNS:
        if re.search(p, error, re.I):
            return "environment"
    for p in _LOGIC_PATTERNS:
        if re.search(p, error, re.I):
            return "logic"
    return "cascade"


def _is_error(result: str) -> bool:
    if not result:
        return False
    error_signals = [
        "Error", "error", "Exception", "Traceback",
        "not found", "Failed", "failed", "🔒", "Blocked",
        "Permission denied", "No such file",
    ]
    return any(s in result for s in error_signals)


def _sanitize_args(args: dict) -> dict:
    """Remove sensitive values, truncate large args."""
    out = {}
    for k, v in args.items():
        if k in ("content", "code", "value") and isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + "…"
        elif k in ("password", "secret", "token", "api_key"):
            out[k] = "***"
        else:
            out[k] = v
    return out


def _slugify(s: str) -> str:
    return re.sub(r"[^\w]+", "_", s.strip()).strip("_").lower()[:40]


def _save_analysis(analysis: dict, session_id: str, cwd: str):
    p = Path(cwd) / RECORDINGS_DIR / f"session_{session_id}_analysis.json"
    p.write_text(json.dumps(analysis, indent=2))


def _stop_no_session(session_id: Optional[str]) -> str:
    return (
        f"No active recording session"
        f"{f' with id {session_id!r}' if session_id else ''}.\n"
        f"Active session: {_registry.active_id() or 'none'}.\n"
        f"Call recording_start() to begin a new session."
    )


def _load_session(
    session_id: Optional[str],
    cwd: str,
) -> tuple[list[ToolEvent], SessionMeta]:
    rec_dir = Path(cwd) / RECORDINGS_DIR
    if not rec_dir.exists():
        return [], SessionMeta("", "", "")

    # Find matching file
    pattern = f"session_{session_id}_*.jsonl" if session_id else "session_*.jsonl"
    files   = sorted(rec_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return [], SessionMeta("", "", "")

    log_file = files[0]
    events: list[ToolEvent] = []
    meta = SessionMeta(session_id or "", "", "")

    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d  = json.loads(line)
                ev = ToolEvent(**{k: d.get(k, v)
                                  for k, v in ToolEvent.__dataclass_fields__.items()})
                events.append(ev)
                if ev.event_type == "session_start":
                    meta.session_id       = ev.label
                    meta.started_at       = ev.timestamp
                    meta.task_description = ev.notes
                if ev.event_type == "session_end":
                    meta.ended_at = ev.timestamp
                    meta.outcome  = ev.label
            except Exception:
                pass

    meta.total_tool_calls = len([e for e in events if e.event_type == "tool_call"])
    meta.total_tokens     = sum(e.tokens_used for e in events)
    return events, meta


# ── Hook: call this from call_function.py ─────────────────────────────────────

def hook_tool_call(tool_name: str, args: dict) -> Optional[int]:
    """
    Call at the START of each tool execution in call_function.py.
    Returns call_index (pass to hook_tool_result) or None if no session.
    """
    session = _registry.get()
    if session:
        return session.record_tool_call(tool_name, args)
    return None


def hook_tool_result(call_index: Optional[int], tool_name: str,
                     result: str, duration_ms: float, tokens: int = 0):
    """
    Call at the END of each tool execution in call_function.py.
    """
    if call_index is None:
        return
    session = _registry.get()
    if session:
        session.record_tool_result(call_index, tool_name, result, duration_ms, tokens)
