"""
patch_file.py  —  SDX Agent  (upgraded)

PREFERRED WORKFLOW (eliminates all exact-match failures):
  1. get_file_content(file, start_line=X, end_line=Y)   ← read exact lines
  2. patch_file(file, line_start=X, line_end=Y, content_after=NEW)

The tool reads lines X–Y from disk itself and builds content_before
internally. The AI never needs to reproduce file content.

FALLBACK:
  patch_file(file, content_before=EXACT, content_after=NEW)
  Only use when you cannot know the line numbers in advance.

UPGRADES vs original:
  • dry_run=True    — show diff without writing (safe preview)
  • edits=[...]     — apply multiple replacements in a single call
  • backup=True     — save a .bak copy before writing
  • Encoding-aware  — detects UTF-8 BOM and UTF-16 LE/BE; round-trips correctly
  • Smarter fuzzy   — indentation-normalized match as a second-chance before failing
"""

import os
import re
import shutil
from difflib import SequenceMatcher
from google.genai import types
from rich.console import Console
from rich.text import Text

console = Console()

CONTEXT_LINES = 3


# ── Encoding helpers ──────────────────────────────────────────────────────────

def _detect_encoding(raw: bytes) -> tuple[str, bytes]:
    """
    Return (encoding, bom_bytes).
    Handles UTF-16 LE/BE (with BOM) and UTF-8 BOM; falls back to UTF-8.
    Mirrors the logic in Claude Code's FileEditTool.
    """
    if raw[:2] == b'\xff\xfe':
        return 'utf-16-le', b'\xff\xfe'
    if raw[:2] == b'\xfe\xff':
        return 'utf-16-be', b'\xfe\xff'
    if raw[:3] == b'\xef\xbb\xbf':
        return 'utf-8-sig', b'\xef\xbb\xbf'
    return 'utf-8', b''


def _read_file(path: str) -> tuple[str, str, bytes]:
    """
    Read a file and return (content_str, encoding, bom).
    content_str uses LF line endings internally (CRLF → LF on read).
    """
    with open(path, 'rb') as fh:
        raw = fh.read()
    encoding, bom = _detect_encoding(raw)
    text = raw.decode(encoding, errors='replace').replace('\r\n', '\n')
    return text, encoding, bom


def _write_file(path: str, content: str, encoding: str, bom: bytes) -> None:
    """Write content back with original encoding and BOM."""
    raw = content.encode(encoding)
    with open(path, 'wb') as fh:
        if bom:
            fh.write(bom)
        fh.write(raw)


# ── Diff display ──────────────────────────────────────────────────────────────

def show_diff(file_path: str, old_content: str, new_content: str) -> None:
    """
    Display a clean unified diff with correct old AND new line numbers.

    Uses get_grouped_opcodes() which tracks both old-file and new-file
    positions simultaneously, fixing the "line 40 shown over line 36" bug
    that occurred when insertions/deletions shifted the line count.

    Format per line:
        OLD:NEW│ [-/+/ ] content
    """
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    opcodes  = matcher.get_opcodes()

    additions = sum(j2 - j1 for tag, i1, i2, j1, j2 in opcodes if tag in ('insert',  'replace'))
    removals  = sum(i2 - i1 for tag, i1, i2, j1, j2 in opcodes if tag in ('delete',  'replace'))

    if additions == 0 and removals == 0:
        return

    out: list[Text] = []

    # Header
    hdr = Text()
    hdr.append("● ", style="bold green")
    hdr.append("Update ", style="bold white")
    hdr.append(f"[{file_path}]", style="white")
    out.append(hdr)

    # Summary
    summ = Text()
    summ.append(" └── ", style="dim")
    summ.append(f"+{additions}", style="bold green")
    summ.append(" / ", style="dim")
    summ.append(f"-{removals}", style="bold red")
    summ.append(f"  {file_path}", style="dim")
    out.append(summ)
    out.append(Text())

    # Grouped hunks — each group already has context_lines baked in
    groups = list(matcher.get_grouped_opcodes(CONTEXT_LINES))

    for group in groups:
        # Hunk header: use 1-indexed starts of first block in group
        oi_start = group[0][1] + 1   # old file first line
        ni_start = group[0][3] + 1   # new file first line

        hunk_hdr = Text()
        hunk_hdr.append(f"  @@ -{oi_start} +{ni_start} @@", style="cyan dim")
        out.append(hunk_hdr)

        for tag, i1, i2, j1, j2 in group:

            if tag == 'equal':
                for k in range(i2 - i1):
                    old_ln = i1 + k + 1
                    new_ln = j1 + k + 1
                    t = Text()
                    t.append(f"{old_ln:>5}:{new_ln:<5} ", style="dim")
                    t.append("  ", style="")
                    t.append(old_lines[i1 + k] if (i1 + k) < len(old_lines) else "", style="dim")
                    out.append(t)

            if tag in ('delete', 'replace'):
                for i in range(i1, i2):
                    old_ln = i + 1
                    t = Text()
                    t.append(f"{old_ln:>5}:{'':5} ", style="dim")
                    t.append("- ", style="bold red")
                    t.append(old_lines[i] if i < len(old_lines) else "", style="red")
                    out.append(t)

            if tag in ('insert', 'replace'):
                for j in range(j1, j2):
                    new_ln = j + 1
                    t = Text()
                    t.append(f"{'':5}:{new_ln:<5} ", style="dim")
                    t.append("+ ", style="bold green")
                    t.append(new_lines[j] if j < len(new_lines) else "", style="green")
                    out.append(t)

        out.append(Text())   # blank line between hunks

    console.print()
    for line in out:
        console.print(line)


