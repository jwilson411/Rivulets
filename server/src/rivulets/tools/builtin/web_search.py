"""Web Search built-in tool (FR-8.1).

Brave Search is used when `RIVULETS_BRAVE_API_KEY` is set; otherwise the
tool falls back to DuckDuckGo HTML search so a fresh workspace can search
without any extra credential. The API key is never a tool argument -- a
model passing a key is how the previous version failed in practice.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from agno.tools import tool

from rivulets.config import get_settings

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
_TIMEOUT_SECONDS = 10.0
_USER_AGENT = "Rivulets/0.6 (local workspace; web_search)"
_RESULT_LINK_RE = re.compile(
    r'class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|td|div)>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _configured_brave_key() -> str | None:
    key = get_settings().brave_api_key
    if key is None:
        return None
    stripped = key.strip()
    return stripped or None


def _plain_text(value: str) -> str:
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", value))).strip()


def _unwrap_ddg_url(href: str) -> str:
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    uddg = qs.get("uddg")
    if uddg:
        return unquote(uddg[0])
    return href


def _format_results(rows: list[tuple[str, str, str]]) -> str:
    if not rows:
        return "No results."
    return "\n\n".join(f"{title}\n{url}\n{snippet}" for title, url, snippet in rows)


def _search_brave(query: str, api_key: str, count: int) -> str:
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        response = client.get(
            _BRAVE_ENDPOINT,
            params={"q": query, "count": count},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
        response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    web = cast(dict[str, Any], payload.get("web") or {})
    results = cast(list[object], web.get("results") or [])
    rows: list[tuple[str, str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, object], item)
        title = entry.get("title")
        url = entry.get("url")
        description = entry.get("description")
        rows.append(
            (
                title if isinstance(title, str) else "",
                url if isinstance(url, str) else "",
                description if isinstance(description, str) else "",
            )
        )
    return _format_results(rows[:count])


def _search_duckduckgo(query: str, count: int) -> str:
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.post(
            _DDG_ENDPOINT,
            data={"q": query},
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        )
        response.raise_for_status()
    links = _RESULT_LINK_RE.findall(response.text)
    snippets = [_plain_text(snippet) for snippet in _SNIPPET_RE.findall(response.text)]
    rows: list[tuple[str, str, str]] = []
    for index, (href, title_html) in enumerate(links[:count]):
        title = _plain_text(title_html)
        url = _unwrap_ddg_url(unescape(href))
        snippet = snippets[index] if index < len(snippets) else ""
        if title or url:
            rows.append((title, url, snippet))
    return _format_results(rows)


@tool
def web_search(query: str, count: int = 5) -> str:
    """Search the public web and return titles, URLs, and snippets.

    Uses Brave Search when a workspace Brave API key is configured,
    otherwise DuckDuckGo. Does not require the caller to supply a key.
    """
    bounded = max(1, min(count, 10))
    brave_key = _configured_brave_key()
    if brave_key is not None:
        return _search_brave(query, brave_key, bounded)
    return _search_duckduckgo(query, bounded)
