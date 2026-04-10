"""
PathGuard — Sandbox for AI agent file access.

Drop this next to main.py, then import PathGuard everywhere
a file path is used (call_function.py, func/*.py wrappers).

Usage:
    from path_guard import guard
    safe_path = guard.resolve("some/path.py")   # raises GuardError if blocked
"""

from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Optional


# ── Configurable ─────────────────────────────────────────────────────────────

# Directories the AI is ALLOWED to read/write (relative to CWD at startup).
# Empty list = entire CWD is allowed (minus the deny list below).
ALLOWED_ROOTS: list[str] = [
    # "src",
    # "backend",
]

# Patterns that are ALWAYS blocked, regardless of ALLOWED_ROOTS.
DENY_PATTERNS: list[re.Pattern] = [
    re.compile(r"(^|[\\/])\.env(\.[^.\\/]+)?$"),          # .env  .env.local
    re.compile(r"(^|[\\/])\.git[\\/]"),                    # .git/ internals
    re.compile(r"(^|[\\/])node_modules[\\/]"),             # node_modules
    re.compile(r"(^|[\\/])__pycache__[\\/]"),              # bytecode
    re.compile(r"(^|[\\/])\.venv[\\/]"),                   # virtualenv
    re.compile(r"(^|[\\/])venv[\\/]"),                     # virtualenv alt
    re.compile(r"\.pyc$"),                                 # compiled python
    re.compile(r"(^|[\\/])sessions[\\/]"),                 # agent sessions
    re.compile(r"(^|[\\/])logs[\\/]"),                     # agent logs
    re.compile(r"(secret|password|passwd|credentials)",    # credential files
               re.IGNORECASE),
    re.compile(r"id_rsa|id_ed25519|\.pem$|\.key$"),        # private keys
]

# Extensions blocked from being written (read-only for AI).
WRITE_BLOCKED_EXTENSIONS: set[str] = {
    ".env", ".key", ".pem", ".pfx", ".crt", ".p12",
}


class GuardError(PermissionError):
    """Raised when a path violates the sandbox policy."""


class PathGuard:
    def __init__(self, cwd: Optional[str] = None):
        self.cwd = Path(cwd or os.getcwd()).resolve()
        self._allowed: list[Path] = (
            [self.cwd / r for r in ALLOWED_ROOTS]
            if ALLOWED_ROOTS else [self.cwd]
        )

    # ── Public API ───────────────────────────────────────────────────────────

    def resolve(self, raw: str, *, write: bool = False) -> Path:
        """
        Resolve *raw* to an absolute Path, enforcing all sandbox rules.
        Returns the resolved path on success, raises GuardError on violation.
        """
        path = self._to_abs(raw)
        self._check_traversal(path)
        self._check_deny_patterns(raw, path)
        self._check_allowed_roots(path)
        if write:
            self._check_write_blocked(path)
        return path

    def is_safe(self, raw: str, *, write: bool = False) -> bool:
        """Non-raising variant — returns True if resolve() would succeed."""
        try:
            self.resolve(raw, write=write)
            return True
        except GuardError:
            return False

    def relative(self, path: Path) -> str:
        """Return a display-friendly relative path string."""
        try:
            return str(path.relative_to(self.cwd))
        except ValueError:
            return str(path)

    # ── Internal checks ──────────────────────────────────────────────────────

    def _to_abs(self, raw: str) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = self.cwd / p
        return p.resolve()

    def _check_traversal(self, path: Path):
        try:
            path.relative_to(self.cwd)
        except ValueError:
            raise GuardError(
                f"🔒 Path escapes working directory: {path}\n"
                f"   Allowed root: {self.cwd}"
            )

    def _check_deny_patterns(self, raw: str, path: Path):
        check = raw.replace("\\", "/")
        for pattern in DENY_PATTERNS:
            if pattern.search(check) or pattern.search(str(path)):
                raise GuardError(
                    f"🔒 Path is blocked by security policy: {raw}"
                )

    def _check_allowed_roots(self, path: Path):
        if not self._allowed:
            return
        for root in self._allowed:
            try:
                path.relative_to(root)
                return  # found a valid root
            except ValueError:
                continue
        roots = ", ".join(str(r.relative_to(self.cwd)) for r in self._allowed)
        raise GuardError(
            f"🔒 Path is outside allowed directories: {self.relative(path)}\n"
            f"   Allowed: {roots}"
        )

    def _check_write_blocked(self, path: Path):
        if path.suffix.lower() in WRITE_BLOCKED_EXTENSIONS:
            raise GuardError(
                f"🔒 Write-blocked extension: {path.suffix} ({self.relative(path)})"
            )


# ── Module-level singleton (import and use directly) ─────────────────────────
guard = PathGuard()