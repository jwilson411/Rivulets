"""Point-to-point full-state snapshot transfer for catch-up sync (#347).

gossipsub (engine.py's `_STATE_TOPIC`) only ever carries *live* envelopes:
a peer that wasn't in the mesh at the instant something was published
never sees it — a second machine joining with the same recovery phrase
started empty and stayed empty, and a peer offline during a delete kept
the deleted row forever. This protocol is the catch-up half gossip can't
provide: one node streams its entire synced state (every entity it has a
vector clock for — live payloads plus tombstones for clock-tracked
entities whose row is gone) to one specific peer, which applies each
envelope through the exact same vector-clock machinery (sync/apply.py's
handle_incoming_state_change) live gossip messages go through. That
machinery is what makes a full-state push safe to repeat: an envelope the
receiver already has compares EQUAL and no-ops, a stale one compares
LOCAL_NEWER and is ignored, and a genuinely concurrent one records a
SyncConflict — exactly as if it had arrived via gossip.

Both sides of a new connection push to each other (app.py's
_on_peer_connected fires on the dialing and accepting side alike), so one
exchange converges both nodes; sync/catchup.py's periodic loop re-pushes
on an interval as anti-entropy for anything a live gossip publish dropped.

Wire protocol: a stream of 4-byte big-endian length-prefixed UTF-8 JSON
envelopes (the same envelope shape `_STATE_TOPIC` messages use:
entity_type/entity_id/vector_clock/origin_node_id/payload), sender to
receiver only, terminated by the sender closing the stream. No response:
the receiver applies asynchronously, and the periodic re-push is the
retry mechanism, so an ack would only ever mean "received", not
"applied". Frame lengths above MAX_SNAPSHOT_FRAME_BYTES and streams
above MAX_SNAPSHOT_TOTAL_BYTES are rejected before buffering more — the
PSK is the only gate on who can open this protocol, so a peer-declared
length is never trusted (same discipline as file_transfer.MAX_FILE_BYTES
and agent_dispatch.MAX_DISPATCH_FRAME_BYTES).

An envelope too large for one frame (#392) is not skipped — that entity
would simply never catch up anywhere — and not sent as one oversized
frame either (every receiver rejects those). Instead the sender splits
its bytes across consecutive ENVELOPE_CHUNK_FIELD frames
(chunk_envelope) that the receiver reassembles in stream order
(EnvelopeAssembler) before treating the result as a normal envelope.
Total memory stays bounded by MAX_SNAPSHOT_TOTAL_BYTES exactly as
before — the per-frame cap never bounded the whole stream, only one
peer-declared length.
"""

import base64
import json
from typing import Any

from libp2p.abc import INetStream
from libp2p.custom_types import TProtocol
from libp2p.network.stream.exceptions import StreamEOF

from rivulets.sync.file_transfer import read_exactly

STATE_SNAPSHOT_PROTOCOL = TProtocol("/rivulets/state-snapshot/1.0.0")

_LENGTH_PREFIX_BYTES = 4

# Same sizing rationale as agent_dispatch.MAX_DISPATCH_FRAME_BYTES: one
# envelope is small JSON, so 1 MiB leaves escaping headroom without letting
# a peer demand meaningful memory per frame. Not a hard payload cap (#392
# — a message body or a custom tool's source can legitimately exceed it):
# a bigger envelope rides across multiple chunk frames (chunk_envelope)
# rather than being dropped from the snapshot.
MAX_SNAPSHOT_FRAME_BYTES = 1024 * 1024

# Cap on one whole snapshot read into memory. Entities are metadata-sized
# (file *content* never rides this protocol — file_transfer.py), so a real
# workspace sits orders of magnitude below this; the cap exists so a
# malicious peer streaming frames forever can't buffer unbounded memory.
MAX_SNAPSHOT_TOTAL_BYTES = 64 * 1024 * 1024


