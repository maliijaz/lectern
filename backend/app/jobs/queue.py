"""The in-process job queue.

Public surface: :func:`enqueue` to submit work, :func:`request_cancel` to stop it.
The worker pool in :mod:`app.jobs.worker` consumes from here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus
from app.jobs import events
from app.jobs.registry import JobContext, get_handler
from app.jobs.store import create_job

# Job ids awaiting a worker, and the loop the queue is bound to.
# asyncio primitives bind to the first event loop that awaits them, so the queue has to be
# created lazily and rebuilt if the app is restarted on a fresh loop (as tests do).
_queue: asyncio.Queue[str] | None = None
_bound_loop: asyncio.AbstractEventLoop | None = None
# Contexts of jobs currently executing, so they can be cancelled cooperatively.
_running: dict[str, JobContext] = {}


def get_queue() -> asyncio.Queue[str]:
    """The queue for the running event loop. Must be called from async code."""
    global _queue, _bound_loop
    loop = asyncio.get_running_loop()
    if _queue is None or _bound_loop is not loop:
        _queue = asyncio.Queue()
        _bound_loop = loop
    return _queue


def running_contexts() -> dict[str, JobContext]:
    return _running


async def enqueue(
    db: AsyncSession,
    kind: str,
    params: dict[str, Any] | None = None,
    *,
    document_id: str | None = None,
    artifact_id: str | None = None,
) -> Job:
    """Create a job row and hand it to the worker pool.

    The row is committed before dispatch so the worker (which opens its own session)
    can always find it.
    """
    if get_handler(kind) is None:
        raise ValueError(f"No handler registered for job kind {kind!r}")

    job = await create_job(db, kind, params, document_id=document_id, artifact_id=artifact_id)
    await db.commit()

    get_queue().put_nowait(job.id)
    events.publish(
        job.id,
        {"type": "queued", "status": JobStatus.QUEUED, "kind": kind, "progress": 0.0},
    )
    return job


def requeue(job_id: str) -> None:
    """Re-dispatch an existing job row (used on startup for interrupted work)."""
    get_queue().put_nowait(job_id)


def request_cancel(job_id: str) -> bool:
    """Ask a running job to stop. Returns False if it is not currently running."""
    ctx = _running.get(job_id)
    if ctx is None:
        return False
    ctx.cancel()
    return True
