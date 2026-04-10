##main.py  — MIX Agent  (modernised)
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


from func.get_files_info import schema_get_files_info
from func.get_file_content import schema_get_file_content
from func.write_file import schema_write_file
from func.run_python_file import schema_run_python_file
from func.run_shell import schema_run_shell
from func.patch_file import schema_patch_file
from func.build import schema_build_project, schema_install_dependencies
from func.plan_project import schema_plan_project
from call_function import call_function

# ── @ token injector & path guard ────────────────────────────────────────────
from file_injector import inject_files, InjectionResult
from path_guard import guard, GuardError, PathGuard

# Codespace schemas — loaded safely so a missing file doesn't crash startup
try:
    from func.codespace_tools import cs_schemas as _cs_schemas
    _CODESPACE_AVAILABLE = True
except ImportError:
    _cs_schemas = []
    _CODESPACE_AVAILABLE = False

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.syntax import Syntax
from rich import box
from rich.logging import RichHandler
from rich.columns import Columns

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, merge_completers
from prompt_toolkit.styles import Style as PromptStyle


# ============================================================================
# THEME & STYLING
# ============================================================================

class Theme:
    ORANGE = "#FF8C42"
    DIM    = "#6B7280"
    TEXT   = "#F9FAFB"
    GREEN  = "#10B981"
    RED    = "#EF4444"
    YELLOW = "#F59E0B"
    CYAN   = "#06B6D4"
    CYANa  = "#ffffff"
    PURPLE = "#A78BFA"
    BLUE   = "#3B82F6"
    GRAY   = "#374151"
    SLATE  = "#94A3B8"


# ============================================================================
# AUTOCOMPLETE SYSTEM
# ============================================================================

class CommandCompleter(Completer):
    COMMANDS = [
        'help', 'history', 'clear', 'status',
        'monitor_on', 'monitor_off', 'reload', 'exit', 'quit', 'q'
    ]

    def get_completions(self, document, complete_event):
        word_before_cursor = document.get_word_before_cursor(
            pattern=re.compile(r'/[^\s]*')
        )
        if not word_before_cursor.startswith('/'):
            return
        search_term = word_before_cursor[1:].lower()
        for cmd in self.COMMANDS:
            if search_term in cmd.lower():
                yield Completion(
                    f"/{cmd}",
                    start_position=-len(word_before_cursor),
                    display=f"/{cmd}"
                )


class FilePathCompleter(Completer):
    IGNORE_DIRS      = {'__pycache__', 'node_modules', 'venv', '.git',
                        '.next', 'dist', 'build', '.mypy_cache', '.pytest_cache',
                        'sessions', 'logs', '.venv'}
    REFRESH_INTERVAL = 30

    def __init__(self):
        self._cache: List[str] = []
        self._last_refresh: float = 0.0
        self._lock = threading.Lock()

    def _rebuild(self):
        new_cache: List[str] = []
        for root, dirs, files in os.walk('.'):
            dirs[:] = [
                d for d in dirs
                if not d.startswith('.') and d not in self.IGNORE_DIRS
            ]
            for d in dirs:
                rel = os.path.relpath(os.path.join(root, d), '.')
                # Only show safe paths in autocomplete
                if guard.is_safe(rel):
                    new_cache.append(rel + '/')
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), '.')
                if guard.is_safe(rel):
                    new_cache.append(rel)
        with self._lock:
            self._cache = new_cache
            self._last_refresh = time.time()

    def _ensure_fresh(self):
        if time.time() - self._last_refresh > self.REFRESH_INTERVAL:
            self._last_refresh = time.time()
            threading.Thread(target=self._rebuild, daemon=True).start()

    def get_completions(self, document, complete_event):
        word_before_cursor = document.get_word_before_cursor(
            pattern=re.compile(r'@[^\s]*')
        )
        if not word_before_cursor.startswith('@'):
            return
        self._ensure_fresh()
        search_term = word_before_cursor[1:].lower()
        with self._lock:
            snapshot = list(self._cache)
        for entry in snapshot:
            if search_term in entry.lower():
                yield Completion(
                    f"@{entry}",
                    start_position=-len(word_before_cursor),
                    display=f"@{entry}"
                )


