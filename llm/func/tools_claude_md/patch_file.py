#patch_file
import os
import sys
from datetime import datetime
from google.genai import types
from rich.console import Console
from rich.text import Text
from difflib import SequenceMatcher

# Add backend to sys.path to import merge utilities
BACKEND_PATH = r"/home/user/agent/other_side/backend"
if BACKEND_PATH not in sys.path:
    sys.path.append(BACKEND_PATH)

try:
    from merge.file_merger import apply_single_task_changes
    from merge.types import TaskSnapshot, SemanticChange, ChangeType
except ImportError:
    # Fallback if backend is not available
    def apply_single_task_changes(baseline, snapshot, file_path):
        content = baseline
        for change in snapshot.semantic_changes:
            if change.content_before and change.content_after:
                content = content.replace(change.content_before, change.content_after)
        return content

console = Console()

def show_diff(file_path: str, old_content: str, new_content: str):
    """Show a clean, sequential diff without progress bar interference."""
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    
    # Use SequenceMatcher for better diff quality
    matcher = SequenceMatcher(None, old_lines, new_lines)
    
    # Count additions and removals
    additions = 0
    removals = 0
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'delete':
            removals += (i2 - i1)
        elif tag == 'insert':
            additions += (j2 - j1)
        elif tag == 'replace':
            removals += (i2 - i1)
            additions += (j2 - j1)
    
    if additions == 0 and removals == 0:
        return
    
    # Create output buffer to print all at once (avoid interference)
    output_lines = []
    
    # Header
    header = Text()
    header.append("● ", style="bold green")
    header.append("Update", style="bold white")
    header.append(f" [{file_path}]", style="white")
    output_lines.append(header)
    
    # Summary
    summary = Text()
    summary.append(" │\n", style="bold white")
    summary.append(" └── Updated ", style="italic white")
    summary.append(file_path, style="italic bold white")
    summary.append(" with ", style="italic white")
    summary.append(f"{additions}", style="italic bold green")
    summary.append(f" addition{'s' if additions != 1 else ''}", style="italic green")
    summary.append(" and ", style="italic white")
    summary.append(f"{removals}", style="italic bold red")
    summary.append(f" removal{'s' if removals != 1 else ''}", style="italic red")
    output_lines.append(summary)
    output_lines.append(Text())  # Empty line
    
    # Build sequential diff with proper line tracking
    context_size = 2
    displayed_old_lines = set()
    prev_old_end = -1
    line_offset = 0  # Track cumulative offset from insertions/deletions
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
            
        # Show ellipsis if there's a gap
        if prev_old_end >= 0 and i1 > prev_old_end + context_size * 2:
            output_lines.append(Text("    ...", style="dim"))
        
        # Context before (from old file)
        start_context = max(0, i1 - context_size)
        for i in range(start_context, i1):
            if i not in displayed_old_lines:
                line_text = Text()
                line_text.append(f"{i + 1:4d}", style="dim")
                line_text.append("   ", style="")
                line_text.append(old_lines[i] if i < len(old_lines) else "", style="dim")
                output_lines.append(line_text)
                displayed_old_lines.add(i)
        
        # Show deletions (using old line numbers)
        if tag in ('delete', 'replace'):
            for i in range(i1, i2):
                line_text = Text()
                line_text.append(f"{i + 1:4d}", style="dim")
                line_text.append(" - ", style="bold red")
                line_text.append(old_lines[i] if i < len(old_lines) else "", style="red on rgb(64,0,0)")
                output_lines.append(line_text)
                displayed_old_lines.add(i)
        
        # Show insertions (using new line numbers)
        if tag in ('insert', 'replace'):
            for j in range(j1, j2):
                line_text = Text()
                # Use actual new line number (j + 1)
                line_text.append(f"{j + 1:4d}", style="dim")
                line_text.append(" + ", style="bold green")
                line_text.append(new_lines[j] if j < len(new_lines) else "", style="green on rgb(0,64,0)")
                output_lines.append(line_text)
        
        # Context after (from old file, or new file if beyond old length)
        if i2 < len(old_lines):
            # Show context from old file
            end_context = min(len(old_lines), i2 + context_size)
            for i in range(i2, end_context):
                if i not in displayed_old_lines:
                    line_text = Text()
                    line_text.append(f"{i + 1:4d}", style="dim")
                    line_text.append("   ", style="")
                    line_text.append(old_lines[i] if i < len(old_lines) else "", style="dim")
                    output_lines.append(line_text)
                    displayed_old_lines.add(i)
        else:
            # If we're beyond old file, show context from new file
            if j2 < len(new_lines):
                end_context = min(len(new_lines), j2 + context_size)
                for j in range(j2, end_context):
                    line_text = Text()
                    line_text.append(f"{j + 1:4d}", style="dim")
                    line_text.append("   ", style="")
                    line_text.append(new_lines[j] if j < len(new_lines) else "", style="dim")
                    output_lines.append(line_text)
        
        prev_old_end = i2
    
    output_lines.append(Text())  # Final spacing
    
    # Print everything at once to avoid interference
    console.print("\n")  # Clear line
    for line in output_lines:
        console.print(line)