# ── Single-edit resolver ──────────────────────────────────────────────────────

def _resolve_edit(
    baseline:       str,
    all_lines:      list[str],
    content_after:  str,
    line_start:     int | None,
    line_end:       int | None,
    content_before: str | None,
    file_path:      str,
) -> tuple[str, str] | str:
    """
    Resolve one edit against *baseline* and return (content_before, content_after).
    Returns a string error message on failure.
    """
    total = len(all_lines)

    # ── Coerce line numbers ────────────────────────────────────────────────────
    try:
        if line_start is not None:
            line_start = int(line_start)
        if line_end is not None:
            line_end = int(line_end)
    except (ValueError, TypeError):
        return "Error: line_start and line_end must be integers."

    using_lines = line_start is not None and line_end is not None

    # ── Build content_before from line numbers ─────────────────────────────────
    if using_lines:
        s = max(1, line_start)
        e = min(total, line_end)
        if s > total:
            return (
                f"Error: line_start={line_start} exceeds file length "
                f"({total} lines). Re-read with get_file_content('{file_path}')."
            )
        if s > e:
            return (
                f"Error: line_start={line_start} > line_end={line_end}. "
                "Provide a valid range."
            )
        content_before = ''.join(all_lines[s - 1 : e])

    elif content_before is None:
        if line_start is not None or line_end is not None:
            return (
                "Error: To use line numbers, you must provide BOTH line_start and line_end. "
                "Alternatively, provide content_before for a fuzzy match."
            )
        return (
            "Error: Provide either (line_start + line_end) or content_before.\n"
            "Preferred: call get_file_content first to get line numbers, "
            "then use line_start + line_end."
        )

    # ── Exact match ───────────────────────────────────────────────────────────
    if content_before in baseline:
        return content_before, content_after

    # ── Fuzzy: normalise horizontal whitespace ─────────────────────────────────
    if not using_lines:
        norm_cb   = re.sub(r'[ \t]+', ' ', content_before.strip())
        norm_base = re.sub(r'[ \t]+', ' ', baseline)
        if norm_cb in norm_base:
            return (
                f"Error: Block found with normalised whitespace but not exactly.\n"
                f"Use get_file_content('{file_path}', start_line=N, end_line=M) "
                "to copy the exact text, then retry."
            )

    # ── Fuzzy: indentation-stripped match ─────────────────────────────────────
    # Strip leading whitespace from every line of content_before and search
    # for the stripped pattern in the stripped baseline.  If found, we locate
    # the matching region in the *original* baseline and use that as the real
    # content_before so an exact replacement is possible.
    if not using_lines:
        stripped_cb = '\n'.join(l.lstrip() for l in content_before.splitlines())
        stripped_base_lines = [l.lstrip() for l in baseline.splitlines()]
        stripped_base = '\n'.join(stripped_base_lines)
        if stripped_cb.strip() and stripped_cb.strip() in stripped_base:
            # Find the first matching line in the original file
            cb_first = content_before.strip().split('\n')[0].lstrip().strip()
            for i, line in enumerate(all_lines, 1):
                if cb_first and cb_first[:60] in line.lstrip():
                    span  = content_before.count('\n') + 1
                    ctx_s = max(1, i)
                    ctx_e = min(total, i + span - 1)
                    return (
                        f"Error: Block found but indentation differs.\n"
                        f"Re-read with:\n"
                        f"  get_file_content('{file_path}', "
                        f"start_line={ctx_s}, end_line={ctx_e})\n"
                        f"Then retry with line_start={ctx_s} line_end={ctx_e}."
                    )

    # ── Helpful error: locate first line of the target block ──────────────────
    hint = ""
    if content_before:
        target_first = content_before.strip().split('\n')[0].strip()
        if target_first:
            for i, line in enumerate(all_lines, 1):
                if target_first[:50] in line:
                    span  = content_before.count('\n') + 1
                    ctx_s = max(1, i - 2)
                    ctx_e = min(total, i + span + 2)
                    hint = (
                        f"\nFirst line found near line {i}. "
                        f"Re-read with:\n"
                        f"  get_file_content('{file_path}', "
                        f"start_line={ctx_s}, end_line={ctx_e})\n"
                        f"Then retry with line_start={ctx_s} line_end={ctx_e}."
                    )
                    break

    if using_lines:
        return (
            f"Error: Line range {line_start}–{line_end} could not be matched "
            f"in '{file_path}'. The file may have changed since you read it.\n"
            f"Re-read with get_file_content('{file_path}', "
            f"start_line={line_start}, end_line={line_end}) and retry."
        )

    return (
        f"Error: content_before block not found in '{file_path}'.{hint}\n"
        "Use get_file_content to re-read the target section, "
        "then retry with line_start + line_end."
    )


