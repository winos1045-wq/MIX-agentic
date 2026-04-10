## main.py  —  MIX Agent latest
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
from rich.panel   import Panel
from rich.syntax  import Syntax
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.rule     import Rule
from rich.text     import Text
from rich.columns  import Columns
from rich.padding  import Padding
from rich.theme    import Theme as RichTheme

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


# Rich console theme — overrides Rich's default magenta/pink markdown headings
RICH_THEME = RichTheme({
    "markdown.h1":           "bold white",
    "markdown.h1.border":    "white",
    "markdown.h2":           "bold white",
    "markdown.h2.border":    "dim white",
    "markdown.h3":           "bold white",
    "markdown.h4":           "bold white",
    "markdown.h5":           "bold white",
    "markdown.h6":           "dim white",
    "markdown.hr":           "dim #374151",
    "markdown.code":         "bold #06B6D4",
    "markdown.code_block":   "default",
    "markdown.block_quote":  "italic #94A3B8",
    "markdown.item.bullet":  "bold #FF8C42",
    "markdown.item.number":  "bold #FF8C42",
    "markdown.link":         "underline #3B82F6",
    "markdown.link_url":     "underline dim #3B82F6",
    "table.header":          "bold white",
    "markdown.table.header": "bold white",
})


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
    def truncate_text(text: str, max_width: int, suffix: str = "...") -> str:
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

    @staticmethod
    def is_narrow_terminal() -> bool:
        return TerminalUtils.get_width() < 60

    @staticmethod
    def create_robot() -> str:
        """Block-character robot for the startup banner."""
        if TerminalUtils.is_narrow_terminal():
            return ""
        return (
            "         \u2580\u2584     \u2584\u2580\n"
            "         \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\n"
            "         \u2588\u2584\u2588\u2588\u2588\u2588\u2588\u2584\u2588\n"
            "         \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\n"
            "         \u2588 \u2588   \u2588 \u2588\n"
        )


# ============================================================================
# STATUS BAR
# ============================================================================

class StatusBar:
    SPINNERS = ['\u28fe', '\u28fd', '\u28fb', '\u28bf', '\u287f', '\u28df', '\u28ef', '\u28f7']

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
            tok_str = f"{total:,} tok" if total else ""
            sep     = "  .  " if tok_str else ""
            line    = f" {frame}  {self._phase}  {t_str}{sep}{tok_str}  .  esc to cancel"
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


StatusTracker = StatusBar


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
        return "  " + "  .  ".join(parts)

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
# LOGGER
# ============================================================================

