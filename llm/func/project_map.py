"""
func/project_map.py — Automated Architectural Mapping for SDX Agent

Gives the AI an instant "mental model" of any codebase without
reading individual files. One call replaces 10-20 get_files_info calls.

Output sections:
  - Project type & framework (auto-detected)
  - Entry points
  - Directory structure (smart, filtered)
  - Key files (config, routes, models, tests...)
  - Dependencies (parsed from lock/manifest files)
  - Data flow summary
  - Estimated token cost before the AI reads anything

Detection supports:
  Python  : FastAPI, Flask, Django, Poetry, pip
  Node.js : Next.js, Express, React, Vue, NestJS, npm/yarn/pnpm/bun
  Rust    : Cargo workspaces
  Go      : go.mod modules
  Java    : Maven, Gradle, Spring
  Generic : any project with a README / Makefile
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

try:
    from path_guard import guard, GuardError
    _GUARD = True
except ImportError:
    _GUARD = False

# ── Schema ────────────────────────────────────────────────────────────────────

schema_get_project_map = {
    "name": "get_project_map",
    "description": (
        "Analyze the project and return a high-level architectural map: "
        "framework, entry points, key files, dependency graph, and data flow. "
        "Call this ONCE at the start of any task on an unfamiliar codebase. "
        "Replaces dozens of get_files_info + get_file_content calls."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Root directory to map. Defaults to project root.",
                "default": "."
            },
            "depth": {
                "type": "integer",
                "description": "Directory tree depth to include (1-5). Default: 3.",
                "default": 3
            },
            "include_dependencies": {
                "type": "boolean",
                "description": "Parse and include dependency list. Default: true.",
                "default": True
            },
            "include_data_flow": {
                "type": "boolean",
                "description": "Include inferred data flow summary. Default: true.",
                "default": True
            },
            "focus": {
                "type": "string",
                "description": (
                    "Optional subdirectory or module to focus on. "
                    "Provides deeper analysis of that area. E.g. 'src/auth', 'backend/api'."
                )
            }
        },
        "required": []
    }
}


# ── Constants ─────────────────────────────────────────────────────────────────

IGNORE_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__",
    ".venv", "venv", "env", ".env",
    "dist", "build", ".next", ".nuxt", "out", "target",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "coverage", ".nyc_output", ".turbo",
    "sessions", "logs", "tmp", ".cache",
    "vendor",                    # Go / PHP
    ".gradle", ".idea", ".vscode",
}

IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll",
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".lock",                    # shown separately as dep files
}

# Key files that always get surfaced
KEY_FILE_PATTERNS = [
    # Entry points
    "main.py", "app.py", "run.py", "server.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts",
    "app.js", "app.ts", "cmd/main.go", "src/main.rs",
    # Config / manifest
    "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "Cargo.toml", "go.mod",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example",
    # Framework config
    "next.config.js", "next.config.ts", "next.config.mjs",
    "vite.config.js", "vite.config.ts",
    "nuxt.config.js", "nuxt.config.ts",
    "angular.json",
    "tailwind.config.js", "tailwind.config.ts",
    "tsconfig.json", "jsconfig.json",
    "webpack.config.js",
    "settings.py", "config.py", "config.yaml", "config.yml",
    # Tests
    "pytest.ini", "jest.config.js", "jest.config.ts", "vitest.config.ts",
    # CI
    ".github/workflows",
    # API / schema
    "openapi.yaml", "openapi.json", "swagger.yaml", "schema.graphql",
]

# Dependency files (in order of priority)
DEP_FILE_PRIORITY = [
    # Python
    ("pyproject.toml",    "python"),
    ("requirements.txt",  "python"),
    ("Pipfile",           "python"),
    # Node
    ("package.json",      "node"),
    # Rust
    ("Cargo.toml",        "rust"),
    # Go
    ("go.mod",            "go"),
    # Java
    ("pom.xml",           "java"),
    ("build.gradle",      "java"),
]

# Framework fingerprints: (file_or_dir, content_pattern, framework_name)
FRAMEWORK_FINGERPRINTS = [
    # Python
    ("manage.py",           None,                 "Django"),
    ("pyproject.toml",      r"fastapi",           "FastAPI"),
    ("requirements.txt",    r"fastapi",           "FastAPI"),
    ("pyproject.toml",      r"flask",             "Flask"),
    ("requirements.txt",    r"flask",             "Flask"),
    ("pyproject.toml",      r"django",            "Django"),
    ("requirements.txt",    r"django",            "Django"),
    ("pyproject.toml",      r"typer",             "Typer CLI"),
    # Node / TS
    ("next.config.js",      None,                 "Next.js"),
    ("next.config.ts",      None,                 "Next.js"),
    ("next.config.mjs",     None,                 "Next.js"),
    ("nuxt.config.js",      None,                 "Nuxt.js"),
    ("nuxt.config.ts",      None,                 "Nuxt.js"),
    ("angular.json",        None,                 "Angular"),
    ("vite.config.js",      None,                 "Vite"),
    ("vite.config.ts",      None,                 "Vite"),
    ("package.json",        r'"nest"',            "NestJS"),
    ("package.json",        r'"express"',         "Express"),
    ("package.json",        r'"react"',           "React"),
    ("package.json",        r'"vue"',             "Vue"),
    ("package.json",        r'"svelte"',          "Svelte"),
    ("package.json",        r'"elysia"',          "Elysia (Bun)"),
    ("package.json",        r'"hono"',            "Hono"),
    ("package.json",        r'"electron"',        "Electron"),
    # Rust
    ("Cargo.toml",          r"axum",              "Axum"),
    ("Cargo.toml",          r"actix-web",         "Actix-Web"),
    ("Cargo.toml",          r"tokio",             "Tokio (async)"),
    # Go
    ("go.mod",              r"gin-gonic",         "Gin"),
    ("go.mod",              r"labstack/echo",     "Echo"),
    ("go.mod",              r"fiber",             "Fiber"),
    # Java / Kotlin
    ("pom.xml",             r"spring-boot",       "Spring Boot"),
    ("build.gradle",        r"spring-boot",       "Spring Boot"),
]

# Important directory roles
DIR_ROLES = {
    "api":          "API routes / handlers",
    "routes":       "Route definitions",
    "controllers":  "Request controllers",
    "handlers":     "Request handlers",
    "services":     "Business logic",
    "models":       "Data models / schemas",
    "schemas":      "Validation schemas",
    "repositories": "Data access layer",
    "db":           "Database layer",
    "database":     "Database layer",
    "migrations":   "DB migrations",
    "middleware":   "Middleware",
    "auth":         "Authentication",
    "utils":        "Utilities / helpers",
    "helpers":      "Utilities / helpers",
    "lib":          "Shared library code",
    "core":         "Core logic",
    "config":       "Configuration",
    "tests":        "Tests",
    "test":         "Tests",
    "__tests__":    "Tests",
    "spec":         "Tests / specs",
    "ui":           "UI layer",
    "components":   "UI components",
    "pages":        "Page components",
    "views":        "View layer",
    "static":       "Static assets",
    "public":       "Public assets",
    "assets":       "Assets",
    "templates":    "HTML templates",
    "scripts":      "Build / utility scripts",
    "cli":          "CLI interface",
    "cmd":          "Command entry points",
    "workers":      "Background workers / jobs",
    "tasks":        "Task queue",
    "integrations": "Third-party integrations",
    "plugins":      "Plugin system",
    "agents":       "AI agent modules",
    "memory":       "Memory / storage layer",
    "prompts":      "LLM prompts",
}


# ── Public entry point ────────────────────────────────────────────────────────

def get_project_map(
    working_directory: str,
    path: str = ".",
    depth: int = 3,
    include_dependencies: bool = True,
    include_data_flow: bool = True,
    focus: Optional[str] = None,
) -> str:
    if _GUARD:
        try:
            root = Path(guard.resolve(path))
        except GuardError as e:
            return f"🔒 Blocked: {e}"
    else:
        root = Path(working_directory) / path
        root = root.resolve()

    if not root.exists():
        return f"Path not found: {path}"

    sections: list[str] = []

    # ── Project identity ──────────────────────────────────────────────────────
    identity = _detect_identity(root)
    sections.append(_fmt_identity(identity))

    # ── Directory tree ────────────────────────────────────────────────────────
    tree = _build_tree(root, depth)
    sections.append(_fmt_tree(tree, root))

    # ── Key files ─────────────────────────────────────────────────────────────
    key_files = _find_key_files(root)
    if key_files:
        sections.append(_fmt_key_files(key_files, root))

    # ── Focus area ────────────────────────────────────────────────────────────
    if focus:
        focus_path = root / focus
        if focus_path.exists():
            focus_section = _deep_focus(focus_path, root)
            sections.append(focus_section)

    # ── Dependencies ──────────────────────────────────────────────────────────
    if include_dependencies:
        deps = _parse_dependencies(root)
        if deps:
            sections.append(_fmt_dependencies(deps))

    # ── Data flow ─────────────────────────────────────────────────────────────
    if include_data_flow:
        flow = _infer_data_flow(root, identity, tree)
        if flow:
            sections.append(_fmt_data_flow(flow))

    # ── Token estimate ────────────────────────────────────────────────────────
    stats = _project_stats(root)
    sections.append(_fmt_stats(stats))

    return "\n\n".join(sections)


# ── Identity detection ────────────────────────────────────────────────────────

def _detect_identity(root: Path) -> dict:
    lang      = _detect_language(root)
    framework = _detect_framework(root)
    pm        = _detect_package_manager(root)
    proj_type = _detect_project_type(root, framework)
    name      = _detect_name(root)

    return {
        "name":      name,
        "language":  lang,
        "framework": framework,
        "pm":        pm,
        "type":      proj_type,
    }


def _detect_language(root: Path) -> str:
    markers = {
        "Python":     ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
        "TypeScript": ["tsconfig.json"],
        "JavaScript": ["package.json"],
        "Rust":       ["Cargo.toml"],
        "Go":         ["go.mod"],
        "Java":       ["pom.xml", "build.gradle"],
        "Kotlin":     ["build.gradle.kts"],
        "Ruby":       ["Gemfile"],
        "PHP":        ["composer.json"],
        "C/C++":      ["CMakeLists.txt"],
    }
    for lang, files in markers.items():
        if any((root / f).exists() for f in files):
            return lang

    # Fallback: count file extensions
    counts: dict[str, int] = {}
    for p in root.rglob("*"):
        if p.suffix and p.name not in IGNORE_DIRS:
            counts[p.suffix] = counts.get(p.suffix, 0) + 1
    if counts:
        top_ext = max(counts, key=counts.get)
        ext_map = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".rs": "Rust", ".go": "Go", ".java": "Java", ".rb": "Ruby",
        }
        return ext_map.get(top_ext, top_ext.lstrip(".").capitalize())
    return "Unknown"


def _detect_framework(root: Path) -> Optional[str]:
    for fname, pattern, fw in FRAMEWORK_FINGERPRINTS:
        fpath = root / fname
        if fpath.exists():
            if pattern is None:
                return fw
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace").lower()
                if re.search(pattern, content, re.IGNORECASE):
                    return fw
            except OSError:
                pass
    return None


def _detect_package_manager(root: Path) -> Optional[str]:
    if (root / "bun.lockb").exists():      return "Bun"
    if (root / "pnpm-lock.yaml").exists(): return "pnpm"
    if (root / "yarn.lock").exists():      return "Yarn"
    if (root / "package-lock.json").exists(): return "npm"
    if (root / "Cargo.lock").exists():     return "Cargo"
    if (root / "Pipfile.lock").exists():   return "Pipenv"
    if (root / "poetry.lock").exists():    return "Poetry"
    if (root / "go.sum").exists():         return "Go modules"
    return None


def _detect_project_type(root: Path, framework: Optional[str]) -> str:
    has_pkg = (root / "package.json").exists()

    if framework in ("Next.js", "Nuxt.js", "Angular", "React", "Vue", "Svelte"):
        return "Frontend / Full-stack web app"
    if framework in ("FastAPI", "Flask", "Django", "Express", "NestJS",
                     "Gin", "Echo", "Fiber", "Axum", "Actix-Web", "Hono"):
        return "Backend API / web server"
    if framework in ("Elysia (Bun)",):
        return "Backend API (Bun runtime)"
    if framework == "Electron":
        return "Desktop app"
    if (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists():
        return "Containerised service"
    if has_pkg:
        pkg = json.loads((root / "package.json").read_text(errors="replace"))
        if pkg.get("bin"):
            return "CLI tool (Node.js)"
    if (root / "setup.py").exists() or (root / "pyproject.toml").exists():
        pyproj = (root / "pyproject.toml").read_text(errors="replace") if (root / "pyproject.toml").exists() else ""
        if "scripts" in pyproj or "console_scripts" in pyproj:
            return "Python CLI / package"
    return "Library / module"


def _detect_name(root: Path) -> str:
    # Try package.json
    pkg = root / "package.json"
    if pkg.exists():
        try:
            return json.loads(pkg.read_text(errors="replace")).get("name", root.name)
        except Exception:
            pass
    # Try pyproject.toml
    pyp = root / "pyproject.toml"
    if pyp.exists():
        m = re.search(r'^name\s*=\s*["\'](.+?)["\']', pyp.read_text(errors="replace"), re.M)
        if m:
            return m.group(1)
    return root.name


# ── Directory tree ────────────────────────────────────────────────────────────

def _build_tree(root: Path, depth: int) -> list[dict]:
    """Build a filtered, annotated directory tree."""
    def _walk(path: Path, current_depth: int) -> list[dict]:
        if current_depth > depth:
            return []
        entries = []
        try:
            items = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return []

        for item in items:
            if item.name.startswith(".") and item.name not in (".github",):
                continue
            if item.name in IGNORE_DIRS:
                continue
            if item.suffix in IGNORE_EXTENSIONS:
                continue

            node = {
                "name": item.name,
                "is_dir": item.is_dir(),
                "role": DIR_ROLES.get(item.name.lower()),
                "children": [],
            }

            if item.is_dir() and current_depth < depth:
                node["children"] = _walk(item, current_depth + 1)
                # Count files in this dir
                try:
                    node["file_count"] = sum(1 for _ in item.rglob("*") if _.is_file()
                                             and _.name not in IGNORE_DIRS)
                except Exception:
                    node["file_count"] = 0

            entries.append(node)

        return entries

    return _walk(root, 1)


def _fmt_tree(tree: list[dict], root: Path, prefix: str = "") -> str:
    lines = [f"DIRECTORY STRUCTURE  ({root.name}/)"]
    lines.append("─" * 50)

    def _render(nodes: list[dict], indent: str = ""):
        for i, node in enumerate(nodes):
            is_last = (i == len(nodes) - 1)
            connector = "└── " if is_last else "├── "
            ext       = "│   " if not is_last else "    "

            role_str  = f"  [{node['role']}]" if node.get("role") else ""
            count_str = f"  ({node.get('file_count', '')} files)" if node.get("file_count") else ""
            suffix    = "/" if node["is_dir"] else ""

            lines.append(f"{indent}{connector}{node['name']}{suffix}{role_str}{count_str}")

            if node.get("children"):
                _render(node["children"], indent + ext)

    _render(tree)
    return "\n".join(lines)


# ── Key files ─────────────────────────────────────────────────────────────────

def _find_key_files(root: Path) -> list[tuple[str, str]]:
    """Return list of (relative_path, role) for key files that exist."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for pattern in KEY_FILE_PATTERNS:
        p = root / pattern
        if p.exists() and str(p) not in seen:
            rel = str(p.relative_to(root))
            role = _file_role(p, root)
            found.append((rel, role))
            seen.add(str(p))

    # Also scan for route/model/schema files in common locations
    for subdir in ["routes", "api", "models", "schemas", "controllers", "handlers"]:
        d = root / subdir
        if not d.exists():
            # try src/subdir
            d = root / "src" / subdir
        if d.exists() and d.is_dir():
            for f in sorted(d.iterdir())[:10]:
                if f.is_file() and f.suffix in (".py", ".js", ".ts", ".go", ".rs"):
                    rel = str(f.relative_to(root))
                    if rel not in seen:
                        found.append((rel, _file_role(f, root)))
                        seen.add(rel)

    return found


