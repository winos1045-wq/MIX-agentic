"""
Function Call Router — with PathGuard + Recording hooks on every tool call.
"""

import os
import time
from google.genai import types
from rich.console import Console

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
    function_name     = function_call.name
    args              = function_call.args
    working_directory = os.getcwd()

    # ── Recording hook: START ─────────────────────────────────────────────────
    # Fires before every tool, regardless of which tool it is.
    # If no recording session is active, hook_tool_call returns None (no-op).
    _t0 = time.perf_counter()
    _call_index = None
    try:
        from func.sys_agent_recording import hook_tool_call
        _call_index = hook_tool_call(function_name, args)
    except ImportError:
        pass  # recording module not installed — silently skip

    # ── Main dispatch ─────────────────────────────────────────────────────────
    result = ""
    try:

        # ── Memory functions ──────────────────────────────────────────────────
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

        # ── Build functions ───────────────────────────────────────────────────
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

        # ── File operations (all guarded) ─────────────────────────────────────
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

        # ── remember_fact ─────────────────────────────────────────────────────
        elif function_name == "remember_fact":
            from func.remember_fact import remember_fact
            result = remember_fact(
                working_directory=working_directory,
                key=args.get("key", ""),
                value=args.get("value", ""),
                category=args.get("category", "fact"),
                confidence=float(args.get("confidence", 1.0)),
                source=args.get("source", "agent"),
                tags=args.get("tags", []),
            )

        elif function_name == "recall_fact":
            from func.remember_fact import recall_fact
            result = recall_fact(
                working_directory=working_directory,
                query=args.get("query", ""),
                category=args.get("category", "all"),
                limit=int(args.get("limit", 20)),
                semantic=bool(args.get("semantic", False)),
            )

        elif function_name == "forget_fact":
            from func.remember_fact import forget_fact
            result = forget_fact(
                working_directory=working_directory,
                key=args.get("key", ""),
            )

        elif function_name == "list_facts":
            from func.remember_fact import list_facts
            result = list_facts(
                working_directory=working_directory,
                group_by_category=bool(args.get("group_by_category", True)),
                category=args.get("category", "all"),
            )

        # ── benchmark_solution ────────────────────────────────────────────────
        elif function_name == "benchmark_solution":
            from func.benchmark_solution import benchmark_solution
            result = benchmark_solution(
                working_directory=working_directory,
                task_id=args.get("task_id", "default"),
                target=args.get("target", ""),
                target_type=args.get("target_type", "python_file"),
                args=args.get("args", []),
                iterations=int(args.get("iterations", 10)),
                warmup_runs=int(args.get("warmup_runs", 2)),
                timeout_seconds=int(args.get("timeout_seconds", 30)),
                thresholds=args.get("thresholds", {}),
                compare_baseline=bool(args.get("compare_baseline", True)),
                save_as_baseline=bool(args.get("save_as_baseline", False)),
                http_method=args.get("http_method", "GET"),
                http_body=args.get("http_body"),
            )

        # ── task_decomposer ───────────────────────────────────────────────────
        elif function_name == "task_decomposer":
            from func.task_decomposer import task_decomposer
            result = task_decomposer(
                working_directory=working_directory,
                task_description=args.get("task_description", ""),
                context=args.get("context", {}),
                strategy=args.get("strategy", "dag"),
                depth_level=int(args.get("depth_level", 3)),
                save_plan=bool(args.get("save_plan", True)),
            )

        # ── sys_agent_recording ───────────────────────────────────────────────
        elif function_name == "recording_start":
            from func.sys_agent_recording import recording_start
            result = recording_start(
                working_directory=working_directory,
                session_id=args.get("session_id"),
                task_description=args.get("task_description", ""),
                metadata=args.get("metadata", {}),
            )

        elif function_name == "recording_stop":
            from func.sys_agent_recording import recording_stop
            result = recording_stop(
                working_directory=working_directory,
                session_id=args.get("session_id"),
                outcome=args.get("outcome", "success"),
                notes=args.get("notes", ""),
            )

        elif function_name == "recording_snapshot":
            from func.sys_agent_recording import recording_snapshot
            result = recording_snapshot(
                working_directory=working_directory,
                label=args.get("label", "checkpoint"),
                notes=args.get("notes", ""),
            )

        elif function_name == "recording_analyze":
            from func.sys_agent_recording import recording_analyze
            result = recording_analyze(
                working_directory=working_directory,
                session_id=args.get("session_id"),
                focus=args.get("focus", "full"),
            )

        # ── execute_task ──────────────────────────────────────────────────────
        elif function_name == "execute_task":
            from func.task_executor import execute_task
            result = execute_task(
                working_directory=working_directory,
                task_id=args.get("task_id"),
                subtask_title=args.get("subtask_title"),
                plan_file=args.get("plan_file")
            )

        # ── team_agent ────────────────────────────────────────────────────────
        elif function_name == "team_agent":
            teams   = _get_teams_instance()
            command = args.get("command", "").strip()
            if not command.lower().startswith("/team"):
                command = f"/team {command}"
            response = teams.handle(command)
            result = response or "Team command executed."

        # ── plan_project ──────────────────────────────────────────────────────
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

        # ── web search / fetch ────────────────────────────────────────────────
        elif function_name == "web_search":
            from func.web_fetch_search import web_search
            result = web_search(
                working_directory=working_directory,
                query=args.get("query", ""),
                allowed_domains=args.get("allowed_domains"),
                blocked_domains=args.get("blocked_domains"),
                max_results=int(args.get("max_results", 5)),
            )

        elif function_name == "web_fetch":
            from func.web_fetch_search import web_fetch
            result = web_fetch(
                working_directory=working_directory,
                url=args.get("url", ""),
                prompt=args.get("prompt", "Summarise the key information on this page."),
                max_chars=int(args.get("max_chars", 8000)),
            )

        # ── search_code (grep) ────────────────────────────────────────────────
        elif function_name == "search_code":
            from func.grep_tool import search_code
            result = search_code(
                working_directory=working_directory,
                pattern=args.get("pattern", ""),
                path=args.get("path", "."),
                glob=args.get("glob"),
                file_type=args.get("file_type"),
                output_mode=args.get("output_mode", "files_with_matches"),
                context_before=int(args.get("context_before", 0)),
                context_after=int(args.get("context_after", 0)),
                context=int(args.get("context", 0)),
                case_insensitive=bool(args.get("case_insensitive", False)),
                show_line_numbers=bool(args.get("show_line_numbers", True)),
                multiline=bool(args.get("multiline", False)),
                head_limit=int(args.get("head_limit", 250)),
                offset=int(args.get("offset", 0)),
            )

        # ── get_project_map ───────────────────────────────────────────────────
        elif function_name == "get_project_map":
            from func.project_map import get_project_map
            result = get_project_map(
                working_directory=working_directory,
                path=args.get("path", "."),
                depth=int(args.get("depth", 3)),
                include_dependencies=bool(args.get("include_dependencies", True)),
                include_data_flow=bool(args.get("include_data_flow", True)),
                focus=args.get("focus"),
            )

        # ── verify_change ─────────────────────────────────────────────────────
        elif function_name == "verify_change":
            from func.verify_change import verify_change
            result = verify_change(
                working_directory,
                scope=args.get("scope", "lint"),
            )

        # ── codespace tools (guarded) ─────────────────────────────────────────
        elif function_name in ("cs_run_shell", "cs_write_file", "cs_read_file",
                               "cs_patch_file", "cs_list_files", "cs_run_python",
                               "cs_connect", "cs_create", "cs_status",
                               "cs_list_codespaces"):
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

        # ── unknown ───────────────────────────────────────────────────────────
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

    finally:
        # ── Recording hook: END ───────────────────────────────────────────────
        # Always fires — captures result, duration, success/failure.
        # No-op if no session is active or module not installed.
        if _call_index is not None:
            try:
                from func.sys_agent_recording import hook_tool_result
                _duration_ms = (time.perf_counter() - _t0) * 1000
                hook_tool_result(_call_index, function_name, str(result), _duration_ms)
            except ImportError:
                pass

    # ── Return to AI ──────────────────────────────────────────────────────────
    return types.Content(
        role="user",
        parts=[types.Part(
            function_response=types.FunctionResponse(
                name=function_name,
                response={"result": result}
            )
        )]
    )