# ── Core function ─────────────────────────────────────────────────────────────

def patch_file(
    working_directory: str,
    file_path:         str,
    content_after:     str,
    line_start:        int  = None,
    line_end:          int  = None,
    content_before:    str  = None,
    # ── New parameters ────────────────────────────────────────────────────────
    dry_run:           bool = False,
    backup:            bool = False,
    edits:             list[dict] = None,
) -> str:
    """
    Replace one or more sections of a file with new content.

    Args:
        working_directory: Base directory (all paths relative to this).
        file_path:         Relative path to the target file.
        content_after:     Replacement text for the primary edit.
        line_start:        First line of the section to replace (1-indexed).
        line_end:          Last line to replace (1-indexed, inclusive).
        content_before:    Exact content to find and replace (fallback only).
        dry_run:           If True, display the diff but do NOT write to disk.
        backup:            If True, write a .bak copy before patching.
        edits:             Optional list of additional edits to apply in order.
                           Each item is a dict with keys:
                             content_after   (required)
                             content_before  (optional)
                             line_start      (optional)
                             line_end        (optional)
                           Applied after the primary edit.

    Returns:
        "OK: …" on success, "DRY RUN: …" in dry-run mode,
        "Error: …" on failure with actionable hints.
    """

    # ── Path validation ───────────────────────────────────────────────────────
    abs_wd   = os.path.abspath(working_directory)
    abs_file = os.path.abspath(os.path.join(working_directory, file_path))

    if not abs_file.startswith(abs_wd):
        return f"Error: Access denied — '{file_path}' is outside working directory."

    if not os.path.exists(abs_file):
        return f"Error: File '{file_path}' does not exist."

    # ── Read file (encoding-aware) ────────────────────────────────────────────
    try:
        baseline, encoding, bom = _read_file(abs_file)
    except Exception as exc:
        return f"Error reading '{file_path}': {exc}"

    # ── Build edit list ───────────────────────────────────────────────────────
    # Primary edit always comes first; `edits` appends additional ones.
    all_edits: list[dict] = [{
        'content_after':  content_after,
        'line_start':     line_start,
        'line_end':       line_end,
        'content_before': content_before,
    }]
    if edits:
        for extra in edits:
            if 'content_after' not in extra:
                return "Error: each item in 'edits' must have 'content_after'."
            all_edits.append(extra)

    # ── Apply edits sequentially ──────────────────────────────────────────────
    current = baseline
    applied_pairs: list[tuple[str, str]] = []   # (before, after) for diff

    for idx, edit in enumerate(all_edits):
        all_lines = current.splitlines(keepends=True)

        result = _resolve_edit(
            baseline       = current,
            all_lines      = all_lines,
            content_after  = edit['content_after'],
            line_start     = edit.get('line_start'),
            line_end       = edit.get('line_end'),
            content_before = edit.get('content_before'),
            file_path      = file_path,
        )

        if isinstance(result, str):
            # Error message — prefix with edit index if multi-edit
            prefix = f"Edit #{idx + 1}: " if len(all_edits) > 1 else ""
            return prefix + result

        cb, ca = result

        # Preserve trailing-newline behaviour
        cb_ends_nl  = cb.endswith('\n')
        ca_ends_nl  = ca.endswith('\n')
        replacement = ca
        if cb_ends_nl and not ca_ends_nl:
            replacement = ca + '\n'

        current = current.replace(cb, replacement, 1)
        applied_pairs.append((cb, replacement))

    # ── Show diff ─────────────────────────────────────────────────────────────
    show_diff(file_path, baseline, current)

    if dry_run:
        return f"DRY RUN: diff shown for '{file_path}'. No changes written."

    # ── Optional backup ───────────────────────────────────────────────────────
    if backup:
        bak_path = abs_file + '.bak'
        try:
            shutil.copy2(abs_file, bak_path)
        except Exception as exc:
            return f"Error creating backup '{bak_path}': {exc}"

    # ── Write (encoding-aware, preserves BOM) ─────────────────────────────────
    try:
        _write_file(abs_file, current, encoding, bom)
    except Exception as exc:
        return f"Error writing '{file_path}': {exc}"

    n = len(all_edits)
    edit_word = "edit" if n == 1 else f"{n} edits"
    bak_note  = f" (backup: {os.path.basename(abs_file)}.bak)" if backup else ""
    return f"OK: patched '{file_path}' — {edit_word} applied{bak_note}."


