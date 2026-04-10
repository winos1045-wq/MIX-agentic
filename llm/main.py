## main.py  —  MIX Agent
import os
import sys
import json
import logging
import threading
import time
import shutil
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

from google import genai
from google.genai import types
#___________________________tools_____________________
from func.web_fetch_search import schema_web_search, schema_web_fetch
from func.get_files_info import schema_get_files_info
from func.get_file_content import schema_get_file_content
from func.write_file import schema_write_file
from func.run_python_file import schema_run_python_file
from func.run_shell import schema_run_shell
from func.patch_file import schema_patch_file
from func.build import schema_build_project, schema_install_dependencies
from func.plan_project import schema_plan_project
from func.grep_tool import schema_search_code
from func.verify_change import schema_verify_change
from func.project_map import schema_get_project_map

from call_function import call_function

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    load_dotenv(env_file)
else:
    load_dotenv()

from file_injector import inject_files, InjectionResult
from path_guard import guard, GuardError, PathGuard
from func.agent_group import AgentGroup

try:
    from func.codespace_tools import cs_schemas as _cs_schemas
    _CODESPACE_AVAILABLE = True
except ImportError:
    _cs_schemas = []
    _CODESPACE_AVAILABLE = False

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.logging import RichHandler

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, merge_completers
from prompt_toolkit.styles import Style as PromptStyle


# ============================================================================
# THEME
# ============================================================================

class Theme:
    ORANGE = "#FF8C42"
    DIM    = "#6B7280"
    TEXT   = "#F9FAFB"
    GREEN  = "#10B981"
    RED    = "#EF4444"
    YELLOW = "#F59E0B"
    CYAN   = "#06B6D4"
    PURPLE = "#A78BFA"
    BLUE   = "#3B82F6"
    GRAY   = "#374151"
    SLATE  = "#94A3B8"
    PINK   = "#F472B6"


# ============================================================================
# AUTOCOMPLETE
# ============================================================================

class CommandCompleter(Completer):
    COMMANDS = [
        'help', 'history', 'clear', 'status',
        'monitor_on', 'monitor_off', 'reload', 'exit', 'quit', 'q',
        'agent', 'agent status', 'agent leave',
        'agent inbox', 'agent send', 'agent broadcast',
    ]

    def get_completions(self, document, complete_event):
        word = document.get_word_before_cursor(pattern=re.compile(r'/[^\s]*'))
        if not word.startswith('/'):
            return
        search = word[1:].lower()
        for cmd in self.COMMANDS:
            if search in cmd.lower():
                yield Completion(f"/{cmd}", start_position=-len(word),
                                 display=f"/{cmd}")


class FilePathCompleter(Completer):
    IGNORE_DIRS      = {'__pycache__', 'node_modules', 'venv', '.git',
                        '.next', 'dist', 'build', '.mypy_cache', '.pytest_cache',
                        'agents', '.venv'}
    REFRESH_INTERVAL = 30

    def __init__(self):
        self._cache: List[str] = []
        self._last_refresh     = 0.0
        self._lock             = threading.Lock()

    def _rebuild(self):
        new: List[str] = []
        for root, dirs, files in os.walk('.'):
            dirs[:] = [d for d in dirs
                       if not d.startswith('.') and d not in self.IGNORE_DIRS]
            for d in dirs:
                rel = os.path.relpath(os.path.join(root, d), '.')
                if guard.is_safe(rel):
                    new.append(rel + '/')
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), '.')
                if guard.is_safe(rel):
                    new.append(rel)
        with self._lock:
            self._cache        = new
            self._last_refresh = time.time()

    def _ensure_fresh(self):
        if time.time() - self._last_refresh > self.REFRESH_INTERVAL:
            self._last_refresh = time.time()
            threading.Thread(target=self._rebuild, daemon=True).start()

    def get_completions(self, document, complete_event):
        word = document.get_word_before_cursor(pattern=re.compile(r'@[^\s]*'))
        if not word.startswith('@'):
            return
        self._ensure_fresh()
        search = word[1:].lower()
        with self._lock:
            snapshot = list(self._cache)
        for entry in snapshot:
            if search in entry.lower():
                yield Completion(f"@{entry}", start_position=-len(word),
                                 display=f"@{entry}")


autocomplete_style = PromptStyle.from_dict({
    'completion-menu':                    'bg:#0d1117 fg:#6B7280',
    'completion-menu.completion.current': 'bg:#1c2128 fg:#58a6ff',
    'completion-menu.completion':         'fg:#6B7280',
})


# ============================================================================
# TERMINAL UTILITIES
# ============================================================================

class TerminalUtils:
    @staticmethod
    def get_width() -> int:
        try:
            return shutil.get_terminal_size().columns
        except Exception:
            return 80

    @staticmethod
    def get_height() -> int:
        try:
            return shutil.get_terminal_size().lines
        except Exception:
            return 24

    @staticmethod
    def truncate_text(text: str, max_width: int, suffix: str = "…") -> str:
        return text if len(text) <= max_width else text[:max_width - len(suffix)] + suffix

    @staticmethod
    def wrap_text(text: str, width: int) -> List[str]:
        words = text.split()
        lines, current, length = [], [], 0
        for word in words:
            wl = len(word) + 1
            if length + wl <= width:
                current.append(word)
                length += wl
            else:
                if current:
                    lines.append(' '.join(current))
                current, length = [word], wl
        if current:
            lines.append(' '.join(current))
        return lines

    @staticmethod
    def is_narrow() -> bool:
        return TerminalUtils.get_width() < 80


