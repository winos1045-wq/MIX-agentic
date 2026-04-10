"""
FileInjector — Pre-processes @ tokens in user input.

When a user writes:   "fix the bug in @src/app.py"
This module turns it into:
    "fix the bug in @src/app.py

<injected_file path="src/app.py" lines="143">
...file content here...
</injected_file>"

The AI receives the content directly — no get_file_content roundtrip needed.
This saves 1–3 API iterations and hundreds of tokens per file reference.
"""

from __future__ import annotations
import re
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from path_guard import guard, GuardError

# Token pattern: @word/path.ext  or  @./path  (not @user on twitter etc.)
_AT_PATTERN = re.compile(r"@([\w./\\-]+)")

# Max lines to inject per file (prevents context explosion)
MAX_INJECT_LINES = 300
# Max total injected chars across all files in one message
MAX_INJECT_CHARS = 40_000


@dataclass
class InjectionResult:
    prompt: str                        # final prompt sent to AI
    injected: list[str] = field(default_factory=list)   # paths that were injected
    blocked: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    missing: list[str] = field(default_factory=list)    # paths that don't exist


def inject_files(user_input: str, cwd: Optional[str] = None) -> InjectionResult:
    """
    Parse @ tokens from user_input, read each file (if safe + exists),
    and append the contents as XML blocks at the end of the prompt.
    Returns an InjectionResult with the modified prompt and metadata.
    """
    cwd = cwd or os.getcwd()
    matches = _AT_PATTERN.findall(user_input)

    if not matches:
        return InjectionResult(prompt=user_input)

    result = InjectionResult(prompt=user_input)
    blocks: list[str] = []
    total_chars = 0

    seen: set[str] = set()
    for raw in matches:
        if raw in seen:
            continue
        seen.add(raw)

        # ── Security check ───────────────────────────────────────────────────
        try:
            abs_path = guard.resolve(raw)
        except GuardError as e:
            result.blocked.append((raw, str(e)))
            continue

        # ── Existence check ──────────────────────────────────────────────────
        if not abs_path.exists():
            # Could be a directory reference — handled differently below
            if not abs_path.is_dir():
                result.missing.append(raw)
            continue

        # ── Directory? List it, don't read it ────────────────────────────────
        if abs_path.is_dir():
            listing = _list_dir(abs_path, cwd)
            block = f'<injected_dir path="{raw}">\n{listing}\n</injected_dir>'
            blocks.append(block)
            result.injected.append(raw)
            continue

        # ── File: read and inject ────────────────────────────────────────────
        if total_chars >= MAX_INJECT_CHARS:
            result.blocked.append((raw, "total injection limit reached"))
            continue

        try:
            content, line_count, truncated = _read_file(abs_path)
        except OSError as e:
            result.blocked.append((raw, f"read error: {e}"))
            continue

        rel = str(abs_path.relative_to(Path(cwd)))
        trunc_note = f" [truncated at {MAX_INJECT_LINES} lines]" if truncated else ""
        block = (
            f'<injected_file path="{rel}" lines="{line_count}"{trunc_note}>\n'
            f'{content}\n'
            f'</injected_file>'
        )
        total_chars += len(block)
        blocks.append(block)
        result.injected.append(raw)

    if blocks:
        result.prompt = user_input + "\n\n" + "\n\n".join(blocks)

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_file(path: Path) -> tuple[str, int, bool]:
    """Returns (content, total_lines, was_truncated)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = path.read_bytes().decode("latin-1", errors="replace")

    lines = text.splitlines()
    total = len(lines)
    truncated = total > MAX_INJECT_LINES
    if truncated:
        lines = lines[:MAX_INJECT_LINES]
    return "\n".join(lines), total, truncated


def _list_dir(path: Path, cwd: str, max_entries: int = 50) -> str:
    """Simple one-level directory listing (safe files only)."""
    entries: list[str] = []
    try:
        items = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        for item in items[:max_entries]:
            if guard.is_safe(str(item)):
                kind = "DIR " if item.is_dir() else "FILE"
                size = f"{item.stat().st_size:>8,}B" if item.is_file() else ""
                entries.append(f"  {kind}  {item.name:<40} {size}")
        if len(list(path.iterdir())) > max_entries:
            entries.append(f"  ... ({len(list(path.iterdir()))} total entries)")
    except PermissionError:
        entries.append("  [permission denied]")
    return "\n".join(entries)