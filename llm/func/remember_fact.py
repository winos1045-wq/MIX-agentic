"""
func/remember_fact.py — Persistent Long-Term Memory for SDX Agent

Stores project insights across sessions so the agent never asks the
same question twice and stays consistent with project conventions.

Four tools in one file:
  remember_fact     store a fact (key, value, category)
  recall_fact       retrieve by key or fuzzy search
  forget_fact       delete a fact
  list_facts        browse all facts by category

Storage:
  Primary   → JSON flat-file  (always available, zero deps)
  Secondary → SQLite FTS      (fast full-text search, stdlib)
  Optional  → Vector index    (semantic search via numpy cosine sim,
                               no external DB needed)

Categories (built-in):
  convention    naming, formatting, style rules
  architecture  framework, DB, patterns, data flow
  constraint    "never do X", "always use Y"
  credential    API endpoints, service names (never actual secrets)
  decision      "we chose X over Y because Z"
  todo          deferred tasks, known tech debt
  fact          general project knowledge
  preference    user/team preferences
  error         known bugs, gotchas, pitfalls

Auto-populated facts (detected from project files):
  Language, framework, package manager, DB, test framework
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Optional: numpy for vector/semantic search ────────────────────────────────
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# ── Optional: Gemini embeddings ───────────────────────────────────────────────
try:
    from google import genai as _genai
    _HAS_GENAI = True
except ImportError:
    _HAS_GENAI = False


# ── Constants ─────────────────────────────────────────────────────────────────

VALID_CATEGORIES = {
    "convention", "architecture", "constraint", "credential",
    "decision", "todo", "fact", "preference", "error", "goal",
}

MEMORY_DIR  = ".sdx_memory"
FACTS_FILE  = "facts.json"
DB_FILE     = "facts.db"
VEC_FILE    = "vectors.json"

MAX_VALUE_LEN = 2000
MAX_KEY_LEN   = 120


# ── Schemas ───────────────────────────────────────────────────────────────────

schema_remember_fact = {
    "name": "remember_fact",
    "description": (
        "Store a long-term project insight that persists across sessions. "
        "Use for: naming conventions, architecture decisions, DB type, "
        "framework choices, known constraints, recurring errors, user preferences. "
        "These facts are auto-injected into future sessions so you never ask twice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": (
                    "Short, unique identifier. Use snake_case. "
                    "E.g. 'db_type', 'naming_convention', 'auth_method', "
                    "'api_base_url', 'test_framework'."
                )
            },
            "value": {
                "type": "string",
                "description": "The fact to store. Be specific and actionable."
            },
            "category": {
                "type": "string",
                "enum": list(VALID_CATEGORIES),
                "description": (
                    "convention: style/naming rules. "
                    "architecture: tech stack, patterns. "
                    "constraint: things to never do. "
                    "decision: why X was chosen over Y. "
                    "credential: service names/endpoints (no secrets). "
                    "todo: deferred tasks. "
                    "error: known bugs/gotchas. "
                    "preference: user/team preferences. "
                    "fact: general project knowledge. "
                    "goal: project objectives."
                ),
                "default": "fact"
            },
            "confidence": {
                "type": "number",
                "description": "Confidence 0.0-1.0 (default 1.0 for explicit facts).",
                "default": 1.0
            },
            "source": {
                "type": "string",
                "description": "Where this fact came from. E.g. 'user', 'inferred', 'README.md'.",
                "default": "agent"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for grouping. E.g. ['database', 'postgres']."
            }
        },
        "required": ["key", "value"]
    }
}

schema_recall_fact = {
    "name": "recall_fact",
    "description": (
        "Retrieve stored facts by key (exact), keyword search, or category. "
        "Call this at session start or before making decisions that depend on "
        "project conventions (naming, DB, auth, etc.)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Exact key (e.g. 'db_type') or search phrase "
                    "(e.g. 'database', 'naming convention'). "
                    "Leave empty to return all facts."
                ),
                "default": ""
            },
            "category": {
                "type": "string",
                "enum": list(VALID_CATEGORIES) + ["all"],
                "description": "Filter by category. Default: all.",
                "default": "all"
            },
            "limit": {
                "type": "integer",
                "description": "Max results. Default: 20.",
                "default": 20
            },
            "semantic": {
                "type": "boolean",
                "description": "Use semantic/vector search (requires numpy). Default: false.",
                "default": False
            }
        },
        "required": []
    }
}

schema_forget_fact = {
    "name": "forget_fact",
    "description": "Delete a stored fact by key. Use when a fact is outdated or wrong.",
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The key of the fact to delete."
            }
        },
        "required": ["key"]
    }
}

schema_list_facts = {
    "name": "list_facts",
    "description": (
        "List all stored facts, optionally grouped by category. "
        "Use at session start to load project context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "group_by_category": {
                "type": "boolean",
                "description": "Group output by category. Default: true.",
                "default": True
            },
            "category": {
                "type": "string",
                "description": "Show only this category. Default: all.",
                "default": "all"
            }
        },
        "required": []
    }
}


# ── Memory store ──────────────────────────────────────────────────────────────

class FactStore:
    """
    Layered fact store:
      Layer 1: JSON  (source of truth, always available)
      Layer 2: SQLite FTS (keyword search)
      Layer 3: Vector index (semantic search, optional)
    """

    def __init__(self, working_directory: str):
        self.cwd      = Path(working_directory)
        self.mem_dir  = self.cwd / MEMORY_DIR
        self.mem_dir.mkdir(exist_ok=True)

        self.facts_path = self.mem_dir / FACTS_FILE
        self.db_path    = self.mem_dir / DB_FILE
        self.vec_path   = self.mem_dir / VEC_FILE

        self._facts: dict[str, dict] = {}
        self._load_json()
        self._init_db()

    # ── JSON layer ────────────────────────────────────────────────────────────

    def _load_json(self):
        if self.facts_path.exists():
            try:
                self._facts = json.loads(self.facts_path.read_text())
            except Exception:
                self._facts = {}

    def _save_json(self):
        self.facts_path.write_text(json.dumps(self._facts, indent=2))

    # ── SQLite FTS layer ──────────────────────────────────────────────────────

    def _init_db(self):
        self._db = sqlite3.connect(str(self.db_path))
        self._db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
            USING fts5(key, value, category, tags, content='')
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS facts_meta (
                key TEXT PRIMARY KEY,
                category TEXT,
                confidence REAL,
                source TEXT,
                created_at TEXT,
                updated_at TEXT,
                access_count INTEGER DEFAULT 0
            )
        """)
        self._db.commit()
        self._sync_db()

    def _sync_db(self):
        """Sync JSON → SQLite (on startup)."""
        for key, fact in self._facts.items():
            self._db.execute(
                "INSERT OR REPLACE INTO facts_fts(key, value, category, tags) VALUES (?,?,?,?)",
                (key, fact.get("value",""), fact.get("category","fact"),
                 " ".join(fact.get("tags",[])))
            )
            self._db.execute(
                "INSERT OR REPLACE INTO facts_meta VALUES (?,?,?,?,?,?,?)",
                (key, fact.get("category","fact"), fact.get("confidence",1.0),
                 fact.get("source","agent"), fact.get("created_at",""),
                 fact.get("updated_at",""), fact.get("access_count",0))
            )
        self._db.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def put(self, key: str, value: str, category: str,
            confidence: float, source: str, tags: list[str]) -> dict:
        now    = datetime.now().isoformat()
        exists = key in self._facts
        prev   = self._facts.get(key, {})

        fact = {
            "key":         key,
            "value":       value,
            "category":    category,
            "confidence":  confidence,
            "source":      source,
            "tags":        tags,
            "created_at":  prev.get("created_at", now),
            "updated_at":  now,
            "access_count": prev.get("access_count", 0),
            "version":     prev.get("version", 0) + 1,
        }

        # Store previous value in history
        if exists and prev.get("value") != value:
            history = prev.get("history", [])
            history.append({"value": prev["value"], "at": prev.get("updated_at", now)})
            fact["history"] = history[-5:]  # keep last 5 versions

        self._facts[key] = fact
        self._save_json()

        # Update DB
        self._db.execute(
            "INSERT OR REPLACE INTO facts_fts(key, value, category, tags) VALUES (?,?,?,?)",
            (key, value, category, " ".join(tags))
        )
        self._db.execute(
            "INSERT OR REPLACE INTO facts_meta VALUES (?,?,?,?,?,?,?)",
            (key, category, confidence, source,
             fact["created_at"], now, fact["access_count"])
        )
        self._db.commit()

        return {"action": "updated" if exists else "created", "fact": fact}

    def get(self, key: str) -> Optional[dict]:
        fact = self._facts.get(key)
        if fact:
            fact["access_count"] = fact.get("access_count", 0) + 1
            self._facts[key] = fact
            self._save_json()
        return fact

    def delete(self, key: str) -> bool:
        if key not in self._facts:
            return False
        del self._facts[key]
        self._save_json()
        self._db.execute("DELETE FROM facts_fts WHERE key=?", (key,))
        self._db.execute("DELETE FROM facts_meta WHERE key=?", (key,))
        self._db.commit()
        return True

    def search_keyword(self, query: str, category: str = "all",
                       limit: int = 20) -> list[dict]:
        """FTS5 keyword search across key + value + tags."""
        if not query:
            return self._all(category, limit)

        # Try exact key match first
        exact = self._facts.get(query)
        results: list[dict] = []
        if exact:
            results.append(exact)

        try:
            # FTS search
            cat_filter = "" if category == "all" else f"AND category='{category}'"
            rows = self._db.execute(
                f"""SELECT key FROM facts_fts
                    WHERE facts_fts MATCH ?
                    {cat_filter}
                    LIMIT ?""",
                (query, limit)
            ).fetchall()
            for (key,) in rows:
                if key != query and key in self._facts:
                    results.append(self._facts[key])
        except sqlite3.OperationalError:
            # FTS query syntax error — fall back to substring
            results += self._substring_search(query, category, limit)

        # Filter by category
        if category != "all":
            results = [r for r in results if r.get("category") == category]

        # Deduplicate
        seen:  set[str]   = set()
        deduped: list[dict] = []
        for r in results:
            if r["key"] not in seen:
                seen.add(r["key"])
                deduped.append(r)

        return deduped[:limit]

    def _substring_search(self, query: str, category: str, limit: int) -> list[dict]:
        q = query.lower()
        out: list[dict] = []
        for key, fact in self._facts.items():
            if category != "all" and fact.get("category") != category:
                continue
            if (q in key.lower() or
                q in fact.get("value","").lower() or
                any(q in t.lower() for t in fact.get("tags",[]))):
                out.append(fact)
            if len(out) >= limit:
                break
        return out

    def search_semantic(self, query: str, limit: int = 10) -> list[dict]:
        """Cosine similarity search using cached embeddings."""
        if not _HAS_NUMPY:
            return self.search_keyword(query, limit=limit)

        vecs = self._load_vectors()
        if not vecs:
            return self.search_keyword(query, limit=limit)

        q_vec = self._embed(query)
        if q_vec is None:
            return self.search_keyword(query, limit=limit)

        q_arr = np.array(q_vec)
        scores: list[tuple[float, str]] = []
        for key, vec in vecs.items():
            if key in self._facts:
                v = np.array(vec)
                cos = float(np.dot(q_arr, v) / (np.linalg.norm(q_arr) * np.linalg.norm(v) + 1e-9))
                scores.append((cos, key))

        scores.sort(reverse=True)
        return [self._facts[k] for _, k in scores[:limit] if k in self._facts]

    def _all(self, category: str, limit: int) -> list[dict]:
        facts = list(self._facts.values())
        if category != "all":
            facts = [f for f in facts if f.get("category") == category]
        # Sort: most recently updated first
        facts.sort(key=lambda f: f.get("updated_at", ""), reverse=True)
        return facts[:limit]

    def all_by_category(self) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for fact in self._facts.values():
            cat = fact.get("category", "fact")
            grouped.setdefault(cat, []).append(fact)
        return grouped

    # ── Vector layer ──────────────────────────────────────────────────────────

    def _load_vectors(self) -> dict[str, list[float]]:
        if self.vec_path.exists():
            try:
                return json.loads(self.vec_path.read_text())
            except Exception:
                pass
        return {}

    def _save_vectors(self, vecs: dict):
        self.vec_path.write_text(json.dumps(vecs))

    def _embed(self, text: str) -> Optional[list[float]]:
        """Generate embedding via Gemini or fall back to TF-IDF-like hash."""
        if _HAS_GENAI and os.environ.get("GEMINI_API_KEY"):
            try:
                client = _genai.Client(api_key=os.environ["GEMINI_API_KEY"])
                result = client.models.embed_content(
                    model="models/text-embedding-004",
                    contents=text,
                )
                return result.embeddings[0].values
            except Exception:
                pass

        # Fallback: deterministic character n-gram hash vector (dim=128)
        if not _HAS_NUMPY:
            return None
        vec = np.zeros(128)
        text_lower = text.lower()
        for i in range(len(text_lower) - 1):
            bigram = text_lower[i:i+2]
            idx    = (ord(bigram[0]) * 31 + ord(bigram[1])) % 128
            vec[idx] += 1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()

    def embed_all(self) -> int:
        """Generate and cache embeddings for all stored facts."""
        vecs = self._load_vectors()
        count = 0
        for key, fact in self._facts.items():
            if key not in vecs:
                text = f"{key} {fact.get('value','')} {' '.join(fact.get('tags',[]))}"
                v = self._embed(text)
                if v:
                    vecs[key] = v
                    count += 1
        self._save_vectors(vecs)
        return count

    def close(self):
        self._db.close()


