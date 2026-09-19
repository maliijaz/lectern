"""Job handlers.

Imported once by the registry on first dispatch. Heavy dependencies (Docling, torch,
python-pptx) are imported *inside* the handlers so that merely importing this module —
which happens at API startup — stays fast and does not fail when an optional extra is
not installed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.jobs.registry import JobContext, job

log = get_logger(__name__)


@job("echo")
async def echo(ctx: JobContext) -> dict[str, Any]:
    """Diagnostic job: reports progress and returns its params.

    Used by the smoke test and by the Settings page's "test background worker" button to
    prove the queue, the worker pool and the SSE stream are all wired up.
    """
    steps = int(ctx.params.get("steps", 4))
    for i in range(steps):
        ctx.raise_if_cancelled()
        await ctx.progress((i + 1) / steps, f"Step {i + 1} of {steps}")
        await asyncio.sleep(float(ctx.params.get("delay", 0.2)))
    return {"echoed": ctx.params}


@job("ingest_document")
async def ingest_document(ctx: JobContext) -> dict[str, Any]:
    """Parse, chunk and index an uploaded document."""
    from app.db.session import session_scope
    from app.ingest.pipeline import ingest_document as run_ingest
    from app.services import settings_service

    document_id = ctx.params["document_id"]
    async with session_scope() as db:
        settings = await settings_service.effective_settings(db)

    result = await run_ingest(document_id, settings=settings, ctx=ctx)
    return {
        "document_id": result.document_id,
        "title": result.title,
        "chunks": result.chunk_count,
        "pages": result.page_count,
        "words": result.word_count,
        "used_ocr": result.used_ocr,
        "indexed": result.indexed,
    }


@job("generate_artifact")
async def generate_artifact(ctx: JobContext) -> dict[str, Any]:
    """Run a generator and export the default formats.

    Generation gets 85% of the progress bar and exporting the rest, because that is
    roughly how the wait actually divides.
    """
    from app.db.models import Artifact
    from app.db.session import session_scope
    from app.services import export_service, generation_service, settings_service

    artifact_id = ctx.params["artifact_id"]
    export_formats = ctx.params.get("export_formats")

    async with session_scope() as db:
        settings = await settings_service.effective_settings(db)
        artifact = await db.get(Artifact, artifact_id)
        if artifact is None:
            raise ValueError(f"No artifact {artifact_id}")

        generation_ctx = ctx.span(0.0, 0.85)
        outcome = await generation_service.generate(
            db, artifact, settings=settings, progress=generation_ctx.progress
        )

        # Commit before exporting. Rendering a deck or a PDF takes seconds, and holding a
        # write transaction across it would block the progress updates this same job is
        # making from a separate connection — SQLite allows one writer at a time.
        await db.commit()

        ctx.raise_if_cancelled()
        formats = (
            export_formats
            if export_formats is not None
            else export_service.default_formats(artifact.kind)
        )
        export_ctx = ctx.span(0.85, 1.0)
        exported = await export_service.export_many(
            db,
            outcome.artifact,
            formats,
            settings=settings,
            progress=export_ctx.progress,
        )

        return {
            "artifact_id": artifact_id,
            "kind": artifact.kind,
            "title": outcome.artifact.title,
            "seconds": outcome.artifact.generation_seconds,
            "report": outcome.report,
            "exports": {
                key: (value if isinstance(value, str) else [f.filename for f in value])
                for key, value in exported.items()
            },
        }


@job("export_artifact")
async def export_artifact(ctx: JobContext) -> dict[str, Any]:
    """Render an existing artifact into one or more formats."""
    from app.db.models import Artifact
    from app.db.session import session_scope
    from app.services import export_service, settings_service

    artifact_id = ctx.params["artifact_id"]
    formats = ctx.params.get("formats") or ["pdf"]

    async with session_scope() as db:
        settings = await settings_service.effective_settings(db)
        artifact = await db.get(Artifact, artifact_id)
        if artifact is None:
            raise ValueError(f"No artifact {artifact_id}")

        results = await export_service.export_many(
            db,
            artifact,
            formats,
            settings=settings,
            theme_key=ctx.params.get("theme", ""),
            include_answer_key=ctx.params.get("include_answer_key", True),
            progress=ctx.progress,
        )

        return {
            "artifact_id": artifact_id,
            "exports": {
                key: (value if isinstance(value, str) else [f.filename for f in value])
                for key, value in results.items()
            },
        }


@job("adapt_artifact")
async def adapt_artifact(ctx: JobContext) -> dict[str, Any]:
    """Create a reading-level, translated or differentiated variant of an artifact."""
    from app.db.models import Artifact
    from app.db.session import session_scope
    from app.services import export_service, generation_service, settings_service

    source_id = ctx.params["artifact_id"]

    async with session_scope() as db:
        settings = await settings_service.effective_settings(db)
        source = await db.get(Artifact, source_id)
        if source is None:
            raise ValueError(f"No artifact {source_id}")

        adapt_ctx = ctx.span(0.0, 0.85)
        adapted = await generation_service.adapt(
            db, source, ctx.params, settings=settings, progress=adapt_ctx.progress
        )
        await db.commit()  # release the write lock before rendering — see generate_artifact

        export_ctx = ctx.span(0.85, 1.0)
        await export_service.export_many(
            db,
            adapted,
            export_service.default_formats(adapted.kind),
            settings=settings,
            progress=export_ctx.progress,
        )

        return {"artifact_id": adapted.id, "adapted_from": source_id, "title": adapted.title}
