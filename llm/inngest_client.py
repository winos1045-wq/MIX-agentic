"""
inngest_client.py  —  MIX Agent × Inngest Integration
======================================================
Durable execution + observability layer for every tool call,
LLM request, and agent event in the MIX Agent.

Dev vs Prod mode is controlled entirely by the INNGEST_DEV env var:
    INNGEST_DEV=1  → Dev Server  (http://127.0.0.1:8288)
    INNGEST_DEV=0  → Production  (Inngest Cloud)

Usage:
    from inngest_client import mix_inngest, send_event, EventNames

    send_event(EventNames.TOOL_CALLED, {
        "tool_name": "write_file",
        "args":      {"file_path": "main.py"},
        "session":   session_id,
    })
"""

from __future__ import annotations

import os
import time
import uuid
import threading
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# ── Inngest ───────────────────────────────────────────────────────────────────
try:
    import inngest
    _INNGEST_AVAILABLE = True
except ImportError:
    _INNGEST_AVAILABLE = False
    logging.getLogger("MIX.inngest").warning(
        "inngest package not found. Run: pip install inngest\n"
        "Falling back to no-op stubs — the agent still works normally."
    )

log = logging.getLogger("MIX.inngest")

# ── Resolve mode from env ─────────────────────────────────────────────────────
_IS_DEV = os.getenv("INNGEST_DEV", "1") == "1"

# When in dev mode, always point at the local Dev Server
_EVENT_API_URL = (
    os.getenv("INNGEST_EVENT_API_BASE_URL", "http://127.0.0.1:8288")
    if _IS_DEV
    else os.getenv("INNGEST_EVENT_API_BASE_URL", "https://inn.gs")
)

_SIGNING_KEY = os.getenv("INNGEST_SIGNING_KEY", "local" if _IS_DEV else "")

log.debug(
    f"[Inngest] mode={'DEV' if _IS_DEV else 'PROD'}  "
    f"event_api={_EVENT_API_URL}"
)


# ============================================================================
# EVENT NAME REGISTRY
# ============================================================================

class EventNames:
    # LLM lifecycle
    LLM_REQUEST_STARTED   = "agent/llm.request.started"
    LLM_REQUEST_COMPLETED = "agent/llm.request.completed"
    LLM_REQUEST_FAILED    = "agent/llm.request.failed"

    # Tool lifecycle
    TOOL_CALLED           = "agent/tool.called"
    TOOL_COMPLETED        = "agent/tool.completed"
    TOOL_BLOCKED          = "agent/tool.blocked"
    TOOL_ERRORED          = "agent/tool.errored"

    # Session lifecycle
    SESSION_STARTED       = "agent/session.started"
    SESSION_ENDED         = "agent/session.ended"
    SESSION_CLEARED       = "agent/session.cleared"

    # Agent group
    GROUP_JOINED          = "agent/group.joined"
    GROUP_LEFT            = "agent/group.left"
    GROUP_MESSAGE_SENT    = "agent/group.message.sent"

    # Memory / facts
    FACT_REMEMBERED       = "agent/fact.remembered"
    FACT_RECALLED         = "agent/fact.recalled"
    FACT_FORGOTTEN        = "agent/fact.forgotten"

    # Recording
    RECORDING_STARTED     = "agent/recording.started"
    RECORDING_STOPPED     = "agent/recording.stopped"
    RECORDING_SNAPSHOT    = "agent/recording.snapshot"


# ============================================================================
# INNGEST CLIENT + FUNCTIONS
# ============================================================================

