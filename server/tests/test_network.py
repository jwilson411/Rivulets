import socket
from typing import Any

import httpcore
import httpx
import pytest

from rivulets.security.network import (
    BlockedHostError,
    PublicAsyncHTTPTransport,
    PublicHTTPTransport,
    _PublicAsyncBackend,
    _PublicSyncBackend,
    assert_public_http_url,
    check_host_is_public,
    create_public_mcp_http_client,
    detect_lan_address,
    is_loopback_host,
    public_http_client,
    resolve_public_addresses,
)


def test_is_loopback_host_recognizes_common_loopback_forms() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("LOCALHOST")
    assert is_loopback_host("::1")


def test_is_loopback_host_rejects_non_loopback_hosts() -> None:
    assert not is_loopback_host("192.168.1.5")
    assert not is_loopback_host("example.com")
    assert not is_loopback_host("")


def test_detect_lan_address_returns_a_string_or_none_and_never_raises() -> None:
    # Best-effort: on a machine with no network route this legitimately
    # returns None, so only assert on the type, not a specific value.
    result = detect_lan_address()
    assert result is None or isinstance(result, str)


def test_resolve_public_addresses_returns_unique_public_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def getaddrinfo(_host: str, _port: object) -> list[tuple[object, ...]]:
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("1.2.3.4", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    assert resolve_public_addresses("example.com") == ("93.184.216.34", "1.2.3.4")
    assert check_host_is_public("example.com") == ("93.184.216.34", "1.2.3.4")


def test_resolve_public_addresses_blocks_mixed_public_and_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def getaddrinfo(_host: str, _port: object) -> list[tuple[object, ...]]:
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    with pytest.raises(BlockedHostError, match="internal/private network"):
        resolve_public_addresses("mixed.example")


def test_resolve_public_addresses_blocks_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def getaddrinfo(_host: str, _port: object) -> list[tuple[object, ...]]:
        return []

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    with pytest.raises(BlockedHostError, match="Could not resolve host"):
        resolve_public_addresses("empty.example")


def test_resolve_public_addresses_blocks_unresolvable_host() -> None:
    with pytest.raises(BlockedHostError, match="Could not resolve host"):
        resolve_public_addresses("this-host-should-never-resolve.invalid")


def test_assert_public_http_url_rejects_non_http_scheme() -> None:
    with pytest.raises(BlockedHostError, match="Unsupported URL scheme"):
        assert_public_http_url(httpx.URL("file:///etc/passwd"))


def test_assert_public_http_url_rejects_missing_host() -> None:
    with pytest.raises(BlockedHostError, match="no host"):
        assert_public_http_url(httpx.URL("http:///"))


def _patch_sync_connect(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    def connect_tcp(
        self: httpcore.SyncBackend,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        seen.append(host)
        raise httpcore.ConnectError("do not actually connect")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_tcp)


def test_public_transport_dials_the_addresses_from_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#477: connect must use the IPs that just passed, not the hostname
    (which httpx would resolve again)."""
    answers = {"evil.example": "93.184.216.34"}

    def getaddrinfo(host: str, _port: object) -> list[tuple[object, ...]]:
        if host == "evil.example":
            ip = answers["evil.example"]
            # Flip after the check so a second lookup would hit loopback.
            answers["evil.example"] = "127.0.0.1"
            return [(None, None, None, None, (ip, 0))]
        return [(None, None, None, None, (host, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    seen: list[str] = []
    _patch_sync_connect(monkeypatch, seen)

    with pytest.raises(httpx.ConnectError):
        PublicHTTPTransport().handle_request(httpx.Request("GET", "http://evil.example/"))
    assert seen == ["93.184.216.34"]


def test_public_transport_tries_each_public_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def getaddrinfo(_host: str, _port: object) -> list[tuple[object, ...]]:
        return [
            (None, None, None, None, ("1.2.3.4", 0)),
            (None, None, None, None, ("93.184.216.34", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    seen: list[str] = []
    _patch_sync_connect(monkeypatch, seen)

    with pytest.raises(httpx.ConnectError):
        PublicHTTPTransport().handle_request(httpx.Request("GET", "http://multi.example/"))
    assert seen == ["1.2.3.4", "93.184.216.34"]


def test_public_transport_rejects_unix_socket() -> None:
    with pytest.raises(BlockedHostError, match="Unix sockets"):
        _PublicSyncBackend().connect_unix_socket("/var/run/mcp.sock")


async def test_public_async_transport_rejects_unix_socket() -> None:
    with pytest.raises(BlockedHostError, match="Unix sockets"):
        await _PublicAsyncBackend().connect_unix_socket("/var/run/mcp.sock")


def test_public_transport_rejects_private_host_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    _patch_sync_connect(monkeypatch, seen)
    with pytest.raises(BlockedHostError, match="internal/private network"):
        PublicHTTPTransport().handle_request(httpx.Request("GET", "http://127.0.0.1/"))
    assert seen == []


def test_public_http_client_uses_public_transport() -> None:
    with public_http_client() as client:
        assert isinstance(client._transport, PublicHTTPTransport)


async def test_create_public_mcp_http_client_default_timeout() -> None:
    async with create_public_mcp_http_client() as client:
        assert isinstance(client._transport, PublicAsyncHTTPTransport)


async def test_create_public_mcp_http_client_uses_async_public_transport() -> None:
    timeout = httpx.Timeout(5.0)
    async with create_public_mcp_http_client(
        headers={"X-Test": "1"}, timeout=timeout, auth=httpx.BasicAuth("u", "p")
    ) as client:
        assert isinstance(client._transport, PublicAsyncHTTPTransport)
        assert client.headers["X-Test"] == "1"
        assert client.timeout == timeout


async def test_public_async_backend_sleep_delegates() -> None:
    await _PublicAsyncBackend().sleep(0)


async def test_public_async_transport_dials_the_addresses_from_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {"evil.example": "93.184.216.34"}

    def getaddrinfo(host: str, _port: object) -> list[tuple[object, ...]]:
        if host == "evil.example":
            ip = answers["evil.example"]
            answers["evil.example"] = "127.0.0.1"
            return [(None, None, None, None, (ip, 0))]
        return [(None, None, None, None, (host, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    seen: list[str] = []

    async def connect_tcp(
        self: object,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        seen.append(host)
        raise httpcore.ConnectError("do not actually connect")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect_tcp)

    with pytest.raises(httpx.ConnectError):
        await PublicAsyncHTTPTransport().handle_async_request(
            httpx.Request("GET", "http://evil.example/")
        )
    assert seen == ["93.184.216.34"]
