"""http_request builtin tool's SSRF guard (tools/builtin/http_request.py).

No prior test file exercised this tool at all. socket.getaddrinfo is
monkeypatched for the "allowed" cases so these don't depend on real DNS/
network access; the "blocked" cases use real resolution for addresses
that are guaranteed to resolve locally without any network call
(localhost, IP literals) so the guard itself is exercised for real.
"""

import ipaddress
import socket
import sys
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from agent_hive.tools.builtin.http_request import http_request

# tools/builtin/__init__.py's `from .http_request import http_request`
# rebinds the `http_request` attribute on the `agent_hive.tools.builtin`
# package to the Function object below, shadowing the submodule --
# `import agent_hive.tools.builtin.http_request as x` would silently bind
# x to that Function (via attribute traversal), not the module. Going
# through sys.modules sidesteps that shadowing entirely.
http_request_module = sys.modules["agent_hive.tools.builtin.http_request"]

assert http_request.entrypoint is not None
# entrypoint is typed Optional on Function -- the assert above only
# narrows for this module scope, not for every test function below, so
# cast to a concrete callable type once here rather than re-asserting
# (or ignoring reportOptionalCall) at every call site.
_call = cast("Callable[..., str]", http_request.entrypoint)

_RealClient = httpx.Client  # captured before any monkeypatching below


def _fake_getaddrinfo_public(ip: str) -> Any:
    """Resolves any *hostname* to the given public IP, but still resolves
    an IP literal to itself -- a redirect Location header can carry a raw
    IP (e.g. http://127.0.0.1/...), and a fake that ignored the requested
    host entirely would mask exactly the redirect-bypass case this guard
    exists to catch."""

    def getaddrinfo(host: str, _port: object) -> list[tuple[object, ...]]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [(None, None, None, None, (ip, 0))]
        return [(None, None, None, None, (host, 0))]

    return getaddrinfo


def _mock_client_factory(handler: Any) -> Any:
    """Patched in place of httpx.Client so the tool's real client
    construction routes through a MockTransport instead of the network --
    must reference the pre-patch _RealClient, not httpx.Client, since
    patching httpx.Client would otherwise make this recurse into itself."""
    return lambda **kwargs: _RealClient(  # pyright: ignore[reportUnknownLambdaType]
        transport=httpx.MockTransport(handler), **kwargs
    )


def test_blocks_localhost_hostname() -> None:
    with pytest.raises(ValueError, match="internal/private network"):
        _call(url="http://localhost/")


def test_blocks_loopback_ip_literal() -> None:
    with pytest.raises(ValueError, match="internal/private network"):
        _call(url="http://127.0.0.1/")


def test_blocks_private_ip_literal() -> None:
    with pytest.raises(ValueError, match="internal/private network"):
        _call(url="http://192.168.1.1/")


def test_blocks_link_local_metadata_address() -> None:
    """169.254.169.254 -- the cloud-provider instance-metadata endpoint
    address, the canonical SSRF target."""
    with pytest.raises(ValueError, match="internal/private network"):
        _call(url="http://169.254.169.254/")


def test_blocks_unresolvable_host() -> None:
    with pytest.raises(ValueError, match="Could not resolve host"):
        _call(url="http://this-host-should-never-resolve.invalid/")


def test_blocks_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        _call(url="file:///etc/passwd")


def test_allows_public_address_and_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo_public("93.184.216.34"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello")

    monkeypatch.setattr(
        http_request_module.httpx,
        "Client",
        _mock_client_factory(handler),
    )

    result = _call(url="http://example.com/")
    assert "HTTP 200" in result
    assert "hello" in result


def test_redirect_to_private_address_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SSRF-via-redirect case: the initial URL resolves to a public
    address and passes the guard, but the server redirects to an internal
    one -- the second hop must be checked too, not just the first."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo_public("93.184.216.34"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    monkeypatch.setattr(
        http_request_module.httpx,
        "Client",
        _mock_client_factory(handler),
    )

    with pytest.raises(ValueError, match="internal/private network"):
        _call(url="http://example.com/")


def test_follows_redirect_to_another_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo_public("93.184.216.34"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "http://example.com/":
            return httpx.Response(302, headers={"location": "http://example.com/final"})
        return httpx.Response(200, text="redirected ok")

    monkeypatch.setattr(
        http_request_module.httpx,
        "Client",
        _mock_client_factory(handler),
    )

    result = _call(url="http://example.com/")
    assert "HTTP 200" in result
    assert "redirected ok" in result