# #392: sole key of a frame that carries one piece of an oversized
# envelope rather than an envelope itself. Same never-collides-with-real-
# fields naming as apply.py's TOMBSTONE_FIELD, but at the frame level: a
# receiver that predates chunking parses the frame fine, finds no
# entity_type, and discards it as malformed — degrading to the old
# missing-entity behavior instead of aborting the whole snapshot.
ENVELOPE_CHUNK_FIELD = "__envelope_chunk__"

# Raw envelope bytes per chunk frame: base64 expands by 4/3, and the JSON
# wrapper (key, seq/total, quotes) needs headroom under the frame cap.
_CHUNK_DATA_BYTES = 3 * (MAX_SNAPSHOT_FRAME_BYTES - 1024) // 4


def chunk_envelope(envelope: bytes) -> list[bytes]:
    """#392: splits an envelope too large for one frame into an ordered
    run of chunk frames, each under MAX_SNAPSHOT_FRAME_BYTES. The sender
    writes them consecutively on the stream (never interleaved with other
    frames), which is what lets EnvelopeAssembler reassemble by position
    alone."""
    parts = [
        envelope[i : i + _CHUNK_DATA_BYTES] for i in range(0, len(envelope), _CHUNK_DATA_BYTES)
    ]
    return [
        json.dumps(
            {
                ENVELOPE_CHUNK_FIELD: {
                    "seq": seq,
                    "total": len(parts),
                    "data": base64.b64encode(part).decode(),
                }
            }
        ).encode()
        for seq, part in enumerate(parts)
    ]


class EnvelopeAssembler:
    """Receiver-side counterpart to chunk_envelope — one instance per
    snapshot stream. `add` returns the reassembled envelope bytes on the
    final chunk, None while more are expected. A chunk that doesn't
    continue the run in progress (wrong seq, changed total) discards the
    partial envelope and raises ValueError so the caller's per-frame
    malformed-envelope handling logs it; the assembler itself is reset and
    a later seq-0 chunk starts a fresh envelope."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._total = 0

    def add(self, chunk: dict[str, Any]) -> bytes | None:
        seq, total, data = chunk["seq"], chunk["total"], base64.b64decode(chunk["data"])
        if total < 1 or seq != len(self._parts) or (self._parts and total != self._total):
            self._parts, self._total = [], 0
            raise ValueError(f"snapshot envelope chunk out of order (seq={seq}, total={total})")
        self._total = total
        self._parts.append(data)
        if len(self._parts) < total:
            return None
        assembled = b"".join(self._parts)
        self._parts, self._total = [], 0
        return assembled


async def write_snapshot_frame(stream: INetStream, data: bytes) -> None:
    await stream.write(len(data).to_bytes(_LENGTH_PREFIX_BYTES, "big") + data)


async def read_snapshot_frames(stream: INetStream) -> list[bytes]:
    """Reads envelope frames until the sender closes the stream. A clean
    EOF is only valid at a frame boundary — mid-prefix or mid-body EOF
    raises (via read_exactly), so a truncated snapshot is surfaced rather
    than silently applied as if complete."""
    frames: list[bytes] = []
    total = 0
    while True:
        # A real libp2p NetStream raises StreamEOF once the sender has
        # closed; a plainer transport just returns b"". Both are the same
        # clean end-of-snapshot signal *here*, between frames — inside a
        # frame, read_exactly's own EOFError (or StreamEOF) propagates as
        # the truncation error it is.
        try:
            first = await stream.read(1)
        except StreamEOF:
            return frames
        if not first:
            return frames
        prefix = first + await read_exactly(stream, _LENGTH_PREFIX_BYTES - 1)
        length = int.from_bytes(prefix, "big")
        if length > MAX_SNAPSHOT_FRAME_BYTES:
            raise ValueError(
                f"Peer declared snapshot frame length {length} exceeds "
                f"{MAX_SNAPSHOT_FRAME_BYTES} byte limit"
            )
        total += length
        if total > MAX_SNAPSHOT_TOTAL_BYTES:
            raise ValueError(f"Peer's snapshot exceeds {MAX_SNAPSHOT_TOTAL_BYTES} byte total limit")
        frames.append(await read_exactly(stream, length))
