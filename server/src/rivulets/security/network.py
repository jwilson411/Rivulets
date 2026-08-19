"""LAN reachability detection for invite links (#121), and the shared SSRF
guard for any outbound connection whose target can be influenced by
untrusted input.

`create_invite` (api/invites.py) used to build the invite URL purely from
`request.base_url` -- whatever host the owner's own browser used to reach
the App Server. Since Rivulets binds to loopback by default (NFR-3.4,
docker-compose.yml), that's almost always `127.0.0.1`, making the link
meaningless off the owner's machine with no warning that it will be.

`app_server_host` (config.py) isn't a reliable signal for whether a
*remote* peer can actually reach this node: inside the Docker image it's
always `0.0.0.0` regardless of whether the host's `-p` flag published that
to loopback or the LAN (see main.py's host-guard comment), and even for a
native install, "bound to 0.0.0.0" doesn't tell us what address a peer
would dial. So instead of trusting either the bind address or the
request's own Host header, we do a best-effort local network lookup and
let the caller decide what to do with a loopback-only base URL.

check_host_is_public below is the opposite direction -- outbound, not
inbound -- and is shared by every built-in/API surface where the
connection target can be driven by content this node didn't originate:
the http_request / fetch_webpage tools (an agent can be steered by
synced or channel content) and MCP server registration by a non-owner
session (api/mcp_servers.py). An owner registering an MCP server is a
deliberate, trusted configuration action -- same category as a Provider
base_url -- so it's deliberately *not* run through this check; only
non-owner (invite-grant) callers are.

A check that only resolves, then lets httpx resolve again, is not
enough: a name that is public at check time and loopback a moment later
(DNS rebinding, or a record that returns both) would still reach
localhost / RFC1918 / 169.254.169.254. PublicHTTPTransport and
PublicAsyncHTTPTransport resolve once per hop and connect to those
addresses. Agent-driven MCP tool invocation uses the same pin
(agentos/tool_resolution.py); owner-driven local MCP is unchanged.
"""

import contextvars
import ipaddress
import socket
from typing import Any

import httpcore
import httpx
from httpcore import ConnectError as HTTPCoreConnectError
from httpcore import ConnectTimeout as HTTPCoreConnectTimeout

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Set around a single httpx request so connect_tcp dials the addresses
# resolve_public_addresses just returned, not a second DNS lookup
# (#477). contextvars so concurrent AsyncClient requests don't share a pin.
_pinned_addresses: contextvars.ContextVar[tuple[str, ...] | None] = contextvars.ContextVar(
    "rivulets_pinned_public_addresses", default=None
)


