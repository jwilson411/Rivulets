"""In-process pub/sub backing the rivulet SSE stream (FR-12.3,
api-design.md#sse-protocol).

Single-process, in-memory, not persisted — fine for the current
architecture (one App Server process, no horizontal scaling; see
ADR-002). dispatch/service.py publishes events as a dispatch round runs;
api/rivulets.py publishes `dispatch_status` as soon as the human
message is committed (#413) and `/rivulets/{id}/stream` subscribes for
the lifetime of one client connection. A subscriber that never connects
just means publish() has nothing to deliver to — dispatch itself doesn't
block on whether anyone's listening.

#286: a rivulet's subscriber list is not uniformly trusted -- an
invite-grant session can open the same stream an owner session can
(api/rivulets.py's stream_rivulet has no OwnerGrant gate, by design, so
an invitee sees the conversation they were invited into). Some events
(a freshly created invite's one-shot URL) must reach the owner only, so
each subscriber records the grant its session was opened with, and
publish() takes `owner_only` to skip every non-owner queue for those."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, TypedDict


class SSEEvent(TypedDict):
    event: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Subscription:
    queue: "asyncio.Queue[SSEEvent]"
    is_owner: bool


_subscribers: dict[str, list[_Subscription]] = defaultdict(list)


def subscribe(rivulet_id: str, *, is_owner: bool) -> "asyncio.Queue[SSEEvent]":
    queue: asyncio.Queue[SSEEvent] = asyncio.Queue()
    _subscribers[rivulet_id].append(_Subscription(queue, is_owner))
    return queue


def unsubscribe(rivulet_id: str, queue: "asyncio.Queue[SSEEvent]") -> None:
    subs = _subscribers.get(rivulet_id)
    if not subs:
        return
    remaining = [sub for sub in subs if sub.queue is not queue]
    if remaining:
        _subscribers[rivulet_id] = remaining
    else:
        _subscribers.pop(rivulet_id, None)


def publish(
    rivulet_id: str, event_type: str, data: dict[str, Any], *, owner_only: bool = False
) -> None:
    for sub in _subscribers.get(rivulet_id, []):
        if owner_only and not sub.is_owner:
            continue
        sub.queue.put_nowait({"event": event_type, "data": data})
