"""Point-to-point file content transfer over a raw libp2p stream (FR-9.7).

gossipsub (used for entity metadata sync, engine.py's `_STATE_TOPIC`) is a
pub/sub broadcast mechanism sized for small JSON messages, not for
streaming files up to 100MB to one specific peer. This uses a plain
libp2p stream protocol instead: one request, one response, no pub/sub.

Wire protocol (deliberately simple, not the architecture doc's "rsync-
style delta" — content-hash comparison already satisfies FR-9.7's actual
requirement, "only files with differing hashes are transferred": a node
that already has a given hash never asks for it again, since
apply_remote_file_change only fetches when the local path doesn't exist.
True byte-level delta transfer for a file that exists locally in a
slightly different version would be a further optimization, not
attempted here):

    client -> server: 64 raw bytes, the requested content_hash (sha256 hex)
    client: half-closes its write side (close_write())
    server -> client: either
        b"MISS"                                   (4 bytes, hash not found)
        b"HIT " + <8-byte big-endian length> + <file bytes>
    server: closes the stream

Validated against two real libp2p hosts (including a 2MB payload
exercising multi-chunk reads) before being wired into SyncEngine —
`INetStream.read(n)` is not guaranteed to return exactly n bytes in one
call (it wraps a muxed stream, not a fixed-size buffer), hence
`read_exactly()`'s loop rather than trusting a single `read()` call.
"""

from libp2p.abc import INetStream
from libp2p.custom_types import TProtocol

FILE_TRANSFER_PROTOCOL = TProtocol("/rivulets/file-transfer/1.0.0")
HASH_LEN = 64
MISS_MARKER = b"MISS"
HIT_PREFIX = b"HIT "

# Mirrors api/files.py's HTTP upload cap (_MAX_FILE_BYTES) -- a peer is
# never entitled to make this node hold more file content than a human
# uploader is. Enforced against the length prefix a peer sends *before*
# read_exactly(stream, length) is ever called for the body, so a peer
# advertising e.g. length=2**64-1 gets rejected instead of turned into an
# unbounded read that tries to buffer that many bytes in memory.
MAX_FILE_BYTES = 100 * 1024 * 1024


async def read_exactly(stream: INetStream, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = await stream.read(n - len(buf))
        if not chunk:
            raise EOFError(f"stream closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)
