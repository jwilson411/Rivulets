"""web_search builtin tool (tools/builtin/web_search.py).

httpx.Client is monkeypatched to route through a MockTransport rather
than the network, the same pattern test_http_request_tool.py uses.
"""

import sys
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from rivulets.tools.builtin.web_search import _configured_brave_key, web_search

web_search_module = sys.modules["rivulets.tools.builtin.web_search"]

assert web_search.entrypoint is not None
_call = cast("Callable[..., str]", web_search.entrypoint)

_RealClient = httpx.Client


def _mock_client_factory(handler: Any) -> Any:
    return lambda **kwargs: _RealClient(  # pyright: ignore[reportUnknownLambdaType]
        transport=httpx.MockTransport(handler), **kwargs
    )


def test_configured_brave_key_strips_and_treats_blank_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_search_module,
        "get_settings",
        lambda: type("S", (), {"brave_api_key": "  secret  "})(),
    )
    assert _configured_brave_key() == "secret"
    monkeypatch.setattr(
        web_search_module, "get_settings", lambda: type("S", (), {"brave_api_key": "   "})()
    )
    assert _configured_brave_key() is None
    monkeypatch.setattr(
        web_search_module, "get_settings", lambda: type("S", (), {"brave_api_key": None})()
    )
    assert _configured_brave_key() is None


def test_duckduckgo_formats_results_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            text=(
                '<a class="result__a" href="//duckduckgo.com/l/'
                '?uddg=https%3A%2F%2Fexample.com%2Fone">'
                "Result One</a>"
                '<a class="result__snippet">First result</a>'
                '<a class="result__a" href="https://example.com/two">Result Two</a>'
                '<a class="result__snippet">Second result</a>'
            ),
        )

    monkeypatch.setattr(web_search_module, "_configured_brave_key", lambda: None)
    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    result = _call(query="rivulets")

    assert result == (
        "Result One\nhttps://example.com/one\nFirst result"
        "\n\n"
        "Result Two\nhttps://example.com/two\nSecond result"
    )
    assert "html.duckduckgo.com" in captured["url"]
    assert "q=rivulets" in captured["body"]


def test_brave_is_used_when_api_key_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Result One",
                            "url": "https://example.com/one",
                            "description": "First result",
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(web_search_module, "_configured_brave_key", lambda: "secret-key")
    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    result = _call(query="rivulets")

    assert result == "Result One\nhttps://example.com/one\nFirst result"
    assert captured["headers"]["x-subscription-token"] == "secret-key"
    assert "q=rivulets" in captured["url"]
    assert "count=5" in captured["url"]


def test_missing_description_defaults_to_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "No Desc", "url": "https://example.com/x"}]}},
        )

    monkeypatch.setattr(web_search_module, "_configured_brave_key", lambda: "k")
    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    result = _call(query="anything")
    assert result == "No Desc\nhttps://example.com/x\n"


def test_no_results_returns_placeholder_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"web": {"results": []}})

    monkeypatch.setattr(web_search_module, "_configured_brave_key", lambda: "k")
    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    assert _call(query="nothing") == "No results."


def test_custom_count_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"web": {"results": []}})

    monkeypatch.setattr(web_search_module, "_configured_brave_key", lambda: "k")
    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    _call(query="q", count=10)
    assert "count=10" in captured["url"]


def test_non_2xx_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    monkeypatch.setattr(web_search_module, "_configured_brave_key", lambda: "wrong")
    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    with pytest.raises(httpx.HTTPStatusError):
        _call(query="q")
