import os
import subprocess
from google.genai import types
from rich.console import Console
from rich.panel import Panel

console = Console()

def run_python_file(working_directory: str, file_path: str, args: list = None) -> str:
    """
    Execute a Python file with optional command-line arguments and live output display.
    
    Args:
        working_directory: The base working directory
        file_path: Relative path to the Python file to execute
        args: Optional list of command-line arguments
    
    Returns:
        String containing stdout, stderr, and exit code
    """
    if args is None:
        args = []
    
    abs_working_dir = os.path.abspath(working_directory)
    abs_file_path = os.path.abspath(os.path.join(working_directory, file_path))
    
    # Security check: prevent directory traversal
    if not abs_file_path.startswith(abs_working_dir):
        return f'Error: Access denied - {file_path} is outside working directory'
    
    if not os.path.exists(abs_file_path):
        return f'Error: File {file_path} not found'
    
    if not os.path.isfile(abs_file_path):
        return f'Error: {file_path} is not a file'
    
    if not file_path.endswith('.py'):
        return f'Error: {file_path} is not a Python file'
    
    # Display command being executed
    cmd_display = f"python3 {file_path}" + (f" {' '.join(args)}" if args else "")
    console.print()
    console.print(Panel(
        f"[cyan]$ {cmd_display}[/cyan]",
        title="[yellow]🐍 Executing Python Script[/yellow]",
        border_style="yellow",
        padding=(0, 2)
    ))
    
    try:
        # Build command
        final_args = ['python3', file_path]
        final_args.extend(args)
        
        # Execute with live output
        process = subprocess.Popen(
            final_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=abs_working_dir
        )
        
        console.print("[dim]─" * 80 + "[/dim]")
        
        # Read output with timeout
        try:
            stdout, stderr = process.communicate(timeout=30)
            
            # Display stdout
            if stdout:
                for line in stdout.split('\n'):
                    if line.strip():
                        console.print(f"[green]{line}[/green]")
            
            # Display stderr
            if stderr:
                for line in stderr.split('\n'):
                    if line.strip():
                        console.print(f"[red]{line}[/red]")
            
            console.print("[dim]─" * 80 + "[/dim]")
            
            # Show exit code
            if process.returncode == 0:
                console.print(f"[green]✓ Exit code: {process.returncode}[/green]")
            else:
                console.print(f"[red]✗ Exit code: {process.returncode}[/red]")
            console.print()
            
        except subprocess.TimeoutExpired:
            process.kill()
            console.print("[red]✗ Script timed out![/red]\n")
            return f'Error: Execution timed out after 30 seconds'
        
        # Format output for AI
        stdout_output = stdout.strip() if stdout else ""
        stderr_output = stderr.strip() if stderr else ""
        
        final_string = f'''Command: python3 {file_path} {' '.join(args)}
Working Directory: {abs_working_dir}

STDOUT:
{stdout_output if stdout_output else "(empty)"}

STDERR:
{stderr_output if stderr_output else "(empty)"}

Exit Code: {process.returncode}
'''
        
        if process.returncode != 0:
            final_string += f'\n⚠ Process exited with non-zero code: {process.returncode}'
        
        return final_string
    
    except Exception as e:
        error_msg = f'Error executing Python file {file_path}: {str(e)}'
        console.print(f"[red]{error_msg}[/red]\n")
        return error_msg


# Schema definition for the AI agent
schema_run_python_file = types.FunctionDeclaration(
    name="run_python_file",
    description="Execute a Python file with the python3 interpreter and display live output. Accepts additional CLI arguments as an optional array. Useful for testing scripts and running code.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "file_path": types.Schema(
                type=types.Type.STRING,
                description="The file to run, relative to the working directory (e.g., 'script.py', 'tests/test_main.py')",
            ),
            "args": types.Schema(
                type=types.Type.ARRAY,
                description="An optional array of strings to be used as CLI arguments for the Python file",
                items=types.Schema(
                    type=types.Type.STRING,
                ),
            ),
        },
        required=["file_path"],
    ),
)