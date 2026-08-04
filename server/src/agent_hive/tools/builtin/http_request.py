"""HTTP Request built-in tool (FR-8.1)."""

import httpx
from agno.tools import tool

_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_CHARS = 20_000  # keep tool output out of an agent's context budget


@tool
def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> str:
    """Make an HTTP request and return the response status and truncated body."""
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.request(method.upper(), url, headers=headers, content=body)
    text = response.text[:_MAX_RESPONSE_CHARS]
    return f"HTTP {response.status_code}\n{text}"