# ============================================================================
# STATUS BAR
# ============================================================================

class StatusBar:
    SPINNERS = ['⣾', '⣽', '⣻', '⢿', '⡿', '⣟', '⣯', '⣷']

    def __init__(self):
        self.running           = False
        self.thread: Optional[threading.Thread] = None
        self.start_time: Optional[float] = None
        self.prompt_tokens     = 0
        self.completion_tokens = 0
        self._lock             = threading.Lock()
        self._written          = 0
        self._phase            = "Thinking"

    def start(self, phase: str = "Thinking"):
        if self.running:
            return
        self._phase     = phase
        self.start_time = time.time()
        self.prompt_tokens = self.completion_tokens = 0
        self.running    = True
        self.thread     = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def set_phase(self, phase: str):
        self._phase = phase

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.6)
        self._erase()

    def update_tokens(self, prompt: int = 0, completion: int = 0):
        with self._lock:
            self.prompt_tokens     += prompt
            self.completion_tokens += completion

    def _erase(self):
        sys.stdout.write('\r' + ' ' * (self._written + 2) + '\r')
        sys.stdout.flush()

    @staticmethod
    def _fmt_time(secs: float) -> str:
        if secs < 60:
            return f"{secs:.1f}s"
        return f"{int(secs // 60)}m{int(secs % 60):02d}s"

    def _loop(self):
        idx = 0
        while self.running:
            elapsed = time.time() - (self.start_time or time.time())
            frame   = self.SPINNERS[idx % len(self.SPINNERS)]
            t_str   = self._fmt_time(elapsed)
            with self._lock:
                total = self.prompt_tokens + self.completion_tokens
            tok_str = f"{total:,} tok" if total else "…"
            line    = f" {frame} {self._phase}  {t_str}  ·  {tok_str}  ·  esc to cancel"
            self._erase()
            sys.stdout.write(f"\033[2m{line}\033[0m")
            sys.stdout.flush()
            self._written = len(line)
            idx += 1
            time.sleep(0.08)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


StatusTracker = StatusBar   # backward compat


# ============================================================================
# TOKEN COUNTER
# ============================================================================

class TokenCounter:
    def __init__(self):
        self.session_prompt:     int = 0
        self.session_completion: int = 0
        self.session_thinking:   int = 0
        self.session_cached:     int = 0
        self.requests:           int = 0

    def record(self, meta) -> dict:
        p  = getattr(meta, "prompt_token_count",         0) or 0
        c  = getattr(meta, "candidates_token_count",     0) or 0
        th = getattr(meta, "thoughts_token_count",       0) or 0
        ca = getattr(meta, "cached_content_token_count", 0) or 0
        self.session_prompt     += p
        self.session_completion += c
        self.session_thinking   += th
        self.session_cached     += ca
        self.requests           += 1
        return {"prompt": p, "completion": c, "thinking": th, "cached": ca}

    @property
    def session_total(self) -> int:
        return self.session_prompt + self.session_completion + self.session_thinking

    def format_request(self, counts: dict) -> str:
        parts = [f"in {counts['prompt']:,}", f"out {counts['completion']:,}"]
        if counts["thinking"]: parts.append(f"think {counts['thinking']:,}")
        if counts["cached"]:   parts.append(f"cached {counts['cached']:,}")
        parts.append(f"session {self.session_total:,}")
        return "  ↳ " + "  ·  ".join(parts)

    def format_status(self) -> str:
        lines = [
            "  Tokens (session)",
            f"    requests    {self.requests}",
            f"    prompt      {self.session_prompt:,}",
            f"    completion  {self.session_completion:,}",
        ]
        if self.session_thinking:
            lines.append(f"    thinking    {self.session_thinking:,}")
        if self.session_cached:
            lines.append(f"    cached      {self.session_cached:,}")
        lines.append(f"    total       {self.session_total:,}")
        return "\n".join(lines)


# ============================================================================
# LOGGER  — console only, no files
# ============================================================================

class Logger:
    def __init__(self):
        self.monitoring_enabled = False
        logging.basicConfig(
            level=logging.WARNING,
            handlers=[RichHandler(console=Console(), show_time=True,
                                  show_path=False)]
        )
        self.logger = logging.getLogger("MIX")
        self._set_external(False)

    def enable_monitoring(self):
        self.monitoring_enabled = True
        self.logger.setLevel(logging.INFO)
        self._set_external(True)

    def disable_monitoring(self):
        self.monitoring_enabled = False
        self.logger.setLevel(logging.WARNING)
        self._set_external(False)

    def _set_external(self, enabled: bool):
        level = logging.INFO if enabled else logging.WARNING
        for name in ("google_genai", "httpx", "google.generativeai",
                     "google.api_core"):
            logging.getLogger(name).setLevel(level)

    def info(self, msg: str):    self.logger.info(msg)
    def error(self, msg: str):   self.logger.error(msg)
    def warning(self, msg: str): self.logger.warning(msg)
    def debug(self, msg: str):   self.logger.debug(msg)


# ============================================================================
# SESSION — in-memory only, no file I/O
# ============================================================================