# ── Auto-detection ────────────────────────────────────────────────────────────

def _auto_detect_facts(cwd: str) -> list[dict]:
    """Scan the project and infer facts automatically."""
    root  = Path(cwd)
    facts: list[dict] = []

    def _add(key, value, cat="architecture", source="auto-detected", conf=0.85):
        facts.append({"key": key, "value": value, "category": cat,
                      "source": source, "confidence": conf, "tags": []})

    # ── Language / framework ──────────────────────────────────────────────────
    if (root / "pyproject.toml").exists():
        raw = (root / "pyproject.toml").read_text(errors="replace")
        if "fastapi" in raw.lower():   _add("framework", "FastAPI")
        if "flask"   in raw.lower():   _add("framework", "Flask")
        if "django"  in raw.lower():   _add("framework", "Django")
        m = re.search(r'^python\s*=\s*["\']([^"\']+)', raw, re.M)
        if m: _add("python_version", m.group(1))

    if (root / "package.json").exists():
        try:
            pkg = json.loads((root / "package.json").read_text())
            deps = {**pkg.get("dependencies",{}), **pkg.get("devDependencies",{})}
            if "next"     in deps: _add("framework", "Next.js")
            if "express"  in deps: _add("framework", "Express")
            if "react"    in deps: _add("ui_library", "React")
            if "vue"      in deps: _add("ui_library", "Vue")
            if "jest"     in deps: _add("test_framework", "Jest", "convention")
            if "vitest"   in deps: _add("test_framework", "Vitest", "convention")
            if "eslint"   in deps: _add("linter", "ESLint", "convention")
            if "prettier" in deps: _add("formatter", "Prettier", "convention")
            if "prisma"   in deps: _add("orm", "Prisma")
            if "mongoose" in deps: _add("orm", "Mongoose / MongoDB")
            if "pg"       in deps: _add("database", "PostgreSQL")
            if "mysql2"   in deps: _add("database", "MySQL")
            if "redis"    in deps: _add("cache", "Redis")
            name = pkg.get("name")
            if name: _add("project_name", name, "fact", conf=1.0)
        except Exception:
            pass

    # ── Database ──────────────────────────────────────────────────────────────
    for fname in [".env.example", ".env.sample", "config.py", "settings.py"]:
        fp = root / fname
        if fp.exists():
            raw = fp.read_text(errors="replace")
            if "postgresql" in raw.lower() or "postgres" in raw.lower():
                _add("database", "PostgreSQL")
            if "mysql"   in raw.lower(): _add("database", "MySQL")
            if "mongodb" in raw.lower(): _add("database", "MongoDB")
            if "sqlite"  in raw.lower(): _add("database", "SQLite")
            if "redis"   in raw.lower(): _add("cache", "Redis")

    # ── Test framework ────────────────────────────────────────────────────────
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
        raw = ""
        if (root / "pyproject.toml").exists():
            raw = (root / "pyproject.toml").read_text(errors="replace")
        if "pytest" in raw or (root / "pytest.ini").exists():
            _add("test_framework", "pytest", "convention")

    # ── Git presence ──────────────────────────────────────────────────────────
    if (root / ".git").exists():
        _add("version_control", "git", "fact", conf=1.0)

    # ── Docker ───────────────────────────────────────────────────────────────
    if (root / "Dockerfile").exists():
        _add("containerized", "yes — Dockerfile present", "architecture")
    if (root / "docker-compose.yml").exists() or (root / "docker-compose.yaml").exists():
        _add("orchestration", "Docker Compose", "architecture")

    # ── README — extract key lines ────────────────────────────────────────────
    for readme in ["README.md", "README.rst", "README.txt"]:
        fp = root / readme
        if fp.exists():
            lines = fp.read_text(errors="replace").splitlines()[:30]
            for line in lines:
                if re.match(r"^#\s+", line):
                    title = line.lstrip("# ").strip()
                    if title:
                        _add("project_description", title, "fact", "README.md", 0.7)
                    break

    return facts


