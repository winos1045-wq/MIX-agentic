"""
func/task_decomposer.py — Hierarchical Task Decomposer for SDX Agent

The planning brain. Turns a vague natural language goal into a structured,
executable tree of atomic subtasks with dependency graph.

Implements:
  - Orchestrator-Workers pattern (Anthropic)
  - ADAPT strategy: decompose only as far as needed, re-evaluate after each step
  - HTN (Hierarchical Task Network): recursive decomposition until atomic
  - DAG dependency resolution: parallel-safe execution ordering

Strategies:
  sequential  → strict order chain
  parallel    → independent batch
  dag         → dependency graph (default for complex tasks)
  adaptive    → built at runtime, each step informs the next

Output:
  - Structured JSON plan saved to plans/
  - Human-readable tree printed to console
  - Execution queue in dependency order
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Schema ────────────────────────────────────────────────────────────────────

schema_task_decomposer = {
    "name": "task_decomposer",
    "description": (
        "Decompose a complex or ambiguous task into a structured tree of atomic, "
        "executable subtasks with a dependency graph. "
        "Use this BEFORE starting any multi-step task. "
        "Returns a plan with execution order, assigned tools, and parallelism hints. "
        "Strategies: 'sequential' (strict order), 'parallel' (independent batch), "
        "'dag' (dependency graph, default), 'adaptive' (dynamic, built at runtime)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "The main task in natural language. Can be vague or complex."
            },
            "context": {
                "type": "object",
                "description": (
                    "Current project state to inform decomposition. "
                    "Keys: available_tools, completed_subtasks, project_type, "
                    "relevant_files, constraints."
                )
            },
            "strategy": {
                "type": "string",
                "enum": ["sequential", "parallel", "dag", "adaptive"],
                "description": (
                    "sequential: strict order. "
                    "parallel: run all at once. "
                    "dag: dependency graph (best for most tasks). "
                    "adaptive: dynamic, next steps decided after each result."
                ),
                "default": "dag"
            },
            "depth_level": {
                "type": "integer",
                "description": "Max recursion depth (1-5). Default: 3.",
                "default": 3
            },
            "save_plan": {
                "type": "boolean",
                "description": "Save plan to plans/ directory. Default: true.",
                "default": True
            }
        },
        "required": ["task_description"]
    }
}


# ── Available tools registry (what the AI can assign subtasks to) ─────────────

TOOL_REGISTRY = {
    # Discovery
    "get_project_map":    "Understand codebase structure",
    "search_code":        "Find code by pattern/regex",
    "get_file_content":   "Read a specific file or range",
    "get_files_info":     "List directory contents",
    # Editing
    "patch_file":         "Edit existing file (preferred)",
    "write_file":         "Create new file",
    "create_directory":   "Create directory",
    "rename_file":        "Rename/move a file",
    "delete_file":        "Delete a file",
    # Execution
    "run_shell":          "Run shell command",
    "run_python_file":    "Execute a Python script",
    "build_project":      "Build/scaffold project",
    "install_dependencies": "Install packages",
    # Verification
    "verify_change":      "Lint + test + build check",
    "run_tests":          "Run test suite",
    # Intelligence
    "web_search":         "Search the web",
    "web_fetch":          "Fetch a URL",
    "plan_project":       "Generate project plan",
    # Git
    "get_git_diff":       "Show git diff",
    "get_git_log":        "Show commit history",
}

# Complexity keywords → estimated complexity
_COMPLEXITY_HIGH   = {"refactor", "migrate", "architect", "rewrite", "implement", "build", "create", "add feature", "integrate"}
_COMPLEXITY_MEDIUM = {"fix", "update", "modify", "improve", "optimize", "add", "change"}
_COMPLEXITY_LOW    = {"read", "list", "show", "explain", "find", "search", "check"}

# Task type → likely tools needed
_TASK_TOOL_MAP: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"test|spec|coverage",      re.I), ["search_code", "run_tests", "write_file", "verify_change"]),
    (re.compile(r"bug|fix|error|crash",     re.I), ["search_code", "get_file_content", "patch_file", "verify_change"]),
    (re.compile(r"refactor|rename|move",    re.I), ["search_code", "patch_file", "rename_file", "verify_change"]),
    (re.compile(r"api|endpoint|route",      re.I), ["get_project_map", "search_code", "patch_file", "write_file"]),
    (re.compile(r"auth|login|jwt|oauth",    re.I), ["search_code", "get_file_content", "patch_file", "write_file"]),
    (re.compile(r"database|schema|model|migration", re.I), ["search_code", "write_file", "run_shell"]),
    (re.compile(r"install|setup|config",    re.I), ["install_dependencies", "write_file", "run_shell"]),
    (re.compile(r"build|deploy|docker",     re.I), ["build_project", "run_shell", "write_file"]),
    (re.compile(r"search|find|where",       re.I), ["search_code", "get_project_map"]),
    (re.compile(r"document|readme|comment", re.I), ["search_code", "get_file_content", "patch_file"]),
    (re.compile(r"ui|component|style|css",  re.I), ["search_code", "get_file_content", "patch_file", "write_file"]),
    (re.compile(r"perf|optim|slow|speed",   re.I), ["search_code", "get_file_content", "patch_file", "verify_change"]),
]


# ── Public entry point ────────────────────────────────────────────────────────

def task_decomposer(
    working_directory: str,
    task_description: str,
    context: Optional[dict] = None,
    strategy: str = "dag",
    depth_level: int = 3,
    save_plan: bool = True,
) -> str:
    ctx = context or {}

    # ── Step 1: Analyse the task ──────────────────────────────────────────────
    analysis = _analyse_task(task_description, ctx)

    # ── Step 2: Decompose into subtasks ───────────────────────────────────────
    subtasks = _decompose(task_description, analysis, depth_level, strategy, ctx)

    # ── Step 3: Build dependency graph ────────────────────────────────────────
    graph = _build_dependency_graph(subtasks, strategy)

    # ── Step 4: Resolve execution order ───────────────────────────────────────
    exec_order = _topological_sort(graph, subtasks)

    # ── Step 5: Assign tools ──────────────────────────────────────────────────
    subtasks = _assign_tools(subtasks, task_description, ctx)

    # ── Step 6: Determine parallelism ─────────────────────────────────────────
    parallel_groups = _find_parallel_groups(graph, exec_order)

    # ── Assemble plan ─────────────────────────────────────────────────────────
    plan = {
        "metadata": {
            "task":             task_description,
            "strategy":         strategy,
            "complexity":       analysis["complexity"],
            "total_subtasks":   len(subtasks),
            "depth_used":       analysis["depth_needed"],
            "created_at":       datetime.now().isoformat(),
            "replan_triggers":  ["tool_failure", "unexpected_output", "test_failure"],
            "estimated_tools":  analysis["likely_tools"],
        },
        "subtasks":         subtasks,
        "dependency_graph": graph,
        "execution_order":  exec_order,
        "parallel_groups":  parallel_groups,
    }

    # ── Save plan ─────────────────────────────────────────────────────────────
    plan_file = None
    if save_plan:
        plan_file = _save_plan(plan, task_description, working_directory)

    # ── Format output ─────────────────────────────────────────────────────────
    return _format_output(plan, plan_file)


# ── Task analysis ─────────────────────────────────────────────────────────────

def _analyse_task(task: str, ctx: dict) -> dict:
    task_lower = task.lower()

    # Complexity
    if any(kw in task_lower for kw in _COMPLEXITY_HIGH):
        complexity = "HIGH"
        depth_needed = 3
    elif any(kw in task_lower for kw in _COMPLEXITY_MEDIUM):
        complexity = "MEDIUM"
        depth_needed = 2
    else:
        complexity = "LOW"
        depth_needed = 1

    # Override if context says completed_subtasks already exist
    if ctx.get("completed_subtasks"):
        depth_needed = max(1, depth_needed - 1)

    # Likely tools from task keywords
    likely_tools: list[str] = []
    for pattern, tools in _TASK_TOOL_MAP:
        if pattern.search(task):
            likely_tools.extend(t for t in tools if t not in likely_tools)

    # Default: always start with discovery
    if not likely_tools:
        likely_tools = ["get_project_map", "search_code", "patch_file"]

    # Detect if task is already atomic
    is_atomic = (
        complexity == "LOW" and
        len(task.split()) < 10 and
        not any(conj in task_lower for conj in [" and ", " then ", " after ", " also ", " plus "])
    )

    return {
        "complexity":    complexity,
        "depth_needed":  depth_needed,
        "likely_tools":  likely_tools[:6],
        "is_atomic":     is_atomic,
        "has_deps":      any(w in task_lower for w in ["after", "before", "then", "once", "when"]),
        "is_parallel":   any(w in task_lower for w in ["and", "also", "both", "all", "simultaneously"]),
    }


# ── Decomposition engine ──────────────────────────────────────────────────────

def _decompose(
    task: str,
    analysis: dict,
    max_depth: int,
    strategy: str,
    ctx: dict,
) -> list[dict]:
    """
    Build the subtask list using template patterns matched to task type.
    In production this would call an LLM — here we use deterministic
    pattern matching so the tool works without an extra API call.
    The plan_project tool (which DOES call an LLM) handles open-ended planning;
    task_decomposer handles structured, typed decomposition.
    """
    task_lower = task.lower()

    # ── Pattern: Bug fix ──────────────────────────────────────────────────────
    if re.search(r"fix|bug|error|crash|broken|issue", task_lower):
        return _pattern_bug_fix(task, analysis)

    # ── Pattern: New feature / implementation ─────────────────────────────────
    if re.search(r"add|implement|create|build|new feature|integrate", task_lower):
        return _pattern_new_feature(task, analysis)

    # ── Pattern: Refactor ─────────────────────────────────────────────────────
    if re.search(r"refactor|restructure|reorgani[sz]e|clean up|rewrite", task_lower):
        return _pattern_refactor(task, analysis)

    # ── Pattern: Testing ──────────────────────────────────────────────────────
    if re.search(r"test|spec|coverage|unit test|integration test", task_lower):
        return _pattern_testing(task, analysis)

    # ── Pattern: Configuration / setup ───────────────────────────────────────
    if re.search(r"config|setup|install|docker|deploy|env", task_lower):
        return _pattern_setup(task, analysis)

    # ── Pattern: Documentation ────────────────────────────────────────────────
    if re.search(r"document|readme|comment|docstring", task_lower):
        return _pattern_documentation(task, analysis)

    # ── Generic fallback ──────────────────────────────────────────────────────
    return _pattern_generic(task, analysis)


def _pattern_bug_fix(task: str, analysis: dict) -> list[dict]:
    return [
        _st("1", "Map codebase & understand structure",
            tool="get_project_map", atomic=True,
            output="Architecture overview",
            deps=[]),
        _st("2", f"Search for the bug location: {_extract_subject(task)}",
            tool="search_code", atomic=True,
            output="File + line number of the bug",
            deps=["1"],
            notes="Use output_mode='content' with context=5"),
        _st("3", "Read the relevant code section",
            tool="get_file_content", atomic=True,
            output="Buggy code in context",
            deps=["2"],
            notes="Use start_line/end_line from step 2 result"),
        _st("4", "Understand the root cause",
            tool=None, atomic=True,
            output="Root cause identified (reasoning step)",
            deps=["3"],
            notes="Think before patching — check callers and dependencies"),
        _st("5", "Apply the fix",
            tool="patch_file", atomic=True,
            output="File patched",
            deps=["4"]),
        _st("6", "Verify: run tests + lint",
            tool="verify_change", atomic=True,
            output="Green tests / lint pass",
            deps=["5"],
            notes="If red → return to step 4"),
    ]


def _pattern_new_feature(task: str, analysis: dict) -> list[dict]:
    subject = _extract_subject(task)
    return [
        _st("1", "Map codebase — understand architecture",
            tool="get_project_map", atomic=True,
            output="Project type, entry points, data flow",
            deps=[]),
        _st("2", f"Find relevant existing code for: {subject}",
            tool="search_code", atomic=True,
            output="Related files and patterns",
            deps=["1"]),
        _st("3", "Read key files for context",
            tool="get_file_content", atomic=True,
            output="Patterns and conventions understood",
            deps=["2"],
            notes="Read only files directly related to the feature area"),
        _st("4", "Design the implementation plan",
            tool=None, atomic=True,
            output="List of files to create/modify and what changes",
            deps=["3"],
            notes="Reasoning step — no tool call needed"),
        _st("5a", "Create new files (if required)",
            tool="write_file", atomic=True,
            output="New files created",
            deps=["4"],
            can_parallel_with=["5b"]),
        _st("5b", "Modify existing files",
            tool="patch_file", atomic=True,
            output="Existing files updated",
            deps=["4"],
            can_parallel_with=["5a"]),
        _st("6", "Wire up: imports, routes, exports, registrations",
            tool="patch_file", atomic=True,
            output="Feature connected to the rest of the codebase",
            deps=["5a", "5b"]),
        _st("7", "Verify: lint + tests",
            tool="verify_change", atomic=True,
            output="Feature works, no regressions",
            deps=["6"]),
    ]


def _pattern_refactor(task: str, analysis: dict) -> list[dict]:
    return [
        _st("1", "Map current structure",
            tool="get_project_map", atomic=True,
            output="Current architecture",
            deps=[]),
        _st("2", "Search for all usages of the target",
            tool="search_code", atomic=True,
            output="All files and lines affected",
            deps=["1"],
            notes="Use output_mode='files_with_matches' first, then 'content'"),
        _st("3", "Snapshot: check git diff baseline",
            tool="get_git_diff", atomic=True,
            output="Clean working tree confirmed",
            deps=[]),
        _st("4", "Apply refactor changes",
            tool="patch_file", atomic=True,
            output="Code restructured",
            deps=["2", "3"],
            notes="Use patch_file, not write_file — preserves surrounding code"),
        _st("5", "Update all import/reference sites",
            tool="patch_file", atomic=True,
            output="All references updated",
            deps=["4"],
            notes="Search for old name again to confirm nothing missed"),
        _st("6", "Verify: full test suite",
            tool="verify_change", atomic=True,
            output="No regressions",
            deps=["5"]),
    ]


def _pattern_testing(task: str, analysis: dict) -> list[dict]:
    return [
        _st("1", "Find the code to test",
            tool="search_code", atomic=True,
            output="Target functions/classes located",
            deps=[]),
        _st("2", "Read the implementation",
            tool="get_file_content", atomic=True,
            output="Function signatures and behaviour understood",
            deps=["1"]),
        _st("3", "Check existing test patterns",
            tool="search_code", atomic=True,
            output="Test conventions and fixtures understood",
            deps=["1"],
            notes="Search in tests/ or __tests__/ directory"),
        _st("4", "Write test cases",
            tool="write_file", atomic=True,
            output="Test file created",
            deps=["2", "3"],
            notes="Cover: happy path, edge cases, error cases"),
        _st("5", "Run tests",
            tool="run_tests", atomic=True,
            output="All tests pass",
            deps=["4"]),
        _st("6", "Fix failures if any",
            tool="patch_file", atomic=True,
            output="Tests green",
            deps=["5"],
            notes="Only if step 5 had failures"),
    ]


def _pattern_setup(task: str, analysis: dict) -> list[dict]:
    return [
        _st("1", "Read existing config/environment",
            tool="get_file_content", atomic=True,
            output="Current config understood",
            deps=[],
            notes="Check .env.example, config files, Dockerfile"),
        _st("2", "Install/update dependencies",
            tool="install_dependencies", atomic=True,
            output="Dependencies installed",
            deps=["1"]),
        _st("3", "Create/update config files",
            tool="write_file", atomic=True,
            output="Config files in place",
            deps=["2"]),
        _st("4", "Apply environment changes",
            tool="patch_file", atomic=True,
            output="Environment updated",
            deps=["3"]),
        _st("5", "Verify setup works",
            tool="run_shell", atomic=True,
            output="Service starts / build succeeds",
            deps=["4"],
            notes="Run the actual start command to confirm"),
    ]


def _pattern_documentation(task: str, analysis: dict) -> list[dict]:
    return [
        _st("1", "Map codebase structure",
            tool="get_project_map", atomic=True,
            output="Project overview",
            deps=[]),
        _st("2", "Read key files to document",
            tool="get_file_content", atomic=True,
            output="Code understood",
            deps=["1"]),
        _st("3", "Write documentation",
            tool="write_file", atomic=True,
            output="Docs written",
            deps=["2"]),
        _st("4", "Add inline docstrings/comments",
            tool="patch_file", atomic=True,
            output="Code annotated",
            deps=["2"]),
    ]


def _pattern_generic(task: str, analysis: dict) -> list[dict]:
    return [
        _st("1", "Understand the codebase context",
            tool="get_project_map", atomic=True,
            output="Project overview",
            deps=[]),
        _st("2", "Locate relevant code",
            tool="search_code", atomic=True,
            output="Relevant files found",
            deps=["1"]),
        _st("3", "Read relevant files",
            tool="get_file_content", atomic=True,
            output="Context understood",
            deps=["2"]),
        _st("4", f"Execute: {task}",
            tool="patch_file", atomic=True,
            output="Task complete",
            deps=["3"]),
        _st("5", "Verify result",
            tool="verify_change", atomic=True,
            output="No regressions",
            deps=["4"]),
    ]


# ── Subtask builder helper ────────────────────────────────────────────────────

def _st(
    id: str,
    title: str,
    tool: Optional[str],
    atomic: bool,
    output: str,
    deps: list[str],
    notes: str = "",
    can_parallel_with: Optional[list[str]] = None,
) -> dict:
    return {
        "id":                 id,
        "title":              title,
        "is_atomic":          atomic,
        "assigned_tool":      tool,
        "expected_output":    output,
        "dependencies":       deps,
        "notes":              notes,
        "can_parallel_with":  can_parallel_with or [],
        "status":             "pending",
    }


# ── Dependency graph ──────────────────────────────────────────────────────────

def _build_dependency_graph(subtasks: list[dict], strategy: str) -> dict:
    graph: dict[str, list[str]] = {}
    for st in subtasks:
        graph[st["id"]] = st.get("dependencies", [])

    if strategy == "parallel":
        # All tasks independent
        for k in graph:
            graph[k] = []
    elif strategy == "sequential":
        # Each task depends on the previous
        ids = [st["id"] for st in subtasks]
        for i, sid in enumerate(ids):
            graph[sid] = [ids[i - 1]] if i > 0 else []

    return graph


def _topological_sort(graph: dict, subtasks: list[dict]) -> list[str]:
    """Kahn's algorithm — returns execution order respecting dependencies."""
    in_degree: dict[str, int] = {k: 0 for k in graph}
    for deps in graph.values():
        for d in deps:
            if d in in_degree:
                in_degree[d] = in_degree.get(d, 0)
            # d is a prerequisite, increment target's in_degree
    # rebuild correctly
    in_degree = {k: 0 for k in graph}
    for node, deps in graph.items():
        for _ in deps:
            pass  # deps are prerequisites, node depends on them
    # in_degree[node] = how many prerequisites does node have
    in_degree = {sid: len(deps) for sid, deps in graph.items()}

    ready = [sid for sid, deg in in_degree.items() if deg == 0]
    order: list[str] = []

    while ready:
        ready.sort()  # deterministic
        node = ready.pop(0)
        order.append(node)
        # Find nodes that depend on `node`
        for sid, deps in graph.items():
            if node in deps:
                in_degree[sid] -= 1
                if in_degree[sid] == 0:
                    ready.append(sid)

    # Append any remaining (handles cycles gracefully)
    for sid in graph:
        if sid not in order:
            order.append(sid)

    return order


