"""
Function Call Router — with PathGuard on every file operation.
"""

import os
from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from path_guard import guard, GuardError

console = Console()

# ── Tool schemas for team agent ───────────────────────────────────────────────

def _build_team_tools() -> types.Tool:
    from func.get_files_info import schema_get_files_info
    from func.get_file_content import schema_get_file_content
    from func.write_file import schema_write_file
    from func.run_python_file import schema_run_python_file
    from func.run_shell import schema_run_shell
    from func.patch_file import schema_patch_file
    from func.build import schema_build_project, schema_install_dependencies
    from func.task_executor import schema_execute_task
    from func.plan_project import schema_plan_project

    return types.Tool(function_declarations=[
        schema_get_files_info, schema_get_file_content, schema_run_python_file,
        schema_write_file, schema_run_shell, schema_build_project,
        schema_install_dependencies, schema_patch_file, schema_plan_project,
        schema_execute_task,
    ])


def _get_teams_instance():
    if not hasattr(_get_teams_instance, "_instance"):
        from team_agent.teams_cli import TeamsCLI
        _get_teams_instance._instance = TeamsCLI(
            env_path=".env", tools=_build_team_tools()
        )
    return _get_teams_instance._instance


# ── Guard helpers ─────────────────────────────────────────────────────────────

def _safe_path(raw: str, write: bool = False) -> str:
    """Resolve path through PathGuard. Returns str path or raises GuardError."""
    return str(guard.resolve(raw, write=write))


def _guard_error_result(fn_name: str, raw_path: str, err: GuardError) -> types.Content:
    short = str(err).split('\n')[0]
    console.print(f"  [red]🔒 Blocked {fn_name}({raw_path!r})[/red]  [dim]{short}[/dim]")
    return types.Content(
        role="user",
        parts=[types.Part(
            function_response=types.FunctionResponse(
                name=fn_name,
                response={"result": f"🔒 Access denied: {short}"}
            )
        )]
    )


# ============================================================================
# MAIN ROUTER
# ============================================================================