# ── Public API ────────────────────────────────────────────────────────────────

def remember_fact(
    working_directory: str,
    key: str,
    value: str,
    category: str = "fact",
    confidence: float = 1.0,
    source: str = "agent",
    tags: Optional[list[str]] = None,
) -> str:
    key   = key.strip().lower().replace(" ", "_")[:MAX_KEY_LEN]
    value = value.strip()[:MAX_VALUE_LEN]
    tags  = tags or []

    if not key:
        return "Error: key cannot be empty"
    if not value:
        return "Error: value cannot be empty"
    if category not in VALID_CATEGORIES:
        category = "fact"

    store  = FactStore(working_directory)
    result = store.put(key, value, category, confidence, source, tags)
    store.close()

    action = result["action"]
    fact   = result["fact"]
    ver    = fact.get("version", 1)

    lines = [
        f"{'✓  Stored' if action == 'created' else '✓  Updated'}  [{category}]  {key}",
        f"   Value    {value[:80]}{'…' if len(value)>80 else ''}",
        f"   Version  {ver}  ·  Source: {source}  ·  Confidence: {confidence:.0%}",
    ]
    if tags:
        lines.append(f"   Tags     {', '.join(tags)}")
    if action == "updated" and fact.get("history"):
        prev = fact["history"][-1]["value"]
        lines.append(f"   Previous {prev[:60]}{'…' if len(prev)>60 else ''}")

    return "\n".join(lines)