CONTEXT_WINDOW_MESSAGES = 25


class SessionManager:
    def __init__(self):
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.history: List[Dict[str, Any]] = []

    def add_message(self, role: str, content: str,
                    metadata: Optional[Dict] = None):
        self.history.append({
            "timestamp": datetime.now().isoformat(),
            "role":      role,
            "content":   content,
            "metadata":  metadata or {},
        })

    def get_context(self) -> List[Dict]:
        return self.history[-CONTEXT_WINDOW_MESSAGES:]

    def clear_history(self):
        self.history = []


# ============================================================================
# COMMAND HANDLER
# ============================================================================

class CommandHandler:
    COMMANDS = {
        'help':              'Show help information',
        'history':           'Show recent chat history',
        'clear':             'Clear chat history',
        'status':            'Show agent status',
        'monitor_on':        'Enable request monitoring',
        'monitor_off':       'Disable request monitoring',
        'reload':            'Reload the agent',
        'exit':              'Exit the agent',
        'quit':              'Exit the agent',
        'q':                 'Exit (shorthand)',
        'agent':             'Join or create an agent group',
        'agent status':      'Show group members',
        'agent send':        'Send a message to another agent',
        'agent broadcast':   'Send a message to all agents',
        'agent inbox':       'Read pending messages',
        'agent leave':       'Leave the current group',
    }

    def __init__(self, session: SessionManager, console: Console,
                 log: Logger, tokens: TokenCounter,
                 agent_group: AgentGroup):
        self.session = session
        self.console = console
        self.logger  = log
        self.tokens  = tokens
        self.group   = agent_group

    def is_command(self, text: str) -> bool:
        return text.lower().strip().startswith('/')

    def handle(self, text: str) -> Optional[str]:
        stripped = text.strip().lstrip('/')

        # ── agent commands ────────────────────────────────────────────────────
        if stripped == 'agent' or stripped.startswith('agent '):
            return self._handle_agent(stripped)

        cmd = stripped.lower()
        if cmd == 'reload':               return "RELOAD"
        if cmd in ('exit', 'quit', 'q'):  return "EXIT"
        if cmd == 'help':                 return self._show_help()
        if cmd == 'history':              return self._show_history()
        if cmd == 'clear':
            self.session.clear_history()
            return "Chat history cleared."
        if cmd == 'status':               return self._show_status()
        if cmd == 'monitor_on':
            self.logger.enable_monitoring()
            return "✓ Monitoring enabled"
        if cmd == 'monitor_off':
            self.logger.disable_monitoring()
            return "✓ Monitoring disabled"
        return None

    # ── /agent sub-commands ───────────────────────────────────────────────────

    def _handle_agent(self, raw: str) -> str:
        parts = raw.split(maxsplit=1)
        sub   = parts[1].strip() if len(parts) > 1 else ""

        # /agent status
        if sub in ("status", "st"):
            return self.group.format_status()

        # /agent leave
        if sub == "leave":
            if not self.group.is_active:
                return "  Not in any group."
            name = self.group.group_name
            self.group.leave()
            return f"  Left group  {name}"

        # /agent inbox
        if sub == "inbox":
            if not self.group.is_active:
                return "  Not in any group."
            msgs = self.group.read_inbox()
            if not msgs:
                return "  Inbox empty."
            lines = ["  Inbox\n"]
            for m in msgs:
                lines.append(
                    f"  From  {m['from_name']} [{m['from_rank']}]"
                    f"  ·  {m['ts'][11:16]}\n"
                    f"  {m['message']}\n"
                )
            return "\n".join(lines)

        # /agent broadcast <message>
        if sub.startswith("broadcast "):
            msg = sub[len("broadcast "):].strip()
            if not self.group.is_active:
                return "  Not in any group."
            n = self.group.broadcast(msg)
            return f"  Broadcast sent to {n} agent(s)."

        # /agent send <id_prefix> <message>
        if sub.startswith("send "):
            rest  = sub[len("send "):].strip()
            tok   = rest.split(maxsplit=1)
            if len(tok) < 2:
                return "  Usage:  /agent (beta) send <id_prefix> <message>"
            id_prefix, msg = tok
            member = self.group.get_member_by_prefix(id_prefix)
            if not member:
                return f"  No agent with id starting '{id_prefix}'."
            ok = self.group.send(member["id"], msg)
            if ok:
                return f"  ✓ Sent to {member['name']}  [{member['id']}]"
            return f"  ✗ Could not reach {member['name']}"

        # /agent  — wizard: join or create
        return self._agent_wizard()

    def _agent_wizard(self) -> str:
        """Interactive join/create wizard."""
        self.console.print()
        self.console.print(
            f"  [{Theme.CYAN}]◈  Agent Group[/{Theme.CYAN}]"
            f"  [dim]Link agents across terminals[/dim]"
        )
        self.console.print()

        group_name  = self.console.input(
            f"  [{Theme.SLATE}]Group name :[/{Theme.SLATE}] "
        ).strip()
        if not group_name:
            return "  Cancelled."

        agent_name  = self.console.input(
            f"  [{Theme.SLATE}]Agent name :[/{Theme.SLATE}] "
        ).strip()
        if not agent_name:
            return "  Cancelled."

        self.console.print(
            f"\n  [dim]Connecting to group  {group_name} …[/dim]"
        )

        # on_message callback — prints inbox notifications live
        ui_console = self.console

        def _on_msg(msg: Dict):
            # Print inline notification without breaking current prompt
            ui_console.print()
            ui_console.print(
                f"  [{Theme.PINK}]◈  {msg['from_name']}"
                f"[/{Theme.PINK}]"
                f"  [dim]{msg['from_rank']}  ·  {msg['from_id'][:8]}"
                f"  ·  {msg['type']}[/dim]"
            )
            ui_console.print(
                f"  [dim]│[/dim]  [bold {Theme.TEXT}]{msg['message']}[/bold {Theme.TEXT}]"
            )
            ui_console.print()

        info = self.group.join(group_name, agent_name, on_message=_on_msg)

        action = "Created" if info["created"] else "Joined"
        lines  = [
            f"\n  [{Theme.GREEN}]✓  {action} group[/{Theme.GREEN}]"
            f"  [bold]{group_name}[/bold]\n",
            f"  Name    {agent_name}",
            f"  ID      {info['id']}",
            f"  Rank    {info['rank']}",
            f"  Members {len(info['members'])}",
        ]
        self.console.print("\n".join(lines))

        if len(info["members"]) > 1:
            self.console.print(f"\n  [{Theme.SLATE}]Members in group:[/{Theme.SLATE}]")
            for m in info["members"].values():
                marker = "▶" if m["id"] == info["id"] else "○"
                self.console.print(
                    f"    {marker}  {m['name']:<18} {m['rank']:<10} {m['id']}"
                )
        self.console.print()
        return ""

    # ── other commands ────────────────────────────────────────────────────────

    def _show_help(self) -> str:
        lines = ["Available Commands\n\n"]
        for cmd, desc in self.COMMANDS.items():
            lines.append(
                f"  /{cmd}\n      {desc}\n" if TerminalUtils.is_narrow()
                else f"  /{cmd:<22} {desc}\n"
            )
        lines += [
            "\nFile Injection (@)\n\n",
            "  @path/to/file     Inject file content into prompt\n",
            "  @path/to/dir      Inject directory listing\n",
        ]
        return ''.join(lines)

    def _show_history(self) -> str:
        if not self.session.history:
            return "No chat history yet."
        max_w = max(50, TerminalUtils.get_width() - 20)
        lines = ["\nRecent History\n\n"]
        for i, msg in enumerate(self.session.history[-10:], 1):
            role    = msg['role'].upper()
            content = msg['content']
            if len(content) > max_w:
                content = content[:max_w] + "…"
            lines.append(f"  {i:2}. [{role}] {content}\n")
        return ''.join(lines)

    def _show_status(self) -> str:
        cwd   = os.getcwd()
        width = TerminalUtils.get_width()
        if len(cwd) > width - 25:
            cwd = "…" + cwd[-(width - 28):]
        mon = "ON" if self.logger.monitoring_enabled else "OFF"
        cs  = "✓ available" if _CODESPACE_AVAILABLE else "✗ not installed"
        ctx = (f"{len(self.session.history)} msgs "
               f"(sending last {CONTEXT_WINDOW_MESSAGES})")
        grp = (
            f"{self.group.group_name}  ·  {self.group.agent_id}  "
            f"·  {self.group.agent_rank}"
            if self.group.is_active else "not joined"
        )
        return (
            f"  CWD          {cwd}\n"
            f"  Session      {self.session.session_id}\n"
            f"  Context      {ctx}\n"
            f"  Monitoring   {mon}\n"
            f"  Codespace    {cs}\n"
            f"  Agent group  {grp}\n"
            f"  Path guard   ✓ active\n"
            f"  @ injection  ✓ active\n"
            f"  Terminal     {width}×{TerminalUtils.get_height()}\n\n"
            + self.tokens.format_status()
        )


