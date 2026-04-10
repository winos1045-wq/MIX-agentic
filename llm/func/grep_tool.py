"""
func/grep_tool.py — High-performance code search for SDX Agent

Backend priority:
  1. ripgrep (rg)  — fastest, respects .gitignore
  2. Pure Python   — fallback if rg not installed

Features:
  - Regex or literal search
  - output_mode: content | files_with_matches | count
  - Context lines (-B / -A / -C)
  - File type / glob filter
  - Case insensitive
  - head_limit + offset  (pagination)
  - Multiline mode
  - Converts absolute → relative paths (saves tokens)
  - Excludes: .git, node_modules, __pycache__, venv, sessions, logs …
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

try:
    from path_guard import guard, GuardError
    _GUARD = True
except ImportError:
    _GUARD = False

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_HEAD_LIMIT = 250
MAX_COLUMNS        = 500          # truncate long lines (base64 / minified)

VCS_EXCLUDE = [".git", ".svn", ".hg", ".bzr", ".jj"]
NOISE_EXCLUDE = [
    "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".mypy_cache",
    ".pytest_cache", "sessions", "logs", "*.pyc",
]

# ── Schema ────────────────────────────────────────────────────────────────────

schema_search_code = {
    "name": "search_code",
    "description": (
        "Search for a regex or string pattern across project files using ripgrep. "
        "Use this BEFORE reading files to locate relevant code without blind directory scans. "
        "Supports three output modes: 'content' (matching lines), "
        "'files_with_matches' (file list, default), 'count' (match counts per file). "
        "Use head_limit + offset to paginate large result sets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex or literal string to search for."
            },
            "path": {
                "type": "string",
                "description": "File or directory to search. Defaults to project root.",
                "default": "."
            },
            "glob": {
                "type": "string",
                "description": (
                    "Glob pattern to filter files. Examples: '*.py', '*.{ts,tsx}', "
                    "'src/**/*.js'. Can be comma-separated for multiple patterns."
                )
            },
            "file_type": {
                "type": "string",
                "description": (
                    "File type shorthand (ripgrep --type). "
                    "Common: py, js, ts, rust, go, java, css, html, json, yaml."
                )
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": (
                    "'content' — show matching lines with optional context. "
                    "'files_with_matches' — list files that have matches (default, cheapest). "
                    "'count' — show match count per file."
                ),
                "default": "files_with_matches"
            },
            "context_before": {
                "type": "integer",
                "description": "Lines to show BEFORE each match (-B). Only for content mode.",
                "default": 0
            },
            "context_after": {
                "type": "integer",
                "description": "Lines to show AFTER each match (-A). Only for content mode.",
                "default": 0
            },
            "context": {
                "type": "integer",
                "description": "Lines before AND after each match (-C). Overrides context_before/after.",
                "default": 0
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "Case-insensitive search. Default: false.",
                "default": False
            },
            "show_line_numbers": {
                "type": "boolean",
                "description": "Show line numbers in content mode. Default: true.",
                "default": True
            },
            "multiline": {
                "type": "boolean",
                "description": "Allow pattern to span multiple lines. Default: false.",
                "default": False
            },
            "head_limit": {
                "type": "integer",
                "description": (
                    "Max results to return (default 250). "
                    "Pass 0 for unlimited (expensive). Use with offset to paginate."
                ),
                "default": 250
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N results before applying head_limit. Default: 0.",
                "default": 0
            }
        },
        "required": ["pattern"]
    }
}


# ── Public entry point ────────────────────────────────────────────────────────

def search_code(
    working_directory: str,
    pattern: str,
    path: str = ".",
    glob: Optional[str] = None,
    file_type: Optional[str] = None,
    output_mode: str = "files_with_matches",
    context_before: int = 0,
    context_after: int = 0,
    context: int = 0,
    case_insensitive: bool = False,
    show_line_numbers: bool = True,
    multiline: bool = False,
    head_limit: int = DEFAULT_HEAD_LIMIT,
    offset: int = 0,
) -> str:
    """
    Main entry point called by call_function.py.
    Returns a formatted string result for the AI.
    """
    # ── Guard ─────────────────────────────────────────────────────────────────
    if _GUARD:
        try:
            abs_search_path = str(guard.resolve(path))
        except GuardError as e:
            return f"🔒 Search blocked: {e}"
    else:
        abs_search_path = str(Path(working_directory) / path)

    # ── Dispatch to backend ───────────────────────────────────────────────────
    if _has_ripgrep():
        raw_lines = _rg_search(
            pattern=pattern,
            search_path=abs_search_path,
            working_directory=working_directory,
            glob=glob,
            file_type=file_type,
            output_mode=output_mode,
            context_before=context_before,
            context_after=context_after,
            context=context,
            case_insensitive=case_insensitive,
            show_line_numbers=show_line_numbers,
            multiline=multiline,
        )
    else:
        raw_lines = _py_search(
            pattern=pattern,
            search_path=abs_search_path,
            working_directory=working_directory,
            glob=glob,
            output_mode=output_mode,
            context_before=context_before,
            context_after=context_after,
            context=context,
            case_insensitive=case_insensitive,
            show_line_numbers=show_line_numbers,
        )

    # ── Relativize paths ──────────────────────────────────────────────────────
    raw_lines = _relativize(raw_lines, working_directory)

    # ── Pagination ────────────────────────────────────────────────────────────
    lines, applied_limit = _apply_head_limit(raw_lines, head_limit, offset)

    # ── Format output ─────────────────────────────────────────────────────────
    return _format_result(
        lines=lines,
        output_mode=output_mode,
        pattern=pattern,
        applied_limit=applied_limit,
        offset=offset,
        head_limit=head_limit,
    )


# ── ripgrep backend ───────────────────────────────────────────────────────────

def _has_ripgrep() -> bool:
    return bool(os.popen("which rg 2>/dev/null").read().strip()
                or os.popen("where rg 2>nul").read().strip())


def _rg_search(
    pattern: str,
    search_path: str,
    working_directory: str,
    glob: Optional[str],
    file_type: Optional[str],
    output_mode: str,
    context_before: int,
    context_after: int,
    context: int,
    case_insensitive: bool,
    show_line_numbers: bool,
    multiline: bool,
) -> list[str]:

    args = ["rg", "--hidden", f"--max-columns={MAX_COLUMNS}"]

    # Exclude noisy dirs
    for d in VCS_EXCLUDE + NOISE_EXCLUDE:
        args += ["--glob", f"!{d}"]
        args += ["--glob", f"!**/{d}/**"]

    # Output mode
    if output_mode == "files_with_matches":
        args.append("-l")
    elif output_mode == "count":
        args.append("-c")

    # Flags
    if case_insensitive:
        args.append("-i")
    if multiline:
        args += ["-U", "--multiline-dotall"]
    if show_line_numbers and output_mode == "content":
        args.append("-n")

    # Context lines (only for content mode)
    if output_mode == "content":
        if context:
            args += ["-C", str(context)]
        else:
            if context_before:
                args += ["-B", str(context_before)]
            if context_after:
                args += ["-A", str(context_after)]

    # Pattern (handle leading-dash patterns safely)
    if pattern.startswith("-"):
        args += ["-e", pattern]
    else:
        args.append(pattern)

    # File type filter
    if file_type:
        args += ["--type", file_type]

    # Glob filter
    if glob:
        for g in _split_globs(glob):
            args += ["--glob", g]

    args.append(search_path)

    try:
        result = subprocess.run(
            args,
            cwd=working_directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            text=True,
            errors="replace",
        )
        if result.stdout:
            return result.stdout.splitlines()
        # rc=1 means no matches (not an error)
        if result.returncode == 1:
            return []
        # rc=2+ is real error
        if result.returncode >= 2:
            return [f"[rg error] {result.stderr.strip()}"]
        return []
    except subprocess.TimeoutExpired:
        return ["[search timed out after 30s]"]
    except FileNotFoundError:
        return []  # rg not found — caller falls back to Python


# ── Pure Python fallback ──────────────────────────────────────────────────────

_PY_IGNORE_DIRS = set(VCS_EXCLUDE + NOISE_EXCLUDE)
_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".scala",
    ".sh", ".bash", ".zsh", ".fish", ".lua", ".r", ".m", ".f90",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".rst", ".txt", ".xml", ".svg", ".env.example",
    ".sql", ".graphql", ".proto", ".tf", ".hcl", ".nix",
}


def _py_search(
    pattern: str,
    search_path: str,
    working_directory: str,
    glob: Optional[str],
    output_mode: str,
    context_before: int,
    context_after: int,
    context: int,
    case_insensitive: bool,
    show_line_numbers: bool,
) -> list[str]:

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as e:
        return [f"[invalid regex: {e}]"]

    root = Path(search_path)
    results: list[str] = []

    ctx_before = context if context else context_before
    ctx_after  = context if context else context_after

    for filepath in _walk_text_files(root, glob):
        try:
            text   = filepath.read_text(encoding="utf-8", errors="replace")
            lines  = text.splitlines()
            rel    = str(filepath)  # relativized later

            if output_mode == "files_with_matches":
                if compiled.search(text):
                    results.append(rel)
                continue

            if output_mode == "count":
                count = len(compiled.findall(text))
                if count:
                    results.append(f"{rel}:{count}")
                continue

            # content mode
            file_hits: list[str] = []
            for i, line in enumerate(lines):
                if compiled.search(line):
                    start = max(0, i - ctx_before)
                    end   = min(len(lines), i + ctx_after + 1)
                    for j in range(start, end):
                        prefix = f"{j + 1}:" if show_line_numbers else ""
                        marker = "" if j != i else ""
                        file_hits.append(f"{rel}:{prefix}{lines[j]}{marker}")
                    if ctx_before or ctx_after:
                        file_hits.append("--")  # separator between blocks

            if file_hits:
                results.extend(file_hits)

        except (OSError, PermissionError):
            continue

    return results


def _walk_text_files(root: Path, glob_pattern: Optional[str]):
    """Yield text files under root, respecting ignore dirs and glob filter."""
    import fnmatch

    globs = _split_globs(glob_pattern) if glob_pattern else []

    for dirpath, dirs, files in os.walk(root):
        # Prune ignored directories in-place
        dirs[:] = [
            d for d in dirs
            if d not in _PY_IGNORE_DIRS and not d.startswith(".")
        ]
        for fname in files:
            fp = Path(dirpath) / fname
            # Extension filter
            if fp.suffix.lower() not in _TEXT_EXTENSIONS and not globs:
                continue
            # Glob filter
            if globs and not any(fnmatch.fnmatch(fname, g) for g in globs):
                continue
            yield fp


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_globs(glob_str: str) -> list[str]:
    """Split 'a, b, *.{ts,tsx}' into individual glob patterns."""
    if not glob_str:
        return []
    # Preserve brace expressions
    parts = re.split(r",(?![^{]*})", glob_str)
    return [p.strip() for p in parts if p.strip()]


def _relativize(lines: list[str], cwd: str) -> list[str]:
    """Convert absolute paths to relative in each line."""
    cwd_norm = cwd.rstrip("/\\") + "/"
    out: list[str] = []
    for line in lines:
        if line.startswith(cwd_norm):
            line = line[len(cwd_norm):]
        elif line.startswith(cwd.rstrip("/\\")):
            line = line[len(cwd.rstrip("/\\")) + 1:]
        out.append(line)
    return out


def _apply_head_limit(
    lines: list[str], limit: int, offset: int
) -> tuple[list[str], Optional[int]]:
    """
    Apply offset + limit pagination.
    Returns (sliced_lines, applied_limit_or_None).
    applied_limit is set only when truncation actually happened.
    """
    if limit == 0:
        return lines[offset:], None
    effective = limit or DEFAULT_HEAD_LIMIT
    sliced = lines[offset: offset + effective]
    truncated = (len(lines) - offset) > effective
    return sliced, (effective if truncated else None)


def _format_result(
    lines: list[str],
    output_mode: str,
    pattern: str,
    applied_limit: Optional[int],
    offset: int,
    head_limit: int,
) -> str:

    if not lines:
        return f"No matches found for: {pattern!r}"

    body = "\n".join(lines)

    # Build pagination note
    pagination_parts: list[str] = []
    if applied_limit:
        pagination_parts.append(f"showing first {applied_limit}")
    if offset:
        pagination_parts.append(f"offset {offset}")
    pagination_note = ""
    if pagination_parts:
        more_hint = (
            f"  →  use offset={offset + (applied_limit or DEFAULT_HEAD_LIMIT)} to see more"
            if applied_limit else ""
        )
        pagination_note = f"\n\n[{', '.join(pagination_parts)}]{more_hint}"

    # Build header
    if output_mode == "files_with_matches":
        header = f"Found {len(lines)} file(s) matching {pattern!r}"
    elif output_mode == "count":
        total = sum(
            int(l.rsplit(":", 1)[-1])
            for l in lines
            if l.rsplit(":", 1)[-1].isdigit()
        )
        header = f"Found {total} occurrence(s) across {len(lines)} file(s) for {pattern!r}"
    else:
        header = f"Search results for {pattern!r}"

    return f"{header}\n{'─' * min(len(header), 60)}\n{body}{pagination_note}"