class Logger:
    def __init__(self):
        self.monitoring_enabled = False
        logging.basicConfig(
            level=logging.WARNING,
            handlers=[RichHandler(console=Console(theme=RICH_THEME),
                                  show_time=True, show_path=False)]
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
# SESSION
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
            return "Monitoring enabled"
        if cmd == 'monitor_off':
            self.logger.disable_monitoring()
            return "Monitoring disabled"
        return None

    def _handle_agent(self, raw: str) -> str:
        parts = raw.split(maxsplit=1)
        sub   = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("status", "st"):
            return self.group.format_status()

        if sub == "leave":
            if not self.group.is_active:
                return "  Not in any group."
            name = self.group.group_name
            self.group.leave()
            return f"  Left group  {name}"

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
                    f"  .  {m['ts'][11:16]}\n"
                    f"  {m['message']}\n"
                )
            return "\n".join(lines)

        if sub.startswith("broadcast "):
            msg = sub[len("broadcast "):].strip()
            if not self.group.is_active:
                return "  Not in any group."
            n = self.group.broadcast(msg)
            return f"  Broadcast sent to {n} agent(s)."

        if sub.startswith("send "):
            rest  = sub[len("send "):].strip()
            tok   = rest.split(maxsplit=1)
            if len(tok) < 2:
                return "  Usage:  /agent send <id_prefix> <message>"
            id_prefix, msg = tok
            member = self.group.get_member_by_prefix(id_prefix)
            if not member:
                return f"  No agent with id starting '{id_prefix}'."
            ok = self.group.send(member["id"], msg)
            if ok:
                return f"  Sent to {member['name']}  [{member['id']}]"
            return f"  Could not reach {member['name']}"

        return self._agent_wizard()

    def _agent_wizard(self) -> str:
        self.console.print()
        self.console.print(
            f"  [{Theme.CYAN}]Agent Group[/{Theme.CYAN}]"
            f"  [dim]Link agents across terminals[/dim]"
        )
        self.console.print()
        group_name = self.console.input(
            f"  [{Theme.SLATE}]Group name :[/{Theme.SLATE}] "
        ).strip()
        if not group_name:
            return "  Cancelled."
        agent_name = self.console.input(
            f"  [{Theme.SLATE}]Agent name :[/{Theme.SLATE}] "
        ).strip()
        if not agent_name:
            return "  Cancelled."
        self.console.print(f"\n  [dim]Connecting to group  {group_name} ...[/dim]")

        ui_console = self.console

        def _on_msg(msg: Dict):
            ui_console.print()
            ui_console.print(
                f"  [{Theme.PINK}]{msg['from_name']}[/{Theme.PINK}]"
                f"  [dim]{msg['from_rank']}  .  {msg['from_id'][:8]}"
                f"  .  {msg['type']}[/dim]"
            )
            ui_console.print(
                f"  [dim]|[/dim]  [bold {Theme.TEXT}]{msg['message']}[/bold {Theme.TEXT}]"
            )
            ui_console.print()

        info   = self.group.join(group_name, agent_name, on_message=_on_msg)
        action = "Created" if info["created"] else "Joined"
        self.console.print(
            f"\n  [{Theme.GREEN}]{action} group[/{Theme.GREEN}]"
            f"  [bold]{group_name}[/bold]\n"
        )
        self.console.print(f"  Name    {agent_name}")
        self.console.print(f"  ID      {info['id']}")
        self.console.print(f"  Rank    {info['rank']}")
        self.console.print(f"  Members {len(info['members'])}")
        if len(info["members"]) > 1:
            self.console.print(f"\n  [{Theme.SLATE}]Members:[/{Theme.SLATE}]")
            for m in info["members"].values():
                marker = ">" if m["id"] == info["id"] else " "
                self.console.print(
                    f"    {marker}  {m['name']:<18} {m['rank']:<10} {m['id']}"
                )
        self.console.print()
        return ""

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
                content = content[:max_w] + "..."
            lines.append(f"  {i:2}. [{role}] {content}\n")
        return ''.join(lines)

    def _show_status(self) -> str:
        cwd   = os.getcwd()
        width = TerminalUtils.get_width()
        if len(cwd) > width - 25:
            cwd = "..." + cwd[-(width - 28):]
        mon = "ON" if self.logger.monitoring_enabled else "OFF"
        cs  = "available" if _CODESPACE_AVAILABLE else "not installed"
        ctx = (f"{len(self.session.history)} msgs "
               f"(sending last {CONTEXT_WINDOW_MESSAGES})")
        grp = (
            f"{self.group.group_name}  .  {self.group.agent_id}  "
            f".  {self.group.agent_rank}"
            if self.group.is_active else "not joined"
        )
        return (
            f"  CWD          {cwd}\n"
            f"  Session      {self.session.session_id}\n"
            f"  Context      {ctx}\n"
            f"  Monitoring   {mon}\n"
            f"  Codespace    {cs}\n"
            f"  Agent group  {grp}\n"
            f"  Path guard   active\n"
            f"  @ injection  active\n"
            f"  Terminal     {width}x{TerminalUtils.get_height()}\n\n"
            + self.tokens.format_status()
        )


# ============================================================================
# MARKDOWN RENDERER
# ============================================================================

