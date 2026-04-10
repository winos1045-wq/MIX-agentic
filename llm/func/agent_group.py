"""
Multi-Agent Group Coordination
File-based IPC — same machine, multiple terminals.

Layout:
  agents/{group}/registry.json          — live member registry
  agents/{group}/inbox/{agent_id}/      — per-agent message queue
  agents/{group}/broadcast.log          — group event log
"""

import json
import uuid
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Callable

AGENTS_BASE    = Path("agents")
POLL_INTERVAL  = 1.5   # seconds between heartbeat + inbox poll


class AgentGroup:
    """Manages one agent instance's participation in a named group."""

    def __init__(self):
        self.group_name:  Optional[str] = None
        self.agent_name:  Optional[str] = None
        self.agent_id:    Optional[str] = None
        self.agent_rank:  str           = "member"
        self.is_active:   bool          = False

        self._poll_thread: Optional[threading.Thread] = None
        self._stop_evt    = threading.Event()
        self._on_message: Optional[Callable[[Dict], None]] = None

    # ── paths ────────────────────────────────────────────────────────────────

    @property
    def _gdir(self) -> Path:
        return AGENTS_BASE / self.group_name

    @property
    def _reg(self) -> Path:
        return self._gdir / "registry.json"

    @property
    def _idir(self) -> Path:
        return self._gdir / "inbox"

    @property
    def _my_inbox(self) -> Path:
        return self._idir / self.agent_id

    # ── registry helpers ─────────────────────────────────────────────────────

    def _read_reg(self) -> Dict:
        if not self._reg.exists():
            return {"group": self.group_name, "leader": None, "members": {}}
        try:
            with open(self._reg) as f:
                return json.load(f)
        except Exception:
            return {"group": self.group_name, "leader": None, "members": {}}

    def _write_reg(self, data: Dict):
        """Atomic write via temp file."""
        tmp = self._reg.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self._reg)

    def _log(self, event: str):
        """Append one line to the group broadcast log."""
        log = self._gdir / "broadcast.log"
        ts  = datetime.now().strftime("%H:%M:%S")
        with open(log, "a") as f:
            f.write(f"[{ts}] [{self.agent_name}/{self.agent_id[:8]}] {event}\n")

    # ── join / leave ─────────────────────────────────────────────────────────

    def join(self, group_name: str, agent_name: str,
             on_message: Optional[Callable[[Dict], None]] = None) -> Dict:
        """
        Create or join a group.
        First member → leader automatically.

        Returns dict: {created, group, id, rank, members}
        """
        AGENTS_BASE.mkdir(exist_ok=True)

        self.group_name  = group_name
        self.agent_name  = agent_name
        self.agent_id    = uuid.uuid4().hex[:12]
        self._on_message = on_message

        self._gdir.mkdir(exist_ok=True)
        self._idir.mkdir(exist_ok=True)
        self._my_inbox.mkdir(exist_ok=True)

        reg     = self._read_reg()
        created = not reg["members"]

        self.agent_rank = "leader" if created else "member"
        if created:
            reg["leader"] = self.agent_id

        reg["members"][self.agent_id] = {
            "name":      self.agent_name,
            "id":        self.agent_id,
            "rank":      self.agent_rank,
            "status":    "idle",
            "joined":    datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
        }
        self._write_reg(reg)
        self._log(f"joined as {self.agent_rank}")

        self.is_active = True
        self._start_poll()

        return {
            "created": created,
            "group":   group_name,
            "id":      self.agent_id,
            "rank":    self.agent_rank,
            "members": reg["members"],
        }

    def leave(self):
        if not self.is_active:
            return
        self._stop_evt.set()
        try:
            reg = self._read_reg()
            reg["members"].pop(self.agent_id, None)
            # Promote oldest remaining member if leader left
            if reg.get("leader") == self.agent_id:
                remaining = list(reg["members"].keys())
                if remaining:
                    reg["leader"] = remaining[0]
                    reg["members"][remaining[0]]["rank"] = "leader"
                else:
                    reg["leader"] = None
            self._write_reg(reg)
            self._log("left the group")
        except Exception:
            pass
        self.is_active = False

    # ── status ────────────────────────────────────────────────────────────────

    def set_status(self, status: str):
        """Update own status + last_seen in shared registry."""
        if not self.is_active:
            return
        try:
            reg = self._read_reg()
            if self.agent_id in reg.get("members", {}):
                reg["members"][self.agent_id]["status"]    = status
                reg["members"][self.agent_id]["last_seen"] = \
                    datetime.now().isoformat()
                self._write_reg(reg)
        except Exception:
            pass

    # ── messaging ─────────────────────────────────────────────────────────────

    def send(self, to_id: str, message: str,
             msg_type: str = "task") -> bool:
        """
        Drop a JSON file into another agent's inbox directory.
        Returns False if target inbox doesn't exist.
        """
        target = self._idir / to_id
        if not target.exists():
            return False
        fname = f"{int(time.time() * 1000)}_{self.agent_id[:6]}.json"
        payload = {
            "from_id":   self.agent_id,
            "from_name": self.agent_name,
            "from_rank": self.agent_rank,
            "type":      msg_type,
            "message":   message,
            "ts":        datetime.now().isoformat(),
        }
        with open(target / fname, "w") as f:
            json.dump(payload, f)
        self._log(f"→ {to_id[:8]}  {message[:80]}")
        return True

    def broadcast(self, message: str) -> int:
        """Send to every member except self. Returns count sent."""
        reg  = self._read_reg()
        sent = 0
        for mid in reg.get("members", {}):
            if mid != self.agent_id:
                if self.send(mid, message, "broadcast"):
                    sent += 1
        self._log(f"[broadcast·{sent}] {message[:80]}")
        return sent

    def read_inbox(self) -> List[Dict]:
        """Consume and return all pending messages (deletes files after read)."""
        if not self._my_inbox.exists():
            return []
        msgs = []
        for f in sorted(self._my_inbox.iterdir()):
            if f.suffix == ".json":
                try:
                    with open(f) as fp:
                        msgs.append(json.load(fp))
                    f.unlink()
                except Exception:
                    pass
        return msgs

    # ── info ──────────────────────────────────────────────────────────────────

    def get_members(self) -> List[Dict]:
        return list(self._read_reg().get("members", {}).values())

    def get_member_by_prefix(self, prefix: str) -> Optional[Dict]:
        """Find a member whose id starts with prefix."""
        for m in self.get_members():
            if m["id"].startswith(prefix):
                return m
        return None

    # ── polling thread ────────────────────────────────────────────────────────

    def _start_poll(self):
        self._stop_evt.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="agent-poll"
        )
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._stop_evt.wait(POLL_INTERVAL):
            # heartbeat
            try:
                reg = self._read_reg()
                if self.agent_id in reg.get("members", {}):
                    reg["members"][self.agent_id]["last_seen"] = \
                        datetime.now().isoformat()
                    self._write_reg(reg)
            except Exception:
                pass
            # inbox
            if self._on_message:
                try:
                    for msg in self.read_inbox():
                        self._on_message(msg)
                except Exception:
                    pass

    # ── display helpers ───────────────────────────────────────────────────────

    @property
    def identity_tag(self) -> str:
        """
        Short identity string shown in AI response headers.
        Empty string when not in any group.
        """
        if not self.is_active:
            return ""
        return (
            f"{self.agent_name}"
            f"  ·  {self.group_name}"
            f"  ·  {self.agent_rank}"
            f"  ·  {self.agent_id}"
        )

    def format_status(self) -> str:
        """Formatted table for /agent status."""
        if not self.is_active:
            return "  Not in any group."

        members = self.get_members()
        col_w   = [18, 10, 14, 14, 8]
        header  = (
            f"  {'NAME':<{col_w[0]}} {'RANK':<{col_w[1]}} "
            f"{'STATUS':<{col_w[2]}} {'ID':<{col_w[3]}} SEEN"
        )
        divider = (
            f"  {'─'*col_w[0]} {'─'*col_w[1]} "
            f"{'─'*col_w[2]} {'─'*col_w[3]} {'─'*col_w[4]}"
        )
        lines = [
            f"  Group    {self.group_name}",
            f"  You      {self.agent_name}  ·  {self.agent_id}  ·  {self.agent_rank}",
            "",
            header,
            divider,
        ]
        for m in members:
            marker = "▶" if m["id"] == self.agent_id else " "
            ls     = m.get("last_seen", "")
            ls_fmt = ls[11:16] if len(ls) > 10 else "?"
            lines.append(
                f"  {marker} {m['name']:<{col_w[0]-2}} {m['rank']:<{col_w[1]}} "
                f"{m['status']:<{col_w[2]}} {m['id']:<{col_w[3]}} {ls_fmt}"
            )
        return "\n".join(lines)