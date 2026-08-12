"""HTTP Request built-in tool (FR-8.1).

SSRF-guarded (undocumented gap until now): an agent's outbound requests
here can be driven by content this node didn't originate — a channel
message, a synced tool assignment (tools sync per FR-9.1) — so "any URL
an agent decides to fetch" is real untrusted input, not just a developer
convenience. Every hostname (the original URL and every redirect hop) is
resolved and checked against private/loopback/link-local/reserved IP
ranges before a request is sent, closing both the direct case and the
classic SSRF-via-redirect bypass (an allowed public URL that 302s to
http://169.254.169.254/... or http://localhost/...). Redirects are
therefore followed manually rather than via httpx's own
follow_redirects=True, which only validates the first URL — a server
that validated the request URL and then redirected somewhere internal
would otherwise sail straight through.
"""

import httpx
from agno.tools import tool

from rivulets.security.network import BlockedHostError, check_host_is_public

_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_CHARS = 20_000  # keep tool output out of an agent's context budget
_MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def _check_url_is_allowed(url: httpx.URL) -> None:
    if url.scheme not in ("http", "https"):
        raise BlockedHostError(f"Unsupported URL scheme {url.scheme!r}")
    if not url.host:
        raise BlockedHostError(f"URL has no host: {url!r}")
    check_host_is_public(url.host)


@tool
def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> str:
    """Make an HTTP request and return the response status and truncated body."""
    target = httpx.URL(url)
    _check_url_is_allowed(target)

    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = client.request(method.upper(), str(target), headers=headers, content=body)
        redirects_followed = 0
        while response.status_code in _REDIRECT_STATUS_CODES and "location" in response.headers:
            if redirects_followed >= _MAX_REDIRECTS:
                # Erroring here (rather than silently returning the last,
                # unfollowed redirect response as if it were the real
                # answer) matches httpx/requests' own default behavior for
                # exceeding a redirect limit -- a 3xx body handed back with
                # no indication *why* would look like the server's actual
                # response, not like a safety cutoff.
                raise BlockedHostError(f"Too many redirects (exceeded {_MAX_REDIRECTS})")
            target = target.join(response.headers["location"])
            _check_url_is_allowed(target)
            response = client.request(method.upper(), str(target), headers=headers, content=body)
            redirects_followed += 1

    text = response.text[:_MAX_RESPONSE_CHARS]
    return f"HTTP {response.status_code}\n{text}"