def _file_role(path: Path, root: Path) -> str:
    name = path.name.lower()
    parent = path.parent.name.lower()

    if name in ("main.py", "main.js", "main.ts", "index.js", "index.ts", "app.py"):
        return "Entry point"
    if name in ("package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml"):
        return "Project manifest"
    if "config" in name or "settings" in name:
        return "Configuration"
    if "docker" in name:
        return "Docker"
    if "test" in name or "spec" in name or parent in ("tests", "test", "__tests__", "spec"):
        return "Tests"
    if parent in ("routes", "api"):
        return "Routes / API"
    if parent == "models":
        return "Data model"
    if parent == "schemas":
        return "Schema"
    if parent == "middleware":
        return "Middleware"
    if ".env" in name:
        return "Environment template"
    return "Key file"


def _fmt_key_files(files: list[tuple[str, str]], root: Path) -> str:
    lines = ["KEY FILES"]
    lines.append("─" * 50)
    for rel, role in files:
        size = ""
        try:
            s = (root / rel).stat().st_size
            size = f"  {_fmt_size(s)}"
        except Exception:
            pass
        lines.append(f"  {rel:<45} {role}{size}")
    return "\n".join(lines)


# ── Focus area ────────────────────────────────────────────────────────────────

def _deep_focus(focus_path: Path, root: Path) -> str:
    """Deep analysis of a specific subdirectory."""
    rel = str(focus_path.relative_to(root))
    lines = [f"FOCUS: {rel}/"]
    lines.append("─" * 50)

    files = sorted(focus_path.rglob("*"))
    shown = 0
    for f in files:
        if f.is_file() and f.suffix not in IGNORE_EXTENSIONS:
            if any(ig in f.parts for ig in IGNORE_DIRS):
                continue
            frel = str(f.relative_to(root))
            size = _fmt_size(f.stat().st_size)
            lines.append(f"  {frel:<50} {size}")
            shown += 1
            if shown >= 30:
                remaining = sum(1 for _ in files if _.is_file()) - shown
                if remaining > 0:
                    lines.append(f"  … {remaining} more files")
                break

    return "\n".join(lines)


