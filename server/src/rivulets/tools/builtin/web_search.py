"""Web Search built-in tool (FR-8.1).

Provider is configurable (Brave, Tavily, etc.) per the requirement; this
implements Brave Search as the first provider since it needs only a
single API key header. The API key comes from the OS keychain reference
stored on `provider_config`-style config, not from this module — callers
pass it in explicitly so the tool stays provider-agnostic and untestable
network calls stay out of unit tests by default.
"""

import httpx
from agno.tools import tool

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT_SECONDS = 10.0


@tool
def web_search(query: str, api_key: str, count: int = 5) -> str:
    """Search the web via Brave Search and return titles, URLs, and snippets."""
    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        response = client.get(
            _BRAVE_ENDPOINT,
            params={"q": query, "count": count},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
        response.raise_for_status()
    results = response.json().get("web", {}).get("results", [])
    lines = [f"{r['title']}\n{r['url']}\n{r.get('description', '')}" for r in results]
    return "\n\n".join(lines) or "No results."