def call_function(function_call: types.FunctionCall, verbose: bool = False) -> types.Content:
    function_name = function_call.name
    args          = function_call.args
    working_directory = os.getcwd()

    result = ""
    try:

        # ── Memory functions ─────────────────────────────────────────────────
        if function_name == "memory_add_pattern":
            from func.mem_integration import memory_add_pattern
            result = memory_add_pattern(
                category=args.get("category"),
                description=args.get("description"),
                example=args.get("example"),
                working_directory=working_directory
            )

        elif function_name == "memory_save_project_structure":
            from func.mem_integration import memory_save_project_structure
            result = memory_save_project_structure(
                structure=args.get("structure"),
                working_directory=working_directory
            )

        elif function_name == "memory_save_project_commands":
            from func.mem_integration import memory_save_project_commands
            result = memory_save_project_commands(
                commands=args.get("commands"),
                working_directory=working_directory
            )

        elif function_name == "memory_save_file_purpose":
            from func.mem_integration import memory_save_file_purpose
            result = memory_save_file_purpose(
                file_path=args.get("file_path"),
                purpose=args.get("purpose"),
                working_directory=working_directory
            )

        elif function_name == "memory_create_checkpoint":
            from func.mem_integration import memory_create_checkpoint
            result = memory_create_checkpoint(
                task_id=args.get("task_id"),
                description=args.get("description"),
                working_directory=working_directory
            )

        elif function_name == "memory_restore_checkpoint":
            from func.mem_integration import memory_restore_checkpoint
            result = memory_restore_checkpoint(
                checkpoint_id=args.get("checkpoint_id"),
                working_directory=working_directory
            )

        elif function_name == "memory_list_checkpoints":
            from func.mem_integration import memory_list_checkpoints
            result = memory_list_checkpoints(working_directory=working_directory)

        elif function_name == "memory_get_context":
            from func.mem_integration import memory_get_context
            result = memory_get_context(
                query=args.get("query"),
                working_directory=working_directory
            )

        elif function_name == "memory_enable":
            from func.mem_integration import memory_enable
            result = memory_enable(working_directory=working_directory)

        elif function_name == "memory_disable":
            from func.mem_integration import memory_disable
            result = memory_disable(working_directory=working_directory)

        elif function_name == "memory_toggle_feature":
            from func.mem_integration import memory_toggle_feature
            result = memory_toggle_feature(
                feature=args.get("feature"),
                enabled=args.get("enabled"),
                working_directory=working_directory
            )

        elif function_name == "memory_get_stats":
            from func.mem_integration import memory_get_stats
            result = memory_get_stats(working_directory=working_directory)

        elif function_name == "memory_cleanup":
            from func.mem_integration import memory_cleanup
            result = memory_cleanup(working_directory=working_directory)

        # ── Build functions ──────────────────────────────────────────────────
        elif function_name == "build_project":
            from func.build import build_project
            result = build_project(
                working_directory=args.get("working_directory", working_directory),
                project_name=args.get("project_name"),
                project_type=args.get("project_type"),
                framework=args.get("framework", "nextjs"),
                options=args.get("options", {}),
                timeout=args.get("timeout", 300),
                show_live=True
            )

        elif function_name == "install_dependencies":
            from func.build import install_dependencies
            result = install_dependencies(
                working_directory=args.get("working_directory", working_directory),
                package_manager=args.get("package_manager", "npm"),
                timeout=args.get("timeout", 300),
                show_live=True
            )

        # ── File operations (all guarded) ────────────────────────────────────

        elif function_name == "patch_file":
            raw = args.get("file_path", "")
            try:
                _safe_path(raw, write=True)
            except GuardError as e:
                return _guard_error_result(function_name, raw, e)

            from func.patch_file import patch_file
            result = patch_file(
                working_directory,
                file_path=raw,
                content_before=args.get("content_before"),
                content_after=args.get("content_after")
            )

        elif function_name == "get_file_content":
            raw = args.get("file_path", "")
            try:
                _safe_path(raw)
            except GuardError as e:
                return _guard_error_result(function_name, raw, e)

            from func.get_file_content import get_file_content
            result = get_file_content(
                working_directory,
                file_path=raw,
                start_line=args.get("start_line"),
                end_line=args.get("end_line")
            )

        elif function_name == "write_file":
            raw = args.get("file_path", "")
            try:
                _safe_path(raw, write=True)
            except GuardError as e:
                return _guard_error_result(function_name, raw, e)

            from func.write_file import write_file
            result = write_file(
                working_directory,
                file_path=raw,
                content=args.get("content")
            )

        elif function_name == "get_files_info":
            raw = args.get("path", ".")
            # For directory listings, we only block clearly sensitive paths
            try:
                _safe_path(raw)
            except GuardError as e:
                return _guard_error_result(function_name, raw, e)

            from func.get_files_info import get_files_info
            result = get_files_info(
                working_directory,
                path=raw,
                recursive=args.get("recursive", False)
            )

        elif function_name == "run_python_file":
            raw = args.get("file_path", "")
            try:
                _safe_path(raw)
            except GuardError as e:
                return _guard_error_result(function_name, raw, e)

            from func.run_python_file import run_python_file
            result = run_python_file(
                working_directory,
                file_path=raw,
                args=args.get("args", [])
            )

        elif function_name == "run_shell":
            from func.run_shell import run_shell
            result = run_shell(
                working_directory,
                command=args.get("command"),
                timeout=args.get("timeout", 30)
            )

        # ── Task execution ───────────────────────────────────────────────────
        elif function_name == "execute_task":
            from func.task_executor import execute_task
            result = execute_task(
                working_directory=working_directory,
                task_id=args.get("task_id"),
                subtask_title=args.get("subtask_title"),
                plan_file=args.get("plan_file")
            )

        # ── Team agent ───────────────────────────────────────────────────────
        elif function_name == "team_agent":
            teams   = _get_teams_instance()
            command = args.get("command", "").strip()
            if not command.lower().startswith("/team"):
                command = f"/team {command}"
            response = teams.handle(command)
            result = response or "Team command executed."

        # ── Planning ─────────────────────────────────────────────────────────
        elif function_name == "plan_project":
            from func.plan_project import plan_project
            result = plan_project(
                working_directory=args.get("working_directory", working_directory),
                task_description=args.get("task_description"),
                file_patterns=args.get("file_patterns"),
                max_files=args.get("max_files", 50),
                max_file_size=args.get("max_file_size", 100000),
                include_dependencies=args.get("include_dependencies", True),
                save_plan=True,
                show_live=True
            )

        # ── Codespace tools (guarded) ────────────────────────────────────────
        elif function_name in ("cs_run_shell", "cs_write_file", "cs_read_file",
                               "cs_patch_file", "cs_list_files", "cs_run_python",
                               "cs_connect", "cs_create", "cs_status",
                               "cs_list_codespaces"):
            # File-bearing codespace ops: guard the path
            for farg in ("file_path", "path"):
                raw = args.get(farg)
                if raw:
                    write = function_name in ("cs_write_file", "cs_patch_file")
                    try:
                        _safe_path(raw, write=write)
                    except GuardError as e:
                        return _guard_error_result(function_name, raw, e)

            try:
                from func.codespace_tools import dispatch_cs
                result = dispatch_cs(function_name, args, working_directory)
            except ImportError:
                result = "Codespace tools not installed."

        else:
            result = f"Unknown function: '{function_name}'"
            console.print(f"  [red]{result}[/red]")

    except GuardError as e:
        result = f"🔒 Access denied: {e}"
        console.print(f"  [red bold]{result}[/red bold]")

    except Exception as e:
        result = f"Error in {function_name}: {e}"
        console.print(f"  [red bold]✗ {result}[/red bold]")
        if verbose:
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")

    return types.Content(
        role="user",
        parts=[types.Part(
            function_response=types.FunctionResponse(
                name=function_name,
                response={"result": result}
            )
        )]
    )