def recall_fact(
    working_directory: str,
    query: str = "",
    category: str = "all",
    limit: int = 20,
    semantic: bool = False,
) -> str:
    store = FactStore(working_directory)

    if semantic and _HAS_NUMPY and query:
        results = store.search_semantic(query, limit)
    else:
        results = store.search_keyword(query, category, limit)

    store.close()

    if not results:
        msg = f"No facts found"
        if query:   msg += f" matching {query!r}"
        if category != "all": msg += f" in category '{category}'"
        return msg

    lines = [
        f"RECALLED FACTS  ({len(results)} result{'s' if len(results)!=1 else ''})",
        f"{'─' * 55}",
    ]
    for fact in results:
        conf_str = f"  [{fact.get('confidence',1):.0%}]" if fact.get("confidence",1) < 1 else ""
        lines.append(
            f"  [{fact.get('category','fact')}]  "
            f"{fact['key']}{conf_str}"
        )
        lines.append(f"    {fact.get('value','')}")
        if fact.get("tags"):
            lines.append(f"    tags: {', '.join(fact['tags'])}")
        lines.append("")

    return "\n".join(lines)


def forget_fact(working_directory: str, key: str) -> str:
    store = FactStore(working_directory)
    key   = key.strip().lower()
    ok    = store.delete(key)
    store.close()

    if ok:
        return f"✓  Deleted fact: {key}"
    return f"Fact not found: {key!r}"


