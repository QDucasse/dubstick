from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Iterator


class EventBus:
    """In-process fan-out: every subscriber gets every event published after it joined."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()

    def publish(self, kind: str, **data) -> None:
        event = {"kind": kind, **data}
        for queue in self._subscribers:
            # A stalled browser tab must not block the worker; it just misses events.
            if queue.qsize() < 100:
                queue.put_nowait(event)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[dict]]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
