"""In-process pub/sub backing the thread SSE stream (FR-12.3,
api-design.md#sse-protocol).

Single-process, in-memory, not persisted — fine for the current
architecture (one App Server process, no horizontal scaling; see
ADR-002). dispatch/service.py publishes events as a dispatch round runs;
api/threads.py's `/threads/{id}/stream` subscribes for the lifetime of
one client connection. A subscriber that never connects just means
publish() has nothing to deliver to — dispatch itself doesn't block on
whether anyone's listening.
"""

import asyncio
from collections import defaultdict
from typing import Any, TypedDict


class SSEEvent(TypedDict):
    event: str
    data: dict[str, Any]


_subscribers: dict[str, list["asyncio.Queue[SSEEvent]"]] = defaultdict(list)


def subscribe(thread_id: str) -> "asyncio.Queue[SSEEvent]":
    queue: asyncio.Queue[SSEEvent] = asyncio.Queue()
    _subscribers[thread_id].append(queue)
    return queue


def unsubscribe(thread_id: str, queue: "asyncio.Queue[SSEEvent]") -> None:
    subs = _subscribers.get(thread_id)
    if not subs:
        return
    if queue in subs:
        subs.remove(queue)
    if not subs:
        _subscribers.pop(thread_id, None)


def publish(thread_id: str, event_type: str, data: dict[str, Any]) -> None:
    for queue in _subscribers.get(thread_id, []):
        queue.put_nowait({"event": event_type, "data": data})
