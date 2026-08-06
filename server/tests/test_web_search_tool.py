"""web_search builtin tool (tools/builtin/web_search.py).

No prior test file exercised this tool at all. httpx.Client is
monkeypatched to route through a MockTransport rather than the network,
the same pattern test_http_request_tool.py uses for the http_request tool.
"""

import sys
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from rivulets.tools.builtin.web_search import web_search

web_search_module = sys.modules["rivulets.tools.builtin.web_search"]

assert web_search.entrypoint is not None
_call = cast("Callable[..., str]", web_search.entrypoint)

_RealClient = httpx.Client


def _mock_client_factory(handler: Any) -> Any:
    return lambda **kwargs: _RealClient(  # pyright: ignore[reportUnknownLambdaType]
        transport=httpx.MockTransport(handler), **kwargs
    )


def test_formats_results_with_title_url_and_description(monkeypatch: pytest.MonkeyPatch) -> None:
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
                        },
                        {
                            "title": "Result Two",
                            "url": "https://example.com/two",
                            "description": "Second result",
                        },
                    ]
                }
            },
        )

    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    result = _call(query="rivulets", api_key="secret-key")

    assert result == (
        "Result One\nhttps://example.com/one\nFirst result"
        "\n\n"
        "Result Two\nhttps://example.com/two\nSecond result"
    )
    assert captured["headers"]["x-subscription-token"] == "secret-key"
    assert "q=rivulets" in captured["url"]
    assert "count=5" in captured["url"]


def test_missing_description_defaults_to_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "No Desc", "url": "https://example.com/x"}]}},
        )

    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    result = _call(query="anything", api_key="k")
    assert result == "No Desc\nhttps://example.com/x\n"


def test_no_results_returns_placeholder_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"web": {"results": []}})

    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    assert _call(query="nothing", api_key="k") == "No results."


def test_custom_count_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"web": {"results": []}})

    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    _call(query="q", api_key="k", count=10)
    assert "count=10" in captured["url"]


def test_non_2xx_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    monkeypatch.setattr(web_search_module.httpx, "Client", _mock_client_factory(handler))

    with pytest.raises(httpx.HTTPStatusError):
        _call(query="q", api_key="wrong")
