"""Database access for jobs, kept separate so handlers and the worker share one code path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus
from app.db.session import session_scope


async def create_job(
    db: AsyncSession,
    kind: str,
    params: dict[str, Any] | None = None,
    *,
    document_id: str | None = None,
    artifact_id: str | None = None,
    message: str = "Queued",
) -> Job:
    job = Job(
        kind=kind,
        params=params or {},
        document_id=document_id,
        artifact_id=artifact_id,
        status=JobStatus.QUEUED,
        message=message,
    )
    db.add(job)
    await db.flush()
    return job


async def update_job(job_id: str, **fields: Any) -> None:
    """Update a job in its own transaction.

    Handlers run long; using a dedicated short transaction per update keeps progress
    visible to readers immediately and avoids holding a write lock for minutes.
    """
    async with session_scope() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)


async def mark_running(job_id: str) -> None:
    await update_job(
        job_id,
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
        message="Starting",
        progress=0.0,
    )


async def mark_succeeded(job_id: str, result: dict[str, Any]) -> None:
    await update_job(
        job_id,
        status=JobStatus.SUCCEEDED,
        result=result,
        progress=1.0,
        message="Done",
        finished_at=datetime.now(UTC),
    )


async def mark_failed(job_id: str, error: str) -> None:
    await update_job(
        job_id,
        status=JobStatus.FAILED,
        error=error[:8000],
        message="Failed",
        finished_at=datetime.now(UTC),
    )


async def mark_cancelled(job_id: str) -> None:
    await update_job(
        job_id,
        status=JobStatus.CANCELLED,
        message="Cancelled",
        finished_at=datetime.now(UTC),
    )


async def pending_job_ids() -> list[str]:
    """Jobs left queued or running by a previous process, oldest first."""
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(Job.id)
                .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
                .order_by(Job.created_at)
            )
        ).scalars()
        return list(rows)