autocomplete_style = PromptStyle.from_dict({
    'completion-menu':                    'bg:#0d1117 fg:#6B7280',
    'completion-menu.completion.current': 'bg:#1c2128 fg:#58a6ff',
    'completion-menu.completion':         'fg:#6B7280',
})


# ============================================================================
# TERMINAL SIZE UTILITIES
# ============================================================================

class TerminalUtils:
    @staticmethod
    def get_terminal_size() -> tuple:
        try:
            return shutil.get_terminal_size()
        except Exception:
            return (80, 24)

    @staticmethod
    def get_width() -> int:
        return TerminalUtils.get_terminal_size()[0]

    @staticmethod
    def get_height() -> int:
        return TerminalUtils.get_terminal_size()[1]

    @staticmethod
    def truncate_text(text: str, max_width: int, suffix: str = "…") -> str:
        if len(text) <= max_width:
            return text
        return text[:max_width - len(suffix)] + suffix

    @staticmethod
    def wrap_text(text: str, width: int) -> List[str]:
        words = text.split()
        lines: List[str] = []
        current_line: List[str] = []
        current_length = 0
        for word in words:
            word_length = len(word) + 1
            if current_length + word_length <= width:
                current_line.append(word)
                current_length += word_length
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
                current_length = word_length
        if current_line:
            lines.append(' '.join(current_line))
        return lines

    @staticmethod
    def is_narrow_terminal() -> bool:
        return TerminalUtils.get_width() < 80


# ============================================================================
# REAL-TIME STATUS BAR  (Claude Code style)
# ============================================================================

class StatusBar:
    """
    Renders a compact, always-updating status line while the AI is thinking.
    Shows: spinner · elapsed · token count · ESC hint
    """
    SPINNERS = ['⣾', '⣽', '⣻', '⢿', '⡿', '⣟', '⣯', '⣷']

    def __init__(self):
        self.running           = False
        self.thread: Optional[threading.Thread] = None
        self.start_time: Optional[float] = None
        self.prompt_tokens     = 0
        self.completion_tokens = 0
        self._lock             = threading.Lock()
        self._written          = 0   # chars written on current line
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
            phase   = self._phase

            line = f" {frame} {phase}  {t_str}  ·  {tok_str}  ·  esc to cancel"
            # ANSI: dim grey
            ansi = f"\033[2m{line}\033[0m"

            self._erase()
            sys.stdout.write(ansi)
            sys.stdout.flush()
            self._written = len(line)

            idx += 1
            time.sleep(0.08)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


# Keep old name for compat
StatusTracker = StatusBar


# ============================================================================
# LOGGER
# ============================================================================

class Logger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.monitoring_enabled = False

        log_file = self.log_dir / f"MIX_agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        logging.basicConfig(
            level=logging.INFO,
            format="( %(asctime)s )",
            handlers=[
                RichHandler(console=Console(), show_time=True, show_path=True),
                logging.FileHandler(log_file)
            ]
        )
        self.logger = logging.getLogger("MIX Agent")

        self.rich_handler: Optional[RichHandler] = None
        self.file_handler: Optional[logging.FileHandler] = None
        for h in self.logger.handlers:
            if isinstance(h, RichHandler):
                self.rich_handler = h
            elif isinstance(h, logging.FileHandler):
                self.file_handler = h

        self._set_external_logging(False)

    def enable_monitoring(self):
        self.monitoring_enabled = True
        self._set_external_logging(True)
        if self.rich_handler:
            self.rich_handler.setLevel(logging.INFO)
        self.logger.info("Monitoring enabled")

    def disable_monitoring(self):
        self.monitoring_enabled = False
        self._set_external_logging(False)
        self.logger.info("Monitoring disabled")

    def _set_external_logging(self, enabled: bool):
        level = logging.INFO if enabled else logging.WARNING
        for name in ("google_genai", "httpx", "google.generativeai", "google.api_core"):
            logging.getLogger(name).setLevel(level)

    def info(self, msg: str):    self.logger.info(msg)
    def error(self, msg: str):   self.logger.error(msg)
    def warning(self, msg: str): self.logger.warning(msg)
    def debug(self, msg: str):   self.logger.debug(msg)