# ============================================================================
# UI
# ============================================================================

class UI:
    def __init__(self):
        self.console = Console(highlight=False)

    def clear(self):
        self.console.clear()

    def welcome_screen(self):
        self.clear()
        width = TerminalUtils.get_width()
        cwd   = os.getcwd()
        if len(cwd) > width - 10:
            cwd = "…" + cwd[-(width - 13):]

        self.console.print()
        self.console.print(
            f"[bold {Theme.ORANGE}]  ✻  MIX Agent[/bold {Theme.ORANGE}]"
            f"  [dim]by FAHFAH MOHAMED[/dim]"
        )
        self.console.print(f"  [dim]{'─' * min(width - 4, 60)}[/dim]")
        self.console.print(f"  [dim]{cwd}[/dim]")
        self.console.print()
        self.console.print(
            f"  [dim]Type [/dim][bold white]/help[/bold white]"
            f"[dim] for commands · [/dim][bold white]/agent (Beta)[/bold white]"
            f"[dim] to link agents · [/dim][bold white]@file[/bold white]"
            f"[dim] to inject[/dim]"
        )
        self.console.print()

    def separator(self):
        w = min(TerminalUtils.get_width(), 80)
        self.console.print(f"[{Theme.GRAY}]{'─' * w}[/{Theme.GRAY}]")

    def print_injection_report(self, result: InjectionResult):
        if result.injected:
            for p in result.injected:
                self.console.print(
                    f"  [dim]⊕[/dim] [bold {Theme.CYAN}]{p}[/bold {Theme.CYAN}]"
                    f"[dim] → injected[/dim]"
                )
        if result.blocked:
            for p, reason in result.blocked:
                self.console.print(
                    f"  [dim]⊗[/dim] [{Theme.RED}]{p}[/{Theme.RED}]"
                    f"[dim]  {reason.split(chr(10))[0]}[/dim]"
                )
        if result.missing:
            for p in result.missing:
                self.console.print(
                    f"  [dim]?[/dim] [{Theme.YELLOW}]{p}[/{Theme.YELLOW}]"
                    f"[dim]  not found[/dim]"
                )
        if result.injected or result.blocked or result.missing:
            self.console.print()

    def print_tool_execution(self, tool_name: str, args: Dict[str, Any],
                              result: str = None):
        DISPATCH: Dict[str, tuple] = {
            "get_file_content":     ("Read",    "file_path",       Theme.BLUE),
            "write_file":           ("Write",   "file_path",       Theme.GREEN),
            "patch_file":           ("Patch",   "file_path",       Theme.YELLOW),
            "run_shell":            ("Shell",   "command",         Theme.PURPLE),
            "run_python_file":      ("Python",  "file_path",       Theme.CYAN),
            "get_files_info":       ("List",    "path",            Theme.SLATE),
            "build_project":        ("Build",   "build_tool",      Theme.ORANGE),
            "install_dependencies": ("Install", "package_manager", Theme.ORANGE),
            "plan_project":         ("Plan",    "task_description",Theme.BLUE),
            "cs_run_shell":         ("CS·Shell","command",         Theme.PURPLE),
            "cs_write_file":        ("CS·Write","file_path",       Theme.GREEN),
            "cs_read_file":         ("CS·Read", "file_path",       Theme.BLUE),
            "cs_patch_file":        ("CS·Patch","file_path",       Theme.YELLOW),
        }
        if tool_name in DISPATCH:
            label, key, color = DISPATCH[tool_name]
            val_str = TerminalUtils.truncate_text(str(args.get(key, "") or ""), 60)
        else:
            label   = tool_name.replace("_", " ").title()
            color   = Theme.SLATE
            vals    = list(args.values())
            val_str = TerminalUtils.truncate_text(str(vals[0]), 60) if vals else ""

        self.console.print(
            f"  [dim]│[/dim] [{color}]{label:<8}[/{color}]"
            f"  [bold white]{val_str}[/bold white]"
        )
        if result:
            first = TerminalUtils.truncate_text(
                str(result).strip().split('\n')[0], 72
            )
            self.console.print(f"  [dim]│         {first}[/dim]")

    def print_response(self, text: str, agent_tag: str = ""):
        """
        Print AI response.
        agent_tag — shown when agent is part of a group so you can see
                    which instance is speaking.
        """
        self.console.print()

        if agent_tag:
            # Show full agent identity when in a group
            self.console.print(
                f"  [{Theme.ORANGE}]●[/{Theme.ORANGE}]  "
                f"[bold {Theme.ORANGE}]{agent_tag.split('·')[0].strip()}[/bold {Theme.ORANGE}]"
                f"  [dim]{' · '.join(p.strip() for p in agent_tag.split('·')[1:])}[/dim]"
            )
        else:
            self.console.print(
                f"  [{Theme.ORANGE}]●[/{Theme.ORANGE}]  "
                f"[bold {Theme.TEXT}]MIX[/bold {Theme.TEXT}]"
            )

        self.console.print()

        parts = re.split(r'(```[\w]*\n.*?```)', text, flags=re.DOTALL)
        for part in parts:
            if part.startswith('```'):
                lang_match = re.match(r'```(\w+)\n', part)
                lang = lang_match.group(1) if lang_match else "text"
                code = re.sub(r'^```\w*\n', '', part).rstrip('`').strip()
                self.console.print(Syntax(
                    code, lang, theme="github-dark",
                    line_numbers=not TerminalUtils.is_narrow(),
                    word_wrap=True, padding=(0, 2)
                ))
            else:
                for line in part.split('\n'):
                    if line.strip():
                        for wl in TerminalUtils.wrap_text(
                                line, TerminalUtils.get_width() - 6):
                            self.console.print(f"  {wl}")
                    else:
                        self.console.print()
        self.console.print()

    def print_token_summary(self, summary: str):
        self.console.print(f"[dim]{summary}[/dim]\n")

    def print_inbox_message(self, msg: Dict):
        """Live notification when another agent sends a message."""
        self.console.print()
        self.console.print(
            f"  [{Theme.PINK}]◈  {msg['from_name']}[/{Theme.PINK}]"
            f"  [dim]{msg['from_rank']}"
            f"  ·  {msg['from_id'][:8]}"
            f"  ·  {msg['type']}[/dim]"
        )
        self.console.print(
            f"  [dim]│[/dim]  "
            f"[bold {Theme.TEXT}]{msg['message']}[/bold {Theme.TEXT}]"
        )
        self.console.print()

    def error(self, title: str, content: str):
        self.console.print(Panel(
            content, title=f"[{Theme.RED}]✗  {title}[/{Theme.RED}]",
            border_style=Theme.RED, padding=(0, 2), expand=False
        ))

    def warning(self, title: str, content: str):
        self.console.print(Panel(
            content, title=f"[{Theme.YELLOW}]⚠  {title}[/{Theme.YELLOW}]",
            border_style=Theme.YELLOW, padding=(0, 2), expand=False
        ))

    def info(self, title: str, content: str):
        self.console.print(Panel(
            content, title=f"[{Theme.CYAN}]  {title}[/{Theme.CYAN}]",
            border_style=Theme.CYAN, padding=(0, 2), expand=False
        ))


