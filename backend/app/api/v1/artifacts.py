"""Generating, editing, exporting and downloading teaching artifacts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailed
from app.core.logging import get_logger
from app.db.models import Artifact, ArtifactFile, ArtifactKind, ArtifactStatus, BankQuestion
from app.db.session import get_db
from app.jobs.queue import enqueue
from app.render.theme import theme_list
from app.schemas import ARTIFACT_SCHEMAS
from app.services import export_service, generation_service, settings_service

log = get_logger(__name__)
router = APIRouter(prefix="/artifacts", tags=["artifacts"])


class ArtifactFileOut(BaseModel):
    id: str
    fmt: str
    role: str
    filename: str
    size_bytes: int
    source_version: int
    created_at: datetime
    #: False once the artifact has been edited since this file was rendered.
    stale: bool = False

    model_config = {"from_attributes": True}


class ArtifactOut(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    error: str
    params: dict[str, Any]
    report: dict[str, Any]
    source_document_ids: list[Any]
    course_id: str | None
    model_used: str
    generation_seconds: float
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ArtifactDetail(ArtifactOut):
    content: dict[str, Any]
    files: list[ArtifactFileOut] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    kind: ArtifactKind
    params: dict[str, Any] = Field(default_factory=dict)
    export_formats: list[str] | None = Field(
        default=None, description="Formats to render on completion; null uses the defaults"
    )


class GenerateResponse(BaseModel):
    artifact: ArtifactOut
    job_id: str


class ContentUpdate(BaseModel):
    content: dict[str, Any] = Field(description="The edited artifact content")
    title: str | None = None


class ExportRequest(BaseModel):
    formats: list[str] = Field(min_length=1)
    theme: str = ""
    include_answer_key: bool = True
    force: bool = Field(default=False, description="Re-render even if an export is current")


def _with_files(artifact: Artifact, files: list[ArtifactFile]) -> ArtifactDetail:
    """Build the detail response from an explicitly loaded file list.

    Validating the ORM object directly would make pydantic touch ``Artifact.files``, and a
    lazy relationship load is not allowed in async context — so the files are passed in.
    """
    return ArtifactDetail(
        **ArtifactOut.model_validate(artifact).model_dump(),
        content=artifact.content,
        files=[
            ArtifactFileOut.model_validate(f).model_copy(
                update={"stale": f.source_version != artifact.version}
            )
            for f in files
        ],
    )


# --------------------------------------------------------------------------- catalogue


@router.get("/kinds")
async def list_kinds() -> list[dict[str, Any]]:
    """What can be generated, with each kind's request schema for the UI to build a form."""
    labels = {
        ArtifactKind.SLIDES: ("Slide deck", "A lecture presentation with speaker notes"),
        ArtifactKind.NOTES: ("Lecture notes", "Structured notes students can revise from"),
        ArtifactKind.EXAM: ("Question paper", "An exam built to a marks blueprint"),
        ArtifactKind.LESSON_PLAN: (
            "Lesson plan",
            "A timed plan with activities and differentiation",
        ),
        ArtifactKind.RUBRIC: ("Rubric", "Assessment criteria with observable descriptors"),
        ArtifactKind.WORKSHEET: ("Worksheet", "Practice questions with an answer key"),
        ArtifactKind.FLASHCARDS: ("Flashcards", "A spaced-repetition deck"),
        ArtifactKind.GRADING: ("Grading assistant", "A first-pass marking proposal"),
    }
    return [
        {
            "kind": kind,
            "label": labels.get(kind, (kind, ""))[0],
            "description": labels.get(kind, (kind, ""))[1],
            "request_schema": model.model_json_schema(),
            "content_schema": ARTIFACT_SCHEMAS[kind].model_json_schema(),
            "formats": export_service.available_formats(kind),
        }
        for kind, model in generation_service.REQUEST_MODELS.items()
    ]


@router.get("/themes")
async def list_themes() -> list[dict[str, str]]:
    return theme_list()


# --------------------------------------------------------------------------- generation


@router.post("", response_model=GenerateResponse, status_code=202)
async def create(body: GenerateRequest, db: AsyncSession = Depends(get_db)) -> GenerateResponse:
    """Validate the request, create the artifact, and queue generation."""
    artifact = await generation_service.create_pending(db, body.kind, body.params)
    job = await enqueue(
        db,
        "generate_artifact",
        {"artifact_id": artifact.id, "export_formats": body.export_formats},
        artifact_id=artifact.id,
    )
    return GenerateResponse(artifact=ArtifactOut.model_validate(artifact), job_id=job.id)


