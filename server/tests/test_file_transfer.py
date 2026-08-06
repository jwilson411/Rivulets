"""read_exactly (sync/file_transfer.py) -- FR-9.7's file-transfer wire protocol.

No prior test file exercised this module. A fake stream (duck-typing
INetStream's async read(n)) stands in for a real libp2p connection, since
the multi-chunk-read behavior this guards against (a single read() call
not returning all n requested bytes) is exactly what's under test here,
not real network I/O.
"""

import pytest

from rivulets.sync.file_transfer import read_exactly


class _FakeStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


async def test_read_exactly_single_chunk() -> None:
    stream = _FakeStream([b"hello"])
    assert await read_exactly(stream, 5) == b"hello"  # type: ignore[arg-type]


async def test_read_exactly_assembles_multiple_chunks() -> None:
    """The core guarantee: read() isn't trusted to return all n bytes in
    one call -- a muxed libp2p stream can hand back partial reads."""
    stream = _FakeStream([b"he", b"ll", b"o"])
    assert await read_exactly(stream, 5) == b"hello"  # type: ignore[arg-type]


async def test_read_exactly_raises_eof_on_early_close() -> None:
    stream = _FakeStream([b"he"])  # then read() returns b"" forever
    with pytest.raises(EOFError, match=r"stream closed after 2/5 bytes"):
        await read_exactly(stream, 5)  # type: ignore[arg-type]


async def test_read_exactly_zero_length_returns_empty_without_reading() -> None:
    stream = _FakeStream([])
    assert await read_exactly(stream, 0) == b""  # type: ignore[arg-type]
