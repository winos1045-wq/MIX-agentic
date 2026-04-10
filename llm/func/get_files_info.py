import os
from pathlib import Path
from google.genai import types
from rich.console import Console
from rich.tree import Tree

console = Console()

def get_files_info(
    working_directory: str,
    path: str = ".",
    recursive: bool = False,
    max_depth: int = 2,
    exclude_patterns: list = None
) -> str:
    """
    Get directory structure with smart filtering to prevent token overflow.
    
    Args:
        working_directory: Base directory
        path: Relative path to list (default: ".")
        recursive: Whether to list recursively (default: False)
        max_depth: Maximum depth for recursive listing (default: 2)
        exclude_patterns: Folders to exclude (default: common build/dependency dirs)
    
    Returns:
        Formatted directory listing string
    """
    # Default exclusions to prevent massive token usage
    if exclude_patterns is None:
        exclude_patterns = [
            'node_modules', '.next', '.git', '__pycache__', 
            'venv', '.venv', 'dist', 'build', '.cache',
            'coverage', '.pytest_cache', '.turbo', '.vercel'
        ]
    
    abs_working_dir = os.path.abspath(working_directory)
    target_path = os.path.join(abs_working_dir, path)
    
    if not os.path.exists(target_path):
        return f"Error: Path '{path}' does not exist in {working_directory}"
    
    if not os.path.isdir(target_path):
        return f"Error: '{path}' is not a directory"
    
    try:
        if recursive:
            return _get_recursive_tree(target_path, exclude_patterns, max_depth)
        else:
            return _get_simple_listing(target_path, exclude_patterns)
    
    except PermissionError:
        return f"Error: Permission denied accessing '{path}'"
    except Exception as e:
        return f"Error reading directory: {str(e)}"


def _get_simple_listing(directory: str, exclude_patterns: list) -> str:
    """
    Get a simple, non-recursive directory listing (like ls -la).
    """
    try:
        entries = []
        total_size = 0
        
        for entry in sorted(os.listdir(directory)):
            # Skip excluded patterns
            if entry in exclude_patterns or entry.startswith('.'):
                if entry not in ['.', '..', '.gitignore', '.env.example']:
                    continue
            
            full_path = os.path.join(directory, entry)
            
            try:
                stat_info = os.stat(full_path)
                size = stat_info.st_size
                total_size += size
                
                is_dir = os.path.isdir(full_path)
                icon = "📁" if is_dir else "📄"
                
                # Format size
                if is_dir:
                    size_str = "<DIR>"
                elif size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size/1024:.1f}KB"
                else:
                    size_str = f"{size/(1024*1024):.1f}MB"
                
                entries.append(f"{icon} {entry:<40} {size_str:>12}")
            
            except (OSError, PermissionError):
                entries.append(f"❌ {entry:<40} <INACCESSIBLE>")
        
        # Build output
        output = f"Directory: {directory}\n"
        output += f"Total entries: {len(entries)}\n"
        output += "=" * 60 + "\n"
        output += "\n".join(entries)
        
        return output
    
    except Exception as e:
        return f"Error listing directory: {str(e)}"


def _get_recursive_tree(directory: str, exclude_patterns: list, max_depth: int) -> str:
    """
    Get a recursive tree view with depth limiting.
    """
    result = []
    result.append(f"Directory Tree: {directory}")
    result.append("=" * 60)
    
    def walk_tree(current_path: str, prefix: str = "", depth: int = 0):
        if depth > max_depth:
            return
        
        try:
            entries = []
            for entry in sorted(os.listdir(current_path)):
                # Skip excluded patterns
                if entry in exclude_patterns:
                    continue
                
                full_path = os.path.join(current_path, entry)
                entries.append((entry, full_path, os.path.isdir(full_path)))
            
            for i, (name, full_path, is_dir) in enumerate(entries):
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                icon = "📁" if is_dir else "📄"
                
                # Get size for files
                size_str = ""
                if not is_dir:
                    try:
                        size = os.path.getsize(full_path)
                        if size < 1024:
                            size_str = f" ({size}B)"
                        elif size < 1024 * 1024:
                            size_str = f" ({size/1024:.1f}KB)"
                        else:
                            size_str = f" ({size/(1024*1024):.1f}MB)"
                    except OSError:
                        size_str = " (size unknown)"
                
                result.append(f"{prefix}{connector}{icon} {name}{size_str}")
                
                # Recurse into directories
                if is_dir and depth < max_depth:
                    extension = "    " if is_last else "│   "
                    walk_tree(full_path, prefix + extension, depth + 1)
        
        except PermissionError:
            result.append(f"{prefix}❌ <Permission Denied>")
    
    walk_tree(directory)
    
    return "\n".join(result)


# Updated schema with better guidance
schema_get_files_info = types.FunctionDeclaration(
    name="get_files_info",
    description="""Get directory structure information. 
    
    IMPORTANT: 
    - By default, lists ONLY the current directory (non-recursive)
    - Automatically excludes node_modules, .next, .git, etc. to prevent token overflow
    - For simple listings, use recursive=False (recommended)
    - For project exploration, use recursive=True with max_depth=2
    - For large projects, prefer using run_shell with 'ls -la' or 'tree -L 1' commands
    
    Best practices:
    - Quick view: get_files_info(path=".", recursive=False)
    - Project structure: get_files_info(path=".", recursive=True, max_depth=2)
    - Specific folder: get_files_info(path="app", recursive=False)""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "path": types.Schema(
                type=types.Type.STRING,
                description="Relative path to list (default: '.' for current directory)",
            ),
            "recursive": types.Schema(
                type=types.Type.BOOLEAN,
                description="List recursively (default: False). Use False for quick listings!",
            ),
            "max_depth": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum recursion depth (default: 2). Prevents massive token usage.",
            ),
        },
        required=["path"],
    ),
)
