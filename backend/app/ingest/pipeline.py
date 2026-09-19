"""The ingestion pipeline: upload → parse → chunk → embed → index."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.db.models import Document, DocumentChunk, DocumentStatus
from app.db.session import session_scope
from app.ingest import chunk as chunking
from app.ingest import embed, parse
from app.ingest.store import VectorStore, get_store
from app.jobs.registry import JobContext
from app.jobs.worker import run_blocking

log = get_logger(__name__)


@dataclass
class IngestResult:
    document_id: str
    chunk_count: int
    page_count: int
    word_count: int
    used_ocr: bool
    indexed: bool
    title: str


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def ingest_document(
    document_id: str,
    *,
    settings: Settings,
    ctx: JobContext | None = None,
    store: VectorStore | None = None,
    index: bool = True,
) -> IngestResult:
    """Parse and index one document, reporting progress as it goes.

    Indexing is optional: with ``index=False`` the document is still parsed and chunked
    (so it can be summarised or fed whole to a generator), it just is not searchable.
    That is the path a minimal install without sentence-transformers takes.
    """

    async def report(fraction: float, message: str) -> None:
        log.info("[%s] %s", document_id[:8], message)
        if ctx is not None:
            await ctx.progress(fraction, message)

    async with session_scope() as db:
        document = await db.get(Document, document_id)
        if document is None:
            raise ValueError(f"No document {document_id}")
        source = Path(document.stored_path)
        original_name = document.original_name
        document.status = DocumentStatus.PARSING
        document.error = ""

    try:
        await report(0.05, f"Reading {original_name}")
        parsed = await run_blocking(
            parse.parse,
            source,
            ocr_enabled=settings.ocr_enabled,
            ocr_languages=settings.ocr_languages,
        )

        if ctx is not None:
            ctx.raise_if_cancelled()
        await report(0.4, f"Parsed {parsed.word_count:,} words")

        # Keep the Markdown next to the upload: it is what generators read when a request
        # asks for the whole document rather than a retrieval over it.
        markdown_path = settings.parsed_dir / f"{document_id}.md"
        markdown_path.write_text(parsed.markdown, encoding="utf-8")

        chunks = await run_blocking(
            chunking.chunk_docling_document,
            parsed.doc_dict,
            parsed.markdown,
            target_tokens=settings.chunk_tokens,
            overlap_tokens=settings.chunk_overlap,
        )
        await report(0.5, f"Split into {len(chunks)} passages")

        async with session_scope() as db:
            await _replace_chunks(db, document_id, chunks)

        indexed = False
        if index and chunks:
            if ctx is not None:
                ctx.raise_if_cancelled()
            await report(0.6, "Building the search index")
            indexed = await _index_chunks(
                document_id=document_id,
                document_title=parsed.title or original_name,
                chunks=chunks,
                settings=settings,
                store=store,
                report=report,
                ctx=ctx,
            )

        async with session_scope() as db:
            document = await db.get(Document, document_id)
            if document is not None:
                document.status = DocumentStatus.READY
                document.title = document.title or parsed.title or Path(original_name).stem
                document.markdown_path = str(markdown_path)
                document.page_count = parsed.page_count
                document.word_count = parsed.word_count
                document.chunk_count = len(chunks)
                document.used_ocr = parsed.used_ocr
                document.error = ""

        await report(1.0, "Ready")
        return IngestResult(
            document_id=document_id,
            chunk_count=len(chunks),
            page_count=parsed.page_count,
            word_count=parsed.word_count,
            used_ocr=parsed.used_ocr,
            indexed=indexed,
            title=parsed.title or original_name,
        )

    except Exception as exc:
        async with session_scope() as db:
            document = await db.get(Document, document_id)
            if document is not None:
                document.status = DocumentStatus.FAILED
                document.error = f"{type(exc).__name__}: {exc}"[:4000]
        raise


async def _replace_chunks(db: AsyncSession, document_id: str, chunks: list[chunking.Chunk]) -> None:
    """Swap in a fresh set of chunks — re-ingesting a document must not duplicate them."""
    await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    await db.flush()
    db.add_all(
        DocumentChunk(
            document_id=document_id,
            ordinal=c.ordinal,
            text=c.text,
            heading=c.heading,
            section_path=c.section_path,
            page_from=c.page_from,
            page_to=c.page_to,
            token_count=c.token_count,
        )
        for c in chunks
    )
    await db.flush()


async def _index_chunks(
    *,
    document_id: str,
    document_title: str,
    chunks: list[chunking.Chunk],
    settings: Settings,
    store: VectorStore | None,
    report,  # noqa: ANN001 — an async callable supplied by the caller
    ctx: JobContext | None,
) -> bool:
    """Embed and upsert. A failure here downgrades the document to unsearchable rather
    than failing the whole ingest — the text is still usable."""
    from app.core.errors import DependencyMissing

    target = store or get_store(settings.vector_dir)

    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .order_by(DocumentChunk.ordinal)
                )
            )
            .scalars()
            .all()
        )
        records = [
            (
                row.id,
                row.text,
                {
                    "document_id": document_id,
                    "document_title": document_title,
                    "ordinal": row.ordinal,
                    "heading": row.heading,
                    "section_path": row.section_path,
                    "page_from": row.page_from,
                    "page_to": row.page_to,
                },
            )
            for row in rows
        ]

    if not records:
        return False

    try:
        target.delete_document(document_id)  # re-ingest: clear the old vectors first
    except Exception:
        log.debug("Nothing to clear for %s", document_id, exc_info=True)

    batch_size = max(8, settings.embed_batch_size)
    try:
        for start in range(0, len(records), batch_size):
            if ctx is not None:
                ctx.raise_if_cancelled()
            batch = records[start : start + batch_size]
            texts = [
                f"{meta['section_path']}\n\n{text}" if meta["section_path"] else text
                for _, text, meta in batch
            ]
            vectors = await run_blocking(
                embed.embed_passages,
                texts,
                model_name=settings.embed_model,
                device=settings.embed_device,
                batch_size=batch_size,
            )
            target.add(
                ids=[cid for cid, _, _ in batch],
                embeddings=vectors,
                documents=[text for _, text, _ in batch],
                metadatas=[meta for _, _, meta in batch],
            )
            done = min(start + batch_size, len(records))
            await report(0.6 + 0.35 * done / len(records), f"Indexed {done}/{len(records)}")
    except DependencyMissing as exc:
        log.warning("Skipping the search index: %s", exc.message)
        return False

    return True


async def remove_document_index(document_id: str, settings: Settings) -> None:
    try:
        get_store(settings.vector_dir).delete_document(document_id)
    except Exception:
        log.debug("Could not clear vectors for %s", document_id, exc_info=True)
