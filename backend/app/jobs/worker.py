"""Asyncio worker pool that drains the job queue.

Handlers are async, but the real work (Docling parsing, embedding, rendering) is CPU-bound
and synchronous. Those steps are expected to call :func:`app.jobs.worker.run_blocking`,
which pushes them to a thread so the event loop keeps serving requests and SSE streams.
"""

from __future__ import annotations

import asyncio
import functools
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import JobStatus
from app.db.session import session_scope
from app.jobs import events
from app.jobs.queue import get_queue, requeue, running_contexts
from app.jobs.registry import Cancelled, JobContext, get_handler
from app.jobs.store import (
    mark_cancelled,
    mark_failed,
    mark_running,
    mark_succeeded,
    pending_job_ids,
)

log = get_logger(__name__)

T = TypeVar("T")

_workers: list[asyncio.Task[None]] = []
_executor: ThreadPoolExecutor | None = None
_stopping = asyncio.Event()


def get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        workers = max(2, get_settings().worker_concurrency * 2)
        _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ta-blocking")
    return _executor


async def run_blocking(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a synchronous function off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_executor(), functools.partial(fn, *args, **kwargs))


async def _run_one(job_id: str) -> None:
    from app.db.models import Job

    async with session_scope() as db:
        job = await db.get(Job, job_id)
        if job is None:
            log.warning("Job %s vanished before it could run", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            return  # cancelled while it sat in the queue
        kind, params = job.kind, dict(job.params or {})

    handler = get_handler(kind)
    if handler is None:
        await mark_failed(job_id, f"No handler registered for job kind {kind!r}")
        events.publish(job_id, {"type": "failed", "status": JobStatus.FAILED, "kind": kind})
        return

    ctx = JobContext(job_id=job_id, kind=kind, params=params)
    running_contexts()[job_id] = ctx
    await mark_running(job_id)
    events.publish(
        job_id, {"type": "started", "status": JobStatus.RUNNING, "kind": kind, "progress": 0.0}
    )

    try:
        result = await handler(ctx) or {}
        await mark_succeeded(job_id, result)
        events.publish(
            job_id,
            {
                "type": "succeeded",
                "status": JobStatus.SUCCEEDED,
                "kind": kind,
                "progress": 1.0,
                "result": result,
            },
        )
    except (Cancelled, asyncio.CancelledError):
        await mark_cancelled(job_id)
        events.publish(job_id, {"type": "cancelled", "status": JobStatus.CANCELLED, "kind": kind})
    except Exception as exc:
        log.exception("Job %s (%s) failed", job_id, kind)
        detail = f"{type(exc).__name__}: {exc}"
        if get_settings().debug:
            detail += "\n" + traceback.format_exc()
        await mark_failed(job_id, detail)
        events.publish(
            job_id,
            {"type": "failed", "status": JobStatus.FAILED, "kind": kind, "error": str(exc)},
        )
    finally:
        running_contexts().pop(job_id, None)


async def _worker_loop(index: int) -> None:
    queue = get_queue()
    log.debug("Worker %d started", index)
    while not _stopping.is_set():
        try:
            job_id = await asyncio.wait_for(queue.get(), timeout=1.0)
        except TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        try:
            await _run_one(job_id)
        except Exception:
            # _run_one already records handler failures; this only catches problems in the
            # bookkeeping itself. Log and keep the worker alive rather than silently
            # losing the pool.
            log.exception("Worker %d failed handling job %s", index, job_id)
        finally:
            queue.task_done()
    log.debug("Worker %d stopped", index)


async def start_workers() -> None:
    """Start the pool and re-dispatch anything left over from a previous process."""
    if _workers:
        return
    _stopping.clear()

    try:
        for job_id in await pending_job_ids():
            requeue(job_id)
    except Exception:  # a fresh database has no jobs table yet on the very first boot
        log.debug("Could not scan for pending jobs", exc_info=True)

    count = max(1, get_settings().worker_concurrency)
    for i in range(count):
        _workers.append(asyncio.create_task(_worker_loop(i), name=f"ta-worker-{i}"))
    log.info("Started %d job worker(s)", count)


async def stop_workers() -> None:
    if not _workers:
        return
    _stopping.set()
    for task in _workers:
        task.cancel()
    await asyncio.gather(*_workers, return_exceptions=True)
    _workers.clear()

    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
    log.info("Job workers stopped")