# ── Dependencies ──────────────────────────────────────────────────────────────

def _parse_dependencies(root: Path) -> Optional[dict]:
    for fname, lang in DEP_FILE_PRIORITY:
        fpath = root / fname
        if not fpath.exists():
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            if lang == "python":
                return _parse_python_deps(fname, content)
            if lang == "node":
                return _parse_node_deps(content)
            if lang == "rust":
                return _parse_rust_deps(content)
            if lang == "go":
                return _parse_go_deps(content)
            if lang == "java":
                return _parse_java_deps(content)
        except Exception:
            continue
    return None


def _parse_python_deps(fname: str, content: str) -> dict:
    deps: list[str] = []
    dev_deps: list[str] = []

    if fname == "pyproject.toml":
        # [project.dependencies]
        in_deps = False
        in_dev  = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("[project.optional-dependencies") or \
               stripped.startswith("[tool.poetry.dev-dependencies") or \
               stripped.startswith("[dependency-groups"):
                in_dev = True; in_deps = False; continue
            if stripped.startswith("[project.dependencies") or \
               stripped.startswith("[tool.poetry.dependencies"):
                in_deps = True; in_dev = False; continue
            if stripped.startswith("[") and stripped.endswith("]"):
                in_deps = False; in_dev = False; continue
            if in_deps or in_dev:
                m = re.match(r'^["\']?([A-Za-z0-9_\-\.]+)["\']?\s*[=><!]', stripped)
                if not m:
                    m = re.match(r'^([A-Za-z0-9_\-\.]+)\s*=', stripped)
                if m and m.group(1).lower() not in ("python", "name", "version"):
                    (deps if in_deps else dev_deps).append(m.group(1))

    elif fname == "requirements.txt":
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                name = re.split(r"[>=<!;\[]", line)[0].strip()
                if name:
                    deps.append(name)

    return {"source": fname, "deps": deps[:40], "dev_deps": dev_deps[:20]}


