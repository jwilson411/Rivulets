"""fetch_webpage builtin tool (tools/builtin/webpage.py)."""

import ipaddress
import socket
import sys
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from rivulets.tools.builtin.webpage import fetch_webpage, html_to_text

webpage_module = sys.modules["rivulets.tools.builtin.webpage"]

assert fetch_webpage.entrypoint is not None
_call = cast("Callable[..., str]", fetch_webpage.entrypoint)

_RealClient = httpx.Client


def _fake_getaddrinfo_public(ip: str) -> Any:
    def getaddrinfo(host: str, _port: object) -> list[tuple[object, ...]]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [(None, None, None, None, (ip, 0))]
        return [(None, None, None, None, (host, 0))]

    return getaddrinfo


def _mock_client_factory(handler: Any) -> Any:
    return lambda **kwargs: _RealClient(  # pyright: ignore[reportUnknownLambdaType]
        transport=httpx.MockTransport(handler), **kwargs
    )


def test_html_to_text_strips_markup_and_scripts() -> None:
    html = (
        "<html><head><style>body{color:red}</style></head>"
        "<body><script>alert(1)</script><h1>Hello</h1><p>World</p></body></html>"
    )
    assert html_to_text(html) == "Hello World"


def test_blocks_localhost() -> None:
    with pytest.raises(ValueError, match="internal/private network"):
        _call(url="http://localhost/")


def test_returns_readable_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_public("93.184.216.34"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><h1>Title</h1><p>Body copy.</p></body></html>",
        )

    monkeypatch.setattr(webpage_module.httpx, "Client", _mock_client_factory(handler))

    result = _call(url="https://example.com/page")
    assert result.startswith("HTTP 200\n")
    assert "Title" in result
    assert "Body copy." in result
    assert "<h1>" not in result
