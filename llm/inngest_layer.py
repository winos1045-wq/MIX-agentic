"""
inngest_layer.py  —  Full Observability Layer for MIX Agent
============================================================

Monitors EVERY layer of the agent:

  Layer 1 — Session         agent/session.start / agent/session.complete
  Layer 2 — AI Generation   agent/ai.generate  (every LLM API call)
  Layer 3 — Iteration       agent/iteration    (each agentic loop turn)
  Layer 4 — Tool Pipeline   agent/tool.call    (every function call)
  Layer 5 — Token Budget    agent/tokens.update
  Layer 6 — Error Tracker   agent/error

Usage (two terminals):

  Terminal 1 — Inngest Dev Server:
    npx --ignore-scripts=false inngest-cli@latest dev \\
        -u http://127.0.0.1:8001/api/inngest --no-discovery

  Terminal 2 — this layer:
    python inngest_layer.py

Then hook main.py with the emit_* helpers at the bottom of this file.

pip install inngest fastapi uvicorn
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Deque

from dotenv import load_dotenv

load_dotenv()

import inngest
import inngest.fast_api
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("InngestLayer")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

# ── Inngest client ─────────────────────────────────────────────────────────────

inngest_client = inngest.Inngest(
    app_id="mix_agent",
    logger=logging.getLogger("inngest"),
)

# ═══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORES
# ═══════════════════════════════════════════════════════════════════════════════

_results: Dict[str, dict] = {}
_results_lock = threading.Lock()

# Live session registry  { session_id: { ... } }
_sessions: Dict[str, dict] = {}
_sessions_lock = threading.Lock()

# Rolling metrics for the dashboard endpoint
_metrics: Dict[str, Any] = {
    "total_sessions":     0,
    "total_ai_calls":     0,
    "total_tool_calls":   0,
    "total_errors":       0,
    "total_tokens_in":    0,
    "total_tokens_out":   0,
    "tool_call_counts":   defaultdict(int),
    "error_types":        defaultdict(int),
    "ai_latencies_ms":    deque(maxlen=200),   # rolling window
    "tool_latencies_ms":  defaultdict(lambda: deque(maxlen=100)),
    "last_updated":       datetime.now().isoformat(),
}
_metrics_lock = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _store_result(job_id: str, result: Any, error: Optional[str] = None):
    with _results_lock:
        _results[job_id] = {
            "job_id":      job_id,
            "result":      result,
            "error":       error,
            "done":        True,
            "finished_at": datetime.now().isoformat(),
        }


def get_result(job_id: str, timeout: float = 120.0) -> Optional[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _results_lock:
            if job_id in _results:
                return _results.pop(job_id)
        time.sleep(0.5)
    return None


def _trim(text: str, n: int = 200) -> str:
    """Trim a string for safe event payload storage."""
    if not text:
        return ""
    s = str(text).replace("\n", " ")
    return s[:n] + "…" if len(s) > n else s


def _update_metrics(**kwargs):
    with _metrics_lock:
        for k, v in kwargs.items():
            if k == "tool_name":
                _metrics["tool_call_counts"][v] += 1
            elif k == "error_type":
                _metrics["error_types"][v] += 1
            elif k == "ai_latency_ms":
                _metrics["ai_latencies_ms"].append(v)
                _metrics["total_ai_calls"] += 1
            elif k == "tool_latency_ms":
                name = kwargs.get("tool_name_for_latency", "unknown")
                _metrics["tool_latencies_ms"][name].append(v)
            elif k == "tokens_in":
                _metrics["total_tokens_in"] += v
            elif k == "tokens_out":
                _metrics["total_tokens_out"] += v
            elif k == "new_session":
                _metrics["total_sessions"] += 1
            elif k == "new_error":
                _metrics["total_errors"] += 1
            elif k == "new_tool_call":
                _metrics["total_tool_calls"] += 1
        _metrics["last_updated"] = datetime.now().isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — SESSION TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

@inngest_client.create_function(
    fn_id="agent_session_start",
    trigger=inngest.TriggerEvent(event="agent/session.start"),
    retries=0,
)
async def fn_session_start(ctx: inngest.Context) -> dict:
    """
    Fires when the user submits a new prompt.
    Opens a session record and surfaces it in the Dev Server UI.
    """
    d = ctx.event.data
    session_id = d.get("session_id", "?")
    user_input = d.get("user_input", "")
    ts         = d.get("ts", datetime.now().isoformat())

    _update_metrics(new_session=1)

    async def _open():
        with _sessions_lock:
            _sessions[session_id] = {
                "session_id":   session_id,
                "user_input":   user_input,
                "started_at":   ts,
                "status":       "running",
                "iterations":   0,
                "ai_calls":     0,
                "tool_calls":   [],
                "errors":       [],
                "tokens_in":    0,
                "tokens_out":   0,
            }
        return {"opened": True, "session_id": session_id}

    result = await ctx.step.run("open-session", _open)
    ctx.logger.info(f"Session opened: {session_id}  input={_trim(user_input, 60)}")
    return result


@inngest_client.create_function(
    fn_id="agent_session_complete",
    trigger=inngest.TriggerEvent(event="agent/session.complete"),
    retries=0,
)
async def fn_session_complete(ctx: inngest.Context) -> dict:
    """
    Fires when the agent has a final text response for the user.
    Closes the session record and logs a summary.
    """
    d          = ctx.event.data
    session_id = d.get("session_id", "?")
    iterations = int(d.get("iterations", 0))
    tool_count = int(d.get("tool_calls_made", 0))
    tokens_in  = int(d.get("tokens_in", 0))
    tokens_out = int(d.get("tokens_out", 0))
    duration_s = float(d.get("duration_s", 0.0))
    response_p = d.get("response_preview", "")

    async def _close():
        with _sessions_lock:
            sess = _sessions.get(session_id, {})
            sess.update({
                "status":       "done",
                "completed_at": datetime.now().isoformat(),
                "iterations":   iterations,
                "tool_calls_made": tool_count,
                "tokens_in":    tokens_in,
                "tokens_out":   tokens_out,
                "duration_s":   duration_s,
                "response_preview": response_p,
            })
        _update_metrics(tokens_in=tokens_in, tokens_out=tokens_out)
        return {
            "session_id": session_id,
            "iterations": iterations,
            "tool_calls": tool_count,
            "tokens":     tokens_in + tokens_out,
            "duration_s": duration_s,
        }

    result = await ctx.step.run("close-session", _close)

    async def _summarise():
        """Log a human-readable summary step so it's visible in the UI."""
        lines = [
            f"Session  : {session_id}",
            f"Iters    : {iterations}",
            f"Tools    : {tool_count}",
            f"Tokens ← : {tokens_in:,}   → : {tokens_out:,}",
            f"Time     : {duration_s:.2f}s",
            f"Response : {_trim(response_p, 120)}",
        ]
        return "\n".join(lines)

    await ctx.step.run("session-summary", _summarise)
    ctx.logger.info(f"Session closed: {session_id}  iters={iterations} tools={tool_count}")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — AI GENERATION MONITORING  ← the most powerful addition
