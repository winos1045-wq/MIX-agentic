import os
import subprocess
import json
from typing import Optional, Dict, List
from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

def build_project(
    working_directory: str,
    project_name: str,
    project_type: str,
    framework: str = "nextjs",
    options: Optional[Dict[str, str]] = None,
    timeout: int = 300,
    show_live: bool = True
) -> str:
    """
    Automate project scaffolding with interactive CLI handling.
    
    Args:
        working_directory: Directory to create project in
        project_name: Name of the project
        project_type: Type of project ('nextjs', 'react', 'vue', 'svelte', 'fastapi', etc.)
        framework: Framework/template to use
        options: Dictionary of CLI responses (e.g., {"typescript": "yes", "eslint": "no"})
        timeout: Maximum execution time in seconds (default: 300)
        show_live: Whether to show live output
    
    Returns:
        String containing build output, status, and exit code
    """
    abs_working_dir = os.path.abspath(working_directory)
    
    # Validate working directory
    if not os.path.isdir(abs_working_dir):
        return f'Error: Working directory {working_directory} does not exist'
    
    if show_live:
        console.print()
        console.print(Panel(
            f"[cyan]Building Project: {project_name}[/cyan]\n[yellow]Type: {project_type} | Framework: {framework}[/yellow]",
            title="[bold blue]Project Builder[/bold blue]",
            border_style="blue",
            padding=(1, 2)
        ))
    
    try:
        # Build command based on framework
        command = _build_command(project_type, project_name, framework)
        
        if not command:
            return f'Error: Unsupported project type: {project_type}'
        
        # Prepare interactive responses
        stdin_input = _prepare_stdin_input(project_type, project_name, options or {})
        
        if show_live:
            console.print(f"[cyan]Command:[/cyan] {command}")
            console.print(f"[cyan]Working Dir:[/cyan] {abs_working_dir}\n")
        
        # Execute with interactive input
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console if show_live else None,
            transient=True
        ) as progress:
            progress.add_task(description="Building project...", total=None)
            
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=abs_working_dir,
                shell=True
            )
            
            try:
                stdout, stderr = process.communicate(
                    input=stdin_input,
                    timeout=timeout
                )
            except subprocess.TimeoutExpired:
                process.kill()
                if show_live:
                    console.print("[red]✗ Build timed out![/red]\n")
                return f'Error: Build execution timed out after {timeout} seconds'
        
        # Display output
        if show_live:
            console.print("[dim]─" * 80 + "[/dim]")
            
            if stdout:
                console.print("[green]✓ Build Output:[/green]")
                for line in stdout.split('\n'):
                    if line.strip():
                        console.print(f"  {line}")
            
            if stderr:
                console.print("[yellow]⚠ Warnings/Errors:[/yellow]")
                for line in stderr.split('\n'):
                    if line.strip():
                        console.print(f"  [yellow]{line}[/yellow]")
            
            console.print("[dim]─" * 80 + "[/dim]")
            
            if process.returncode == 0:
                console.print(f"[green]✓ Build successful![/green]\n")
            else:
                console.print(f"[red]✗ Build failed with exit code: {process.returncode}[/red]\n")
        
        # Format output for AI
        final_string = f'''Project Build Report
{'='*60}
Project Name: {project_name}
Project Type: {project_type}
Framework: {framework}
Working Directory: {abs_working_dir}

STDOUT:
{stdout.strip() if stdout else "(empty)"}

STDERR:
{stderr.strip() if stderr else "(empty)"}

Exit Code: {process.returncode}
Status: {"SUCCESS ✓" if process.returncode == 0 else "FAILED ✗"}
'''
        
        return final_string
    
    except Exception as e:
        error_msg = f'Error during project build: {str(e)}'
        if show_live:
            console.print(f"[red]{error_msg}[/red]\n")
        return error_msg


