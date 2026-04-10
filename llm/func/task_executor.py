"""
Task Executor - Reads JSON plans and tracks execution progress
This utility helps the agent follow the plan step-by-step
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn

console = Console()


def load_plan(plan_file: str) -> Optional[Dict]:
    """
    Load a plan JSON file.
    
    Args:
        plan_file: Path to the plan JSON file (relative or absolute)
    
    Returns:
        Dictionary containing the plan, or None if error
    """
    try:
        if not os.path.isabs(plan_file):
            # Check in plans/ directory
            plan_file = os.path.join('plans', plan_file)
        
        with open(plan_file, 'r', encoding='utf-8') as f:
            plan = json.load(f)
        
        console.print(f"[green]✓ Loaded plan: {plan['project_info']['task']}[/green]")
        return plan
    
    except FileNotFoundError:
        console.print(f"[red]✗ Plan file not found: {plan_file}[/red]")
        return None
    except json.JSONDecodeError as e:
        console.print(f"[red]✗ Invalid JSON in plan file: {e}[/red]")
        return None
    except Exception as e:
        console.print(f"[red]✗ Error loading plan: {e}[/red]")
        return None


def get_latest_plan(working_directory: str = '.') -> Optional[str]:
    """
    Get the most recent plan file from plans/ directory.
    
    Returns:
        Path to the latest plan file, or None if no plans found
    """
    plans_dir = os.path.join(working_directory, 'plans')
    
    if not os.path.exists(plans_dir):
        console.print("[yellow]No plans directory found[/yellow]")
        return None
    
    plan_files = list(Path(plans_dir).glob('*.json'))
    
    if not plan_files:
        console.print("[yellow]No plan files found in plans/[/yellow]")
        return None
    
    # Sort by modification time, get most recent
    latest = max(plan_files, key=lambda p: p.stat().st_mtime)
    
    console.print(f"[cyan]Latest plan: {latest.name}[/cyan]")
    return str(latest)


def list_plans(working_directory: str = '.') -> List[str]:
    """List all available plans in plans/ directory."""
    plans_dir = os.path.join(working_directory, 'plans')
    
    if not os.path.exists(plans_dir):
        console.print("[yellow]No plans directory found[/yellow]")
        return []
    
    plan_files = sorted(
        Path(plans_dir).glob('*.json'),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    
    if not plan_files:
        console.print("[yellow]No plans found[/yellow]")
        return []
    
    table = Table(title="Available Plans", show_header=True)
    table.add_column("File", style="cyan")
    table.add_column("Modified", style="yellow")
    table.add_column("Size", style="green")
    
    for plan_file in plan_files[:10]:  # Show last 10
        modified = datetime.fromtimestamp(plan_file.stat().st_mtime)
        size_kb = plan_file.stat().st_size / 1024
        table.add_row(
            plan_file.name,
            modified.strftime('%Y-%m-%d %H:%M'),
            f"{size_kb:.1f} KB"
        )
    
    console.print(table)
    return [str(f) for f in plan_files]


def display_plan_overview(plan: Dict):
    """Display a nice overview of the plan."""
    
    info = plan['project_info']
    analysis = plan['analysis']
    metadata = plan['metadata']
    
    # Header
    console.print()
    console.print(Panel(
        f"[bold cyan]{info['task']}[/bold cyan]\n"
        f"[dim]Generated: {info['generated_at'][:19]}[/dim]",
        title="[bold]📋 Execution Plan Overview[/bold]",
        border_style="cyan"
    ))
    
    # Project Info
    console.print(f"\n[bold]Project Information:[/bold]")
    console.print(f"  Type: [cyan]{info['project_type']}[/cyan]")
    console.print(f"  Architecture: [cyan]{analysis['architecture']}[/cyan]")
    console.print(f"  Complexity: [yellow]{analysis['complexity']}[/yellow]")
    console.print(f"  Files Analyzed: [green]{info['files_analyzed']}[/green]")
    
    # Tasks Summary
    console.print(f"\n[bold]Tasks Summary:[/bold]")
    console.print(f"  Total Tasks: [cyan]{metadata['total_tasks']}[/cyan]")
    console.print(f"  Total Subtasks: [cyan]{metadata['total_subtasks']}[/cyan]")
    
    # Technology Stack
    if analysis['technology_stack']:
        console.print(f"\n[bold]Technology Stack:[/bold]")
        for tech in analysis['technology_stack']:
            console.print(f"  • {tech}")
    
    console.print()


def display_tasks(plan: Dict):
    """Display all tasks in a clean format."""
    
    tasks = plan['tasks']
    
    console.print(Panel(
        "[bold]Task List[/bold]",
        border_style="blue"
    ))
    
    for task in tasks:
        # Task header
        complexity_color = {
            'Low': 'green',
            'Medium': 'yellow',
            'High': 'red'
        }.get(task['complexity'], 'white')
        
        console.print(f"\n[bold cyan]Task {task['id']}:[/bold cyan] {task['title']}")
        console.print(f"  Complexity: [{complexity_color}]{task['complexity']}[/{complexity_color}]")
        
        if task['dependencies']:
            console.print(f"  Dependencies: [dim]{', '.join(task['dependencies'])}[/dim]")
        
        console.print(f"  [dim]{task['description']}[/dim]")
        
        # Subtasks
        if task['subtasks']:
            console.print(f"\n  [bold]Subtasks ({len(task['subtasks'])}):[/bold]")
            for i, subtask in enumerate(task['subtasks'], 1):
                status_icon = "○" if subtask['status'] == 'pending' else "●"
                status_color = "dim" if subtask['status'] == 'pending' else "green"
                console.print(f"    [{status_color}]{status_icon}[/{status_color}] {subtask['title']}")


def get_next_task(plan: Dict) -> Optional[Dict]:
    """
    Get the next pending task that has all dependencies completed.
    
    Returns:
        Next task to execute, or None if all tasks are done
    """
    tasks = plan['tasks']
    completed_tasks = set()
    
    # Find completed tasks
    for task in tasks:
        all_subtasks_done = all(
            subtask['status'] in ['completed', 'done'] 
            for subtask in task['subtasks']
        )
        if all_subtasks_done:
            completed_tasks.add(task['title'])
    
    # Find next task with dependencies met
    for task in tasks:
        # Check if any subtask is pending
        has_pending = any(
            subtask['status'] == 'pending' 
            for subtask in task['subtasks']
        )
        
        if not has_pending:
            continue
        
        # Check dependencies
        dependencies_met = all(
            dep in completed_tasks 
            for dep in task['dependencies']
        )
        
        if dependencies_met:
            return task
    
    return None


def get_current_progress(plan: Dict) -> tuple:
    """
    Calculate overall progress.
    
    Returns:
        (completed_subtasks, total_subtasks)
    """
    total = 0
    completed = 0
    
    for task in plan['tasks']:
        for subtask in task['subtasks']:
            total += 1
            if subtask['status'] in ['completed', 'done']:
                completed += 1
    
    return (completed, total)


def mark_subtask_complete(plan: Dict, task_id: int, subtask_title: str) -> bool:
    """
    Mark a subtask as completed in the plan.
    
    Args:
        plan: The plan dictionary
        task_id: ID of the task containing the subtask
        subtask_title: Title of the subtask to mark complete
    
    Returns:
        True if updated successfully, False otherwise
    """
    try:
        for task in plan['tasks']:
            if task['id'] == task_id:
                for subtask in task['subtasks']:
                    if subtask['title'] == subtask_title:
                        subtask['status'] = 'completed'
                        console.print(f"[green]✓[/green] Marked complete: {subtask_title}")
                        return True
        
        console.print(f"[yellow]Subtask not found: {subtask_title}[/yellow]")
        return False
    
    except Exception as e:
        console.print(f"[red]Error marking subtask complete: {e}[/red]")
        return False


def save_plan_progress(plan: Dict, plan_file: str):
    """
    Save the updated plan with progress back to the file.
    
    Args:
        plan: The plan dictionary with updated progress
        plan_file: Path to the plan file
    """
    try:
        with open(plan_file, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        
        completed, total = get_current_progress(plan)
        console.print(f"[green]✓ Progress saved: {completed}/{total} subtasks completed[/green]")
    
    except Exception as e:
        console.print(f"[red]Error saving progress: {e}[/red]")


def display_progress(plan: Dict):
    """Display progress bar and statistics."""
    
    completed, total = get_current_progress(plan)
    percentage = (completed / total * 100) if total > 0 else 0
    
    console.print()
    console.print(f"[bold]Overall Progress:[/bold]")
    
    # Progress bar
    bar_width = 40
    filled = int(bar_width * completed / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    
    console.print(f"  {bar} {percentage:.1f}%")
    console.print(f"  {completed}/{total} subtasks completed")
    
    # Task breakdown
    console.print(f"\n[bold]Task Status:[/bold]")
    for task in plan['tasks']:
        task_completed = sum(
            1 for s in task['subtasks'] 
            if s['status'] in ['completed', 'done']
        )
        task_total = len(task['subtasks'])
        
        status_icon = "✓" if task_completed == task_total else "○"
        status_color = "green" if task_completed == task_total else "yellow"
        
        console.print(
            f"  [{status_color}]{status_icon}[/{status_color}] "
            f"Task {task['id']}: {task['title']} "
            f"[dim]({task_completed}/{task_total})[/dim]"
        )
    
    console.print()


# Example usage function
def execute_plan_workflow(plan_file: Optional[str] = None):
    """
    Complete workflow for executing a plan.
    
    This is an example of how the agent should use the plan.
    """
    
    # Load plan
    if plan_file is None:
        plan_file = get_latest_plan()
        if plan_file is None:
            return
    
    plan = load_plan(plan_file)
    if plan is None:
        return
    
    # Display overview
    display_plan_overview(plan)
    
    # Display all tasks
    display_tasks(plan)
    
    # Show initial progress
    display_progress(plan)
    
    # Main execution loop (example)
    console.print("\n[bold cyan]Starting execution...[/bold cyan]\n")
    
    while True:
        next_task = get_next_task(plan)
        
        if next_task is None:
            console.print("[green]✓ All tasks completed![/green]")
            break
        
        console.print(f"\n[bold]Executing Task {next_task['id']}:[/bold] {next_task['title']}")
        
        # Execute each subtask
        for subtask in next_task['subtasks']:
            if subtask['status'] == 'pending':
                console.print(f"  → {subtask['title']}")
                
                # Agent does the work here...
                # This is where the agent would use patch_file, write_file, etc.
                
                # Mark as complete
                mark_subtask_complete(plan, next_task['id'], subtask['title'])
        
        # Save progress after each task
        save_plan_progress(plan, plan_file)
        display_progress(plan)


if __name__ == "__main__":
    # Example: List all plans
    list_plans()
    
    # Example: Execute the latest plan
    # execute_plan_workflow()


# ============================================================================
# SCHEMA FOR AI AGENT INTEGRATION
# ============================================================================

from google.genai import types

schema_execute_task = types.FunctionDeclaration(
    name="execute_task",
    description="""Execute a specific task from the current plan and mark subtasks as complete.