# ═══════════════════════════════════════════════════════════════════════════════

@inngest_client.create_function(
    fn_id="agent_ai_generation",
    trigger=inngest.TriggerEvent(event="agent/ai.generate"),
    retries=0,
)
async def fn_ai_generation(ctx: inngest.Context) -> dict:
    """
    Fires for EVERY call to client.models.generate_content().
    Steps:
      1. pre-flight  — validate & log the outgoing request
      2. analyse     — inspect token counts, prompt complexity
      3. post-flight — log response stats, detect anomalies
      4. alert       — fire warnings if thresholds breached
    """
    d           = ctx.event.data
    session_id  = d.get("session_id", "?")
    iteration   = int(d.get("iteration", 0))
    model       = d.get("model", "unknown")
    msg_count   = int(d.get("message_count", 0))
    prompt_tok  = int(d.get("prompt_tokens", 0))
    compl_tok   = int(d.get("completion_tokens", 0))
    think_tok   = int(d.get("thinking_tokens", 0))
    cached_tok  = int(d.get("cached_tokens", 0))
    latency_ms  = float(d.get("latency_ms", 0.0))
    has_fc      = bool(d.get("has_function_calls", False))
    fc_names    = d.get("function_call_names", [])
    prompt_prev = d.get("prompt_preview", "")
    resp_prev   = d.get("response_preview", "")
    error       = d.get("error")

    total_tok   = prompt_tok + compl_tok + think_tok

    # Step 1 — Pre-flight log
    async def _preflight():
        return {
            "session_id": session_id,
            "iteration":  iteration,
            "model":      model,
            "messages":   msg_count,
            "prompt_preview": _trim(prompt_prev, 160),
            "ts": datetime.now().isoformat(),
        }

    await ctx.step.run("preflight-log", _preflight)

    # Step 2 — Analyse token usage
    async def _analyse():
        issues = []
        if prompt_tok > 50_000:
            issues.append(f"LARGE CONTEXT: {prompt_tok:,} prompt tokens")
        if think_tok > 10_000:
            issues.append(f"HIGH THINKING: {think_tok:,} thinking tokens")
        if latency_ms > 30_000:
            issues.append(f"SLOW RESPONSE: {latency_ms/1000:.1f}s")
        if cached_tok > 0:
            ratio = cached_tok / max(prompt_tok, 1) * 100
            cache_info = f"Cache hit: {ratio:.0f}% ({cached_tok:,} tokens saved)"
        else:
            cache_info = "No cache hit"

        _update_metrics(
            ai_latency_ms=latency_ms,
            tokens_in=prompt_tok,
            tokens_out=compl_tok,
        )

        with _sessions_lock:
            sess = _sessions.get(session_id, {})
            sess["ai_calls"] = sess.get("ai_calls", 0) + 1
            sess["tokens_in"]  = sess.get("tokens_in", 0)  + prompt_tok
            sess["tokens_out"] = sess.get("tokens_out", 0) + compl_tok

        return {
            "total_tokens":    total_tok,
            "prompt_tokens":   prompt_tok,
            "completion_tokens": compl_tok,
            "thinking_tokens": think_tok,
            "cached_tokens":   cached_tok,
            "cache_info":      cache_info,
            "latency_ms":      latency_ms,
            "issues":          issues,
            "has_tool_calls":  has_fc,
            "tool_calls":      fc_names,
        }

    analysis = await ctx.step.run("token-analysis", _analyse)

    # Step 3 — Response quality log
    async def _post_flight():
        if error:
            return {"status": "error", "error": error}
        return {
            "status":           "ok",
            "response_preview": _trim(resp_prev, 200),
            "has_tool_calls":   has_fc,
            "function_calls":   fc_names,
            "tok_per_sec":      round(compl_tok / max(latency_ms / 1000, 0.001), 1),
        }

    post = await ctx.step.run("response-log", _post_flight)

    # Step 4 — Threshold alerts (shows as a separate step in UI)
    async def _alerts():
        fired = []
        issues = analysis.get("issues", [])
        for issue in issues:
            ctx.logger.warning(f"⚠ AI ALERT [{session_id}] iter={iteration}: {issue}")
            fired.append(issue)
        if error:
            ctx.logger.error(f"✗ AI ERROR [{session_id}] iter={iteration}: {error}")
            fired.append(f"ERROR: {error}")
        return {"alerts_fired": len(fired), "alerts": fired}

    await ctx.step.run("threshold-alerts", _alerts)

    ctx.logger.info(
        f"AI call: session={session_id} iter={iteration} "
        f"model={model} tok={total_tok:,} lat={latency_ms:.0f}ms "
        f"fc={'yes' if has_fc else 'no'}"
    )

    return {
        "session_id": session_id,
        "iteration":  iteration,
        "analysis":   analysis,
        "post":       post,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — ITERATION TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

@inngest_client.create_function(
    fn_id="agent_iteration",
    trigger=inngest.TriggerEvent(event="agent/iteration"),
    retries=0,
)
async def fn_iteration(ctx: inngest.Context) -> dict:
    """
    Fires at the start of each agentic loop iteration.
    Tracks how many iterations a single user request takes and builds
    a timeline of decisions (tool_calls vs final_answer).
    """
    d           = ctx.event.data
    session_id  = d.get("session_id", "?")
    iteration   = int(d.get("iteration", 0))
    phase       = d.get("phase", "start")     # "start" | "tool_calls" | "final"
    tool_names  = d.get("tool_names", [])
    ts          = d.get("ts", datetime.now().isoformat())

    async def _record():
        with _sessions_lock:
            sess = _sessions.get(session_id, {})
            sess["iterations"] = max(sess.get("iterations", 0), iteration)
            timeline = sess.setdefault("timeline", [])
            timeline.append({
                "iteration": iteration,
                "phase":     phase,
                "tools":     tool_names,
                "ts":        ts,
            })
        return {
            "session_id": session_id,
            "iteration":  iteration,
            "phase":      phase,
            "tools":      tool_names,
        }

    result = await ctx.step.run("record-iteration", _record)

    if phase == "final":
        ctx.logger.info(
            f"Final answer reached: session={session_id} after {iteration} iterations"
        )
    else:
        ctx.logger.info(
            f"Iteration {iteration}: session={session_id} phase={phase} "
            f"tools={tool_names}"
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — TOOL PIPELINE  (enhanced from original)
# ═══════════════════════════════════════════════════════════════════════════════

@inngest_client.create_function(
    fn_id="agent_tool_call",
    trigger=inngest.TriggerEvent(event="agent/tool.call"),
    retries=0,
)
async def fn_tool_call(ctx: inngest.Context) -> dict:
    """
    Fires for every tool call. Tracks timing, args preview, result preview,
    success/failure, and guard decisions.
    Steps:
      1. classify  — categorise the tool call type
      2. record    — store in session + metrics
      3. analyse   — detect slow or failing tools
    """
    d           = ctx.event.data
    session_id  = d.get("session_id", "?")
    iteration   = int(d.get("iteration", 0))
    tool_name   = d.get("tool_name", "?")
    args_prev   = d.get("args_preview", "")
    result_prev = d.get("result_preview", "")
    duration_ms = float(d.get("duration_ms", 0.0))
    success     = bool(d.get("success", True))
    blocked     = bool(d.get("blocked", False))
    error_msg   = d.get("error_msg", "")

    TOOL_CATEGORIES = {
        "file": {"get_file_content", "write_file", "patch_file", "get_files_info"},
        "exec": {"run_shell", "run_python_file", "build_project", "install_dependencies"},
        "search": {"web_search", "web_fetch", "search_code"},
        "memory": {"remember_fact", "recall_fact", "forget_fact", "list_facts",
                   "memory_add_pattern", "memory_get_context", "memory_save_project_structure"},
        "planning": {"plan_project", "task_decomposer", "execute_task", "get_project_map"},
        "system": {"recording_start", "recording_stop", "recording_snapshot", "recording_analyze",
                   "benchmark_solution", "verify_change"},
    }

    # Step 1 — Classify
    async def _classify():
        cat = "other"
        for category, names in TOOL_CATEGORIES.items():
            if tool_name in names:
                cat = category
                break
        return {"tool_name": tool_name, "category": cat, "blocked": blocked}

    classification = await ctx.step.run("classify-tool", _classify)

    # Step 2 — Record
    async def _record():
        _update_metrics(
            tool_name=tool_name,
            new_tool_call=1,
            tool_latency_ms=duration_ms,
            tool_name_for_latency=tool_name,
        )
        if not success or blocked:
            _update_metrics(new_error=1, error_type=f"tool:{tool_name}")

        entry = {
            "tool":        tool_name,
            "category":    classification["category"],
            "args":        _trim(args_prev, 120),
            "result":      _trim(result_prev, 120),
            "duration_ms": duration_ms,
            "success":     success,
            "blocked":     blocked,
            "iteration":   iteration,
            "ts":          datetime.now().isoformat(),
        }
        with _sessions_lock:
            sess = _sessions.get(session_id, {})
            sess.setdefault("tool_calls", []).append(entry)

        return entry

    record = await ctx.step.run("record-tool-call", _record)

    # Step 3 — Analyse
    async def _analyse():
        warnings = []
        if blocked:
            warnings.append(f"PATH GUARD BLOCKED: {tool_name}({_trim(args_prev, 60)})")
        if not success and not blocked:
            warnings.append(f"TOOL FAILED: {tool_name} — {_trim(error_msg, 80)}")
        if duration_ms > 15_000:
            warnings.append(f"SLOW TOOL: {tool_name} took {duration_ms/1000:.1f}s")
        for w in warnings:
            ctx.logger.warning(f"⚠ TOOL ALERT [{session_id}]: {w}")
        return {"warnings": warnings}

    analysis = await ctx.step.run("tool-analysis", _analyse)

    ctx.logger.info(
        f"Tool: {tool_name} session={session_id} iter={iteration} "
        f"{'BLOCKED' if blocked else 'OK' if success else 'FAIL'} "
        f"lat={duration_ms:.0f}ms"
    )

    return {
        "session_id":     session_id,
        "tool_name":      tool_name,
        "classification": classification,
        "record":         record,
        "analysis":       analysis,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — TOKEN BUDGET TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

# Per-session budget (can be overridden via POST /api/budget)
_token_budgets: Dict[str, int] = {}
_DEFAULT_TOKEN_BUDGET = 200_000   # tokens per session before warning

@inngest_client.create_function(
    fn_id="agent_token_budget",
    trigger=inngest.TriggerEvent(event="agent/tokens.update"),
    retries=0,
)
async def fn_token_budget(ctx: inngest.Context) -> dict:
    """
    Accumulates token usage per session and fires alerts when
    configurable thresholds are crossed (50%, 80%, 100%).
    """
    d           = ctx.event.data
    session_id  = d.get("session_id", "?")
    tokens_in   = int(d.get("tokens_in", 0))
    tokens_out  = int(d.get("tokens_out", 0))
    total_so_far = int(d.get("session_total_tokens", 0))

    budget = _token_budgets.get(session_id, _DEFAULT_TOKEN_BUDGET)

    async def _check():
        pct = (total_so_far / budget * 100) if budget > 0 else 0
        alerts = []
        if pct >= 100:
            alerts.append(f"🔴 TOKEN BUDGET EXCEEDED: {total_so_far:,}/{budget:,} ({pct:.0f}%)")
        elif pct >= 80:
            alerts.append(f"🟡 TOKEN BUDGET 80%: {total_so_far:,}/{budget:,}")
        elif pct >= 50:
            alerts.append(f"🟢 TOKEN BUDGET 50%: {total_so_far:,}/{budget:,}")
        for a in alerts:
            ctx.logger.warning(f"TOKEN BUDGET [{session_id}]: {a}")
        return {
            "session_id":    session_id,
            "tokens_this_call": tokens_in + tokens_out,
            "session_total": total_so_far,
            "budget":        budget,
            "usage_pct":     round(pct, 1),
            "alerts":        alerts,
        }

    return await ctx.step.run("check-budget", _check)


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 6 — ERROR TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

@inngest_client.create_function(
    fn_id="agent_error_tracker",
    trigger=inngest.TriggerEvent(event="agent/error"),
    retries=0,
)
async def fn_error_tracker(ctx: inngest.Context) -> dict:
    """
    Central error collector. Every exception in the agent loop is
    routed here for classification and triage guidance.
    """
    d           = ctx.event.data
    session_id  = d.get("session_id", "?")
    error_type  = d.get("error_type", "unknown")
    error_msg   = d.get("error_msg", "")
    context_str = d.get("context", "")
    iteration   = int(d.get("iteration", 0))
    ts          = d.get("ts", datetime.now().isoformat())

    ERROR_TRIAGE = {
        "KeyboardInterrupt":    ("user",     "User cancelled the request."),
        "GuardError":           ("security", "PathGuard blocked a file access. Check the path."),
        "ValueError":           ("config",   "Configuration or argument error. Check tool args."),
        "TimeoutError":         ("infra",    "Operation timed out. Increase timeout or retry."),
        "ConnectionError":      ("network",  "Network issue. Check connectivity."),
        "ImportError":          ("install",  "Missing module. Check dependencies."),
        "json.JSONDecodeError": ("parsing",  "Bad JSON. Check tool output format."),
        "PermissionError":      ("security", "File permission denied. Check PathGuard config."),
    }

    async def _triage():
        severity, advice = ERROR_TRIAGE.get(error_type, ("unknown", "Inspect logs for details."))
        _update_metrics(new_error=1, error_type=error_type)
        with _sessions_lock:
            sess = _sessions.get(session_id, {})
            sess.setdefault("errors", []).append({
                "type":      error_type,
                "msg":       _trim(error_msg, 200),
                "iteration": iteration,
                "ts":        ts,
            })
        ctx.logger.error(
            f"✗ ERROR [{session_id}] iter={iteration}: "
            f"{error_type}: {_trim(error_msg, 80)}"
        )
        return {
            "session_id": session_id,
            "error_type": error_type,
            "severity":   severity,
            "advice":     advice,
            "context":    _trim(context_str, 150),
            "iteration":  iteration,
        }

    result = await ctx.step.run("triage-error", _triage)

    async def _log_structured():
        return {
            "error":     error_msg,
            "type":      error_type,
            "iteration": iteration,
            "session":   session_id,
            "ts":        ts,
        }

    await ctx.step.run("structured-log", _log_structured)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# ORIGINAL HEAVY TOOL FUNCTIONS  (retained + improved)
# ═══════════════════════════════════════════════════════════════════════════════

@inngest_client.create_function(
    fn_id="agent_plan_project",
    trigger=inngest.TriggerEvent(event="agent/plan_project"),
    retries=3,
)
async def fn_plan_project(ctx: inngest.Context) -> str:
    job_id      = ctx.event.data.get("job_id", "")
    task_desc   = ctx.event.data.get("task_description", "")
    working_dir = ctx.event.data.get("working_directory", os.getcwd())
    ctx.logger.info(f"Planning: {task_desc[:60]}")
    try:
        result = await ctx.step.run(
            "plan",
            lambda: _run_plan_project(task_desc, working_dir)
        )
        _store_result(job_id, result)
        return result
    except Exception as e:
        _store_result(job_id, "", error=str(e))
        raise


def _run_plan_project(task_desc: str, working_dir: str) -> str:
    from func.plan_project import plan_project
    return plan_project(working_directory=working_dir, task_description=task_desc,
                        save_plan=True, show_live=False)


@inngest_client.create_function(
    fn_id="agent_task_decomposer",
    trigger=inngest.TriggerEvent(event="agent/task_decomposer"),
    retries=3,
)
async def fn_task_decomposer(ctx: inngest.Context) -> str:
    job_id      = ctx.event.data.get("job_id", "")
    task_desc   = ctx.event.data.get("task_description", "")
    strategy    = ctx.event.data.get("strategy", "dag")
    working_dir = ctx.event.data.get("working_directory", os.getcwd())
    try:
        result = await ctx.step.run(
            "decompose",
            lambda: _run_decomposer(task_desc, strategy, working_dir)
        )
        _store_result(job_id, result)
        return result
    except Exception as e:
        _store_result(job_id, "", error=str(e))
        raise


def _run_decomposer(task_desc: str, strategy: str, working_dir: str) -> str:
    from func.task_decomposer import task_decomposer
    return task_decomposer(working_directory=working_dir, task_description=task_desc,
                           strategy=strategy, save_plan=True)


@inngest_client.create_function(
    fn_id="agent_build_project",
    trigger=inngest.TriggerEvent(event="agent/build_project"),
    retries=2,
)
async def fn_build_project(ctx: inngest.Context) -> str:
    job_id       = ctx.event.data.get("job_id", "")
    working_dir  = ctx.event.data.get("working_directory", os.getcwd())
    project_name = ctx.event.data.get("project_name", "")
    framework    = ctx.event.data.get("framework", "nextjs")
    project_type = ctx.event.data.get("project_type", "")
    ctx.logger.info(f"Building: {project_name} ({framework})")
    try:
        install_result = await ctx.step.run(
            "install-deps", lambda: _run_install(working_dir)
        )
        ctx.logger.info(f"Install: {install_result[:60]}")
        build_result = await ctx.step.run(
            "build",
            lambda: _run_build(working_dir, project_name, project_type, framework)
        )
        _store_result(job_id, build_result)
        return build_result
    except Exception as e:
        _store_result(job_id, "", error=str(e))
        raise


def _run_install(working_dir: str) -> str:
    from func.build import install_dependencies
    return install_dependencies(working_directory=working_dir, package_manager="npm",
                                show_live=False)


def _run_build(working_dir: str, name: str, ptype: str, framework: str) -> str:
    from func.build import build_project
    return build_project(working_directory=working_dir, project_name=name,
                         project_type=ptype, framework=framework, show_live=False)


@inngest_client.create_function(
    fn_id="agent_benchmark",
    trigger=inngest.TriggerEvent(event="agent/benchmark_solution"),
    retries=2,
)
async def fn_benchmark(ctx: inngest.Context) -> str:
    job_id      = ctx.event.data.get("job_id", "")
    working_dir = ctx.event.data.get("working_directory", os.getcwd())
    task_id     = ctx.event.data.get("task_id", "default")
    target      = ctx.event.data.get("target", "")
    target_type = ctx.event.data.get("target_type", "python_file")
    iterations  = int(ctx.event.data.get("iterations", 10))
    ctx.logger.info(f"Benchmarking: {target} ({target_type})")
    try:
        await ctx.step.run("warmup", lambda: time.sleep(0.1) or "warmup done")
        result = await ctx.step.run(
            "benchmark",
            lambda: _run_benchmark(working_dir, task_id, target, target_type, iterations)
        )
        _store_result(job_id, result)
        return result
    except Exception as e:
        _store_result(job_id, "", error=str(e))
        raise


def _run_benchmark(wd, tid, target, ttype, iters):
    from func.benchmark_solution import benchmark_solution
    return benchmark_solution(working_directory=wd, task_id=tid, target=target,
                              target_type=ttype, iterations=iters)


@inngest_client.create_function(
    fn_id="agent_web_search",
    trigger=inngest.TriggerEvent(event="agent/web_search"),
    retries=3,
)
async def fn_web_search(ctx: inngest.Context) -> str:
    job_id      = ctx.event.data.get("job_id", "")
    query       = ctx.event.data.get("query", "")
    fetch_url   = ctx.event.data.get("fetch_url", "")
    prompt      = ctx.event.data.get("prompt", "Summarise the key information.")
    working_dir = ctx.event.data.get("working_directory", os.getcwd())
    try:
        search_result = await ctx.step.run("search", lambda: _do_web_search(working_dir, query))
        final = search_result
        if fetch_url:
            fetch_result = await ctx.step.run("fetch", lambda: _do_web_fetch(working_dir, fetch_url, prompt))
            final = search_result + "\n\n--- Fetched Content ---\n" + fetch_result
        _store_result(job_id, final)
        return final
    except Exception as e:
        _store_result(job_id, "", error=str(e))
        raise


def _do_web_search(wd, query):
    from func.web_fetch_search import web_search
    return web_search(working_directory=wd, query=query)


def _do_web_fetch(wd, url, prompt):
    from func.web_fetch_search import web_fetch
    return web_fetch(working_directory=wd, url=url, prompt=prompt)


@inngest_client.create_function(
    fn_id="agent_verify_change",
    trigger=inngest.TriggerEvent(event="agent/verify_change"),
    retries=2,
)
async def fn_verify_change(ctx: inngest.Context) -> str:
    job_id      = ctx.event.data.get("job_id", "")
    working_dir = ctx.event.data.get("working_directory", os.getcwd())
    scope       = ctx.event.data.get("scope", "lint")
    try:
        result = await ctx.step.run("verify", lambda: _do_verify(working_dir, scope))
        _store_result(job_id, result)
        return result
    except Exception as e:
        _store_result(job_id, "", error=str(e))
        raise


def _do_verify(wd, scope):
    from func.verify_change import verify_change
    return verify_change(wd, scope=scope)


@inngest_client.create_function(
    fn_id="agent_run_shell",
    trigger=inngest.TriggerEvent(event="agent/run_shell"),
    retries=1,
)
async def fn_run_shell(ctx: inngest.Context) -> str:
    job_id      = ctx.event.data.get("job_id", "")
    working_dir = ctx.event.data.get("working_directory", os.getcwd())
    command     = ctx.event.data.get("command", "")
    timeout     = int(ctx.event.data.get("timeout", 60))
    try:
        result = await ctx.step.run("shell", lambda: _do_shell(working_dir, command, timeout))
        _store_result(job_id, result)
        return result
    except Exception as e:
        _store_result(job_id, "", error=str(e))
        raise


def _do_shell(wd, command, timeout):
    from func.run_shell import run_shell
    return run_shell(wd, command=command, timeout=timeout)


@inngest_client.create_function(
    fn_id="agent_recording_analyze",
    trigger=inngest.TriggerEvent(event="agent/recording_analyze"),
    retries=1,
)
async def fn_recording_analyze(ctx: inngest.Context) -> str:
    job_id      = ctx.event.data.get("job_id", "")
    working_dir = ctx.event.data.get("working_directory", os.getcwd())
    session_id  = ctx.event.data.get("session_id")
    focus       = ctx.event.data.get("focus", "full")
    try:
        result = await ctx.step.run("analyze", lambda: _do_analyze(working_dir, session_id, focus))
        _store_result(job_id, result)
        return result
    except Exception as e:
        _store_result(job_id, "", error=str(e))
        raise


def _do_analyze(wd, session_id, focus):
    from func.sys_agent_recording import recording_analyze
    return recording_analyze(working_directory=wd, session_id=session_id, focus=focus)


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════════

ALL_FUNCTIONS = [
    # Observability layers (new)
    fn_session_start,
    fn_session_complete,
    fn_ai_generation,
    fn_iteration,
    fn_tool_call,
    fn_token_budget,
    fn_error_tracker,
    # Original heavy-job functions
    fn_plan_project,
    fn_task_decomposer,
    fn_build_project,
    fn_benchmark,
    fn_web_search,
    fn_verify_change,
    fn_run_shell,
    fn_recording_analyze,
]

EVENT_MAP = {
    "plan_project":       "agent/plan_project",
    "task_decomposer":    "agent/task_decomposer",
    "build_project":      "agent/build_project",
    "benchmark_solution": "agent/benchmark_solution",
    "web_search":         "agent/web_search",
    "verify_change":      "agent/verify_change",
    "run_shell":          "agent/run_shell",
    "recording_analyze":  "agent/recording_analyze",
}

app = FastAPI(title="MIX Agent — Full Observability Layer")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

inngest.fast_api.serve(app, inngest_client, ALL_FUNCTIONS)


@app.get("/api/result/{job_id}")
async def poll_result(job_id: str):
    with _results_lock:
        r = _results.get(job_id)
    if r is None:
        return {"done": False, "job_id": job_id}
    with _results_lock:
        _results.pop(job_id, None)
    return r


@app.get("/api/sessions")
async def list_sessions():
    """Return all live + recent session records."""
    with _sessions_lock:
        return {"sessions": list(_sessions.values())}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if not sess:
        return {"error": "Session not found"}
    return sess


@app.get("/api/metrics")
async def get_metrics():
    """Rolling metrics dashboard."""
    with _metrics_lock:
        lats = list(_metrics["ai_latencies_ms"])
        avg_lat = round(sum(lats) / len(lats), 1) if lats else 0
        p95_lat = round(sorted(lats)[int(len(lats) * 0.95)], 1) if len(lats) > 5 else 0
        tool_lats = {
            k: round(sum(v) / len(v), 1)
            for k, v in _metrics["tool_latencies_ms"].items() if v
        }
        return {
            "total_sessions":       _metrics["total_sessions"],
            "total_ai_calls":       _metrics["total_ai_calls"],
            "total_tool_calls":     _metrics["total_tool_calls"],
            "total_errors":         _metrics["total_errors"],
            "total_tokens_in":      _metrics["total_tokens_in"],
            "total_tokens_out":     _metrics["total_tokens_out"],
            "ai_latency_avg_ms":    avg_lat,
            "ai_latency_p95_ms":    p95_lat,
            "tool_call_counts":     dict(_metrics["tool_call_counts"]),
            "avg_tool_latency_ms":  tool_lats,
            "error_types":          dict(_metrics["error_types"]),
            "last_updated":         _metrics["last_updated"],
        }


@app.post("/api/budget/{session_id}")
async def set_budget(session_id: str, budget: int = 200_000):
    """Override token budget for a specific session."""
    _token_budgets[session_id] = budget
    return {"session_id": session_id, "budget": budget}


@app.get("/api/health")
async def health():
    return {
        "status":    "ok",
        "functions": len(ALL_FUNCTIONS),
        "sessions":  len(_sessions),
        "ts":        datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DISPATCHER  (used by call_function.py for heavy background jobs)
# ═══════════════════════════════════════════════════════════════════════════════

INNGEST_LAYER_URL = os.environ.get("INNGEST_LAYER_URL", "http://127.0.0.1:8001")
INNGEST_DEV_URL   = os.environ.get("INNGEST_DEV_URL",   "http://127.0.0.1:8288")
DURABLE_TOOLS     = set(EVENT_MAP.keys())


def _fire_event(event_name: str, data: dict, timeout_s: float = 3.0) -> bool:
    """
    Fire-and-forget: send an event to the Inngest Dev Server.
    Returns True on success, False if Inngest is unavailable.
    """
    import urllib.request, urllib.error
    payload = json.dumps({"name": event_name, "data": data}).encode()
    try:
        req = urllib.request.Request(
            f"{INNGEST_DEV_URL}/e/key",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return r.status in (200, 201, 202)
    except Exception:
        return False


def dispatch_as_inngest(
    tool_name: str,
    args: dict,
    working_directory: str,
    block: bool = True,
    timeout: float = 120.0,
) -> Optional[str]:
    """Send a heavy tool call to Inngest as a durable background job."""
    import uuid, urllib.request
    event_name = EVENT_MAP.get(tool_name)
    if not event_name:
        return None

    job_id  = str(uuid.uuid4())
    payload = json.dumps({
        "name": event_name,
        "data": {"job_id": job_id, "working_directory": working_directory, **args},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{INNGEST_DEV_URL}/e/key",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status not in (200, 201, 202):
                return None
    except Exception:
        return None

    if not block:
        return f"[inngest:job_id={job_id}]"

    result = get_result(job_id, timeout=timeout)
    if result is None:
        return f"[inngest timeout after {timeout}s for job {job_id}]"
    if result.get("error"):
        return f"[inngest error] {result['error']}"
    return result.get("result", "")


def is_inngest_available() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{INNGEST_LAYER_URL}/api/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# EMIT HELPERS  ← add these calls into main.py's process_request()
# ═══════════════════════════════════════════════════════════════════════════════

def emit_session_start(session_id: str, user_input: str):
    """Call at the top of process_request()."""
    _fire_event("agent/session.start", {
        "session_id": session_id,
        "user_input": _trim(user_input, 300),
        "ts": datetime.now().isoformat(),
    })


def emit_session_complete(
    session_id: str,
    response: str,
    iterations: int,
    tokens_in: int,
    tokens_out: int,
    duration_s: float,
    tool_calls_made: int = 0,
):
    """Call just before returning from process_request()."""
    _fire_event("agent/session.complete", {
        "session_id":       session_id,
        "response_preview": _trim(response, 200),
        "iterations":       iterations,
        "tokens_in":        tokens_in,
        "tokens_out":       tokens_out,
        "duration_s":       round(duration_s, 3),
        "tool_calls_made":  tool_calls_made,
    })


def emit_ai_generate_start(
    session_id: str,
    iteration: int,
    model: str,
    message_count: int,
    prompt_preview: str = "",
):
    """
    Call BEFORE client.models.generate_content().
    Gives you the outgoing prompt in the Dev Server.
    """
    _fire_event("agent/iteration", {
        "session_id": session_id,
        "iteration":  iteration,
        "phase":      "start",
        "ts":         datetime.now().isoformat(),
    })
    # pre-emit of the AI call (the full record comes in emit_ai_generate_complete)
    _fire_event("agent/ai.generate", {
        "session_id":      session_id,
        "iteration":       iteration,
        "model":           model,
        "message_count":   message_count,
        "prompt_preview":  _trim(prompt_preview, 200),
        "prompt_tokens":   0,    # not yet known
        "completion_tokens": 0,
        "thinking_tokens": 0,
        "cached_tokens":   0,
        "latency_ms":      0,
        "has_function_calls": False,
        "function_call_names": [],
        "phase": "start",
    })


def emit_ai_generate_complete(
    session_id: str,
    iteration: int,
    model: str,
    message_count: int,
    prompt_tokens: int,
    completion_tokens: int,
    thinking_tokens: int,
    cached_tokens: int,
    latency_ms: float,
    has_function_calls: bool,
    function_call_names: List[str],
    response_preview: str = "",
    error: Optional[str] = None,
):
    """Call AFTER client.models.generate_content() returns."""
    session_total = prompt_tokens + completion_tokens + thinking_tokens

    _fire_event("agent/ai.generate", {
        "session_id":          session_id,
        "iteration":           iteration,
        "model":               model,
        "message_count":       message_count,
        "prompt_tokens":       prompt_tokens,
        "completion_tokens":   completion_tokens,
        "thinking_tokens":     thinking_tokens,
        "cached_tokens":       cached_tokens,
        "latency_ms":          latency_ms,
        "has_function_calls":  has_function_calls,
        "function_call_names": function_call_names,
        "response_preview":    _trim(response_preview, 200),
        "error":               error,
        "phase":               "complete",
    })

    _fire_event("agent/tokens.update", {
        "session_id":          session_id,
        "tokens_in":           prompt_tokens,
        "tokens_out":          completion_tokens,
        "session_total_tokens": session_total,
    })

    if has_function_calls:
        _fire_event("agent/iteration", {
            "session_id": session_id,
            "iteration":  iteration,
            "phase":      "tool_calls",
            "tool_names": function_call_names,
            "ts":         datetime.now().isoformat(),
        })
    else:
        _fire_event("agent/iteration", {
            "session_id": session_id,
            "iteration":  iteration,
            "phase":      "final",
            "tool_names": [],
            "ts":         datetime.now().isoformat(),
        })


def emit_tool_call(
    session_id: str,
    iteration: int,
    tool_name: str,
    args: dict,
    result: str,
    duration_ms: float,
    success: bool = True,
    blocked: bool = False,
    error_msg: str = "",
):
    """Call after every call_function() invocation in main.py."""
    args_preview = _trim(json.dumps({k: v for k, v in list(args.items())[:3]}), 150)
    _fire_event("agent/tool.call", {
        "session_id":    session_id,
        "iteration":     iteration,
        "tool_name":     tool_name,
        "args_preview":  args_preview,
        "result_preview": _trim(result, 150),
        "duration_ms":   duration_ms,
        "success":       success,
        "blocked":       blocked,
        "error_msg":     error_msg,
    })


def emit_error(
    session_id: str,
    iteration: int,
    error_type: str,
    error_msg: str,
    context: str = "",
):
    """Call from any except block in main.py."""
    _fire_event("agent/error", {
        "session_id": session_id,
        "iteration":  iteration,
        "error_type": error_type,
        "error_msg":  error_msg,
        "context":    context,
        "ts":         datetime.now().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# HOOK PATCH for call_function.py
# ═══════════════════════════════════════════════════════════════════════════════
# The original call_function.py has hook_tool_call / hook_tool_result stubs.
# If you want Inngest tool monitoring without touching call_function.py,
# set INNGEST_AUTO_HOOK=1 and call install_call_function_hooks() from main.py.

_hook_session_id: Optional[str] = None
_hook_iteration:  int = 0


def set_hook_context(session_id: str, iteration: int):
    """Called from main.py at the start of each iteration."""
    global _hook_session_id, _hook_iteration
    _hook_session_id = session_id
    _hook_iteration  = iteration


def hook_tool_call_inngest(tool_name: str, args: dict) -> float:
    """
    Drop-in replacement / supplement for sys_agent_recording.hook_tool_call.
    Returns t0 for duration tracking.
    """
    return time.perf_counter()


def hook_tool_result_inngest(
    t0: float,
    tool_name: str,
    result: str,
    blocked: bool = False,
    error_msg: str = "",
):
    """
    Drop-in replacement / supplement for sys_agent_recording.hook_tool_result.
    """
    duration_ms = (time.perf_counter() - t0) * 1000
    success     = not blocked and not error_msg
    if _hook_session_id:
        # fire-and-forget in a daemon thread so we never block the agent
        threading.Thread(
            target=emit_tool_call,
            args=(_hook_session_id, _hook_iteration, tool_name, {}, result,
                  duration_ms, success, blocked, error_msg),
            daemon=True,
        ).start()


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER BOOTSTRAP
# ═══════════════════════════════════════════════════════════════════════════════

def run_server(port: int = 8001):
    log.info("=" * 60)
    log.info("  MIX Agent — Full Observability Inngest Layer")
    log.info(f"  Endpoint  → http://127.0.0.1:{port}/api/inngest")
    log.info(f"  Health    → http://127.0.0.1:{port}/api/health")
    log.info(f"  Sessions  → http://127.0.0.1:{port}/api/sessions")
    log.info(f"  Metrics   → http://127.0.0.1:{port}/api/metrics")
    log.info(f"  Result    → http://127.0.0.1:{port}/api/result/<job_id>")
    log.info("")
    log.info("  Layers monitored:")
    log.info("    1  Session       agent/session.start|complete")
    log.info("    2  AI Generation agent/ai.generate")
    log.info("    3  Iterations    agent/iteration")
    log.info("    4  Tool Pipeline agent/tool.call")
    log.info("    5  Token Budget  agent/tokens.update")
    log.info("    6  Errors        agent/error")
    log.info("")
    log.info("  Start Dev Server:")
    log.info(f"  npx inngest-cli@latest dev -u http://127.0.0.1:{port}/api/inngest --no-discovery")
    log.info("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", access_log=False)


def start_background(port: int = 8001):
    t = threading.Thread(target=run_server, args=(port,), daemon=True, name="InngestLayer")
    t.start()
    time.sleep(1.0)
    log.info(f"Inngest layer started on :{port}")
    return t


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ns = ap.parse_args()
    run_server(ns.port)