def list_facts(
    working_directory: str,
    group_by_category: bool = True,
    category: str = "all",
) -> str:
    store   = FactStore(working_directory)
    grouped = store.all_by_category()
    store.close()

    if not grouped:
        return (
            "No facts stored yet.\n"
            "Call remember_fact() to store project knowledge,\n"
            "or call auto_detect_facts() to scan the project automatically."
        )

    total = sum(len(v) for v in grouped.values())
    lines = [
        f"PROJECT MEMORY  ({total} fact{'s' if total!=1 else ''} stored)",
        f"{'─' * 55}",
    ]

    cats = sorted(grouped.keys())
    if category != "all":
        cats = [c for c in cats if c == category]

    for cat in cats:
        facts = grouped[cat]
        if not facts:
            continue
        if group_by_category:
            lines.append(f"\n  ── {cat.upper()} ({len(facts)}) ──")
        for fact in sorted(facts, key=lambda f: f["key"]):
            conf = fact.get("confidence", 1.0)
            conf_str = f" [{conf:.0%}]" if conf < 1.0 else ""
            lines.append(
                f"  {fact['key']:<35} {fact.get('value','')[:50]}"
                f"{'…' if len(fact.get('value',''))>50 else ''}{conf_str}"
            )

    lines += [
        "",
        "─" * 55,
        f"  Memory dir: {MEMORY_DIR}/",
        f"  Use recall_fact(query) to search.",
        f"  Use forget_fact(key) to remove outdated facts.",
    ]

    return "\n".join(lines)