class MarkdownRenderer:
    """
    Splits AI response text into prose segments and fenced code blocks.
    - Prose (including ### headers, | tables |, **bold**, lists) ->
      Rich Markdown engine, which respects RICH_THEME for white headings.
    - Fenced code blocks -> Rich Syntax for language highlighting.
    """

    LANG_ALIASES: Dict[str, str] = {
        "js":         "javascript",
        "ts":         "typescript",
        "py":         "python",
        "sh":         "bash",
        "shell":      "bash",
        "zsh":        "bash",
        "yml":        "yaml",
        "md":         "markdown",
        "dockerfile": "docker",
        "":           "text",
    }

    def __init__(self, console: Console):
        self.console = console

    def render(self, text: str):
        self.console.print()
        for kind, content in self._split_segments(text):
            if kind == "code":
                lang, code = content
                self._render_code_block(lang, code)
            else:
                # Rich Markdown renders tables, headers (white via RICH_THEME),
                # bold, italic, bullet lists, blockquotes, horizontal rules.
                md = Markdown(content, code_theme="github-dark", hyperlinks=True)
                self.console.print(Padding(md, (0, 2)))
        self.console.print()

    @staticmethod
    def _split_segments(text: str) -> List[tuple]:
        segments = []
        pattern  = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
        cursor   = 0
        for m in pattern.finditer(text):
            prose = text[cursor:m.start()].strip()
            if prose:
                segments.append(("text", prose))
            lang = m.group(1).strip().lower()
            code = m.group(2)
            segments.append(("code", (lang, code)))
            cursor = m.end()
        tail = text[cursor:].strip()
        if tail:
            segments.append(("text", tail))
        return segments

    def _render_code_block(self, lang: str, code: str):
        resolved = self.LANG_ALIASES.get(lang, lang) or "text"
        label    = lang.upper() if lang else "CODE"
        width    = min(TerminalUtils.get_width() - 8, 80)

        self.console.print(
            f"  [dim]+-[/dim] [{Theme.CYAN}]{label}[/{Theme.CYAN}]"
        )
        try:
            syntax = Syntax(
                code.rstrip(),
                resolved,
                theme="github-dark",
                line_numbers=not TerminalUtils.is_narrow(),
                word_wrap=True,
                padding=(0, 2),
                background_color="default",
            )
            self.console.print(Padding(syntax, (0, 2)))
        except Exception:
            for line in code.rstrip().splitlines():
                self.console.print(f"  [dim]{line}[/dim]")

        self.console.print(f"  [dim]+{'-' * min(width, 56)}[/dim]")
        self.console.print()


# ============================================================================
# UI
# ============================================================================

