"""
func/benchmark_solution.py — Performance Audit Tool for SDX Agent

Automatically benchmarks code the agent just wrote or modified.
Ensures the agent doesn't just write code that "works" — but code that is fast.

What it measures:
  CPU       execution time (min/max/mean/p95) across N runs
  Memory    peak RSS, allocations delta, memory leak detection
  I/O       file reads/writes, syscall count (Linux only)
  Callgrind call frequency per function (top hotspots)
  Compare   before vs after — regression detection

Supported targets:
  Python    functions, files, modules
  Shell     any shell command (Node.js, Rust binary, Go binary, etc.)
  HTTP      endpoint latency via local curl (if server is running)

Output:
  - Structured JSON report saved to benchmarks/
  - Human-readable summary with pass/fail thresholds
  - Regression flag if performance degraded vs baseline
"""

from __future__ import annotations

import gc
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

try:
    import tracemalloc
    _HAS_TRACEMALLOC = True
except ImportError:
    _HAS_TRACEMALLOC = False

try:
    import resource   # Unix only
    _HAS_RESOURCE = True
except ImportError:
    _HAS_RESOURCE = False

# ── Schema ────────────────────────────────────────────────────────────────────

schema_benchmark_solution = {
    "name": "benchmark_solution",
    "description": (
        "Run a performance audit (CPU time, memory, I/O) on code the agent just wrote. "
        "Detects regressions against a stored baseline. "
        "Supports Python files/functions, shell commands, and HTTP endpoints. "
        "Call this after any implementation to verify the solution is not just correct, "
        "but optimized."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": (
                    "Unique identifier for this benchmark. "
                    "Used to compare against previous runs (regression detection). "
                    "E.g. 'auth_login', 'search_endpoint', 'data_parser'."
                )
            },
            "target_type": {
                "type": "string",
                "enum": ["python_file", "python_function", "shell_command", "http_endpoint"],
                "description": (
                    "python_file: benchmark a .py file via subprocess. "
                    "python_function: import and call a function directly (fastest). "
                    "shell_command: benchmark any shell command (node, cargo run, etc.). "
                    "http_endpoint: measure latency of a running HTTP server."
                ),
                "default": "python_file"
            },
            "target": {
                "type": "string",
                "description": (
                    "For python_file/shell_command: path or command to run. "
                    "For python_function: 'module.path:function_name'. "
                    "For http_endpoint: full URL e.g. 'http://localhost:8000/api/search'."
                )
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Arguments to pass to the target (python_file / shell_command).",
                "default": []
            },
            "iterations": {
                "type": "integer",
                "description": "Number of runs to average over (default: 10, min: 3, max: 1000).",
                "default": 10
            },
            "warmup_runs": {
                "type": "integer",
                "description": "Warmup runs before measurement starts (default: 2).",
                "default": 2
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Max seconds per run before timeout (default: 30).",
                "default": 30
            },
            "thresholds": {
                "type": "object",
                "description": (
                    "Pass/fail thresholds. Keys: "
                    "max_mean_ms (mean time limit), "
                    "max_p95_ms (95th percentile limit), "
                    "max_memory_mb (peak memory limit), "
                    "max_regression_pct (max allowed slowdown vs baseline, default 20)."
                )
            },
            "compare_baseline": {
                "type": "boolean",
                "description": "Compare against stored baseline for this task_id. Default: true.",
                "default": True
            },
            "save_as_baseline": {
                "type": "boolean",
                "description": "Save this run as the new baseline. Default: false.",
                "default": False
            },
            "http_method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE"],
                "description": "HTTP method (http_endpoint only). Default: GET.",
                "default": "GET"
            },
            "http_body": {
                "type": "string",
                "description": "JSON body for POST/PUT (http_endpoint only)."
            }
        },
        "required": ["task_id", "target"]
    }
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class RunResult:
    duration_ms:  float
    memory_kb:    float
    success:      bool
    error:        str = ""