# ============================================================================
# SESSION MANAGEMENT  (with sliding-window context to cut token waste)
# ============================================================================

# Max messages kept in the context window sent to the API.
# Older messages are archived locally but not sent.
CONTEXT_WINDOW_MESSAGES = 20

class SessionManager:
    def __init__(self, session_dir: str = "sessions",
                 log: Optional[Logger] = None):
        self._log         = log
        self.session_dir  = Path(session_dir)
        self.session_dir.mkdir(exist_ok=True)
        self.session_id   = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_file = self.session_dir / f"session_{self.session_id}.json"
        self.history: List[Dict[str, Any]] = []
        self.load_history()

    def add_message(self, role: str, content: str,
                    metadata: Optional[Dict] = None):
        self.history.append({
            "timestamp": datetime.now().isoformat(),
            "role":      role,
            "content":   content,
            "metadata":  metadata or {}
        })
        self.save_history()

    def save_history(self):
        with open(self.session_file, 'w') as f:
            json.dump(self.history, f, indent=2)

    def load_history(self):
        if self.session_file.exists():
            try:
                with open(self.session_file, 'r') as f:
                    self.history = json.load(f)
            except Exception as e:
                if self._log:
                    self._log.warning(f"Could not load session history: {e}")
                self.history = []

    def get_context(self) -> List[Dict]:
        """Return only the last N messages to avoid token bloat."""
        return self.history[-CONTEXT_WINDOW_MESSAGES:]

    def clear_history(self):
        self.history = []
        self.save_history()


# ============================================================================
# COMMAND HANDLER
# ============================================================================

