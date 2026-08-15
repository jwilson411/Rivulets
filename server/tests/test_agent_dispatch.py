"""read_framed/write_framed (sync/agent_dispatch.py) -- issue #364.

Same fake-stream approach as test_file_transfer.py: a duck-typed
INetStream stands in for a real libp2p connection, because what's under
test is the framing discipline (in particular rejecting a peer-declared
length above MAX_DISPATCH_FRAME_BYTES *before* the body read), not
network I/O.
"""

import pytest

from rivulets.sync.agent_dispatch import (
    MAX_DISPATCH_FRAME_BYTES,
    read_framed,
    write_framed,
)

# The wire format's documented 4-byte big-endian length prefix.
_PREFIX_BYTES = 4


class _FakeStream:
    def __init__(self, data: bytes = b"") -> None:
        self._buf = bytearray(data)

    async def read(self, n: int) -> bytes:
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk

    async def write(self, data: bytes) -> None:
        self._buf.extend(data)


async def test_framed_roundtrip() -> None:
    stream = _FakeStream()
    await write_framed(stream, b'{"accepted": true}')  # type: ignore[arg-type]
    assert await read_framed(stream) == b'{"accepted": true}'  # type: ignore[arg-type]


async def test_read_framed_rejects_length_above_cap_before_body_read() -> None:
    """#364: a peer advertising ~4GB (b"\\xff\\xff\\xff\\xff") must be
    rejected from the prefix alone. The fake holds only the 4 prefix
    bytes, so an attempted body read would surface as EOFError, not the
    asserted ValueError -- passing proves the body was never read."""
    stream = _FakeStream(b"\xff\xff\xff\xff")
    with pytest.raises(ValueError, match=r"exceeds \d+ byte limit"):
        await read_framed(stream)  # type: ignore[arg-type]


async def test_read_framed_accepts_length_at_cap() -> None:
    body = b"x" * MAX_DISPATCH_FRAME_BYTES
    stream = _FakeStream(MAX_DISPATCH_FRAME_BYTES.to_bytes(_PREFIX_BYTES, "big") + body)
    assert await read_framed(stream) == body  # type: ignore[arg-type]


async def test_read_framed_rejects_length_just_above_cap() -> None:
    stream = _FakeStream((MAX_DISPATCH_FRAME_BYTES + 1).to_bytes(_PREFIX_BYTES, "big"))
    with pytest.raises(ValueError, match=str(MAX_DISPATCH_FRAME_BYTES + 1)):
        await read_framed(stream)  # type: ignore[arg-type]