# ============================================================================
# AI AGENT
# ============================================================================

class MIXAgent:
    MODEL = 'gemma-4-26b-a4b-it'

    SYSTEM_PROMPT = """
You are an elite software engineer and cybersecurity expert.

# FILE INJECTION PROTOCOL (critical)
- When the user's message contains `<injected_file>` or `<injected_dir>` blocks, those files have **already** been read. Do NOT call `get_file_content` for them – use the injected content directly.
- Only call `get_file_content` for files **not** already injected.
- Never list directories you have no task in – only list what is directly relevant.

# PATH SECURITY
- The path guard restricts what files you can access.
- Blocked paths include: `.env`, `.git`, `node_modules`, `sessions`, `logs`, private keys.
- If a file access fails with a 🔒 error, do **not** retry or attempt workarounds – stop and report.

# LOGIC OF THINKING (internal – run this before every action)

**Step 0 – Parse**  
What is the literal request? What is the intent? Note any injected files.

**Step 1 – Known vs Unknown**  
- Known: facts from injected files, prior conversation.  
- Unknown: anything not verified in current workspace.  
- If a **blocked unknown** exists (cannot resolve), stop and report.

**Step 2 – Smallest verifiable next step**  
Ask: “What single action gives maximum information with minimum risk?”  
Examples: read one file, run one `ls`, execute one test.

**Step 3 – Hypothesis & execute**  
State hypothesis: “I expect that reading `X` shows `Y`.”  
Execute tool. Compare result to hypothesis.  
If mismatch after **two attempts**, stop and report – do not try a third time blindly.

**Step 4 – Verify**  
After any state‑changing action (edit, build, commit), immediately verify.  
If verification fails → one targeted fix → re‑verify.  
If still fails → stop and report.

**Step 5 – User involvement**  
Act without asking if: confident, low‑risk, and user didn’t ask for approval.  
Otherwise ask or report (destructive ops, shared state, >7‑task plan, 🔒 block, loops).

# STRUCTURED APPROACH FOR COMPLEX TASKS

For multi‑file changes, refactors, or architecture work:

## 1. PLAN (use `plan_project` tool)
Generate JSON: `{tasks: [{id, title, dependencies, subtasks, files_to_modify, verification_hints}]}`  
If >7 top‑level tasks, first write a **design note** explaining why.

## 2. REVIEW
Read the plan JSON. Check for missing dependencies, files to read first, flawed assumptions.  
If flawed → revise and report changes to user.

## 3. EXECUTE
Follow tasks sequentially.  
Use `patch_file` for existing files (never `write_file` unless creating new).  
After each major task, run verification (test, lint, type check) and **log the result** (✅/❌).  
If verification fails → diagnose, fix once, re‑verify. Still failing → stop and report.

## 4. REFLECT
After completion, write 2‑3 sentences answering:  
- What worked well?  
- What was harder than expected?  
- What to do differently next time?  
Include this in your final response to the user.

# WHEN TO STOP & REPORT (MANDATORY)

Stop immediately if you experience:
- High cognitive load / unclear requirements
- Repeated errors or loops (same tool fails twice)
- Missing tools or capabilities
- Unclear context or code understanding
- Path guard blocks a needed file
- Verification fails after one fix attempt
- Plan would exceed 7 tasks without user approval

**Output format (MANDATORY)** – use this exact Markdown:


Search-first protocol (CRITICAL):
- Before reading any file, use search_code to locate relevant code.
- Use output_mode='files_with_matches' first to find which files are relevant.
- Then use output_mode='content' with context=3 to read just the match + surroundings.
- Only call get_file_content if you need the full file (e.g. to patch it).
- This workflow: search → targeted read → patch  saves 80% of tokens vs blind reads.
 
Example workflow for "fix the login bug":
  1. search_code(pattern="def login|login(", file_type="py")
  2. search_code(pattern="def login", path="src/auth.py", output_mode="content", context=10)
  3. get_file_content("src/auth.py", start_line=45, end_line=80)
  4. patch_file(...)


Codebase orientation protocol:
- On any task involving an unfamiliar codebase, call get_project_map FIRST.
- get_project_map returns: identity, tree, key files, deps, data flow, token cost.
- After get_project_map → use search_code to locate specific code.
- Only then use get_file_content for targeted reads.
 
Workflow:
  get_project_map()              → understand the codebase
  search_code(pattern)           → find relevant files
  get_file_content(file, L1, L2) → read only what you need
  patch_file(...)                → make changes

Web access protocol:
- Use web_search when you need: current docs, error explanations,
  package versions, API references, or anything not in the codebase.
- Use web_fetch to read a specific URL in full (e.g. a GitHub file,
  docs page, or API spec).
- Max 8 web_search calls per session — use them wisely.
- NEVER use web_fetch on localhost, 127.0.0.1, or internal IPs.
- After web_search, use web_fetch on the most relevant URL to get full content.
 
Example:
  web_search("FastAPI background tasks docs")
  → web_fetch("https://fastapi.tiangolo.com/tutorial/background-tasks/",
              prompt="show all code examples for BackgroundTasks")

_______________________
When you have finished executing all requested tasks, you must format your final answer exactly as follows:

Start each completed or attempted task on a new line with a bullet point (-).

Immediately after the bullet, place a checkbox:

Use ✅ if the task was successfully completed.

Use ❌ if the task failed or could not be completed.

Then write the task number (starting from 1) followed by a space and a short, important description of what was done or what the outcome was.

Do not add extra text before or after this list unless explicitly requested by the user. The list alone is your complete response.



```markdown
🛠️ AI TOOLS NEED

Problem:
[Factual description]

Reason:
[Why this is a blocker – missing info, ambiguity, tool limitation]

Requested Tool:
[Tool or human action that would resolve it]
"""

    def __init__(self, api_key: Optional[str] = None,
                 log: Optional[Logger] = None):
        _env = Path(__file__).parent / ".env"
        load_dotenv(_env, override=True)
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY not found. Set it in .env or export it.\n"
                f"CWD: {os.getcwd()}  .env exists: {os.path.exists('.env')}"
            )
        self.client  = genai.Client(api_key=self.api_key)
        self.ui      = UI()
        self.logger  = log
        self.session = SessionManager()
        self.status  = StatusBar()
        self.tokens  = TokenCounter()
        self.group   = AgentGroup()

        from func.web_fetch_search import reset_search_count
        reset_search_count()
        self.command_handler = CommandHandler(
            self.session, self.ui.console, self.logger,
            self.tokens, self.group
        )
        self.max_iterations = 2000
        self._config        = self._build_config()

        self.prompt_session = PromptSession(
            completer=merge_completers([CommandCompleter(), FilePathCompleter()]),
            style=autocomplete_style,
            complete_while_typing=True,
            complete_in_thread=True,
        )

    # ── config ──────────────────────────────────────────────────────────────

    def _build_config(self) -> types.GenerateContentConfig:
        schemas = [
            schema_get_files_info, schema_get_file_content,
            schema_run_python_file, schema_write_file, schema_run_shell,
            schema_build_project, schema_install_dependencies,
            schema_patch_file, schema_plan_project,
            schema_search_code , schema_get_project_map , schema_verify_change,
            schema_web_search, schema_web_fetch
        ] + list(_cs_schemas)
        return types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=schemas)],
            system_instruction=self.SYSTEM_PROMPT,
            temperature=0.7,
        )

    # ── @ injection ──────────────────────────────────────────────────────────

    def _preprocess_input(self, raw: str) -> tuple:
        result = inject_files(raw)
        if result.injected or result.blocked or result.missing:
            self.ui.print_injection_report(result)
        return result.prompt, result

    # ── context builder ──────────────────────────────────────────────────────

    def _build_messages(self, current_input: str) -> List[types.Content]:
        messages: List[types.Content] = []
        for msg in self.session.get_context()[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            messages.append(
                types.Content(role=role,
                              parts=[types.Part(text=msg["content"])])
            )
        messages.append(
            types.Content(role="user",
                          parts=[types.Part(text=current_input)])
        )
        return messages

    # ── request ──────────────────────────────────────────────────────────────

    def process_request(self, user_input: str, verbose: bool = False):
        injected_input, _ = self._preprocess_input(user_input)
        self.session.add_message("user", user_input)
        messages = self._build_messages(injected_input)

        # Tell group members this agent is busy
        if self.group.is_active:
            self.group.set_status("thinking")

        self.status.start("Thinking")
        last_req_counts: Optional[dict] = None

        try:
            for iteration in range(self.max_iterations):

                response = self.client.models.generate_content(
                    model=self.MODEL,
                    contents=messages,
                    config=self._config,
                )

                if response is None or response.usage_metadata is None:
                    self.status.stop()
                    self.ui.error("Response Error",
                                  "Empty or malformed response from API")
                    return

                req_counts      = self.tokens.record(response.usage_metadata)
                last_req_counts = req_counts
                self.status.update_tokens(prompt=req_counts["prompt"],
                                          completion=req_counts["completion"])

                if verbose and not response.function_calls:
                    self.status.stop()
                    self.ui.info("Tokens", (
                        f"Iteration {iteration + 1}/{self.max_iterations}\n"
                        f"Prompt:     {response.usage_metadata.prompt_token_count}\n"
                        f"Completion: {response.usage_metadata.candidates_token_count}\n"
                        f"Thinking:   {getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0}"
                    ))
                    self.status.start("Thinking")

                if not response.candidates:
                    continue

                for candidate in response.candidates:
                    if candidate and candidate.content:
                        messages.append(candidate.content)

                if response.function_calls:
                    self.status.stop()
                    self.ui.console.print(f"  [{Theme.GRAY}]│[/{Theme.GRAY}]")

                    for fc in response.function_calls:
                        if not self._guard_function_call(fc):
                            messages.append(types.Content(
                                role="user",
                                parts=[types.Part(function_response=types.FunctionResponse(
                                    name=fc.name,
                                    response={"result": "🔒 Blocked: path not allowed"}
                                ))]
                            ))
                            self.ui.console.print(
                                f"  [{Theme.RED}]⊗  Blocked[/{Theme.RED}]"
                                f"  [{Theme.SLATE}]{fc.name}[/{Theme.SLATE}]"
                            )
                            continue

                        result_msg = call_function(fc, verbose)
                        messages.append(result_msg)

                        result_content = ""
                        try:
                            if (result_msg.parts and
                                    result_msg.parts[0].function_response):
                                result_content = str(
                                    result_msg.parts[0].function_response
                                    .response.get("result", "")
                                )
                        except Exception:
                            pass
                        self.ui.print_tool_execution(fc.name, fc.args,
                                                     result_content)
                    self.status.start("Processing")

                else:
                    self.status.stop()
                    response_text = response.text
                    self.session.add_message("assistant", response_text)

                    # Show agent identity when in a group
                    self.ui.print_response(
                        response_text,
                        agent_tag=self.group.identity_tag
                    )

                    if last_req_counts is not None:
                        self.ui.print_token_summary(
                            self.tokens.format_request(last_req_counts)
                        )

                    # Back to idle in group
                    if self.group.is_active:
                        self.group.set_status("idle")
                    return

            self.status.stop()
            self.ui.warning(
                "Limit reached",
                f"Hit max iterations ({self.max_iterations})."
            )

        except KeyboardInterrupt:
            self.status.stop()
            self.ui.console.print(
                f"\n  [{Theme.SLATE}]interrupted[/{Theme.SLATE}]\n"
            )

        except Exception as e:
            self.status.stop()
            self.ui.error("Error", str(e))
            if self.logger:
                self.logger.error(f"Error: {e}")

        finally:
            if self.group.is_active:
                self.group.set_status("idle")

    # ── path guard ───────────────────────────────────────────────────────────

    def _guard_function_call(self, fc: types.FunctionCall) -> bool:
        FILE_ARGS = {"file_path", "path", "working_directory"}
        WRITE_FNS = {"write_file", "patch_file", "cs_write_file", "cs_patch_file"}
        for arg_name in FILE_ARGS:
            val = fc.args.get(arg_name)
            if not val or not isinstance(val, str):
                continue
            if arg_name == "working_directory":
                continue
            if not guard.is_safe(val, write=fc.name in WRITE_FNS):
                return False
        return True

    # ── interactive loop ─────────────────────────────────────────────────────

    def run_interactive(self):
        self.ui.welcome_screen()

        if _CODESPACE_AVAILABLE:
            self.ui.console.print(
                f"  [{Theme.CYAN}]⟶ Codespace tools active "
                f"({len(_cs_schemas)} tools)[/{Theme.CYAN}]\n"
            )

        while True:
            try:
                # Show group tag in prompt when in a group
                if self.group.is_active:
                    prompt_txt = (
                        f"  [{self.group.group_name}·"
                        f"{self.group.agent_name}] ❯ "
                    )
                    prompt_style = Theme.CYAN
                else:
                    prompt_txt   = '  ❯ '
                    prompt_style = Theme.GREEN

                user_input = self.prompt_session.prompt(
                    [('class:prompt', prompt_txt)],
                    style=PromptStyle.from_dict({'prompt': prompt_style})
                ).strip()

                if not user_input:
                    continue

                if self.command_handler.is_command(user_input):
                    result = self.command_handler.handle(user_input)
                    if result == "EXIT":
                        if self.group.is_active:
                            self.group.leave()
                        self.ui.console.print(
                            f"\n  [{Theme.SLATE}]Bye![/{Theme.SLATE}]\n"
                        )
                        break
                    elif result == "RELOAD":
                        if self.group.is_active:
                            self.group.leave()
                        self.ui.console.print(
                            f"\n  [{Theme.CYAN}]Reloading…[/{Theme.CYAN}]\n"
                        )
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                    elif result:
                        self.ui.console.print(
                            f"\n[{Theme.SLATE}]{result}[/{Theme.SLATE}]\n"
                        )
                    continue

                verbose_flag = '--verbose' in user_input
                if verbose_flag:
                    user_input = user_input.replace('--verbose', '').strip()

                self.ui.console.print()
                self.process_request(user_input, verbose_flag)

            except KeyboardInterrupt:
                if self.group.is_active:
                    self.group.leave()
                self.ui.console.print(
                    f"\n  [{Theme.SLATE}]Interrupted. Bye![/{Theme.SLATE}]\n"
                )
                break
            except EOFError:
                if self.group.is_active:
                    self.group.leave()
                self.ui.console.print(
                    f"\n  [{Theme.SLATE}]Bye![/{Theme.SLATE}]\n"
                )
                break
            except Exception as e:
                self.ui.error("Unexpected Error", str(e))
                if self.logger:
                    self.logger.error(f"Loop error: {e}")


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    env_file = Path(__file__).parent / ".env"
    load_dotenv(env_file if env_file.exists() else None, override=True)
    log = Logger()

    try:
        agent = MIXAgent(log=log)
        agent.run_interactive()
    except ValueError as e:
        console = Console()
        console.print(
            f"\n  [bold {Theme.RED}]✗  Configuration Error[/bold {Theme.RED}]"
        )
        console.print(
            f"  [{Theme.YELLOW}]Create a .env file:[/{Theme.YELLOW}]\n"
            "    GEMINI_API_KEY=your_api_key_here\n"
        )
        sys.exit(1)
    except Exception as e:
        Console().print(
            f"\n  [bold {Theme.RED}]✗  Fatal: {e}[/bold {Theme.RED}]\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()