class CommandHandler:
    COMMANDS = {
        'help':        'Show help information',
        'history':     'Show recent chat history',
        'clear':       'Clear chat history',
        'status':      'Show agent status',
        'monitor_on':  'Enable request monitoring',
        'monitor_off': 'Disable request monitoring',
        'reload':      'Reload the agent',
        'exit':        'Exit the agent',
        'quit':        'Exit the agent',
        'q':           'Exit (shorthand)',
    }

    def __init__(self, session: SessionManager, console: Console, log: Logger):
        self.session = session
        self.console = console
        self.logger  = log

    def is_command(self, text: str) -> bool:
        return text.lower().strip().startswith('/')

    def handle(self, text: str) -> Optional[str]:
        cmd = text.lower().strip().lstrip('/')
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

    def _show_help(self) -> str:
        is_narrow = TerminalUtils.is_narrow_terminal()
        lines = ["Available Commands\n\n"]
        for cmd, desc in self.COMMANDS.items():
            lines.append(
                f"  /{cmd}\n      {desc}\n" if is_narrow
                else f"  /{cmd:<15} {desc}\n"
            )
        lines += [
            "\nFile Injection (@)\n\n",
            "  @path/to/file     Inject file content directly into prompt\n",
            "  @path/to/dir      Inject directory listing\n",
            "  No get_file_content call needed — content is pre-loaded.\n",
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
        ctx = f"{len(self.session.history)} msgs (sending last {CONTEXT_WINDOW_MESSAGES})"
        return (
            f"  CWD          {cwd}\n"
            f"  Session      {self.session.session_id}\n"
            f"  Context      {ctx}\n"
            f"  Monitoring   {mon}\n"
            f"  Codespace    {cs}\n"
            f"  Path guard   ✓ active\n"
            f"  @ injection  ✓ active\n"
            f"  Terminal     {width}×{TerminalUtils.get_height()}"
        )


# ============================================================================
# UI COMPONENTS  (Claude Code style)
# ============================================================================

class UI:
    def __init__(self):
        self.console = Console(highlight=False)

    def clear(self):
        self.console.clear()

    # ── Welcome screen ───────────────────────────────────────────────────────

    def welcome_screen(self):
        self.clear()
        width = TerminalUtils.get_width()
        cwd   = os.getcwd()
        if len(cwd) > width - 10:
            cwd = "…" + cwd[-(width - 13):]

        # top bar
        self.console.print()
        self.console.print(
            f"[bold {Theme.ORANGE}]  ✻  MIX Agent[/bold {Theme.ORANGE}]"
            f"  [dim]by FAHFAH MOHAMED[/dim]"
        )
        self.console.print(f"  [dim]{'─' * min(width - 4, 60)}[/dim]")
        self.console.print(f"  [dim]{cwd}[/dim]")
        self.console.print()
        self.console.print(
            f"  [dim]Type [/dim][bold white]/help[/bold white][dim] for commands, "
            f"[/dim][bold white]@file[/bold white][dim] to inject a file[/dim]"
        )
        self.console.print()

    # ── Dividers & simple lines ───────────────────────────────────────────────

    def separator(self):
        w = min(TerminalUtils.get_width(), 80)
        self.console.print(f"[{Theme.GRAY}]{'─' * w}[/{Theme.GRAY}]")

    def thin_line(self):
        self.console.print(f"[{Theme.GRAY}]{'╌' * 40}[/{Theme.GRAY}]")

    # ── Injection report ─────────────────────────────────────────────────────

    def print_injection_report(self, result: InjectionResult):
        """Show what @ tokens were resolved before sending to AI."""
        if result.injected:
            for p in result.injected:
                self.console.print(
                    f"  [dim]⊕[/dim] [bold {Theme.CYAN}]{p}[/bold {Theme.CYAN}]"
                    f"[dim] → injected[/dim]"
                )
        if result.blocked:
            for p, reason in result.blocked:
                short = reason.split('\n')[0]
                self.console.print(
                    f"  [dim]⊗[/dim] [{Theme.RED}]{p}[/{Theme.RED}]"
                    f"[dim]  {short}[/dim]"
                )
        if result.missing:
            for p in result.missing:
                self.console.print(
                    f"  [dim]?[/dim] [{Theme.YELLOW}]{p}[/{Theme.YELLOW}]"
                    f"[dim]  not found[/dim]"
                )
        if result.injected or result.blocked or result.missing:
            self.console.print()

    # ── Tool execution (compact, Claude Code style) ───────────────────────────

    def print_tool_execution(self, tool_name: str, args: Dict[str, Any],
                              result: str = None):
        DISPATCH: Dict[str, tuple] = {
            "read_file":            ("Read",    "file_path",  Theme.BLUE),
            "get_file_content":     ("Read",    "file_path",  Theme.BLUE),
            "write_file":           ("Write",   "file_path",  Theme.GREEN),
            "patch_file":           ("Patch",   "file_path",  Theme.YELLOW),
            "run_shell":            ("Shell",   "command",    Theme.PURPLE),
            "run_cmd":              ("Shell",   "command",    Theme.PURPLE),
            "run_python_file":      ("Python",  "file_path",  Theme.CYAN),
            "get_files_info":       ("List",    "path",       Theme.SLATE),
            "build_project":        ("Build",   "build_tool", Theme.ORANGE),
            "install_dependencies": ("Install", "package_manager", Theme.ORANGE),
            "plan_project":         ("Plan",    "task_description", Theme.BLUE),
            "cs_run_shell":         ("CS·Shell","command",    Theme.PURPLE),
            "cs_write_file":        ("CS·Write","file_path",  Theme.GREEN),
            "cs_read_file":         ("CS·Read", "file_path",  Theme.BLUE),
            "cs_patch_file":        ("CS·Patch","file_path",  Theme.YELLOW),
        }

        if tool_name in DISPATCH:
            label, key, color = DISPATCH[tool_name]
            val = args.get(key, "") or ""
            val_str = TerminalUtils.truncate_text(str(val), 60)
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
            first = str(result).strip().split('\n')[0]
            first = TerminalUtils.truncate_text(first, 72)
            self.console.print(f"  [dim]│         {first}[/dim]")

    # ── Response display ─────────────────────────────────────────────────────

    def print_response(self, text: str):
        """Print the AI response in a clean, readable way."""
        self.console.print()
        self.console.print(
            f"  [{Theme.ORANGE}]●[/{Theme.ORANGE}]  "
            f"[bold {Theme.TEXT}]MIX[/bold {Theme.TEXT}]"
        )
        self.console.print()

        # Detect and render code blocks
        parts = re.split(r'(```[\w]*\n.*?```)', text, flags=re.DOTALL)
        for part in parts:
            if part.startswith('```'):
                lang_match = re.match(r'```(\w+)\n', part)
                lang    = lang_match.group(1) if lang_match else "text"
                code    = re.sub(r'^```\w*\n', '', part).rstrip('`').strip()
                self.console.print(Syntax(
                    code, lang, theme="github-dark",
                    line_numbers=not TerminalUtils.is_narrow_terminal(),
                    word_wrap=True, padding=(0, 2)
                ))
            else:
                # Wrap long prose lines
                for line in part.split('\n'):
                    if line.strip():
                        wrapped = TerminalUtils.wrap_text(line, TerminalUtils.get_width() - 6)
                        for wl in wrapped:
                            self.console.print(f"  {wl}")
                    else:
                        self.console.print()
        self.console.print()

    # ── Error / warning panels ────────────────────────────────────────────────

    def _panel(self, content: str, title: str, border: str):
        self.console.print(Panel(
            content, title=title, border_style=border,
            padding=(0, 2), expand=False
        ))

    def error(self, title: str, content: str):
        self._panel(content, f"[{Theme.RED}]✗  {title}[/{Theme.RED}]", Theme.RED)

    def warning(self, title: str, content: str):
        self._panel(content, f"[{Theme.YELLOW}]⚠  {title}[/{Theme.YELLOW}]", Theme.YELLOW)

    def info(self, title: str, content: str):
        self._panel(content, f"[{Theme.CYAN}]  {title}[/{Theme.CYAN}]", Theme.CYAN)

    def success(self, title: str, content: str):
        # Used to display AI response in old code — now replaced by print_response
        self.print_response(content)


# ============================================================================
# AI AGENT
# ============================================================================

class MIXAgent:
    MODEL = 'gemini-3-flash-preview'

    SYSTEM_PROMPT = """You are an elite software engineer and cybersecurity expert.

FILE INJECTION PROTOCOL (critical):
- When the user's message contains <injected_file> or <injected_dir> blocks,
  those files have ALREADY been read. Do NOT call get_file_content for them.
  Use the injected content directly.
- Only call get_file_content for files NOT already injected.
- Never list directories you have no task in — only list what is relevant.

PATH SECURITY:
- The path guard restricts what files you can access.
- Blocked paths include: .env, .git, node_modules, sessions, logs, private keys.
- If a file access fails with a 🔒 error, do NOT retry or attempt workarounds.

For complex tasks, follow this structured approach:

1. PLAN (use plan_project tool):
   │ Generate detailed JSON plan with tasks and subtasks
   └ Structure: {tasks: [{id, title, dependencies, subtasks, files_to_modify}]}

2. REVIEW the plan:
   │ Read the generated JSON file
   └ Understand task dependencies

3. EXECUTE step-by-step:
   │ Follow tasks sequentially
   │ Use patch_file for updates (NOT write_file for existing files)
   └ Run tests after each major task

When to plan:
  ✓ Feature additions, major refactoring, multi-file changes, architecture changes
  ✗ Simple reads, single file edits, quick fixes, shell commands

Output style — concise monospace, no emoji spam, no bullet spam:

  Update Todos
  │ Add FrameworkSelector to ProjectSettings.tsx
  │ Add framework preset state
  └ Test

Remember: Read injected files → think → plan if needed → execute.
"""

    def __init__(self, api_key: Optional[str] = None,
                 log: Optional[Logger] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables")

        self.client  = genai.Client(api_key=self.api_key)
        self.ui      = UI()
        self.logger  = log
        self.session = SessionManager(log=self.logger)
        self.status  = StatusBar()

        self.command_handler = CommandHandler(
            self.session, self.ui.console, self.logger
        )
        self.max_iterations = 20
        self._config        = self._build_config()

        self.prompt_session = PromptSession(
            completer=merge_completers([CommandCompleter(), FilePathCompleter()]),
            style=autocomplete_style,
            complete_while_typing=True,
            complete_in_thread=True,
        )

    # ── config ──────────────────────────────────────────────────────────────

    def _build_config(self) -> types.GenerateContentConfig:
        base_schemas = [
            schema_get_files_info,
            schema_get_file_content,
            schema_run_python_file,
            schema_write_file,
            schema_run_shell,
            schema_build_project,
            schema_install_dependencies,
            schema_patch_file,
            schema_plan_project,
        ]
        all_schemas = base_schemas + list(_cs_schemas)

        return types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=all_schemas)],
            system_instruction=self.SYSTEM_PROMPT,
            temperature=0.7,
        )

    # ── @ token pre-processing ───────────────────────────────────────────────

    def _preprocess_input(self, raw: str) -> tuple[str, InjectionResult]:
        """Inject @-referenced files into the prompt before sending to AI."""
        result = inject_files(raw)
        if result.injected or result.blocked or result.missing:
            self.ui.print_injection_report(result)
        return result.prompt, result

    # ── context builder (sliding window) ────────────────────────────────────

    def _build_messages(self, current_input: str) -> List[types.Content]:
        """
        Build the message list for the API call.
        Uses only the last CONTEXT_WINDOW_MESSAGES messages to limit tokens.
        """
        messages: List[types.Content] = []
        for msg in self.session.get_context()[:-1]:  # all but last (current)
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

    # ── request processing ───────────────────────────────────────────────────

    def process_request(self, user_input: str, verbose: bool = False):
        # 1. Pre-process @ tokens — inject file content before AI sees it
        injected_input, injection = self._preprocess_input(user_input)

        # 2. Record original user message in history (clean, no injection noise)
        self.session.add_message("user", user_input)

        # 3. Build context (sliding window, not full history)
        messages = self._build_messages(injected_input)

        self.status.start("Thinking")

        try:
            for iteration in range(self.max_iterations):

                response = self.client.models.generate_content(
                    model=self.MODEL,
                    contents=messages,
                    config=self._config,
                )

                if response is None or response.usage_metadata is None:
                    self.status.stop()
                    self.ui.error("Response Error", "Empty or malformed response from API")
                    return

                self.status.update_tokens(
                    prompt     = response.usage_metadata.prompt_token_count or 0,
                    completion = response.usage_metadata.candidates_token_count or 0,
                )

                if verbose and not response.function_calls:
                    self.status.stop()
                    self.ui.info("Tokens", (
                        f"Iteration {iteration + 1}/{self.max_iterations}\n"
                        f"Prompt:     {response.usage_metadata.prompt_token_count}\n"
                        f"Completion: {response.usage_metadata.candidates_token_count}\n"
                        f"Total:      {response.usage_metadata.total_token_count}"
                    ))
                    self.status.start("Thinking")

                if not response.candidates:
                    continue

                for candidate in response.candidates:
                    if candidate and candidate.content:
                        messages.append(candidate.content)

                if response.function_calls:
                    self.status.stop()

                    # Show tool section header
                    self.ui.console.print(
                        f"  [{Theme.GRAY}]│[/{Theme.GRAY}]"
                    )

                    for fc in response.function_calls:
                        # PATH GUARD: validate file paths before execution
                        if not self._guard_function_call(fc):
                            # Replace with error result
                            result_msg = types.Content(
                                role="user",
                                parts=[types.Part(
                                    function_response=types.FunctionResponse(
                                        name=fc.name,
                                        response={"result": f"🔒 Blocked: path not allowed"}
                                    )
                                )]
                            )
                            messages.append(result_msg)
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
                                    result_msg.parts[0]
                                    .function_response.response
                                    .get("result", "")
                                )
                        except Exception:
                            pass

                        self.ui.print_tool_execution(fc.name, fc.args, result_content)

                    self.status.start("Processing")

                else:
                    self.status.stop()
                    response_text = response.text
                    self.session.add_message("assistant", response_text)
                    self.ui.print_response(response_text)
                    if self.logger:
                        self.logger.info("Request processed successfully")
                    return

            self.status.stop()
            self.ui.warning(
                "Limit reached",
                f"Hit max iterations ({self.max_iterations}). Task may need more steps."
            )

        except KeyboardInterrupt:
            self.status.stop()
            self.ui.console.print(
                f"\n  [{Theme.SLATE}]interrupted[/{Theme.SLATE}]\n"
            )
            if self.logger:
                self.logger.info("Task interrupted by user (Ctrl+C)")

        except Exception as e:
            self.status.stop()
            self.ui.error("Error", str(e))
            if self.logger:
                self.logger.error(f"Error processing request: {e}")

    # ── path guard for function calls ────────────────────────────────────────

    def _guard_function_call(self, fc: types.FunctionCall) -> bool:
        """
        Returns True if the function call is allowed, False if blocked.
        Checks file paths in args against the PathGuard.
        """
        FILE_ARGS = {"file_path", "path", "working_directory"}
        WRITE_FNS = {"write_file", "patch_file", "cs_write_file", "cs_patch_file"}

        for arg_name in FILE_ARGS:
            val = fc.args.get(arg_name)
            if not val or not isinstance(val, str):
                continue
            # working_directory is usually CWD itself — allow
            if arg_name == "working_directory":
                continue
            write = fc.name in WRITE_FNS
            if not guard.is_safe(val, write=write):
                return False
        return True

    # ── interactive loop ─────────────────────────────────────────────────────

    def run_interactive(self):
        self.ui.welcome_screen()
        if self.logger:
            self.logger.info("MIX Agent started")

        if _CODESPACE_AVAILABLE:
            self.ui.console.print(
                f"  [{Theme.CYAN}]⟶ Codespace tools active "
                f"({len(_cs_schemas)} tools)[/{Theme.CYAN}]\n"
            )

        while True:
            try:
                user_input = self.prompt_session.prompt(
                    [('class:prompt', '  ❯ ')],
                    style=PromptStyle.from_dict({'prompt': Theme.GREEN})
                ).strip()

                if not user_input:
                    continue

                if self.command_handler.is_command(user_input):
                    result = self.command_handler.handle(user_input)
                    if result == "EXIT":
                        self.ui.console.print(
                            f"\n  [{Theme.SLATE}]Session saved. Bye![/{Theme.SLATE}]\n"
                        )
                        break
                    elif result == "RELOAD":
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
                self.ui.console.print(
                    f"\n  [{Theme.SLATE}]Interrupted. Bye![/{Theme.SLATE}]\n"
                )
                break
            except EOFError:
                self.ui.console.print(
                    f"\n  [{Theme.SLATE}]Session saved. Bye![/{Theme.SLATE}]\n"
                )
                break
            except Exception as e:
                self.ui.error("Unexpected Error", str(e))
                if self.logger:
                    self.logger.error(f"Loop error: {e}", exc_info=True)


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    load_dotenv()

    log = Logger()
    log.info("MIX Agent initializing…")

    try:
        agent = MIXAgent(log=log)
        agent.run_interactive()

    except ValueError as e:
        console = Console()
        console.print(f"\n  [bold {Theme.RED}]✗  Configuration Error[/bold {Theme.RED}]")
        console.print(
            f"  [{Theme.YELLOW}]Create a .env file:[/{Theme.YELLOW}]\n"
            "    GEMINI_API_KEY=your_api_key_here\n"
        )
        log.error(str(e))
        sys.exit(1)

    except Exception as e:
        Console().print(f"\n  [bold {Theme.RED}]✗  Fatal: {e}[/bold {Theme.RED}]\n")
        log.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()