def patch_file(working_directory: str, file_path: str, content_before: str, content_after: str) -> str:
    """
    Apply a targeted change to a file instead of rewriting it.
    
    Args:
        working_directory: The base working directory
        file_path: Relative path to the file to patch
        content_before: The exact block of code to replace
        content_after: The new block of code to insert
    
    Returns:
        Success or error message
    """
    abs_working_dir = os.path.abspath(working_directory)
    abs_file_path = os.path.abspath(os.path.join(working_directory, file_path))
    
    if not abs_file_path.startswith(abs_working_dir):
        return f'Error: Access denied - {file_path} is outside working directory'
    
    if not os.path.exists(abs_file_path):
        return f'Error: File {file_path} does not exist'
    
    try:
        # Read file
        with open(abs_file_path, 'r', encoding='utf-8') as f:
            baseline = f.read()
        
        # Create a mock snapshot for the merger
        change = SemanticChange(
            change_type=ChangeType.MODIFY_FUNCTION,
            target=file_path,
            location="unknown",
            line_start=1,
            line_end=1,
            content_before=content_before,
            content_after=content_after
        )
        
        snapshot = TaskSnapshot(
            task_id="patch_task",
            task_intent=f"Patching {file_path}",
            started_at=datetime.now(),
            semantic_changes=[change]
        )
        
        # Apply the changes
        modified_content = apply_single_task_changes(baseline, snapshot, file_path)
        
        if modified_content == baseline:
            if content_before not in baseline:
                return f"Error: Could not find the 'content_before' block in {file_path}. Please ensure the content matches exactly (including whitespace)."
            return f"Notice: No changes were applied to {file_path}. The provided content might already be present."

        # Show diff
        show_diff(file_path, baseline, modified_content)
        
        # Write changes
        with open(abs_file_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
            
        return f"Successfully updated '{file_path}'."
    
    except Exception as e:
        return f"Error patching file {file_path}: {str(e)}"

# Schema definition for the AI agent
schema_patch_file = types.FunctionDeclaration(
    name="patch_file",
    description="Update a specific part of a file by replacing a block of code with a new one. This is safer and more efficient than rewriting the entire file.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "file_path": types.Schema(
                type=types.Type.STRING,
                description="The relative path to the file to patch.",
            ),
            "content_before": types.Schema(
                type=types.Type.STRING,
                description="The EXACT block of code as it currently exists in the file. MUST match exactly, including whitespace and indentation.",
            ),
            "content_after": types.Schema(
                type=types.Type.STRING,
                description="The new block of code that should replace the 'content_before' block.",
            ),
        },
        required=["file_path", "content_before", "content_after"],
    ),
)