#run_shell.py
import os
import subprocess
import shlex
from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.live import Live
from rich.text import Text

console = Console()

def run_shell(working_directory, command: str, timeout: int = 30, show_live: bool = True):
    """
    Execute shell commands securely for cybersecurity operations with live output display.
    
    Args:
        working_directory: The directory to execute the command in
        command: The shell command to execute
        timeout: Maximum execution time in seconds (default: 30)
        show_live: Whether to show live output in CLI (default: True)
    
    Returns:
        String containing stdout, stderr, and exit code information
    """
    abs_working_dir = os.path.abspath(working_directory)
    
    # Security checks
    if not os.path.isdir(abs_working_dir):
        return f'Error: Working directory {working_directory} does not exist'
    
    # Blacklist dangerous commands for safety
    dangerous_commands = ['rm -rf /', 'mkfs', 'dd if=/dev/zero', ':(){:|:&};:', 'chmod -R 777 /']
    if any(dangerous in command.lower() for dangerous in dangerous_commands):
        return 'Error: Command contains potentially dangerous operations and is blocked'
    
    # Display command being executed
    if show_live:
        console.print()
        console.print(Panel(
            f"[cyan]$ {command}[/cyan]",
            title="[yellow]⚡ Executing Command[/yellow]",
            border_style="yellow",
            padding=(0, 2)
        ))
    
    try:
        # Parse command safely
        cmd_parts = shlex.split(command)
        
        if not cmd_parts:
            return 'Error: Empty command provided'
        
        # Execute the command with real-time output
        process = subprocess.Popen(
            cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=abs_working_dir,
            shell=False
        )
        
        stdout_lines = []
        stderr_lines = []
        
        if show_live:
            # Show live output
            console.print("[dim]─" * 80 + "[/dim]")
            
        # Read stdout
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            
            if show_live and stdout:
                for line in stdout.split('\n'):
                    if line.strip():
                        console.print(f"[green]{line}[/green]")
                    stdout_lines.append(line)
            
            if show_live and stderr:
                for line in stderr.split('\n'):
                    if line.strip():
                        console.print(f"[red]{line}[/red]")
                    stderr_lines.append(line)
            
            if show_live:
                console.print("[dim]─" * 80 + "[/dim]")
                
                # Show exit code
                if process.returncode == 0:
                    console.print(f"[green]✓ Exit code: {process.returncode}[/green]")
                else:
                    console.print(f"[red]✗ Exit code: {process.returncode}[/red]")
                console.print()
            
        except subprocess.TimeoutExpired:
            process.kill()
            if show_live:
                console.print("[red]✗ Command timed out![/red]\n")
            return f'Error: Command execution timed out after {timeout} seconds'
        
        # Format complete output for AI
        stdout_output = stdout.strip() if stdout else ""
        stderr_output = stderr.strip() if stderr else ""
        
        final_string = f'''Command: {command}
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
    
    except FileNotFoundError:
        error_msg = f'Error: Command not found - {cmd_parts[0]}'
        if show_live:
            console.print(f"[red]{error_msg}[/red]\n")
        return error_msg
    
    except PermissionError:
        error_msg = f'Error: Permission denied to execute command'
        if show_live:
            console.print(f"[red]{error_msg}[/red]\n")
        return error_msg
    
    except Exception as e:
        error_msg = f'Error executing shell command: {str(e)}'
        if show_live:
            console.print(f"[red]{error_msg}[/red]\n")
        return error_msg


# Schema definition for the AI agent
schema_run_shell = types.FunctionDeclaration(
    name="run_shell",
    description="""Execute shell commands for cybersecurity operations, penetration testing, and system administration. 
    Supports tools like nmap, netcat, curl, wget, ping, traceroute, and other security utilities.
    Commands are executed securely with timeout protection and live output display.""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "command": types.Schema(
                type=types.Type.STRING,
                description="The shell command to execute (e.g., 'nmap -sV 192.168.1.1', 'curl -I https://example.com')",
            ),
            "timeout": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum execution time in seconds (default: 30, max: 300)",
            ),
        },
        required=["command"],
    ),
)

