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
the http_request tool (an agent can be steered by synced or channel
content) and MCP server registration by a non-owner session (api/
mcp_servers.py). An owner registering an MCP server is a deliberate,
trusted configuration action -- same category as a Provider base_url --
so it's deliberately *not* run through this check; only non-owner
(invite-grant) callers are.
"""

import ipaddress
import socket

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_loopback_host(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


class BlockedHostError(ValueError):
    """`host` resolves to an internal/private network address."""


def check_host_is_public(host: str) -> None:
    """Raise BlockedHostError if `host` resolves to a private, loopback,
    link-local, multicast, reserved, or unspecified IP address on any of
    its addresses -- closing off SSRF against this node's own localhost
    services and LAN. Every hostname a caller resolves as part of an
    untrusted-input-driven outbound connection should go through this
    before the connection is made."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedHostError(f"Could not resolve host {host!r}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise BlockedHostError(
                f"Requests to internal/private network addresses are not permitted "
                f"({host!r} resolves to {ip})"
            )


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