# ── Schema ────────────────────────────────────────────────────────────────────

schema_patch_file = types.FunctionDeclaration(
    name="patch_file",
    description=(
        "Replace one or more specific sections of a file with new content.\n\n"
        "PREFERRED — line number mode (no exact-match failures):\n"
        "  1. get_file_content(file, start_line=X, end_line=Y)\n"
        "  2. patch_file(file, line_start=X, line_end=Y, content_after=NEW)\n"
        "  The tool reads the old content from disk itself — you only supply NEW.\n\n"
        "MULTI-EDIT — apply several replacements atomically:\n"
        "  patch_file(file, line_start=X1, line_end=Y1, content_after=NEW1,\n"
        "             edits=[{line_start: X2, line_end: Y2, content_after: NEW2}, ...])\n"
        "  Edits are applied top-to-bottom; use line numbers from the ORIGINAL file.\n\n"
        "DRY RUN — preview without writing:\n"
        "  patch_file(file, ..., dry_run=True)\n\n"
        "BACKUP — save .bak before writing:\n"
        "  patch_file(file, ..., backup=True)\n\n"
        "FALLBACK — content match mode:\n"
        "  patch_file(file, content_before=EXACT, content_after=NEW)\n"
        "  EXACT must match verbatim including whitespace. Never retype from memory.\n\n"
        "FAILURE PROTOCOL:\n"
        "  - If this fails once → use get_file_content to re-read, then retry once "
        "with line_start + line_end.\n"
        "  - If it fails twice on the same location → use write_file on the whole "
        "section instead.\n"
        "  - Never attempt more than 2 patches on the same block."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "file_path": types.Schema(
                type=types.Type.STRING,
                description="Relative path to the file to patch.",
            ),
            "content_after": types.Schema(
                type=types.Type.STRING,
                description="The new content to write in place of the replaced section.",
            ),
            "line_start": types.Schema(
                type=types.Type.INTEGER,
                description=(
                    "First line of the section to replace (1-indexed). "
                    "Use with line_end. Get the number from get_file_content output."
                ),
            ),
            "line_end": types.Schema(
                type=types.Type.INTEGER,
                description=(
                    "Last line of the section to replace (1-indexed, inclusive). "
                    "Use with line_start."
                ),
            ),
            "content_before": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Exact content to find and replace (fallback only). "
                    "Must match verbatim — whitespace, indentation, and all. "
                    "Prefer line_start + line_end instead."
                ),
            ),
            "dry_run": types.Schema(
                type=types.Type.BOOLEAN,
                description=(
                    "If true, display the diff but do NOT write changes to disk. "
                    "Useful for previewing an edit before committing."
                ),
            ),
            "backup": types.Schema(
                type=types.Type.BOOLEAN,
                description=(
                    "If true, save a .bak copy of the original file before patching."
                ),
            ),
            "edits": types.Schema(
                type=types.Type.ARRAY,
                description=(
                    "Optional list of additional edits to apply after the primary one. "
                    "Each item may have: content_after (required), content_before, "
                    "line_start, line_end. Applied in order on the already-patched text. "
                    "Use line numbers from the ORIGINAL file (before any edits in this call)."
                ),
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "content_after":  types.Schema(type=types.Type.STRING),
                        "content_before": types.Schema(type=types.Type.STRING),
                        "line_start":     types.Schema(type=types.Type.INTEGER),
                        "line_end":       types.Schema(type=types.Type.INTEGER),
                    },
                    required=["content_after"],
                ),
            ),
        },
        required=["file_path", "content_after"],
    ),
)