def _find_parallel_groups(graph: dict, exec_order: list[str]) -> list[list[str]]:
    """Group tasks that can run in parallel (same dependency level)."""
    levels: dict[str, int] = {}
    for sid in exec_order:
        deps = graph.get(sid, [])
        if not deps:
            levels[sid] = 0
        else:
            levels[sid] = max(levels.get(d, 0) for d in deps) + 1

    max_level = max(levels.values()) if levels else 0
    groups: list[list[str]] = []
    for level in range(max_level + 1):
        group = [sid for sid, lv in levels.items() if lv == level]
        if group:
            groups.append(sorted(group))
    return groups


# ── Tool assignment ───────────────────────────────────────────────────────────

def _assign_tools(subtasks: list[dict], task: str, ctx: dict) -> list[dict]:
    """Refine tool assignments based on task context."""
    available = ctx.get("available_tools", list(TOOL_REGISTRY.keys()))

    for st in subtasks:
        tool = st.get("assigned_tool")
        if tool and tool not in available:
            # Find closest available tool
            if tool == "verify_change" and "run_tests" in available:
                st["assigned_tool"] = "run_shell"
                st["notes"] = (st.get("notes", "") +
                               " [verify_change not available, use run_shell with test command]")
            elif tool == "get_git_diff" and "run_shell" in available:
                st["assigned_tool"] = "run_shell"
                st["notes"] = st.get("notes", "") + " [use: run_shell command='git diff']"

    return subtasks