if _INNGEST_AVAILABLE:
    # NOTE: dev/prod mode is driven by INNGEST_DEV env var which the SDK
    # reads automatically — do NOT pass is_dev= as a constructor argument.
    mix_inngest = inngest.Inngest(
        app_id="mix-agent",
        event_api_base_url=_EVENT_API_URL,
        signing_key=_SIGNING_KEY,
        logger=log,
    )

    # ── 1. Tool Execution Analyzer ────────────────────────────────────────────
    @mix_inngest.create_function(
        fn_id="analyze-tool-execution",
        name="Analyze Tool Execution",
        trigger=inngest.TriggerEvent(event=EventNames.TOOL_COMPLETED),
        concurrency=[inngest.Concurrency(limit=5)],
    )
    async def analyze_tool_execution(ctx: inngest.Context, step: inngest.Step):
        data      = ctx.event.data
        tool      = data.get("tool_name", "unknown")
        duration  = data.get("duration_ms", 0)
        session   = data.get("session_id", "?")
        success   = data.get("success", True)

        summary = await step.run(
            "build-summary",
            lambda: {
                "tool":        tool,
                "session":     session,
                "duration_ms": duration,
                "success":     success,
                "slow":        duration > 5000,
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        if summary["slow"]:
            await step.send_event(
                "flag-slow-tool",
                inngest.Event(
                    name="agent/tool.slow",
                    data={**summary, "threshold_ms": 5000},
                )
            )
        return summary

    # ── 2. LLM Request Tracker ────────────────────────────────────────────────
    @mix_inngest.create_function(
        fn_id="track-llm-request",
        name="Track LLM Request",
        trigger=inngest.TriggerEvent(event=EventNames.LLM_REQUEST_COMPLETED),
        concurrency=[inngest.Concurrency(limit=10)],
    )
    async def track_llm_request(ctx: inngest.Context, step: inngest.Step):
        data    = ctx.event.data
        tokens  = data.get("tokens", {})
        session = data.get("session_id", "?")

        record = await step.run(
            "record-tokens",
            lambda: {
                "session_id":        session,
                "prompt_tokens":     tokens.get("prompt", 0),
                "completion_tokens": tokens.get("completion", 0),
                "thinking_tokens":   tokens.get("thinking", 0),
                "cached_tokens":     tokens.get("cached", 0),
                "total_tokens":      sum(tokens.values()),
                "model":             data.get("model", "unknown"),
                "iteration":         data.get("iteration", 0),
                "recorded_at":       datetime.now(timezone.utc).isoformat(),
            }
        )

        if record["total_tokens"] > 50_000:
            await step.send_event(
                "warn-high-token-usage",
                inngest.Event(name="agent/llm.high_tokens", data=record),
            )
        return record

    # ── 3. Session Lifecycle Monitor ──────────────────────────────────────────
    @mix_inngest.create_function(
        fn_id="monitor-session",
        name="Monitor Session Lifecycle",
        trigger=inngest.TriggerEvent(event=EventNames.SESSION_STARTED),
    )
    async def monitor_session(ctx: inngest.Context, step: inngest.Step):
        data       = ctx.event.data
        session_id = data.get("session_id", str(uuid.uuid4()))
        started_at = data.get("started_at", datetime.now(timezone.utc).isoformat())
        cwd        = data.get("cwd", "unknown")

        await step.run(
            "log-session-start",
            lambda: log.info(f"[Inngest] Session {session_id} started  cwd={cwd}")
        )

        ended = await step.wait_for_event(
            "wait-for-session-end",
            event=EventNames.SESSION_ENDED,
            match="data.session_id",
            timeout="24h",
        )

        if ended:
            duration_s = (
                datetime.fromisoformat(
                    ended.data.get("ended_at",
                                   datetime.now(timezone.utc).isoformat())
                )
                - datetime.fromisoformat(started_at)
            ).total_seconds()
            return {
                "session_id":   session_id,
                "duration_s":   duration_s,
                "tool_calls":   ended.data.get("tool_calls_total", 0),
                "llm_requests": ended.data.get("llm_requests_total", 0),
            }
        return {"session_id": session_id, "status": "timed_out"}

    # ── 4. Tool Error Alerter ─────────────────────────────────────────────────
    @mix_inngest.create_function(
        fn_id="handle-tool-error",
        name="Handle Tool Error",
        trigger=inngest.TriggerEvent(event=EventNames.TOOL_ERRORED),
        retries=0,
    )
    async def handle_tool_error(ctx: inngest.Context, step: inngest.Step):
        data = ctx.event.data
        await step.run(
            "log-error",
            lambda: log.error(
                f"[Inngest] Tool error  tool={data.get('tool_name')}  "
                f"error={data.get('error')}  session={data.get('session_id')}"
            )
        )
        return {
            "tool":    data.get("tool_name"),
            "error":   data.get("error"),
            "flagged": True,
        }

    # ── 5. Blocked Call Auditor ───────────────────────────────────────────────
    @mix_inngest.create_function(
        fn_id="audit-blocked-call",
        name="Audit Blocked Tool Call",
        trigger=inngest.TriggerEvent(event=EventNames.TOOL_BLOCKED),
    )
    async def audit_blocked_call(ctx: inngest.Context, step: inngest.Step):
        data   = ctx.event.data
        record = await step.run(
            "write-audit-record",
            lambda: {
                "tool":    data.get("tool_name"),
                "path":    data.get("blocked_path"),
                "reason":  data.get("reason"),
                "session": data.get("session_id"),
                "ts":      datetime.now(timezone.utc).isoformat(),
            }
        )
        log.warning(f"[Inngest] Blocked call  {record}")
        return record

    FUNCTIONS = [
        analyze_tool_execution,
        track_llm_request,
        monitor_session,
        handle_tool_error,
        audit_blocked_call,
    ]

else:
    class _NoOpClient:
        app_id = "mix-agent-noop"
    mix_inngest = _NoOpClient()
    FUNCTIONS   = []


# ============================================================================
# SESSION ID WIRE  (set once from main.py so call_function can read it)
# ============================================================================

_current_session_id: str = ""


def set_session_id(sid: str):
    global _current_session_id
    _current_session_id = sid


def get_session_id() -> str:
    return _current_session_id


# ============================================================================
# SYNC EVENT SENDER  (fire-and-forget, safe from any thread)
# ============================================================================

def send_event(event_name: str, data: Dict[str, Any],
               session_id: Optional[str] = None) -> bool:
    """
    Fire-and-forget helper — call from synchronous code without awaiting.
    Returns True if the event was queued, False if Inngest is unavailable.
    """
    if not _INNGEST_AVAILABLE:
        return False

    payload = {
        **data,
        "session_id": session_id or data.get("session_id", _current_session_id),
        "_ts":        datetime.now(timezone.utc).isoformat(),
        "_pid":       os.getpid(),
    }

    def _fire():
        try:
            import asyncio
            ev   = inngest.Event(name=event_name, data=payload)
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(mix_inngest.send(ev))
            finally:
                loop.close()
        except Exception as exc:
            log.debug(f"[Inngest] send_event failed silently: {exc}")

    threading.Thread(target=_fire, daemon=True).start()
    return True


# ============================================================================
# CONVENIENCE BUILDERS  — typed event factories
# ============================================================================

class AgentEvents:
    """Type-safe wrappers around send_event for common MIX Agent events."""

    @staticmethod
    def tool_called(tool_name: str, args: Dict, session_id: str = ""):
        send_event(EventNames.TOOL_CALLED, {
            "tool_name":  tool_name,
            "args":       _safe_args(args),
            "session_id": session_id or _current_session_id,
        })

    @staticmethod
    def tool_completed(tool_name: str, duration_ms: float,
                       success: bool, session_id: str = "",
                       result_preview: str = ""):
        send_event(EventNames.TOOL_COMPLETED, {
            "tool_name":      tool_name,
            "duration_ms":    round(duration_ms, 2),
            "success":        success,
            "session_id":     session_id or _current_session_id,
            "result_preview": result_preview[:200],
        })

    @staticmethod
    def tool_blocked(tool_name: str, blocked_path: str,
                     reason: str, session_id: str = ""):
        send_event(EventNames.TOOL_BLOCKED, {
            "tool_name":    tool_name,
            "blocked_path": blocked_path,
            "reason":       reason,
            "session_id":   session_id or _current_session_id,
        })

    @staticmethod
    def tool_errored(tool_name: str, error: str, session_id: str = ""):
        send_event(EventNames.TOOL_ERRORED, {
            "tool_name":  tool_name,
            "error":      error[:500],
            "session_id": session_id or _current_session_id,
        })

    @staticmethod
    def llm_request_completed(model: str, tokens: Dict[str, int],
                               iteration: int, session_id: str = ""):
        send_event(EventNames.LLM_REQUEST_COMPLETED, {
            "model":      model,
            "tokens":     tokens,
            "iteration":  iteration,
            "session_id": session_id or _current_session_id,
        })

    @staticmethod
    def session_started(session_id: str, cwd: str):
        send_event(EventNames.SESSION_STARTED, {
            "session_id": session_id,
            "cwd":        cwd,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

    @staticmethod
    def session_ended(session_id: str, tool_calls: int, llm_requests: int):
        send_event(EventNames.SESSION_ENDED, {
            "session_id":         session_id,
            "ended_at":           datetime.now(timezone.utc).isoformat(),
            "tool_calls_total":   tool_calls,
            "llm_requests_total": llm_requests,
        })


# ── internal helper ───────────────────────────────────────────────────────────

def _safe_args(args: Dict) -> Dict:
    """Strip sensitive values (keys containing 'key', 'token', 'secret')."""
    SENSITIVE = {"key", "token", "secret", "password", "auth"}
    return {
        k: ("***" if any(s in k.lower() for s in SENSITIVE) else str(v)[:256])
        for k, v in args.items()
    }