def install_dependencies(
    working_directory: str,
    package_manager: str = "npm",
    timeout: int = 300,
    show_live: bool = True
) -> str:
    """
    Install project dependencies using specified package manager.
    
    Args:
        working_directory: Project directory
        package_manager: 'npm', 'yarn', 'pnpm', or 'bun'
        timeout: Maximum execution time
        show_live: Show live output
    
    Returns:
        Installation status and output
    """
    abs_working_dir = os.path.abspath(working_directory)
    
    if not os.path.isdir(abs_working_dir):
        return f'Error: Directory {working_directory} does not exist'
    
    # Validate package manager
    valid_managers = ['npm', 'yarn', 'pnpm', 'bun']
    if package_manager not in valid_managers:
        return f'Error: Invalid package manager. Use: {", ".join(valid_managers)}'
    
    command_map = {
        'npm': 'npm install',
        'yarn': 'yarn install',
        'pnpm': 'pnpm install',
        'bun': 'bun install'
    }
    
    command = command_map[package_manager]
    
    if show_live:
        console.print()
        console.print(Panel(
            f"[cyan]Installing dependencies with {package_manager}[/cyan]",
            title="[bold green]Dependency Installation[/bold green]",
            border_style="green",
            padding=(0, 2)
        ))
    
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console if show_live else None,
            transient=True
        ) as progress:
            progress.add_task(description=f"Installing with {package_manager}...", total=None)
            
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=abs_working_dir,
                shell=True
            )
            
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                if show_live:
                    console.print("[red]✗ Installation timed out![/red]\n")
                return f'Error: Dependency installation timed out after {timeout} seconds'
        
        if show_live:
            if process.returncode == 0:
                console.print(f"[green]✓ Dependencies installed successfully![/green]\n")
            else:
                console.print(f"[red]✗ Installation failed with exit code: {process.returncode}[/red]\n")
        
        final_string = f'''Dependency Installation Report
{'='*60}
Package Manager: {package_manager}
Working Directory: {abs_working_dir}
Command: {command}

STDOUT:
{stdout.strip() if stdout else "(empty)"}

STDERR:
{stderr.strip() if stderr else "(empty)"}

Exit Code: {process.returncode}
Status: {"SUCCESS ✓" if process.returncode == 0 else "FAILED ✗"}
'''
        
        return final_string
    
    except Exception as e:
        return f'Error during dependency installation: {str(e)}'


def _build_command(project_type: str, project_name: str, framework: str) -> Optional[str]:
    """Generate build command based on project type."""
    commands = {
        'nextjs': f'npx create-next-app@latest {project_name} --yes',
        'react': f'npx create-react-app {project_name}',
        'vue': f'npm create vue@latest {project_name}',
        'svelte': f'npm create svelte@latest {project_name}',
        'vite-react': f'npm create vite@latest {project_name} -- --template react',
        'vite-vue': f'npm create vite@latest {project_name} -- --template vue',
        'fastapi': f'mkdir {project_name} && cd {project_name} && python -m venv venv && source venv/bin/activate',
        'django': f'mkdir {project_name} && cd {project_name} && python -m venv venv && source venv/bin/activate && pip install django && django-admin startproject config .',
        'express': f'npx express-generator {project_name}',
    }
    
    return commands.get(project_type)


def _prepare_stdin_input(project_type: str, project_name: str, options: Dict[str, str]) -> str:
    """
    Prepare stdin responses for interactive CLI prompts.
    
    Common responses:
    - "y" or "yes" for yes/no questions
    - Arrow key navigation (↓, ↑, Enter)
    - Direct selections
    """
    
    # Default responses for common frameworks
    default_responses = {
        'nextjs': [
            project_name,  # Project name
            'No',          # TypeScript
            'No',          # ESLint
            'No',          # Tailwind CSS
            'No',          # src/ directory
            'No',          # App Router
            'No',          # Turbopack
            'No',          # Import alias
            ''             # Final enter
        ],
        'react': [
            'y\n',         # Proceed
        ],
        'vue': [
            project_name,
            'y\n',
            'n\n',
            'n\n',
            ''
        ]
    }
    
    # Merge with user options
    responses = default_responses.get(project_type, [])
    
    # Build stdin string
    stdin_input = '\n'.join(str(r) for r in responses)
    return stdin_input


# Schema definitions for AI agent
schema_build_project = types.FunctionDeclaration(
    name="build_project",
    description="""Automate project scaffolding for various frameworks with interactive prompt handling.
    Supports: Next.js, React, Vue, Svelte, FastAPI, Django, Express, and more.
    Automatically handles CLI prompts without manual intervention.""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Directory where project will be created",
            ),
            "project_name": types.Schema(
                type=types.Type.STRING,
                description="Name of the project to create",
            ),
            "project_type": types.Schema(
                type=types.Type.STRING,
                description="Type of project: 'nextjs', 'react', 'vue', 'svelte', 'fastapi', 'django', 'express'",
            ),
            "framework": types.Schema(
                type=types.Type.STRING,
                description="Framework variant (default: nextjs)",
            ),
            "options": types.Schema(
                type=types.Type.OBJECT,
                description="Dictionary of CLI response options (e.g., {'typescript': 'yes', 'eslint': 'no'})",
            ),
            "timeout": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum execution time in seconds (default: 300)",
            ),
        },
        required=["working_directory", "project_name", "project_type"],
    ),
)

schema_install_dependencies = types.FunctionDeclaration(
    name="install_dependencies",
    description="Install project dependencies using npm, yarn, pnpm, or bun package managers.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Project directory path",
            ),
            "package_manager": types.Schema(
                type=types.Type.STRING,
                description="Package manager to use: 'npm', 'yarn', 'pnpm', or 'bun'",
            ),
            "timeout": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum execution time in seconds (default: 300)",
            ),
        },
        required=["working_directory"],
    ),
)