This tool allows the agent to:
- Load the latest plan from plans/ directory
- Execute a specific task by ID
- Mark individual subtasks as complete
- Track overall progress

Use this tool when:
- You've generated a plan and are ready to execute it
- You need to mark a subtask as complete
- You want to track progress through the plan

The tool will automatically:
- Load the most recent plan
- Find the specified task
- Mark the subtask as complete
- Save progress back to the plan file
- Show updated progress""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "task_id": types.Schema(
                type=types.Type.INTEGER,
                description="ID of the task containing the subtask (e.g., 1, 2, 3)",
            ),
            "subtask_title": types.Schema(
                type=types.Type.STRING,
                description="Exact title of the subtask to mark as complete (e.g., 'Read project structure', 'Create API endpoint')",
            ),
            "plan_file": types.Schema(
                type=types.Type.STRING,
                description="Optional: Path to specific plan file. If not provided, uses the latest plan.",
            ),
        },
        required=["task_id", "subtask_title"],
    ),
)


def execute_task(
    working_directory: str,
    task_id: int,
    subtask_title: str,
    plan_file: Optional[str] = None
) -> str:
    """
    Execute a task and mark a subtask as complete.
    
    Args:
        working_directory: Current working directory
        task_id: ID of the task (1, 2, 3, etc.)
        subtask_title: Title of the subtask to mark complete
        plan_file: Optional path to specific plan file
    
    Returns:
        Status message with progress
    """
    try:
        # Load plan
        if plan_file is None:
            plan_file = get_latest_plan(working_directory)
            if plan_file is None:
                return "Error: No plan found. Create a plan first using plan_project tool."
        
        plan = load_plan(plan_file)
        if plan is None:
            return f"Error: Could not load plan from {plan_file}"
        
        # Mark subtask complete
        success = mark_subtask_complete(plan, task_id, subtask_title)
        
        if not success:
            return f"Error: Could not find subtask '{subtask_title}' in task {task_id}"
        
        # Save progress
        save_plan_progress(plan, plan_file)
        
        # Get progress stats
        completed, total = get_current_progress(plan)
        percentage = (completed / total * 100) if total > 0 else 0
        
        # Build progress bar
        bar_width = 20
        filled = int(bar_width * completed / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_width - filled)
        
        result = f"""Task Execution Complete
{'='*50}
Task ID: {task_id}
Subtask: {subtask_title}
Status: ✓ Completed

Overall Progress:
{bar} {percentage:.0f}%
{completed}/{total} subtasks completed

Next: Continue with remaining subtasks in Task {task_id}
"""
        
        return result
    
    except Exception as e:
        return f"Error executing task: {str(e)}"