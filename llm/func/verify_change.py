"""
verify_change.py – run lint, tests, or build based on scope
"""

import subprocess
import os
from google.genai import types

def verify_change(working_directory: str, scope: str = "lint") -> str:
    """
    scope: 'lint', 'test', 'build', or 'all'
    """
    cwd = os.path.abspath(working_directory)
    results = []

    if scope in ("lint", "all"):
        # Try common linters
        if os.path.exists(os.path.join(cwd, "package.json")):
            cmd = ["npm", "run", "lint", "--", "--fix"]
        elif os.path.exists(os.path.join(cwd, "pyproject.toml")):
            cmd = ["ruff", "check", "."]
        else:
            cmd = None
        if cmd:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
            results.append(f"Lint:\n{proc.stdout[:500]}{proc.stderr[:500]}")

    if scope in ("test", "all"):
        # Detect test command
        if os.path.exists(os.path.join(cwd, "package.json")):
            cmd = ["npm", "test"]
        elif os.path.exists(os.path.join(cwd, "pytest.ini")) or os.path.exists(os.path.join(cwd, "pyproject.toml")):
            cmd = ["pytest", "-q"]
        else:
            cmd = None
        if cmd:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
            results.append(f"Tests:\n{proc.stdout[:500]}{proc.stderr[:500]}")

    if scope in ("build", "all"):
        if os.path.exists(os.path.join(cwd, "Makefile")):
            cmd = ["make", "build"]
        elif os.path.exists(os.path.join(cwd, "package.json")):
            cmd = ["npm", "run", "build"]
        else:
            cmd = None
        if cmd:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180)
            results.append(f"Build:\n{proc.stdout[:500]}{proc.stderr[:500]}")

    if not results:
        return "No verification steps found for this project."

    return "\n\n".join(results)


schema_verify_change = types.FunctionDeclaration(
    name="verify_change",
    description="Run linting, tests, or build to verify recent changes.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "scope": types.Schema(
                type=types.Type.STRING,
                description="One of: 'lint', 'test', 'build', 'all'",
            ),
        },
        required=["scope"],
    ),
)