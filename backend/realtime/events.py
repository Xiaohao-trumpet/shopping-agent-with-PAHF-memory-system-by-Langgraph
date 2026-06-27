"""In-process async pub/sub event bus.

Lightweight backbone for realtime fan-out (no Redis required). Subscribers get
their own asyncio.Queue; publishing pushes a copy of the event into every live
subscriber queue for the topic. Slow/over-full subscribers drop oldest events
rather than block the publisher.

Topics used by the app:
  - ``conv:{conversation_id}``  customer + assigned agent stream for one chat
  - ``agents``                  global agent-console feed (queue/presence updates)
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict, Set


class EventBus:
    def __init__(self, max_queue: int = 200):
        self._topics: Dict[str, Set[asyncio.Queue]] = {}
        self._max_queue = max_queue

    def register(self, topic: str) -> asyncio.Queue:
        """Synchronously register a subscriber queue. Call this BEFORE any await
        that could let a publish slip through, so no early events are missed."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._topics.setdefault(topic, set()).add(q)
        return q

    def unregister(self, topic: str, q: asyncio.Queue) -> None:
        subs = self._topics.get(topic)
        if subs is not None:
            subs.discard(q)
            if not subs:
                self._topics.pop(topic, None)

    async def publish(self, topic: str, event: dict) -> None:
        for q in list(self._topics.get(topic, set())):
            if q.full():
                try:
                    q.get_nowait()  # drop oldest to make room
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, topic: str) -> AsyncIterator[dict]:
        """Async generator yielding events for a topic until cancelled.

        Note: the subscription only becomes live once iteration starts. For
        endpoints that publish right after subscribing, prefer register()/
        unregister() so the queue is in place synchronously."""
        q = self.register(topic)
        try:
            while True:
                yield await q.get()
        finally:
            self.unregister(topic, q)

    def topic_count(self, topic: str) -> int:
        return len(self._topics.get(topic, set()))
