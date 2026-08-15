"""Point-to-point "run this agent on peer X" request (issue #10).

Like file_transfer.py, this is a plain libp2p stream protocol rather than
gossipsub (`_STATE_TOPIC`) — routing one specific agent invocation to one
specific peer is a targeted request/response, not a broadcast, and
gossipsub has no peer-targeting or response semantics at all.

Wire protocol: 4-byte big-endian length prefix + UTF-8 JSON, both
directions (declared lengths above MAX_DISPATCH_FRAME_BYTES are rejected
before the body is read -- see the constant's comment):

    client -> server: AgentDispatchRequest fields as JSON
    client: half-closes its write side (close_write())
    server -> client: {"accepted": bool, "reason": str | null}
    server: closes the stream

The response is an ack ("I will attempt this"), not the agent's reply —
the server validates cheaply and returns immediately without waiting for
the run to finish (running an agent can take much longer than a stream
RPC should stay open for). The actual reply reaches the originating node
through the existing Message gossipsub sync path, the same way any other
peer's message would. Deeper validation (does this agent actually exist
locally, is the rivulet resolvable) happens inside the dispatch handler
itself, not before the ack: the accept/reject decision only reflects
whether a handler is registered at all (i.e. the sync engine is wired up
to dispatch/service.py), not a DB round-trip — bridging from this stream
handler (running on the trio thread) back to the asyncio loop *and*
waiting for a result would need synchronous request/response plumbing this
codebase doesn't otherwise have (see engine.py's module docstring: the
existing trio->asyncio bridge is fire-and-forget,
`asyncio.run_coroutine_threadsafe` with no awaited result). A registered
handler that then fails its own local checks logs and drops, the same
"known v1 gap, no retry" already accepted for a peer crashing mid-run.
"""

import json
from dataclasses import asdict, dataclass

from libp2p.abc import INetStream
from libp2p.custom_types import TProtocol

from rivulets.sync.file_transfer import read_exactly

AGENT_DISPATCH_PROTOCOL = TProtocol("/rivulets/agent-dispatch/1.0.0")

_LENGTH_PREFIX_BYTES = 4

# #364: same frame-length discipline file_transfer.py applies via
# MAX_FILE_BYTES -- the length prefix is peer-declared, and the PSK is the
# only gate on who can open this protocol, so a peer advertising ~4GB must
# be rejected before read_exactly() tries to buffer that much. Dispatch
# frames are small JSON (a handful of IDs plus one message body, which the
# HTTP API caps at 100k characters -- api/rivulets.py); 1 MiB leaves room
# for worst-case JSON escaping of that content without ever letting a peer
# demand meaningful memory.
MAX_DISPATCH_FRAME_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class AgentDispatchRequest:
    rivulet_id: str
    channel_id: str
    agent_id: str
    message_content: str
    from_agent_id: str | None
    from_agent_name: str | None
    triggering_message_id: str | None

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self)).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "AgentDispatchRequest":
        return cls(**json.loads(data.decode()))


async def write_framed(stream: INetStream, data: bytes) -> None:
    await stream.write(len(data).to_bytes(_LENGTH_PREFIX_BYTES, "big") + data)


async def read_framed(stream: INetStream) -> bytes:
    length = int.from_bytes(await read_exactly(stream, _LENGTH_PREFIX_BYTES), "big")
    if length > MAX_DISPATCH_FRAME_BYTES:
        raise ValueError(
            f"Peer declared dispatch frame length {length} exceeds "
            f"{MAX_DISPATCH_FRAME_BYTES} byte limit"
        )
    return await read_exactly(stream, length)