class UI:
    def __init__(self):
        # RICH_THEME makes all Markdown output use white headers
        self.console = Console(highlight=False, theme=RICH_THEME)
        self._md     = MarkdownRenderer(self.console)

    # ── banner ────────────────────────────────────────────────────────────────

    def welcome_screen(self, model_name: str = ""):
        from rich.table import Table
        from rich import box as rich_box

        self.console.clear()
        self.console.print()

        robot = TerminalUtils.create_robot()
        cwd   = os.getcwd()
        width = TerminalUtils.get_width()
        if len(cwd) > width - 18:
            cwd = "..." + cwd[-(width - 21):]

        build = datetime.now().strftime("%Y.%m.%d")

        if not TerminalUtils.is_narrow():
            left = Text(justify="left")
            left.append(" ✻ MIX Agent\n",                                style=f"bold {Theme.TEXT}")
            left.append(" │\n",                                          style=f"dim {Theme.GRAY}")
            left.append(" └── /help for commands  .  @file to inject\n", style=f"italic {Theme.SLATE}")
            left.append("     /agent (beta) link agents\n\n",            style=f"italic {Theme.SLATE}")
            if model_name:
                left.append(f" {model_name}  .  MIX\n",                 style=f"dim")
            left.append(f" {cwd}\n",                                     style=f"dim {Theme.SLATE}")
            left.append(" DEV by : FAHFAH MOHAMED",                      style=f"italic {Theme.SLATE}")

            robot_width = 22
            safe_width  = max(40, width - robot_width - 15)
            layout = Table.grid(expand=False, padding=(0, 3))
            layout.add_column(width=safe_width)
            layout.add_column(width=robot_width, justify="right")
            layout.add_row(
                left,
                Text(robot, style=f"bold {Theme.ORANGE}", justify="left")
            )
            content = layout
        else:
            content = Text(justify="left")
            content.append("✻ MIX Agent\n",           style=f"bold {Theme.TEXT}")
            content.append(robot + "\n",               style=f"bold {Theme.ORANGE}")
            content.append("/help for commands\n",     style=f"italic {Theme.SLATE}")
            content.append("@file to inject\n",        style=f"italic {Theme.SLATE}")
            if model_name:
                content.append(f"{model_name}  .  MIX\n", style="dim")
            content.append(f"{cwd}\n",                 style=f"dim {Theme.SLATE}")
            content.append("DEV by : FAHFAH MOHAMED",  style=f"italic {Theme.SLATE}")

        panel_title = (
            f"[dim {Theme.SLATE}]{model_name}  .  v{build}[/dim {Theme.SLATE}]"
            if model_name else
            f"[dim {Theme.SLATE}]v{build}[/dim {Theme.SLATE}]"
        )

        self.console.print(Panel(
            content,
            title=panel_title,
            title_align="right",
            border_style=Theme.ORANGE,
            box=rich_box.ROUNDED,
            padding=(1, 2),
        ))
        self.console.print()
    # ── separator ─────────────────────────────────────────────────────────────

    def separator(self, label: str = ""):
        w = min(TerminalUtils.get_width(), 88)
        if label:
            self.console.print(Rule(label, style=Theme.GRAY))
        else:
            self.console.print(f"[{Theme.GRAY}]{'-' * w}[/{Theme.GRAY}]")

    # ── file injection report ─────────────────────────────────────────────────

    def print_injection_report(self, result: InjectionResult):
        if result.injected:
            for p in result.injected:
                self.console.print(
                    f"  [dim]+[/dim] [{Theme.CYAN}]{p}[/{Theme.CYAN}]"
                    f"[dim] injected[/dim]"
                )
        if result.blocked:
            for p, reason in result.blocked:
                self.console.print(
                    f"  [dim]x[/dim] [{Theme.RED}]{p}[/{Theme.RED}]"
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

    # ── tool execution row ────────────────────────────────────────────────────

    def print_tool_execution(self, tool_name: str, args: Dict[str, Any],
                              result: str = None):
        DISPATCH: Dict[str, tuple] = {
            "get_file_content":     ("Read",    "file_path",        Theme.BLUE),
            "write_file":           ("Write",   "file_path",        Theme.GREEN),
            "patch_file":           ("Patch",   "file_path",        Theme.YELLOW),
            "run_shell":            ("Shell",   "command",          Theme.PURPLE),
            "run_python_file":      ("Python",  "file_path",        Theme.CYAN),
            "get_files_info":       ("List",    "path",             Theme.SLATE),
            "build_project":        ("Build",   "build_tool",       Theme.ORANGE),
            "install_dependencies": ("Install", "package_manager",  Theme.ORANGE),
            "plan_project":         ("Plan",    "task_description", Theme.BLUE),
            "search_code":          ("Search",  "pattern",          Theme.PINK),
            "get_project_map":      ("Map",     "path",             Theme.CYAN),
            "verify_change":        ("Verify",  "file_path",        Theme.GREEN),
            "web_search":           ("Web",     "query",            Theme.BLUE),
            "web_fetch":            ("Fetch",   "url",              Theme.BLUE),
            "cs_run_shell":         ("CS Shell","command",          Theme.PURPLE),
            "cs_write_file":        ("CS Write","file_path",        Theme.GREEN),
            "cs_read_file":         ("CS Read", "file_path",        Theme.BLUE),
            "cs_patch_file":        ("CS Patch","file_path",        Theme.YELLOW),
        }
        if tool_name in DISPATCH:
            label, key, color = DISPATCH[tool_name]
            val_str = TerminalUtils.truncate_text(str(args.get(key, "") or ""), 62)
        else:
            label   = tool_name.replace("_", " ").title()
            color   = Theme.SLATE
            vals    = list(args.values())
            val_str = TerminalUtils.truncate_text(str(vals[0]), 62) if vals else ""

        self.console.print(
            f"  [{Theme.GRAY}]|[/{Theme.GRAY}] "
            f"[{color}]{label:<8}[/{color}]"
            f"  [bold white]{val_str}[/bold white]"
        )
        if result:
            first = TerminalUtils.truncate_text(
                str(result).strip().split('\n')[0], 72
            )
            self.console.print(
                f"  [{Theme.GRAY}]|[/{Theme.GRAY}]         [dim]{first}[/dim]"
            )

    # ── AI response ───────────────────────────────────────────────────────────

    def print_response(self, text: str, agent_tag: str = ""):
        """
        Render AI output. Headers white, tables formatted, code highlighted.
        """
        self.console.print()
        if agent_tag:
            parts  = [p.strip() for p in agent_tag.split(".")]
            name   = parts[0] if parts else "MIX"
            detail = "  .  ".join(parts[1:]) if len(parts) > 1 else ""
            self.console.print(
                f"  [{Theme.ORANGE}]|[/{Theme.ORANGE}]  "
                f"[bold {Theme.ORANGE}]{name}[/bold {Theme.ORANGE}]"
                + (f"  [dim]{detail}[/dim]" if detail else "")
            )
        else:
            self.console.print(
                f"  [{Theme.ORANGE}]|[/{Theme.ORANGE}]  "
                f"[bold {Theme.TEXT}]MIX[/bold {Theme.TEXT}]"
            )
        self._md.render(text)

    # ── token summary ─────────────────────────────────────────────────────────

    def print_token_summary(self, summary: str):
        self.console.print(f"[dim]{summary}[/dim]\n")

    # ── inbox notification ────────────────────────────────────────────────────

    def print_inbox_message(self, msg: Dict):
        self.console.print()
        self.console.print(
            f"  [{Theme.PINK}]{msg['from_name']}[/{Theme.PINK}]"
            f"  [dim]{msg['from_rank']}"
            f"  .  {msg['from_id'][:8]}"
            f"  .  {msg['type']}[/dim]"
        )
        self.console.print(
            f"  [dim]|[/dim]  "
            f"[bold {Theme.TEXT}]{msg['message']}[/bold {Theme.TEXT}]"
        )
        self.console.print()

    # ── panels ────────────────────────────────────────────────────────────────

    def error(self, title: str, content: str):
        self.console.print(Panel(
            content, title=f"[{Theme.RED}]{title}[/{Theme.RED}]",
            border_style=Theme.RED, padding=(0, 2), expand=False
        ))

    def warning(self, title: str, content: str):
        self.console.print(Panel(
            content, title=f"[{Theme.YELLOW}]{title}[/{Theme.YELLOW}]",
            border_style=Theme.YELLOW, padding=(0, 2), expand=False
        ))

    def info(self, title: str, content: str):
        self.console.print(Panel(
            content, title=f"[{Theme.CYAN}]{title}[/{Theme.CYAN}]",
            border_style=Theme.CYAN, padding=(0, 2), expand=False
        ))


# ============================================================================
# AI AGENT
# ============================================================================

class MIXAgent:
    MODEL = 'gemma-4-26b-a4b-it'

    SYSTEM_PROMPT = """
#  AGENT SYSTEM PROMPT
> Version 2.0 — Built on Anthropic's Context Engineering Principles

---

## <background_information>

You are an **elite software engineer and cybersecurity expert** operating as an autonomous agent.  
You work in a loop: **perceive → plan → act → verify → report**.

### Core Philosophy (internalize these, don't repeat them)

- **Context is finite and precious.** Every token you load costs attention budget. Load only what you need, when you need it — *just in time*, not all upfront.
- **Signal over volume.** A small, high-signal context beats a bloated one every time. Prefer targeted reads over full-file dumps.
- **Progressive disclosure.** Explore the environment layer by layer. Let each tool call inform the next decision.
- **Verify, don't assume.** After every state-changing action, confirm the outcome before moving on.
- **Stop beats looping.** Two failed attempts at the same thing means stop and report — never a blind third try.

</background_information>

---

## <security_constraints>

### PATH SECURITY (non-negotiable)
- The path guard restricts file access.
- **Blocked paths:** `.env`, `.git`, `node_modules`, `sessions`, `logs`, private keys, credential files.
- If any access returns a 🔒 error → **stop immediately, do not retry or attempt workarounds, report to user.**
- Never attempt to bypass, encode around, or approximate a blocked path.

### INJECTED FILE PROTOCOL
- When the user's message contains `<injected_file>` or `<injected_dir>` blocks → those files are **already in context**. Use them directly.
- **Do NOT call `get_file_content`** for already-injected content — this wastes context budget.
- Only call `get_file_content` for files that are explicitly **not** present in context.

</security_constraints>

---

## <thinking_protocol>

Run this internal reasoning loop **silently before every action**. Never skip a step.

### STEP 0 — Parse the Request
- What is the **literal** ask? What is the **underlying intent**?
- Are there injected files? List them mentally.
- Is this a single task or a multi-task job?

### STEP 1 — Inventory: Known vs. Unknown
- **Known:** facts from injected files, tool results already in context, prior conversation.
- **Unknown:** anything not yet verified in the current workspace.
- Identify any **blocked unknowns** (things you need but cannot safely access). If one exists → stop and report before proceeding.

### STEP 2 — Smallest Verifiable Next Step
Ask: *"What single action gives maximum information with minimum context cost?"*
- Prefer `search_code` → targeted read → `patch_file` over blind full-file reads.
- Prefer listing a directory over reading every file in it.
- Load data **just in time**: retrieve only what the current step requires.

### STEP 3 — Hypothesis → Execute → Compare
- State your hypothesis: *"I expect that reading X will show Y."*
- Execute the action.
- Compare the result to your hypothesis.
- **If mismatch after 2 attempts → stop and report. Never attempt a third blind try.**

### STEP 4 — Verify After Every State Change
After any edit, build, commit, or write:
1. Immediately verify the outcome (run a test, check the file, confirm the build).
2. If verification fails → **one targeted fix → re-verify**.
3. If it still fails → stop and report. Do not spiral into repeated fixes.

### STEP 5 — Decide: Act or Ask
- **Act autonomously** when: you are confident, the action is low-risk, and the user did not request approval.
- **Ask or report** when: the action is destructive, affects shared state, requires >7 top-level tasks, hits a 🔒 block, or enters a failure loop.

</thinking_protocol>

---

## <tool_usage_guide>

### 🔍 Search-First Protocol (mandatory for code tasks)
Never read a file blindly. Always locate before loading.

```
1. search_code(pattern, output_mode='files_with_matches')  → find which files matter
2. search_code(pattern, path=<file>, output_mode='content', context=3)  → read the relevant slice
3. get_file_content(file, start_line, end_line)  → only if you need the full block to patch
4. patch_file(...)  → make the change
```

This workflow saves ~80% of context vs. loading full files upfront.

### 🗺️ Codebase Orientation Protocol (unfamiliar repos)
On any task involving a codebase you haven't seen yet:
```
1. get_project_map()       → understand structure, deps, data flow
2. search_code(pattern)    → locate specific code
3. get_file_content(...)   → targeted read only
4. patch_file(...)         → make changes
```

### 🌐 Web Access Protocol
Use `web_search` when you need: current docs, error explanations, package versions, API references, or anything not in the codebase.

- **Max 8 `web_search` calls per session** — use them deliberately.
- After `web_search`, use `web_fetch` on the most relevant URL to get full content.
- **Never use `web_fetch` on localhost, 127.0.0.1, or internal IPs.**

### 🧠 Context Budget Rules
| Action | When to use |
|--------|-------------|
| `search_code` (files_with_matches) | Always first — cheapest way to locate |
| `search_code` (content + context=3) | After locating — read only the slice |
| `get_file_content` (with line range) | Only when you need a specific block to patch |
| `get_file_content` (full file) | Last resort — only if patching requires full context |
| `get_project_map` | Once, at the start of unfamiliar codebase work |

</tool_usage_guide>

---

## <task_decomposition>

### When to Decompose
Decompose any task that involves:
- More than one file to change
- A sequence of dependent actions
- Uncertainty about the full scope (explore first, then plan)
- An outcome that requires verification at multiple stages

### How to Decompose (mandatory for complex tasks)

#### 1. PLAN
Generate a structured plan. If >7 top-level tasks, write a **design note first** and get user approval before executing.

```json
{
  "goal": "<one sentence description of the end state>",
  "context_notes": "<key facts known before starting>",
  "tasks": [
    {
      "id": "T1",
      "title": "<short action title>",
      "intent": "<why this step is needed>",
      "dependencies": [],
      "subtasks": [
        { "id": "T1.1", "action": "<atomic action>", "tool": "<tool to use>" },
        { "id": "T1.2", "action": "<atomic action>", "tool": "<tool to use>" }
      ],
      "files_to_modify": ["<path>"],
      "verification": "<how to confirm this task succeeded>"
    }
  ]
}
```

**Subtask atomicity rule:** Each subtask should be a single, independently verifiable action. If a subtask requires two tool calls, split it into two subtasks.

#### 2. REVIEW (before executing)
Check your plan for:
- Missing dependencies (does T3 actually need T1 to finish first?)
- Files that need to be read before they can be patched
- Assumptions that aren't verified yet
- Tasks that could be parallelized vs. those that are strictly sequential

If flawed → revise and note what changed.

#### 3. EXECUTE (sequentially, with verification)
- Follow tasks in dependency order.
- Use `patch_file` for existing files. Use `write_file` only for new files.
- After each task: run verification (test / lint / type-check / file check).
- Log each result inline: ✅ passed or ❌ failed with reason.
- If ❌: diagnose → fix once → re-verify → if still ❌, stop and report.

#### 4. REFLECT (after completion)
Write 2–3 sentences covering:
- What worked well?
- What was harder than expected?
- What to do differently next time?

Include this in your final response.

### Subtask Breaking Heuristics
When breaking a task into subtasks, use these principles:

| Principle | Meaning |
|-----------|---------|
| **One tool per subtask** | Each subtask calls exactly one tool |
| **Verifiable outcome** | You can confirm the subtask succeeded before moving on |
| **Minimal context load** | Only load what that subtask needs |
| **Explicit dependency** | State which prior subtask must succeed before this one starts |
| **Rollback awareness** | Know how to undo the subtask if it goes wrong |

</task_decomposition>

---

## <context_management>

### Just-In-Time Loading
- Do **not** load all relevant files at the start of a task.
- Load each file only when a specific subtask requires it.
- Prefer lightweight references (file paths, line numbers, function names) over full content in your working memory.

### Note-Taking for Long Tasks
For tasks spanning many tool calls or multiple files, maintain a running mental (or written) note:
```
PROGRESS NOTES:
- Goal: <end state>
- Completed: T1 (✅), T2 (✅)
- In progress: T3 — reading auth.py
- Blockers: none
- Key facts: JWT secret is in config/settings.py:L42
- Next: patch middleware after reading current implementation
```

This prevents context drift and keeps goal-directed behavior intact across many steps.

### When Context Gets Heavy
If you find yourself holding a lot of state, apply compaction mentally:
- Discard raw tool outputs once their key facts are extracted.
- Keep only: architectural decisions, unresolved bugs, implementation details, and the next action.
- Summarize prior steps in one sentence each rather than re-reading them.

</context_management>

---

## <stop_and_report_protocol>

**Immediately stop and use the report format below if you encounter:**

| Trigger | Description |
|---------|-------------|
|  Path guard block | A needed file is blocked — do not retry |
|  Failure loop | Same tool fails twice with same error |
|  Unclear requirements | Ambiguity that changes what the correct solution is |
|  Plan > 7 tasks | Need user approval before a large execution plan |
|  Missing tool | Required capability doesn't exist |
| ✅ Verify fails x2 | Fix attempt failed, second verify still fails |
|  Unknown dependency | Can't determine what a piece of code does without more context |

### Report Format (mandatory — use exactly)

```markdown
🛠️ AGENT NEEDS INPUT

Problem:
[Factual, specific description of what went wrong or what is unclear]

Reason:
[Why this is a blocker — missing info, ambiguity, tool failure, path restriction]

What I've tried:
[Tool calls attempted and their outcomes]

Requested Action:
[Exact tool, file, human decision, or clarification that would unblock this]
```

</stop_and_report_protocol>

---

## <output_format>

### Final Response Format (mandatory after all tasks)

Start your response with the task checklist. Each line = one top-level task:

```
- ✅ 1  <short description of what was done>
- ✅ 2  <short description>
- ❌ 3  <short description of what failed and why>
```

Rules:
- ✅ = fully completed and verified
- ❌ = attempted but failed or could not complete
- One bullet per **top-level task** (not per subtask)
- Include the REFLECT block after the checklist for complex tasks
- Do not add extra prose unless the user explicitly asked for it

### Inline Logging During Execution
While executing tasks, log progress concisely:
```
→ T1.1: search_code("def authenticate") — found in src/auth/jwt.py:L34 ✅
→ T1.2: read src/auth/jwt.py L30–50 ✅
→ T2.1: patch jwt.py — added refresh token logic ✅
→ T2.2: run tests — 42 passed, 0 failed ✅
```

</output_format>

---

## <calibration_reminders>

These are heuristics, not rigid rules. Apply judgment:

- **Right altitude:** Don't hardcode brittle if-else logic into your approach. Don't be so vague you give no guidance. Hit the middle: specific heuristics that generalize.
- **Tool overlap:** If two tools could do the same job, pick the one with the narrower scope. Avoid calling tools with overlapping functionality back-to-back.
- **Minimal examples over exhaustive rules:** A few canonical cases teach better than a list of every edge case.
- **Smarter = more autonomous:** The more confident you are, the less you need to ask. Reserve questions for genuine blockers.
- **Exploration is cheap; mistakes are expensive:** A quick `ls` or `search_code` before a `patch_file` is almost always worth it.

</calibration_reminders>
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
            complete_while_typing=True,
            complete_in_thread=True,
        )

    def _build_config(self) -> types.GenerateContentConfig:
        schemas = [
            schema_get_files_info, schema_get_file_content,
            schema_run_python_file, schema_write_file, schema_run_shell,
            schema_build_project, schema_install_dependencies,
            schema_patch_file, schema_plan_project,
            schema_search_code, schema_get_project_map, schema_verify_change,
            schema_web_search, schema_web_fetch
        ] + list(_cs_schemas)
        return types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=schemas)],
            system_instruction=self.SYSTEM_PROMPT,
            temperature=0.7,
        )

    def _preprocess_input(self, raw: str) -> tuple:
        result = inject_files(raw)
        if result.injected or result.blocked or result.missing:
            self.ui.print_injection_report(result)
        return result.prompt, result

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

    def process_request(self, user_input: str, verbose: bool = False):
        injected_input, _ = self._preprocess_input(user_input)
        self.session.add_message("user", user_input)
        messages = self._build_messages(injected_input)

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
                        f"Thinking:   "
                        f"{getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0}"
                    ))
                    self.status.start("Thinking")

                if not response.candidates:
                    continue

                for candidate in response.candidates:
                    if candidate and candidate.content:
                        messages.append(candidate.content)

                if response.function_calls:
                    self.status.stop()
                    self.ui.console.print(f"  [{Theme.GRAY}]|[/{Theme.GRAY}]")

                    for fc in response.function_calls:
                        if not self._guard_function_call(fc):
                            messages.append(types.Content(
                                role="user",
                                parts=[types.Part(function_response=types.FunctionResponse(
                                    name=fc.name,
                                    response={"result": "Blocked: path not allowed"}
                                ))]
                            ))
                            self.ui.console.print(
                                f"  [{Theme.RED}]Blocked[/{Theme.RED}]"
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

                    self.ui.print_response(
                        response_text,
                        agent_tag=self.group.identity_tag
                    )

                    if last_req_counts is not None:
                        self.ui.print_token_summary(
                            self.tokens.format_request(last_req_counts)
                        )

                    if self.group.is_active:
                        self.group.set_status("idle")
                    return

            self.status.stop()
            self.ui.warning("Limit reached",
                            f"Hit max iterations ({self.max_iterations}).")

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

    def run_interactive(self):
        self.ui.welcome_screen(model_name=self.MODEL)

        if _CODESPACE_AVAILABLE:
            self.ui.console.print(
                f"  [{Theme.CYAN}]Codespace tools active "
                f"({len(_cs_schemas)} tools)[/{Theme.CYAN}]\n"
            )

        while True:
            try:
                if self.group.is_active:
                    prompt_txt   = f"  [{self.group.group_name} . {self.group.agent_name}] > "
                    prompt_fg    = "#06B6D4"
                else:
                    prompt_txt   = "  > "
                    prompt_fg    = "#10B981"

                # Dark charcoal background on the entire input line (image 2 style)
                input_style = PromptStyle.from_dict({
                    'completion-menu':                    'bg:#0d1117 fg:#6B7280',
                    'completion-menu.completion.current': 'bg:#1c2128 fg:#58a6ff',
                    'completion-menu.completion':         'fg:#6B7280',
                    '':       'bg:#1c2128',                  # charcoal bg — whole input area
                    'prompt': f'bg:#1c2128 fg:{prompt_fg}',  # colored > on same bg
                })

                user_input = self.prompt_session.prompt(
                    [('class:prompt', prompt_txt)],
                    style=input_style,
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
                            f"\n  [{Theme.CYAN}]Reloading...[/{Theme.CYAN}]\n"
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
    except ValueError:
        console = Console(theme=RICH_THEME)
        console.print(f"\n  [bold {Theme.RED}]Configuration Error[/bold {Theme.RED}]")
        console.print(
            f"  [{Theme.YELLOW}]Create a .env file:[/{Theme.YELLOW}]\n"
            "    GEMINI_API_KEY=your_api_key_here\n"
        )
        sys.exit(1)
    except Exception as e:
        Console(theme=RICH_THEME).print(
            f"\n  [bold {Theme.RED}]Fatal: {e}[/bold {Theme.RED}]\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