# ── Output formatting ─────────────────────────────────────────────────────────

def _format_output(plan: dict, plan_file: Optional[str]) -> str:
    meta      = plan["metadata"]
    subtasks  = plan["subtasks"]
    groups    = plan["parallel_groups"]
    exec_order = plan["execution_order"]

    lines: list[str] = []

    # Header
    lines += [
        f"TASK DECOMPOSITION",
        f"{'─' * 60}",
        f"  Task        {meta['task'][:70]}",
        f"  Strategy    {meta['strategy']}",
        f"  Complexity  {meta['complexity']}",
        f"  Subtasks    {meta['total_subtasks']}",
        f"  Saved to    {plan_file or 'not saved'}",
        "",
    ]

    # Subtask tree
    lines.append("SUBTASK TREE")
    lines.append("─" * 60)

    subtask_map = {st["id"]: st for st in subtasks}
    for i, sid in enumerate(exec_order):
        st = subtask_map.get(sid, {})
        is_last  = (i == len(exec_order) - 1)
        prefix   = "└─" if is_last else "├─"
        tool_str = f"  [{st.get('assigned_tool') or 'reason'}]" if st.get("assigned_tool") else "  [think]"
        dep_str  = f"  (after {', '.join(st.get('dependencies', []))})" if st.get("dependencies") else ""
        par_str  = f"  ∥ parallel with {', '.join(st.get('can_parallel_with', []))}" if st.get("can_parallel_with") else ""

        lines.append(f"  {prefix} [{sid}] {st.get('title', '')}")
        lines.append(f"  │       {tool_str}{dep_str}{par_str}")
        if st.get("notes"):
            lines.append(f"  │       ↳ {st['notes']}")
        lines.append(f"  │       → {st.get('expected_output', '')}")
        if not is_last:
            lines.append("  │")

    # Parallel execution groups
    if any(len(g) > 1 for g in groups):
        lines += ["", "PARALLEL GROUPS", "─" * 60]
        for i, group in enumerate(groups):
            if len(group) > 1:
                lines.append(f"  Wave {i + 1}:  {' ∥ '.join(group)}  (run simultaneously)")
            else:
                lines.append(f"  Wave {i + 1}:  {group[0]}")

    # Execution order
    lines += [
        "",
        "EXECUTION ORDER",
        "─" * 60,
        "  " + "  →  ".join(exec_order),
        "",
        "REPLAN TRIGGERS",
        "─" * 60,
    ]
    for t in meta["replan_triggers"]:
        lines.append(f"  • {t}")

    lines += [
        "",
        f"→ Follow execution order above.",
        f"  After each subtask, check expected_output was achieved.",
        f"  On failure → stop, report, re-decompose the failed subtask.",
    ]

    return "\n".join(lines)


# ── Plan persistence ──────────────────────────────────────────────────────────

def _save_plan(plan: dict, task: str, working_directory: str) -> str:
    plans_dir = Path(working_directory) / "plans"
    plans_dir.mkdir(exist_ok=True)

    slug  = re.sub(r"[^\w]+", "_", task[:40]).strip("_").lower()
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"decomp_{slug}_{ts}.json"
    fpath = plans_dir / fname

    with open(fpath, "w") as f:
        json.dump(plan, f, indent=2)

    return str(fpath.relative_to(working_directory))


# ── Utilities ─────────────────────────────────────────────────────────────────

def _extract_subject(task: str) -> str:
    """Extract the main noun/subject from a task description."""
    task = re.sub(r"^(fix|add|implement|create|build|refactor|update|improve)\s+", "", task, flags=re.I)
    words = task.split()
    return " ".join(words[:5]) if words else task