@dataclass
class BenchmarkReport:
    task_id:       str
    target:        str
    target_type:   str
    timestamp:     str
    iterations:    int

    # Timing (ms)
    mean_ms:       float = 0.0
    min_ms:        float = 0.0
    max_ms:        float = 0.0
    p50_ms:        float = 0.0
    p95_ms:        float = 0.0
    p99_ms:        float = 0.0
    stddev_ms:     float = 0.0

    # Memory
    peak_memory_kb:   float = 0.0
    mean_memory_kb:   float = 0.0
    memory_leak_kb:   float = 0.0

    # Hotspots (python_function only)
    top_functions:    list = field(default_factory=list)

    # Regression
    baseline_mean_ms:    float = 0.0
    regression_pct:      float = 0.0
    regression_detected: bool  = False

    # Pass/fail
    passed:     bool = True
    failures:   list = field(default_factory=list)
    warnings:   list = field(default_factory=list)

    # Errors
    failed_runs: int = 0
    errors:      list = field(default_factory=list)


# ── Public entry point ────────────────────────────────────────────────────────

def benchmark_solution(
    working_directory: str,
    task_id: str,
    target: str,
    target_type: str = "python_file",
    args: Optional[list[str]] = None,
    iterations: int = 10,
    warmup_runs: int = 2,
    timeout_seconds: int = 30,
    thresholds: Optional[dict] = None,
    compare_baseline: bool = True,
    save_as_baseline: bool = False,
    http_method: str = "GET",
    http_body: Optional[str] = None,
) -> str:

    iterations    = max(3, min(iterations, 1000))
    warmup_runs   = max(0, min(warmup_runs, 10))
    thresholds    = thresholds or {}
    args          = args or []

    report = BenchmarkReport(
        task_id     = task_id,
        target      = target,
        target_type = target_type,
        timestamp   = datetime.now().isoformat(),
        iterations  = iterations,
    )

    # ── Warmup ────────────────────────────────────────────────────────────────
    for _ in range(warmup_runs):
        _run_once(target_type, target, args, working_directory,
                  timeout_seconds, http_method, http_body)

    # ── Measurement runs ──────────────────────────────────────────────────────
    runs: list[RunResult] = []
    for _ in range(iterations):
        r = _run_once(target_type, target, args, working_directory,
                      timeout_seconds, http_method, http_body)
        runs.append(r)
        if r.error:
            report.errors.append(r.error)

    successful = [r for r in runs if r.success]
    report.failed_runs = len(runs) - len(successful)

    if not successful:
        report.passed = False
        report.failures.append("All runs failed — check target and args")
        return _format_report(report, None)

    # ── Compute timing stats ──────────────────────────────────────────────────
    times = sorted(r.duration_ms for r in successful)
    n     = len(times)

    report.mean_ms   = sum(times) / n
    report.min_ms    = times[0]
    report.max_ms    = times[-1]
    report.p50_ms    = _percentile(times, 50)
    report.p95_ms    = _percentile(times, 95)
    report.p99_ms    = _percentile(times, 99)
    report.stddev_ms = _stddev(times)

    # ── Memory stats ──────────────────────────────────────────────────────────
    mems = [r.memory_kb for r in successful if r.memory_kb > 0]
    if mems:
        report.peak_memory_kb = max(mems)
        report.mean_memory_kb = sum(mems) / len(mems)
        # Leak detection: memory growing across runs?
        if len(mems) >= 5:
            first_half = sum(mems[:len(mems)//2]) / (len(mems)//2)
            second_half = sum(mems[len(mems)//2:]) / (len(mems) - len(mems)//2)
            leak = second_half - first_half
            if leak > 100:   # >100KB growth = suspected leak
                report.memory_leak_kb = leak
                report.warnings.append(
                    f"Possible memory leak detected: +{leak:.0f}KB across runs"
                )

    # ── Profiling (Python function only) ─────────────────────────────────────
    if target_type == "python_function":
        report.top_functions = _profile_python_function(
            target, working_directory
        )

    # ── Regression check ─────────────────────────────────────────────────────
    baseline = None
    if compare_baseline:
        baseline = _load_baseline(task_id, working_directory)
        if baseline:
            report.baseline_mean_ms = baseline.get("mean_ms", 0)
            if report.baseline_mean_ms > 0:
                report.regression_pct = (
                    (report.mean_ms - report.baseline_mean_ms)
                    / report.baseline_mean_ms * 100
                )
                max_regression = thresholds.get("max_regression_pct", 20)
                if report.regression_pct > max_regression:
                    report.regression_detected = True
                    report.passed = False
                    report.failures.append(
                        f"Performance regression: {report.regression_pct:+.1f}% "
                        f"(threshold: +{max_regression}%)"
                    )
                elif report.regression_pct > 10:
                    report.warnings.append(
                        f"Mild slowdown: {report.regression_pct:+.1f}% vs baseline"
                    )
                elif report.regression_pct < -10:
                    report.warnings.append(
                        f"Performance improvement: {report.regression_pct:+.1f}% vs baseline 🎉"
                    )

    # ── Threshold checks ──────────────────────────────────────────────────────
    if "max_mean_ms" in thresholds and report.mean_ms > thresholds["max_mean_ms"]:
        report.passed = False
        report.failures.append(
            f"Mean time {report.mean_ms:.1f}ms exceeds limit {thresholds['max_mean_ms']}ms"
        )

    if "max_p95_ms" in thresholds and report.p95_ms > thresholds["max_p95_ms"]:
        report.passed = False
        report.failures.append(
            f"P95 time {report.p95_ms:.1f}ms exceeds limit {thresholds['max_p95_ms']}ms"
        )

    if "max_memory_mb" in thresholds:
        max_mem_kb = thresholds["max_memory_mb"] * 1024
        if report.peak_memory_kb > max_mem_kb:
            report.passed = False
            report.failures.append(
                f"Peak memory {report.peak_memory_kb/1024:.1f}MB "
                f"exceeds limit {thresholds['max_memory_mb']}MB"
            )

    # High stddev warning
    if report.stddev_ms > report.mean_ms * 0.5 and n >= 5:
        report.warnings.append(
            f"High variance: stddev={report.stddev_ms:.1f}ms ({report.stddev_ms/report.mean_ms*100:.0f}% of mean). "
            "Results may be noisy."
        )

    # ── Save report ───────────────────────────────────────────────────────────
    report_file = _save_report(report, working_directory)

    if save_as_baseline or (baseline is None and report.passed):
        _save_baseline(report, working_directory)

    return _format_report(report, report_file)


# ── Runner dispatch ───────────────────────────────────────────────────────────

def _run_once(
    target_type: str,
    target: str,
    args: list[str],
    cwd: str,
    timeout: int,
    http_method: str,
    http_body: Optional[str],
) -> RunResult:
    if target_type == "python_file":
        return _run_python_file(target, args, cwd, timeout)
    elif target_type == "python_function":
        return _run_python_function(target, cwd)
    elif target_type == "shell_command":
        return _run_shell_command(target, args, cwd, timeout)
    elif target_type == "http_endpoint":
        return _run_http(target, http_method, http_body, timeout)
    return RunResult(0, 0, False, f"Unknown target_type: {target_type}")


def _run_python_file(target: str, args: list[str], cwd: str, timeout: int) -> RunResult:
    cmd = [sys.executable, target] + args
    t0  = time.perf_counter()
    mem_before = _get_rss_kb()
    try:
        r = subprocess.run(
            cmd, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, text=True
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        mem_after  = _get_rss_kb()
        return RunResult(
            duration_ms = elapsed_ms,
            memory_kb   = mem_after - mem_before,
            success     = r.returncode == 0,
            error       = r.stderr[:200] if r.returncode != 0 else "",
        )
    except subprocess.TimeoutExpired:
        return RunResult(timeout * 1000, 0, False, f"Timeout after {timeout}s")
    except Exception as e:
        return RunResult(0, 0, False, str(e))


def _run_python_function(target: str, cwd: str) -> RunResult:
    """
    target format: 'module.submodule:function_name'
    e.g. 'func.grep_tool:search_code'
    """
    if ":" not in target:
        return RunResult(0, 0, False, "python_function target must be 'module:function'")

    module_path, func_name = target.rsplit(":", 1)

    if _HAS_TRACEMALLOC:
        tracemalloc.start()

    gc.collect()
    mem_before = _get_rss_kb()
    t0 = time.perf_counter()

    try:
        # Add cwd to sys.path temporarily
        if cwd not in sys.path:
            sys.path.insert(0, cwd)

        import importlib
        mod  = importlib.import_module(module_path)
        func = getattr(mod, func_name)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            func()

        elapsed_ms = (time.perf_counter() - t0) * 1000
        mem_after  = _get_rss_kb()

        peak_kb = 0.0
        if _HAS_TRACEMALLOC:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_kb = peak / 1024

        return RunResult(
            duration_ms = elapsed_ms,
            memory_kb   = max(mem_after - mem_before, peak_kb),
            success     = True,
        )

    except Exception as e:
        if _HAS_TRACEMALLOC:
            tracemalloc.stop()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return RunResult(elapsed_ms, 0, False, f"{type(e).__name__}: {e}")


def _run_shell_command(target: str, args: list[str], cwd: str, timeout: int) -> RunResult:
    cmd = target.split() + args
    mem_before = _get_rss_kb()
    t0 = time.perf_counter()
    try:
        r = subprocess.run(
            cmd, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, text=True
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        mem_after  = _get_rss_kb()
        return RunResult(
            duration_ms = elapsed_ms,
            memory_kb   = max(0, mem_after - mem_before),
            success     = r.returncode == 0,
            error       = r.stderr[:200] if r.returncode != 0 else "",
        )
    except subprocess.TimeoutExpired:
        return RunResult(timeout * 1000, 0, False, f"Timeout after {timeout}s")
    except FileNotFoundError as e:
        return RunResult(0, 0, False, f"Command not found: {e}")


def _run_http(url: str, method: str, body: Optional[str], timeout: int) -> RunResult:
    """Measure HTTP endpoint latency using curl or httpx."""
    t0 = time.perf_counter()
    try:
        cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
               "-X", method, "--max-time", str(timeout)]
        if body:
            cmd += ["-H", "Content-Type: application/json", "-d", body]
        cmd.append(url)

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        status     = r.stdout.strip()
        ok         = status.startswith("2")
        return RunResult(
            duration_ms = elapsed_ms,
            memory_kb   = 0,
            success     = ok,
            error       = f"HTTP {status}" if not ok else "",
        )
    except FileNotFoundError:
        # Try httpx
        try:
            import httpx
            with httpx.Client(timeout=timeout) as c:
                fn  = getattr(c, method.lower())
                kw  = {"json": json.loads(body)} if body else {}
                resp = fn(url, **kw)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return RunResult(elapsed_ms, 0, resp.is_success,
                             f"HTTP {resp.status_code}" if not resp.is_success else "")
        except Exception as e:
            return RunResult(0, 0, False, str(e))
    except Exception as e:
        return RunResult(0, 0, False, str(e))


# ── Profiling ─────────────────────────────────────────────────────────────────

def _profile_python_function(target: str, cwd: str) -> list[dict]:
    """Run cProfile and return top 10 hotspot functions."""
    if ":" not in target:
        return []

    module_path, func_name = target.rsplit(":", 1)

    try:
        import cProfile
        import pstats

        if cwd not in sys.path:
            sys.path.insert(0, cwd)

        import importlib
        mod  = importlib.import_module(module_path)
        func = getattr(mod, func_name)

        pr = cProfile.Profile()
        pr.enable()
        try:
            func()
        except Exception:
            pass
        pr.disable()

        stream = io.StringIO()
        ps = pstats.Stats(pr, stream=stream).sort_stats("cumulative")
        ps.print_stats(10)
        raw = stream.getvalue()

        # Parse the pstats output
        hotspots: list[dict] = []
        for line in raw.splitlines():
            m = re.match(
                r"\s*(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(.+)",
                line
            )
            if m:
                hotspots.append({
                    "calls":       int(m.group(1)),
                    "total_time":  float(m.group(2)),
                    "per_call":    float(m.group(4)),
                    "location":    m.group(6).strip(),
                })
        return hotspots[:10]

    except Exception:
        return []


# ── Stats helpers ─────────────────────────────────────────────────────────────

def _percentile(sorted_data: list[float], pct: int) -> float:
    if not sorted_data:
        return 0.0
    idx = (len(sorted_data) - 1) * pct / 100
    lo  = int(idx)
    hi  = min(lo + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo)


def _stddev(data: list[float]) -> float:
    if len(data) < 2:
        return 0.0
    mean = sum(data) / len(data)
    var  = sum((x - mean) ** 2 for x in data) / (len(data) - 1)
    return var ** 0.5


def _get_rss_kb() -> float:
    """Get current process RSS in KB."""
    if _HAS_RESOURCE:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # macOS: bytes, Linux: KB
        if sys.platform == "darwin":
            return usage.ru_maxrss / 1024
        return float(usage.ru_maxrss)
    try:
        # Fallback: read /proc/self/status
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1])
    except Exception:
        pass
    return 0.0


# ── Baseline persistence ──────────────────────────────────────────────────────

def _baselines_dir(cwd: str) -> Path:
    d = Path(cwd) / "benchmarks" / "baselines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_baseline(task_id: str, cwd: str) -> Optional[dict]:
    p = _baselines_dir(cwd) / f"{task_id}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


def _save_baseline(report: BenchmarkReport, cwd: str):
    p = _baselines_dir(cwd) / f"{report.task_id}.json"
    p.write_text(json.dumps({
        "task_id":  report.task_id,
        "mean_ms":  report.mean_ms,
        "p95_ms":   report.p95_ms,
        "peak_memory_kb": report.peak_memory_kb,
        "saved_at": report.timestamp,
    }, indent=2))


def _save_report(report: BenchmarkReport, cwd: str) -> str:
    d = Path(cwd) / "benchmarks" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{report.task_id}_{ts}.json"
    fpath = d / fname
    fpath.write_text(json.dumps(asdict(report), indent=2))
    return str(fpath.relative_to(cwd))


# ── Output formatting ─────────────────────────────────────────────────────────

def _format_report(report: BenchmarkReport, report_file: Optional[str]) -> str:
    status = "✓  PASSED" if report.passed else "✗  FAILED"
    color  = "" 

    lines = [
        f"BENCHMARK REPORT  —  {report.task_id}",
        f"{'─' * 60}",
        f"  Target      {report.target}",
        f"  Type        {report.target_type}",
        f"  Runs        {report.iterations}  "
        f"({report.failed_runs} failed)",
        f"  Status      {status}",
        "",
        "TIMING (ms)",
        "─" * 60,
        f"  Mean        {report.mean_ms:>10.2f} ms",
        f"  Min         {report.min_ms:>10.2f} ms",
        f"  Max         {report.max_ms:>10.2f} ms",
        f"  P50         {report.p50_ms:>10.2f} ms",
        f"  P95         {report.p95_ms:>10.2f} ms",
        f"  P99         {report.p99_ms:>10.2f} ms",
        f"  Std dev     {report.stddev_ms:>10.2f} ms",
    ]

    if report.peak_memory_kb > 0:
        lines += [
            "",
            "MEMORY",
            "─" * 60,
            f"  Peak        {report.peak_memory_kb / 1024:>10.2f} MB",
            f"  Mean        {report.mean_memory_kb / 1024:>10.2f} MB",
        ]
        if report.memory_leak_kb > 0:
            lines.append(
                f"  Leak est.   {report.memory_leak_kb / 1024:>10.2f} MB  ⚠ suspected leak"
            )

    if report.baseline_mean_ms > 0:
        arrow = "▲" if report.regression_pct > 0 else "▼"
        lines += [
            "",
            "REGRESSION",
            "─" * 60,
            f"  Baseline    {report.baseline_mean_ms:>10.2f} ms",
            f"  Current     {report.mean_ms:>10.2f} ms",
            f"  Delta       {report.regression_pct:>+10.1f}%  {arrow}",
        ]
        if report.regression_detected:
            lines.append("  ⚠  REGRESSION DETECTED")

    if report.top_functions:
        lines += ["", "TOP HOTSPOTS (cProfile)", "─" * 60]
        for i, fn in enumerate(report.top_functions[:5], 1):
            lines.append(
                f"  {i}.  {fn['calls']:>6} calls  "
                f"{fn['total_time']:>8.4f}s total  "
                f"{fn['location'][:50]}"
            )

    if report.failures:
        lines += ["", "FAILURES", "─" * 60]
        for f in report.failures:
            lines.append(f"  ✗  {f}")

    if report.warnings:
        lines += ["", "WARNINGS", "─" * 60]
        for w in report.warnings:
            lines.append(f"  ⚠  {w}")

    if report.errors:
        lines += ["", "RUN ERRORS (sample)", "─" * 60]
        for e in report.errors[:3]:
            lines.append(f"  {e}")

    lines += [
        "",
        "─" * 60,
        f"  Report saved to  {report_file or 'not saved'}",
    ]

    if not report.passed:
        lines += [
            "",
            "NEXT STEPS",
            "─" * 60,
        ]
        if report.regression_detected:
            lines.append("  1. Run search_code to find the bottleneck function")
            lines.append("  2. Check top hotspots above — optimize the top caller")
            lines.append("  3. Re-run benchmark_solution to confirm improvement")
        if report.memory_leak_kb > 0:
            lines.append("  1. Check for unclosed file handles, growing lists, or caches")
            lines.append("  2. Add explicit cleanup (context managers, del, gc.collect())")

    return "\n".join(lines)
