"""In-process pub/sub used to stream job progress to the UI over SSE.

Deliberately tiny: this is a single-process desktop-scale app, so a set of asyncio queues
does the job. If this ever becomes multi-process, replace this module with Redis pub/sub —
nothing else needs to change.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

# Subscribers keyed by job id, plus "*" for the firehose used by the global activity feed.
_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
_ALL = "*"

# Bounded so a disconnected browser tab cannot grow a queue without limit.
_MAX_PENDING = 256


def publish(job_id: str, event: dict[str, Any]) -> None:
    payload = {"job_id": job_id, **event}
    for channel in (job_id, _ALL):
        for queue in list(_subscribers.get(channel, ())):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer: drop the oldest event rather than block the worker.
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


@asynccontextmanager
async def subscribe(job_id: str | None = None) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
    """Subscribe to one job, or to every job when ``job_id`` is None."""
    channel = job_id or _ALL
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_PENDING)
    _subscribers.setdefault(channel, set()).add(queue)
    try:
        yield queue
    finally:
        subs = _subscribers.get(channel)
        if subs:
            subs.discard(queue)
            if not subs:
                _subscribers.pop(channel, None)


async def stream(
    job_id: str | None = None, *, heartbeat: float = 15.0
) -> AsyncIterator[dict[str, Any]]:
    """Yield events, emitting a keep-alive ping when idle so proxies don't close the stream."""
    async with subscribe(job_id) as queue:
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                yield {"job_id": job_id or _ALL, "type": "ping"}
