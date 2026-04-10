"""
get_file_content.py  —  SDX Agent

Always returns content with line numbers prefixed:
    123│ code here
    124│ next line

This makes get_file_content the source of truth for line numbers,
which patch_file then uses directly via line_start + line_end.
"""

import os
from google.genai import types


def get_file_content(
    working_directory: str,
    file_path: str,
    start_line: int = None,
    end_line:   int = None,
) -> str:
    abs_wd   = os.path.abspath(working_directory)
    abs_file = os.path.abspath(os.path.join(working_directory, file_path))

    if not abs_file.startswith(abs_wd):
        return f"Error: Access denied — '{file_path}' is outside working directory."

    if not os.path.exists(abs_file):
        return f"Error: File '{file_path}' does not exist."

    try:
        with open(abs_file, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
    except Exception as e:
        return f"Error reading '{file_path}': {e}"

    total = len(all_lines)

    # clamp range
    s = max(1, start_line) if start_line else 1
    e = min(total, end_line) if end_line else total

    selected = all_lines[s - 1 : e]

    if not selected:
        return (
            f"Error: Line range {s}–{e} is empty or out of bounds "
            f"(file has {total} lines)."
        )

    # Build numbered output — prefix format:  "  NNN│ content"
    width   = len(str(e))   # pad to widest line number in range
    numbered = []
    for i, line in enumerate(selected, start=s):
        numbered.append(f"{i:>{width}}│ {line.rstrip()}")

    header = (
        f"FILE: {file_path}  "
        f"[lines {s}–{e} of {total}]\n"
        f"{'─' * 40}\n"
    )

    return header + "\n".join(numbered)


# ── schema ────────────────────────────────────────────────────────────────────

schema_get_file_content = types.FunctionDeclaration(
    name="get_file_content",
    description=(
        "Read a file's content with line numbers.\n\n"
        "Always returns content in the format:\n"
        "  NNN│ line content\n\n"
        "The NNN line numbers can be passed directly to patch_file as\n"
        "line_start and line_end — this is the preferred patching workflow:\n"
        "  1. get_file_content(file, start_line=X, end_line=Y)\n"
        "  2. patch_file(file, line_start=X, line_end=Y, content_after=...)\n\n"
        "Never re-type file content to build content_before — use line numbers."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "file_path": types.Schema(
                type=types.Type.STRING,
                description="Relative path to the file.",
            ),
            "start_line": types.Schema(
                type=types.Type.INTEGER,
                description="First line to read (1-indexed). Omit to start from line 1.",
            ),
            "end_line": types.Schema(
                type=types.Type.INTEGER,
                description="Last line to read (1-indexed, inclusive). Omit to read to end of file.",
            ),
        },
        required=["file_path"],
    ),
)