"""LAN reachability detection for invite links (#121).

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
"""

import socket

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_loopback_host(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


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
