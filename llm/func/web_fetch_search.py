"""
func/web_fetch_search.py — Web Search + URL Fetch for SDX Agent

Two tools in one file:

  web_search(query, ...)
    → Uses DuckDuckGo (no API key) or Google Custom Search (if configured)
    → Falls back gracefully between backends
    → Returns titles, URLs, snippets
    → max 8 searches per session (mirrors Claude Code behaviour)

  web_fetch(url, prompt)
    → Fetches URL → converts HTML to clean Markdown
    → Applies a focused extraction prompt via the Gemini API
    → Handles redirects, timeouts, binary files
    → Blocks private/local addresses (security)

Backends (web_search):
  1. DuckDuckGo Instant Answer API  — free, no key
  2. DuckDuckGo HTML scrape         — fallback
  3. Google Custom Search API       — if GOOGLE_CSE_KEY + GOOGLE_CSE_CX set

Dependencies:
  pip install httpx markdownify  (markdownify optional — degrades gracefully)

Environment variables (optional):
  GOOGLE_CSE_KEY   — Google Custom Search API key
  GOOGLE_CSE_CX    — Google Custom Search Engine ID
  WEB_SEARCH_MAX   — max searches per session (default 8)
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import time
import urllib.parse
from typing import Optional
from pathlib import Path

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False
    import urllib.request
    import urllib.error

try:
    from markdownify import markdownify as _md
    _HAS_MD = True
except ImportError:
    _HAS_MD = False

# ── Session-level search counter (mirrors Claude Code max_uses=8) ─────────────
_search_count = 0
_SEARCH_MAX   = int(os.environ.get("WEB_SEARCH_MAX", "8"))

# ── Blocked local ranges ──────────────────────────────────────────────────────
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

MAX_FETCH_BYTES  = 2_000_000   # 2 MB cap
MAX_RESULT_CHARS = 80_000      # result truncation


# ============================================================================
# SCHEMAS
# ============================================================================

schema_web_search = {
    "name": "web_search",
    "description": (
        "Search the web for current information, documentation, error messages, "
        "or anything outside the codebase. "
        "Returns titles, URLs, and snippets. "
        "Use allowed_domains to restrict results (e.g. docs.python.org). "
        "Max 8 searches per session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query. Be specific for best results."
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only return results from these domains. E.g. ['docs.python.org', 'github.com']."
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Never return results from these domains."
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (1-10). Default: 5.",
                "default": 5
            }
        },
        "required": ["query"]
    }
}

schema_web_fetch = {
    "name": "web_fetch",
    "description": (
        "Fetch the content of a URL and extract relevant information. "
        "Converts HTML to clean Markdown. "
        "Use prompt to specify what to extract (e.g. 'list all API endpoints', "
        "'summarise the installation steps'). "
        "WILL FAIL for authenticated/private URLs. "
        "For GitHub raw files, docs sites, or public APIs this works well."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to fetch (must start with http:// or https://)."
            },
            "prompt": {
                "type": "string",
                "description": "What to extract or summarise from the page.",
                "default": "Summarise the key information on this page."
            },
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return. Default: 8000.",
                "default": 8000
            }
        },
        "required": ["url"]
    }
}


# ============================================================================
# WEB SEARCH
# ============================================================================

def web_search(
    working_directory: str,
    query: str,
    allowed_domains: Optional[list[str]] = None,
    blocked_domains: Optional[list[str]] = None,
    max_results: int = 5,
) -> str:
    global _search_count

    if _search_count >= _SEARCH_MAX:
        return (
            f"⚠ Search limit reached ({_SEARCH_MAX} searches per session). "
            "Use web_fetch with a direct URL if you already know where to look."
        )

    _search_count += 1
    remaining = _SEARCH_MAX - _search_count

    # Build domain-restricted query
    full_query = query
    if allowed_domains:
        site_clauses = " OR ".join(f"site:{d}" for d in allowed_domains)
        full_query   = f"({site_clauses}) {query}"

    results: list[dict] = []

    # ── Backend 1: Google Custom Search ──────────────────────────────────────
    gkey = os.environ.get("GOOGLE_CSE_KEY")
    gcx  = os.environ.get("GOOGLE_CSE_CX")
    if gkey and gcx and not results:
        results = _google_search(full_query, gkey, gcx, max_results)

    # ── Backend 2: DuckDuckGo Instant Answer ─────────────────────────────────
    if not results:
        results = _ddg_instant(full_query, max_results)

    # ── Backend 3: DuckDuckGo HTML scrape ────────────────────────────────────
    if not results:
        results = _ddg_html(full_query, max_results)

    if not results:
        return f"No results found for: {query!r}"

    # ── Filter blocked domains ────────────────────────────────────────────────
    if blocked_domains:
        results = [
            r for r in results
            if not any(bd in r.get("url", "") for bd in blocked_domains)
        ]

    # ── Format ────────────────────────────────────────────────────────────────
    return _fmt_search_results(query, results, remaining)


def _google_search(query: str, key: str, cx: str, n: int) -> list[dict]:
    url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?key={key}&cx={cx}&q={urllib.parse.quote(query)}&num={min(n, 10)}"
    )
    try:
        body = _get(url, timeout=10)
        import json
        data = json.loads(body)
        items = data.get("items", [])
        return [
            {
                "title":   i.get("title", ""),
                "url":     i.get("link", ""),
                "snippet": i.get("snippet", ""),
            }
            for i in items
        ]
    except Exception:
        return []


def _ddg_instant(query: str, n: int) -> list[dict]:
    """DuckDuckGo Instant Answer API — returns Related Topics as results."""
    url = (
        "https://api.duckduckgo.com/"
        f"?q={urllib.parse.quote(query)}&format=json&no_redirect=1&no_html=1"
    )
    try:
        body = _get(url, timeout=8)
        import json
        data = json.loads(body)
        results: list[dict] = []

        # Abstract (top result)
        if data.get("AbstractURL") and data.get("AbstractText"):
            results.append({
                "title":   data.get("Heading", query),
                "url":     data["AbstractURL"],
                "snippet": data["AbstractText"][:300],
            })

        # Related topics
        for topic in data.get("RelatedTopics", []):
            if len(results) >= n:
                break
            if isinstance(topic, dict) and topic.get("FirstURL"):
                results.append({
                    "title":   _strip_tags(topic.get("Text", ""))[:80],
                    "url":     topic["FirstURL"],
                    "snippet": _strip_tags(topic.get("Text", ""))[:300],
                })

        return results
    except Exception:
        return []


def _ddg_html(query: str, n: int) -> list[dict]:
    """DuckDuckGo HTML search scrape — last resort fallback."""
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SDXAgent/1.0; research-bot)",
    }
    try:
        body = _get(url, timeout=10, headers=headers)
        results: list[dict] = []

        # Extract result blocks via regex (avoids BeautifulSoup dependency)
        blocks = re.findall(
            r'class="result__title".*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
            r'.*?class="result__snippet"[^>]*>(.*?)</div>',
            body, re.DOTALL
        )
        for href, title, snippet in blocks[:n]:
            # DDG wraps URLs in //duckduckgo.com/l/?uddg=<encoded>
            real_url = _ddg_unwrap(href)
            results.append({
                "title":   _strip_tags(title).strip(),
                "url":     real_url,
                "snippet": _strip_tags(snippet).strip()[:300],
            })

        return results
    except Exception:
        return []


def _ddg_unwrap(href: str) -> str:
    """Extract real URL from DuckDuckGo redirect wrapper."""
    if "uddg=" in href:
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    return href


def _fmt_search_results(query: str, results: list[dict], remaining: int) -> str:
    lines = [
        f"Web search: {query!r}",
        f"{'─' * 55}",
        f"Found {len(results)} result(s)  ·  {remaining} searches remaining\n",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('title', 'No title')}")
        lines.append(f"    {r.get('url', '')}")
        if r.get("snippet"):
            lines.append(f"    {r['snippet'][:200]}")
        lines.append("")
    lines.append("→ Use web_fetch(url, prompt) to read any of these pages in full.")
    return "\n".join(lines)


# ============================================================================
# WEB FETCH
# ============================================================================

def web_fetch(
    working_directory: str,
    url: str,
    prompt: str = "Summarise the key information on this page.",
    max_chars: int = 8000,
) -> str:
    start = time.time()

    # ── Validate URL ──────────────────────────────────────────────────────────
    err = _validate_url(url)
    if err:
        return f"⛔ {err}"

    # ── Fetch ─────────────────────────────────────────────────────────────────
    try:
        content, final_url, status, content_type, size_bytes = _fetch_url(url)
    except _RedirectError as e:
        return (
            f"↪ Redirect detected ({e.code}): {url}\n"
            f"  → New URL: {e.location}\n"
            f"  Call web_fetch again with url={e.location!r}"
        )
    except Exception as e:
        return f"Fetch failed: {e}"

    duration_ms = int((time.time() - start) * 1000)

    # ── Status check ──────────────────────────────────────────────────────────
    if status >= 400:
        return f"HTTP {status} — could not fetch {url}"

    # ── HTML → Markdown ───────────────────────────────────────────────────────
    if "text/html" in content_type or "text/plain" in content_type:
        markdown = _html_to_markdown(content)
    elif "application/json" in content_type:
        markdown = content[:max_chars]
    else:
        # Binary / unknown — save info
        return (
            f"Binary content ({content_type}, {_fmt_size(size_bytes)}) at {final_url}\n"
            f"Cannot extract text. Download manually if needed."
        )

    # ── Apply extraction prompt via Gemini ────────────────────────────────────
    extracted = _apply_prompt(markdown, prompt, url)

    # ── Truncate ──────────────────────────────────────────────────────────────
    if len(extracted) > max_chars:
        extracted = extracted[:max_chars] + f"\n\n[truncated at {max_chars} chars]"

    header = (
        f"Fetched: {final_url}\n"
        f"Status: {status}  ·  Size: {_fmt_size(size_bytes)}  ·  {duration_ms}ms\n"
        f"{'─' * 55}\n"
    )
    return header + extracted


# ── URL validation ────────────────────────────────────────────────────────────

def _validate_url(url: str) -> Optional[str]:
    if not url.startswith(("http://", "https://")):
        return f"Invalid URL (must start with http:// or https://): {url}"
    try:
        parsed = urllib.parse.urlparse(url)
        host   = parsed.hostname or ""

        # Block private/loopback addresses
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
            for net in _PRIVATE_NETS:
                if ip in net:
                    return f"Blocked: {host} resolves to a private address ({ip})"
        except (socket.gaierror, ValueError):
            pass  # Can't resolve — let the fetch fail naturally

        # Block obviously internal hostnames
        if host in ("localhost", "0.0.0.0") or host.endswith(".local"):
            return f"Blocked: local hostname {host}"

    except Exception as e:
        return f"URL parse error: {e}"
    return None


class _RedirectError(Exception):
    def __init__(self, code: int, location: str):
        self.code     = code
        self.location = location


# ── HTTP fetch ────────────────────────────────────────────────────────────────

def _fetch_url(
    url: str,
    timeout: int = 20,
) -> tuple[str, str, int, str, int]:
    """
    Returns (text_content, final_url, status_code, content_type, bytes).
    Raises _RedirectError for cross-host redirects.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SDXAgent/1.0; +research)",
        "Accept":     "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if _HAS_HTTPX:
        return _fetch_httpx(url, headers, timeout)
    else:
        return _fetch_urllib(url, headers, timeout)