@router.post("/{artifact_id}/regenerate", response_model=GenerateResponse, status_code=202)
async def regenerate(
    artifact_id: str,
    overrides: dict[str, Any] | None = None,
    db: AsyncSession = Depends(get_db),
) -> GenerateResponse:
    """Run the same request again, optionally with tweaked parameters."""
    source = await db.get(Artifact, artifact_id)
    if source is None:
        raise NotFoundError(f"No artifact {artifact_id}")

    params = {**source.params, **(overrides or {})}
    artifact = await generation_service.create_pending(db, source.kind, params)
    job = await enqueue(
        db, "generate_artifact", {"artifact_id": artifact.id}, artifact_id=artifact.id
    )
    return GenerateResponse(artifact=ArtifactOut.model_validate(artifact), job_id=job.id)


@router.post("/{artifact_id}/adapt", status_code=202)
async def adapt(
    artifact_id: str, body: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Create a reading-level, translated or differentiated variant."""
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"No artifact {artifact_id}")

    job = await enqueue(
        db, "adapt_artifact", {"artifact_id": artifact_id, **body}, artifact_id=artifact_id
    )
    return {"job_id": job.id}


# --------------------------------------------------------------------------- library


@router.get("", response_model=list[ArtifactOut])
async def list_artifacts(
    kind: ArtifactKind | None = None,
    status: ArtifactStatus | None = None,
    course_id: str | None = None,
    q: str | None = Query(None, description="Substring match on the title"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[Artifact]:
    stmt = select(Artifact).order_by(desc(Artifact.created_at)).offset(offset).limit(limit)
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    if status:
        stmt = stmt.where(Artifact.status == status)
    if course_id:
        stmt = stmt.where(Artifact.course_id == course_id)
    if q:
        stmt = stmt.where(Artifact.title.ilike(f"%{q}%"))
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact(artifact_id: str, db: AsyncSession = Depends(get_db)) -> ArtifactDetail:
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"No artifact {artifact_id}")
    files = (
        (
            await db.execute(
                select(ArtifactFile)
                .where(ArtifactFile.artifact_id == artifact_id)
                .order_by(ArtifactFile.fmt, ArtifactFile.role)
            )
        )
        .scalars()
        .all()
    )
    return _with_files(artifact, list(files))


@router.put("/{artifact_id}/content", response_model=ArtifactDetail)
async def update_content(
    artifact_id: str, body: ContentUpdate, db: AsyncSession = Depends(get_db)
) -> ArtifactDetail:
    """Save an edit made in the UI.

    The content is validated against its schema before it is stored — the editor must not
    be able to save something the renderers cannot handle. Saving bumps the version, which
    marks existing exports stale.
    """
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"No artifact {artifact_id}")

    schema = ARTIFACT_SCHEMAS.get(artifact.kind)
    if schema is None:
        raise ValidationFailed(f"{artifact.kind} artifacts cannot be edited.")

    try:
        validated = schema.model_validate(body.content)
    except Exception as exc:
        raise ValidationFailed(
            "The edited content is not valid.", detail={"errors": str(exc)[:2000]}
        ) from exc

    artifact.content = validated.model_dump(mode="json")
    artifact.version += 1
    artifact.status = ArtifactStatus.READY
    if body.title is not None:
        artifact.title = body.title
    await db.flush()

    files = (
        (await db.execute(select(ArtifactFile).where(ArtifactFile.artifact_id == artifact_id)))
        .scalars()
        .all()
    )
    return _with_files(artifact, list(files))


@router.delete("/{artifact_id}", status_code=204)
async def delete_artifact(artifact_id: str, db: AsyncSession = Depends(get_db)) -> None:
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"No artifact {artifact_id}")

    files = (
        (await db.execute(select(ArtifactFile).where(ArtifactFile.artifact_id == artifact_id)))
        .scalars()
        .all()
    )
    for record in files:
        Path(record.path).unlink(missing_ok=True)

    await db.delete(artifact)


# --------------------------------------------------------------------------- export


@router.get("/{artifact_id}/formats")
async def list_formats(artifact_id: str, db: AsyncSession = Depends(get_db)) -> list[dict]:
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"No artifact {artifact_id}")
    return export_service.available_formats(artifact.kind)


@router.post("/{artifact_id}/export", response_model=list[ArtifactFileOut])
async def export_now(
    artifact_id: str, body: ExportRequest, db: AsyncSession = Depends(get_db)
) -> list[ArtifactFileOut]:
    """Render synchronously. Fast formats only — the UI queues a job for slow ones."""
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"No artifact {artifact_id}")
    if artifact.status != ArtifactStatus.READY:
        raise ValidationFailed(f"This artifact is {artifact.status}; it cannot be exported yet.")

    settings = await settings_service.effective_settings(db)
    produced: list[ArtifactFile] = []
    for key in body.formats:
        records = await export_service.export(
            db,
            artifact,
            key,
            settings=settings,
            theme_key=body.theme,
            include_answer_key=body.include_answer_key,
            force=body.force,
        )
        produced.extend(records)

    await db.commit()
    return [
        ArtifactFileOut.model_validate(f).model_copy(
            update={"stale": f.source_version != artifact.version}
        )
        for f in produced
    ]


@router.get("/{artifact_id}/files/{file_id}/download")
async def download(
    artifact_id: str, file_id: str, db: AsyncSession = Depends(get_db)
) -> FileResponse:
    record = await db.get(ArtifactFile, file_id)
    if record is None or record.artifact_id != artifact_id:
        raise NotFoundError(f"No file {file_id} on artifact {artifact_id}")

    path = Path(record.path)
    if not path.exists():
        raise NotFoundError("The exported file is missing from disk. Export it again.")

    media_type = export_service.get_format(record.fmt).media_type
    return FileResponse(path, filename=record.filename, media_type=media_type)


# --------------------------------------------------------------------------- question bank


class BankQuestionOut(BaseModel):
    id: str
    qtype: str
    topic: str
    bloom: str
    difficulty: str
    marks: float
    stem: str
    payload: dict[str, Any]
    times_used: int
    created_at: datetime

    model_config = {"from_attributes": True}


bank_router = APIRouter(prefix="/question-bank", tags=["question bank"])


@bank_router.get("", response_model=list[BankQuestionOut])
async def search_bank(
    q: str | None = Query(None, description="Substring match on the question stem"),
    qtype: str | None = None,
    bloom: str | None = None,
    difficulty: str | None = None,
    topic: str | None = None,
    course_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[BankQuestion]:
    """Find previously generated questions to reuse in a new paper."""
    stmt = select(BankQuestion).order_by(desc(BankQuestion.created_at)).offset(offset).limit(limit)
    if q:
        stmt = stmt.where(BankQuestion.stem.ilike(f"%{q}%"))
    if qtype:
        stmt = stmt.where(BankQuestion.qtype == qtype)
    if bloom:
        stmt = stmt.where(BankQuestion.bloom == bloom)
    if difficulty:
        stmt = stmt.where(BankQuestion.difficulty == difficulty)
    if topic:
        stmt = stmt.where(BankQuestion.topic.ilike(f"%{topic}%"))
    if course_id:
        stmt = stmt.where(BankQuestion.course_id == course_id)
    return list((await db.execute(stmt)).scalars().all())


@bank_router.get("/facets")
async def bank_facets(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Counts per topic, type and level — what the filter sidebar needs."""

    async def counts(column) -> dict[str, int]:  # noqa: ANN001
        rows = (
            await db.execute(select(column, func.count(BankQuestion.id)).group_by(column))
        ).all()
        return {str(k): int(v) for k, v in rows if k}

    return {
        "total": (await db.execute(select(func.count(BankQuestion.id)))).scalar_one(),
        "topics": await counts(BankQuestion.topic),
        "types": await counts(BankQuestion.qtype),
        "bloom": await counts(BankQuestion.bloom),
        "difficulty": await counts(BankQuestion.difficulty),
    }


@bank_router.delete("/{question_id}", status_code=204)
async def delete_bank_question(question_id: str, db: AsyncSession = Depends(get_db)) -> None:
    question = await db.get(BankQuestion, question_id)
    if question is None:
        raise NotFoundError(f"No banked question {question_id}")
    await db.delete(question)
