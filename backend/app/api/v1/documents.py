"""Upload, list, inspect, search and delete source documents."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnsupportedFormat, ValidationFailed
from app.core.logging import get_logger
from app.db.models import Document, DocumentChunk, DocumentStatus, Job
from app.db.session import get_db
from app.ingest import parse
from app.ingest.pipeline import sha256_of
from app.jobs.queue import enqueue
from app.services import settings_service

log = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: str
    original_name: str
    title: str
    mime_type: str
    size_bytes: int
    status: str
    error: str
    page_count: int
    word_count: int
    chunk_count: int
    used_ocr: bool
    subject: str
    grade_level: str
    tags: list[Any]
    course_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChunkOut(BaseModel):
    id: str
    ordinal: int
    text: str
    heading: str
    section_path: str
    page_from: int | None
    page_to: int | None
    token_count: int

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    document: DocumentOut
    job_id: str | None = Field(
        default=None, description="Ingestion job to watch; null when the file was already indexed"
    )
    duplicate_of: str | None = Field(
        default=None, description="Existing document with identical content, if any"
    )


class DocumentUpdate(BaseModel):
    title: str | None = None
    subject: str | None = None
    grade_level: str | None = None
    tags: list[str] | None = None
    course_id: str | None = None


class SearchHitOut(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    text: str
    score: float
    page: int | None
    section: str


@router.post("", response_model=UploadResponse, status_code=201)
async def upload(
    file: UploadFile = File(...),
    subject: str = Form(""),
    grade_level: str = Form(""),
    course_id: str | None = Form(None),
    reingest_duplicates: bool = Form(False),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    """Store a file and queue it for parsing and indexing.

    Identical content is de-duplicated by hash: re-uploading the same chapter returns the
    document already ingested rather than parsing it twice.
    """
    settings = await settings_service.effective_settings(db)
    settings.ensure_dirs()

    name = Path(file.filename or "upload").name
    suffix = Path(name).suffix.lower()
    if suffix not in parse.SUPPORTED_SUFFIXES:
        raise UnsupportedFormat(
            f"Cannot read {suffix or 'files without an extension'}. Supported formats: "
            + ", ".join(sorted(parse.SUPPORTED_SUFFIXES))
        )

    # Write to a temporary name first so the hash decides the final location.
    staging = settings.uploads_dir / f".incoming-{name}"
    size = 0
    limit = settings.max_upload_mb * 1024 * 1024
    try:
        with staging.open("wb") as out:
            while block := await file.read(1024 * 1024):
                size += len(block)
                if size > limit:
                    raise ValidationFailed(
                        f"{name} is larger than the {settings.max_upload_mb} MB limit."
                    )
                out.write(block)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    digest = sha256_of(staging)
    existing = (
        await db.execute(select(Document).where(Document.sha256 == digest).limit(1))
    ).scalar_one_or_none()

    if existing is not None and not reingest_duplicates:
        staging.unlink(missing_ok=True)
        log.info("Upload %s matches existing document %s", name, existing.id)
        return UploadResponse(
            document=DocumentOut.model_validate(existing),
            job_id=None,
            duplicate_of=existing.id,
        )

    document = Document(
        original_name=name,
        title=Path(name).stem,
        stored_path="",
        mime_type=file.content_type or "",
        size_bytes=size,
        sha256=digest,
        status=DocumentStatus.UPLOADED,
        subject=subject,
        grade_level=grade_level,
        course_id=course_id or None,
    )
    db.add(document)
    await db.flush()

    final = settings.uploads_dir / f"{document.id}{suffix}"
    shutil.move(str(staging), final)
    document.stored_path = str(final)
    await db.flush()

    job = await enqueue(
        db, "ingest_document", {"document_id": document.id}, document_id=document.id
    )
    return UploadResponse(
        document=DocumentOut.model_validate(document),
        job_id=job.id,
        duplicate_of=existing.id if existing else None,
    )


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    status: DocumentStatus | None = None,
    course_id: str | None = None,
    q: str | None = Query(None, description="Substring match on title or filename"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[Document]:
    stmt = select(Document).order_by(desc(Document.created_at)).limit(limit)
    if status:
        stmt = stmt.where(Document.status == status)
    if course_id:
        stmt = stmt.where(Document.course_id == course_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Document.title.ilike(pattern) | Document.original_name.ilike(pattern))
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: str, db: AsyncSession = Depends(get_db)) -> Document:
    document = await db.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"No document {document_id}")
    return document


@router.patch("/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: str, body: DocumentUpdate, db: AsyncSession = Depends(get_db)
) -> Document:
    document = await db.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"No document {document_id}")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(document, field, value)
    return document


@router.get("/{document_id}/markdown")
async def get_markdown(
    document_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200_000, ge=1, le=2_000_000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The parsed Markdown, for the document preview pane."""
    document = await db.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"No document {document_id}")
    if not document.markdown_path or not Path(document.markdown_path).exists():
        raise NotFoundError(
            "This document has not been parsed yet."
            if document.status != DocumentStatus.READY
            else "The parsed text for this document is missing; re-ingest it."
        )
    text = Path(document.markdown_path).read_text(encoding="utf-8", errors="replace")
    return {
        "document_id": document_id,
        "total_chars": len(text),
        "offset": offset,
        "markdown": text[offset : offset + limit],
    }