def auto_detect_and_store(working_directory: str) -> str:
    """Scan the project and auto-populate facts. Call once on session start."""
    store  = FactStore(working_directory)
    facts  = _auto_detect_facts(working_directory)
    stored = 0
    skipped = 0

    for f in facts:
        key = f["key"]
        if key not in store._facts:
            store.put(
                key        = key,
                value      = f["value"],
                category   = f["category"],
                confidence = f.get("confidence", 0.85),
                source     = f.get("source", "auto-detected"),
                tags       = f.get("tags", []),
            )
            stored += 1
        else:
            skipped += 1

    store.close()

    return (
        f"✓  Auto-detected {stored} new fact{'s' if stored!=1 else ''}  "
        f"({skipped} already known)\n"
        f"   Call list_facts() to review."
    )


# ── Context injection (for session start) ─────────────────────────────────────

def get_session_context(working_directory: str) -> str:
    """
    Return a compact fact summary for injection into the system prompt
    at the start of each session. Prioritizes high-confidence, recent facts.
    """
    store = FactStore(working_directory)
    all_f = store._facts
    store.close()

    if not all_f:
        return ""

    # Priority order for injection
    PRIORITY_CATS = ["architecture", "convention", "constraint", "credential", "decision"]
    lines = ["PROJECT MEMORY (loaded from previous sessions):"]

    for cat in PRIORITY_CATS:
        facts = [f for f in all_f.values()
                 if f.get("category") == cat and f.get("confidence", 1.0) >= 0.8]
        for fact in facts[:5]:
            lines.append(f"  [{cat}] {fact['key']}: {fact['value']}")

    # High-access facts (frequently recalled = important)
    frequent = sorted(
        [f for f in all_f.values() if f.get("access_count", 0) > 2],
        key=lambda f: f.get("access_count", 0), reverse=True
    )[:5]
    for fact in frequent:
        entry = f"  [freq] {fact['key']}: {fact['value']}"
        if entry not in lines:
            lines.append(entry)

    if len(lines) == 1:
        return ""

    return "\n".join(lines)
