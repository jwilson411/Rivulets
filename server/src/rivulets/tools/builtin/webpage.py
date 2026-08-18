"""Fetch a public web page and return readable text.

Pairs with web_search: search finds URLs, this reads one. Same SSRF
posture as http_request -- every hostname (original URL and each
redirect hop) must resolve to a public address. GET only; arbitrary
methods stay on http_request.
"""

from __future__ import annotations

import re
from html import unescape

import httpx
from agno.tools import tool

from rivulets.security.network import BlockedHostError, check_host_is_public

_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_CHARS = 20_000
_MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_USER_AGENT = "Rivulets/0.6 (local workspace; fetch_webpage)"
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _check_url_is_allowed(url: httpx.URL) -> None:
    if url.scheme not in ("http", "https"):
        raise BlockedHostError(f"Unsupported URL scheme {url.scheme!r}")
    if not url.host:
        raise BlockedHostError(f"URL has no host: {url!r}")
    check_host_is_public(url.host)


def html_to_text(html: str) -> str:
    without = _SCRIPT_RE.sub(" ", html)
    without = _STYLE_RE.sub(" ", without)
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", without))).strip()


@tool
def fetch_webpage(url: str) -> str:
    """Fetch a public web page and return its readable text content."""
    target = httpx.URL(url)
    _check_url_is_allowed(target)

    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = client.get(
            str(target), headers={"User-Agent": _USER_AGENT, "Accept": "text/html"}
        )
        redirects_followed = 0
        while response.status_code in _REDIRECT_STATUS_CODES and "location" in response.headers:
            if redirects_followed >= _MAX_REDIRECTS:
                raise BlockedHostError(f"Too many redirects (exceeded {_MAX_REDIRECTS})")
            target = target.join(response.headers["location"])
            _check_url_is_allowed(target)
            response = client.get(
                str(target), headers={"User-Agent": _USER_AGENT, "Accept": "text/html"}
            )
            redirects_followed += 1

    content_type = response.headers.get("content-type", "")
    body = response.text
    if "html" in content_type.lower() or body.lstrip()[:15].lower().startswith(
        ("<!doctype html", "<html")
    ):
        body = html_to_text(body)
    text = body[:_MAX_RESPONSE_CHARS]
    return f"HTTP {response.status_code}\n{text}"