@router.get("/{document_id}/chunks", response_model=list[ChunkOut])
async def list_chunks(
    document_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentChunk]:
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.ordinal)
        .offset(offset)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("/{document_id}/reingest", status_code=202)
async def reingest(document_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Re-parse and re-index — used after changing the chunk size or OCR settings."""
    document = await db.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"No document {document_id}")
    job = await enqueue(
        db, "ingest_document", {"document_id": document_id}, document_id=document_id
    )
    return {"job_id": job.id}


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db)) -> None:
    document = await db.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"No document {document_id}")

    settings = await settings_service.effective_settings(db)
    from app.ingest.pipeline import remove_document_index

    await remove_document_index(document_id, settings)

    for path in (document.stored_path, document.markdown_path):
        if path:
            Path(path).unlink(missing_ok=True)

    await db.delete(document)


@router.get("/search/query", response_model=list[SearchHitOut])
async def search_documents(
    q: str = Query(..., min_length=2),
    document_ids: list[str] | None = Query(None),
    top_k: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[SearchHitOut]:
    """Semantic search across the library. Also the fastest way to confirm indexing worked."""
    settings = await settings_service.effective_settings(db)
    hits = await _run_search(q, settings, document_ids, top_k)

    titles = (
        dict(
            (
                await db.execute(
                    select(Document.id, Document.title).where(
                        Document.id.in_({h.document_id for h in hits})
                    )
                )
            ).all()
        )
        if hits
        else {}
    )

    return [
        SearchHitOut(
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            document_title=titles.get(h.document_id, ""),
            text=h.text,
            score=round(h.score, 4),
            page=h.page,
            section=h.section,
        )
        for h in hits
    ]


async def _run_search(q: str, settings, document_ids, top_k):  # noqa: ANN001, ANN201
    from app.ingest import retrieve
    from app.jobs.worker import run_blocking

    return await run_blocking(
        retrieve.search,
        q,
        settings=settings,
        document_ids=document_ids,
        top_k=top_k,
    )


@router.get("/stats/summary")
async def library_stats(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    totals = (
        await db.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.word_count), 0),
                func.coalesce(func.sum(Document.chunk_count), 0),
            )
        )
    ).one()
    by_status = dict(
        (
            await db.execute(
                select(Document.status, func.count(Document.id)).group_by(Document.status)
            )
        ).all()
    )
    active = (
        await db.execute(
            select(func.count(Job.id)).where(
                Job.kind == "ingest_document", Job.status.in_(["queued", "running"])
            )
        )
    ).scalar_one()
    return {
        "documents": totals[0],
        "words": int(totals[1]),
        "chunks": int(totals[2]),
        "by_status": by_status,
        "ingesting": active,
    }