def is_loopback_host(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


class BlockedHostError(ValueError):
    """`host` resolves to an internal/private network address."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolve_public_addresses(host: str) -> tuple[str, ...]:
    """Resolve `host` and return its addresses if every one is public.

    Raises BlockedHostError if the name cannot be resolved or any
    address is private, loopback, link-local, multicast, reserved, or
    unspecified -- including a multi-record answer that mixes a public
    address with an internal one. Callers that then connect must dial
    these returned addresses (see PublicHTTPTransport), not resolve
    again: a second lookup is the DNS-rebinding hole (#477).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedHostError(f"Could not resolve host {host!r}") from exc

    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        raw = info[4][0]
        ip = ipaddress.ip_address(raw)
        if _is_blocked_ip(ip):
            raise BlockedHostError(
                f"Requests to internal/private network addresses are not permitted "
                f"({host!r} resolves to {ip})"
            )
        text = str(ip)
        if text not in seen:
            seen.add(text)
            addresses.append(text)
    if not addresses:
        raise BlockedHostError(f"Could not resolve host {host!r}")
    return tuple(addresses)


def check_host_is_public(host: str) -> tuple[str, ...]:
    """Raise BlockedHostError if `host` resolves to a private, loopback,
    link-local, multicast, reserved, or unspecified IP address on any of
    its addresses -- closing off SSRF against this node's own localhost
    services and LAN. Every hostname a caller resolves as part of an
    untrusted-input-driven outbound connection should go through this
    before the connection is made.

    Returns the addresses that passed, in resolve order, so the caller
    can pin the subsequent connect to them instead of asking DNS again.
    """
    return resolve_public_addresses(host)


def assert_public_http_url(url: httpx.URL) -> tuple[str, ...]:
    """Scheme/host gate used by http_request and fetch_webpage, plus the
    same resolve-and-reject check as check_host_is_public. Returns the
    addresses the connect must be pinned to."""
    if url.scheme not in ("http", "https"):
        raise BlockedHostError(f"Unsupported URL scheme {url.scheme!r}")
    if not url.host:
        raise BlockedHostError(f"URL has no host: {url!r}")
    return resolve_public_addresses(url.host)


def _connect_targets(host: str) -> tuple[str, ...]:
    pinned = _pinned_addresses.get()
    return pinned if pinned is not None else resolve_public_addresses(host)


def _pin_request(url: httpx.URL) -> contextvars.Token[tuple[str, ...] | None]:
    return _pinned_addresses.set(assert_public_http_url(url))


class _PublicSyncBackend(httpcore.SyncBackend):
    """TCP connect that only dials addresses that passed the public check.

    SNI still uses the URL hostname (httpcore reads that from the origin,
    not from this host argument), so pinning to an IP does not break TLS.
    """

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        last_exc: Exception | None = None
        for ip in _connect_targets(host):
            try:
                return super().connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (OSError, HTTPCoreConnectError, HTTPCoreConnectTimeout) as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        raise BlockedHostError("Unix sockets are not permitted")


class _PublicAsyncBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        # AnyIOBackend's methods aren't in httpcore's published stubs.
        self._inner: Any = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        last_exc: Exception | None = None
        for ip in _connect_targets(host):
            try:
                return await self._inner.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (OSError, HTTPCoreConnectError, HTTPCoreConnectTimeout) as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedHostError("Unix sockets are not permitted")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PublicHTTPTransport(httpx.HTTPTransport):
    """httpx transport that resolves once, then connects to those IPs."""

    def __init__(self) -> None:
        super().__init__()
        # httpx 0.28 does not take network_backend=; pin after construct.
        self._pool._network_backend = _PublicSyncBackend()  # pyright: ignore[reportPrivateUsage]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        token = _pin_request(request.url)
        try:
            return super().handle_request(request)
        finally:
            _pinned_addresses.reset(token)


class PublicAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """Async counterpart of PublicHTTPTransport, for MCP streamable-http."""

    def __init__(self) -> None:
        super().__init__()
        self._pool._network_backend = _PublicAsyncBackend()  # pyright: ignore[reportPrivateUsage]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        token = _pin_request(request.url)
        try:
            return await super().handle_async_request(request)
        finally:
            _pinned_addresses.reset(token)


def public_http_client(**kwargs: Any) -> httpx.Client:
    """Sync httpx client whose TCP connect is pinned to public addresses."""
    return httpx.Client(transport=PublicHTTPTransport(), **kwargs)


def create_public_mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Drop-in for mcp's create_mcp_http_client that pins connect() (#477).

    Same defaults (follow_redirects, timeout) as the SDK factory; each
    hop — including redirects the SDK follows itself — is re-resolved
    and re-pinned by PublicAsyncHTTPTransport.
    """
    client_kwargs: dict[str, Any] = {
        "follow_redirects": True,
        "transport": PublicAsyncHTTPTransport(),
    }
    if timeout is None:
        client_kwargs["timeout"] = httpx.Timeout(30.0, read=60.0 * 5)
    else:
        client_kwargs["timeout"] = timeout
    if headers is not None:
        client_kwargs["headers"] = headers
    if auth is not None:
        client_kwargs["auth"] = auth
    return httpx.AsyncClient(**client_kwargs)


def detect_lan_address() -> str | None:
    """Best-effort guess at this machine's LAN-facing IP.

    Opens a UDP socket "connected" to a public address -- UDP connect()
    only consults the routing table to pick a local source address, no
    packet is actually sent -- then reads back the address the kernel
    would have used. Returns None (never raises) if there's no route to
    pick from, e.g. no network interface is up.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None
