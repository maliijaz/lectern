"""Job submission, polling, cancellation and the SSE progress stream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.errors import NotFoundError
from app.db.models import Job, JobStatus
from app.db.session import get_db
from app.jobs import events
from app.jobs.queue import enqueue, request_cancel
from app.jobs.registry import known_kinds

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    progress: float
    message: str
    error: str
    params: dict[str, Any]
    result: dict[str, Any]
    document_id: str | None
    artifact_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class JobCreate(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


@router.get("/kinds", response_model=list[str])
async def list_kinds() -> list[str]:
    return known_kinds()


@router.post("", response_model=JobOut, status_code=202)
async def submit(body: JobCreate, db: AsyncSession = Depends(get_db)) -> Job:
    try:
        return await enqueue(db, body.kind, body.params)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc


@router.get("", response_model=list[JobOut])
async def list_jobs(
    status: JobStatus | None = None,
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[Job]:
    stmt = select(Job).order_by(desc(Job.created_at)).limit(limit)
    if status:
        stmt = stmt.where(Job.status == status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"No job {job_id}")
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, db: AsyncSession = Depends(get_db)) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"No job {job_id}")
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        if not request_cancel(job_id):
            # Queued but not yet picked up: mark it so the worker skips it.
            job.status = JobStatus.CANCELLED
            job.message = "Cancelled before start"
    return job


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, db: AsyncSession = Depends(get_db)) -> EventSourceResponse:
    """Server-sent events for one job.

    Emits the current state immediately (so a page refresh mid-job shows the right thing),
    then live updates until the job reaches a terminal state.
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"No job {job_id}")

    snapshot = {
        "type": "snapshot",
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "result": job.result,
    }
    terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
    already_done = job.status in terminal

    async def generator() -> AsyncIterator[dict[str, str]]:
        yield {"event": "message", "data": json.dumps(snapshot, default=str)}
        if already_done:
            return
        async for event in events.stream(job_id):
            yield {"event": "message", "data": json.dumps(event, default=str)}
            if event.get("status") in terminal:
                return

    return EventSourceResponse(generator())


@router.get("/stream/all")
async def stream_all() -> EventSourceResponse:
    """Firehose of every job event — powers the global activity indicator."""

    async def generator() -> AsyncIterator[dict[str, str]]:
        async for event in events.stream(None):
            yield {"event": "message", "data": json.dumps(event, default=str)}

    return EventSourceResponse(generator())