def _parse_node_deps(content: str) -> dict:
    try:
        pkg = json.loads(content)
        deps     = list(pkg.get("dependencies", {}).keys())
        dev_deps = list(pkg.get("devDependencies", {}).keys())
        return {"source": "package.json", "deps": deps[:40], "dev_deps": dev_deps[:20]}
    except Exception:
        return {}


def _parse_rust_deps(content: str) -> dict:
    deps: list[str] = []
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[dependencies]" or stripped.startswith("[dependencies."):
            in_deps = True; continue
        if stripped.startswith("[") and "dependencies" not in stripped:
            in_deps = False; continue
        if in_deps:
            m = re.match(r'^([a-z0-9_\-]+)\s*=', stripped)
            if m:
                deps.append(m.group(1))
    return {"source": "Cargo.toml", "deps": deps[:40], "dev_deps": []}


def _parse_go_deps(content: str) -> dict:
    deps: list[str] = []
    for m in re.finditer(r'^\s+(\S+)\s+v[\d\.]+', content, re.M):
        deps.append(m.group(1).split("/")[-1])
    return {"source": "go.mod", "deps": deps[:40], "dev_deps": []}


def _parse_java_deps(content: str) -> dict:
    # Maven artifactId tags
    deps = re.findall(r'<artifactId>([^<]+)</artifactId>', content)
    # Gradle: implementation/api 'group:artifact:version'
    deps += re.findall(r"(?:implementation|api|compile)[( ]['\"][\w\.]+:([\w\-]+):", content)
    return {"source": "pom.xml / build.gradle", "deps": list(set(deps))[:40], "dev_deps": []}


def _fmt_dependencies(deps: dict) -> str:
    lines = [f"DEPENDENCIES  (from {deps.get('source', '?')})"]
    lines.append("─" * 50)
    main_deps = deps.get("deps", [])
    dev_deps  = deps.get("dev_deps", [])

    if main_deps:
        lines.append(f"  Production ({len(main_deps)}):")
        # Show in columns of 3
        for i in range(0, len(main_deps), 3):
            row = main_deps[i:i+3]
            lines.append("    " + "  ".join(f"{d:<25}" for d in row))

    if dev_deps:
        lines.append(f"\n  Dev ({len(dev_deps)}):")
        for i in range(0, len(dev_deps), 3):
            row = dev_deps[i:i+3]
            lines.append("    " + "  ".join(f"{d:<25}" for d in row))

    return "\n".join(lines)


# ── Data flow inference ───────────────────────────────────────────────────────

def _infer_data_flow(root: Path, identity: dict, tree: list[dict]) -> list[str]:
    """Heuristically infer the data flow based on dirs and framework."""
    fw   = identity.get("framework") or ""
    lang = identity.get("language") or ""

    existing_dirs = {n["name"].lower() for n in tree if n["is_dir"]}

    flow: list[str] = []

    # API-style flows
    if any(d in existing_dirs for d in ("routes", "api", "controllers", "handlers")):
        chain: list[str] = ["Client / HTTP Request"]

        if "middleware" in existing_dirs:
            chain.append("Middleware")
        if "routes" in existing_dirs or "api" in existing_dirs:
            chain.append("Router / Routes")
        if "controllers" in existing_dirs or "handlers" in existing_dirs:
            chain.append("Controller / Handler")
        if "services" in existing_dirs:
            chain.append("Service Layer")
        if any(d in existing_dirs for d in ("repositories", "db", "database", "models")):
            chain.append("Repository / DB")
        chain.append("Database")

        flow = chain

    # Frontend flow
    elif any(d in existing_dirs for d in ("pages", "components", "views")):
        chain = ["User"]
        if "pages" in existing_dirs:
            chain.append("Pages (Next.js / router)")
        elif "views" in existing_dirs:
            chain.append("Views")
        if "components" in existing_dirs:
            chain.append("Components")
        if "hooks" in existing_dirs or "stores" in existing_dirs or "context" in existing_dirs:
            chain.append("State (hooks / store / context)")
        chain.append("API / Backend")
        flow = chain

    # CLI flow
    elif any(d in existing_dirs for d in ("cli", "cmd", "commands")):
        flow = ["User input (CLI args)", "Command parser", "Command handler", "Core logic", "Output"]

    # Agent / AI flow
    elif any(d in existing_dirs for d in ("agents", "prompts", "memory")):
        chain = ["User prompt"]
        if "agents" in existing_dirs:
            chain.append("Agent orchestrator")
        if "memory" in existing_dirs:
            chain.append("Memory / context retrieval")
        if "prompts" in existing_dirs:
            chain.append("Prompt builder")
        chain.append("LLM API")
        if "tools" in existing_dirs or "func" in existing_dirs:
            chain.append("Tool execution")
        chain.append("Response → user")
        flow = chain

    return flow


def _fmt_data_flow(flow: list[str]) -> str:
    if not flow:
        return ""
    lines = ["DATA FLOW"]
    lines.append("─" * 50)
    for i, step in enumerate(flow):
        if i == 0:
            lines.append(f"  {step}")
        else:
            lines.append(f"    ↓")
            lines.append(f"  {step}")
    return "\n".join(lines)


# ── Project stats ─────────────────────────────────────────────────────────────

def _project_stats(root: Path) -> dict:
    total_files = 0
    total_lines = 0
    by_ext: dict[str, int] = {}

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(ig in p.parts for ig in IGNORE_DIRS):
            continue
        if p.suffix in IGNORE_EXTENSIONS:
            continue
        total_files += 1
        by_ext[p.suffix] = by_ext.get(p.suffix, 0) + 1
        if p.suffix in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
                         ".java", ".html", ".css", ".md"):
            try:
                total_lines += p.read_text(errors="replace").count("\n")
            except Exception:
                pass

    top_exts = sorted(by_ext.items(), key=lambda x: x[1], reverse=True)[:6]

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "top_exts":    top_exts,
    }