def _fetch_httpx(url, headers, timeout):
    original_host = urllib.parse.urlparse(url).hostname

    with httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        headers=headers,
        max_redirects=5,
    ) as client:
        r = client.get(url)

        # Check if redirected to a different host
        final_host = urllib.parse.urlparse(str(r.url)).hostname
        if final_host != original_host and r.history:
            last_redirect = r.history[-1]
            if last_redirect.status_code in (301, 302, 307, 308):
                raise _RedirectError(last_redirect.status_code, str(r.url))

        content_type = r.headers.get("content-type", "text/html")
        raw          = r.content

        if len(raw) > MAX_FETCH_BYTES:
            raw = raw[:MAX_FETCH_BYTES]

        text = raw.decode("utf-8", errors="replace")
        return text, str(r.url), r.status_code, content_type, len(raw)


def _fetch_urllib(url, headers, timeout):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "text/html")
            raw          = resp.read(MAX_FETCH_BYTES)
            text         = raw.decode("utf-8", errors="replace")
            return text, resp.url, resp.status, content_type, len(raw)
    except urllib.error.HTTPError as e:
        return "", url, e.code, "", 0


# ── HTML → Markdown ───────────────────────────────────────────────────────────

_SCRIPT_RE  = re.compile(r'<script[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE)
_STYLE_RE   = re.compile(r'<style[^>]*>.*?</style>',  re.DOTALL | re.IGNORECASE)
_NAV_RE     = re.compile(r'<nav[^>]*>.*?</nav>',       re.DOTALL | re.IGNORECASE)
_FOOTER_RE  = re.compile(r'<footer[^>]*>.*?</footer>', re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r'<!--.*?-->',                re.DOTALL)
_TAG_RE     = re.compile(r'<[^>]+>')
_MULTI_NL   = re.compile(r'\n{3,}')


def _html_to_markdown(html: str) -> str:
    if _HAS_MD:
        # Clean noise first
        html = _SCRIPT_RE.sub("", html)
        html = _STYLE_RE.sub("", html)
        html = _NAV_RE.sub("", html)
        html = _FOOTER_RE.sub("", html)
        html = _COMMENT_RE.sub("", html)
        md = _md(html, heading_style="ATX", bullets="-", strip=["img", "script", "style"])
        return _MULTI_NL.sub("\n\n", md).strip()
    else:
        # Fallback: strip tags manually
        text = _SCRIPT_RE.sub("", html)
        text = _STYLE_RE.sub("", text)
        text = _TAG_RE.sub(" ", text)
        text = re.sub(r' {2,}', ' ', text)
        return _MULTI_NL.sub("\n\n", text).strip()


# ── Extraction via Gemini ─────────────────────────────────────────────────────

def _apply_prompt(content: str, prompt: str, url: str) -> str:
    """
    Use Gemini to apply the extraction prompt to the fetched content.
    Falls back to returning truncated raw content if API unavailable.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        # No API key — return raw markdown, truncated
        return content[:12000]

    try:
        from google import genai
        from google.genai import types as gtypes

        client = genai.Client(api_key=api_key)

        system = (
            "You are a precise information extractor. "
            "Given web page content (as Markdown), apply the user's extraction prompt. "
            "Be concise. Include all relevant details. "
            "Preserve code blocks, URLs, and structured data exactly as-is."
        )
        user_msg = (
            f"Source URL: {url}\n\n"
            f"Extraction task: {prompt}\n\n"
            f"--- PAGE CONTENT ---\n{content[:40000]}\n--- END ---"
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[gtypes.Content(
                role="user",
                parts=[gtypes.Part(text=user_msg)]
            )],
            config=gtypes.GenerateContentConfig(
                system_instruction=system,
                temperature=0.1,
                max_output_tokens=4096,
            )
        )
        return response.text or content[:8000]

    except Exception:
        # Graceful degradation
        return content[:8000]


# ── Utilities ─────────────────────────────────────────────────────────────────

def _get(
    url: str,
    timeout: int = 10,
    headers: Optional[dict] = None,
) -> str:
    """Simple GET — uses httpx if available, else urllib."""
    h = {
        "User-Agent": "Mozilla/5.0 (compatible; SDXAgent/1.0)",
        **(headers or {})
    }
    if _HAS_HTTPX:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            return c.get(url, headers=h).text
    else:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


def _strip_tags(html: str) -> str:
    return re.sub(r'<[^>]+>', '', html)


def _fmt_size(n: int) -> str:
    if n < 1024:        return f"{n}B"
    if n < 1024**2:     return f"{n/1024:.1f}KB"
    return f"{n/1024**2:.1f}MB"


def reset_search_count():
    """Call at session start to reset the per-session counter."""
    global _search_count
    _search_count = 0