def _fmt_stats(stats: dict) -> str:
    lines = ["PROJECT STATS"]
    lines.append("─" * 50)
    lines.append(f"  Files (non-ignored)  {stats['total_files']:,}")
    lines.append(f"  Estimated LoC        {stats['total_lines']:,}")

    if stats["top_exts"]:
        ext_str = "  ".join(
            f"{ext or '(no ext)'} ×{count}" for ext, count in stats["top_exts"]
        )
        lines.append(f"  Top file types       {ext_str}")

    # Token estimate: ~4 chars/token, ~80 chars/line avg
    est_tokens = stats["total_lines"] * 20  # rough: 20 tokens/line
    lines.append(f"  Full-read cost est.  ~{est_tokens:,} tokens  ← use search_code instead")
    return "\n".join(lines)


def _fmt_identity(identity: dict) -> str:
    lines = ["PROJECT IDENTITY"]
    lines.append("─" * 50)
    lines.append(f"  Name        {identity['name']}")
    lines.append(f"  Language    {identity['language']}")
    if identity["framework"]:
        lines.append(f"  Framework   {identity['framework']}")
    if identity["pm"]:
        lines.append(f"  Pkg manager {identity['pm']}")
    lines.append(f"  Type        {identity['type']}")
    return "\n".join(lines)


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / 1024 / 1024:.1f}MB"