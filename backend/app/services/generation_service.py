"""Running a generation and storing the result.

One entry point (:func:`generate`) that every caller uses — the API, the job worker and the
CLI — so behaviour cannot drift between them. It owns the parts that are the same whatever
is being generated: validating the request, creating the artifact row up front (so a
failure is visible in the library rather than vanishing), banking generated questions, and
kicking off the default exports.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.db.models import Artifact, ArtifactKind, ArtifactStatus, BankQuestion
from app.generators import (
    adapt as adapt_gen,
)
from app.generators import (
    exam as exam_gen,
)
from app.generators import (
    flashcards as flashcards_gen,
)
from app.generators import (
    grading as grading_gen,
)
from app.generators import (
    lesson_plan as lesson_plan_gen,
)
from app.generators import (
    notes as notes_gen,
)
from app.generators import (
    rubric as rubric_gen,
)
from app.generators import (
    slides as slides_gen,
)
from app.generators import (
    worksheet as worksheet_gen,
)
from app.llm.base import LLMProvider
from app.llm.registry import build_provider
from app.schemas.paper import QuestionPaper
from app.schemas.requests import (
    ExamRequest,
    FlashcardsRequest,
    GradingRequest,
    LessonPlanRequest,
    NotesRequest,
    RubricRequest,
    SlidesRequest,
    WorksheetRequest,
)
from app.schemas.study import Worksheet

log = get_logger(__name__)

#: Which request model and generator each artifact kind uses.
REQUEST_MODELS: dict[str, type[BaseModel]] = {
    ArtifactKind.SLIDES: SlidesRequest,
    ArtifactKind.NOTES: NotesRequest,
    ArtifactKind.EXAM: ExamRequest,
    ArtifactKind.LESSON_PLAN: LessonPlanRequest,
    ArtifactKind.RUBRIC: RubricRequest,
    ArtifactKind.WORKSHEET: WorksheetRequest,
    ArtifactKind.FLASHCARDS: FlashcardsRequest,
    ArtifactKind.GRADING: GradingRequest,
}


@dataclass
class GenerationOutcome:
    artifact: Artifact
    #: Extra information worth surfacing — the blueprint report for an exam, for instance.
    report: dict[str, Any] = field(default_factory=dict)


def parse_request(kind: str, params: dict[str, Any]) -> BaseModel:
    model = REQUEST_MODELS.get(kind)
    if model is None:
        raise ValidationFailed(
            f"Cannot generate {kind!r}. Available: {', '.join(sorted(REQUEST_MODELS))}"
        )
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise ValidationFailed(
            "The generation request is not valid.", detail={"errors": _plain_errors(exc)}
        ) from exc


def _plain_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Pydantic errors as JSON-safe dicts.

    ``exc.errors()`` embeds the original exception object in ``ctx``, which cannot be
    serialised into an HTTP response.
    """
    return [
        {
            "field": ".".join(str(p) for p in err["loc"]) or "(root)",
            "message": err["msg"],
            "type": err["type"],
        }
        for err in exc.errors(include_url=False, include_context=False)
    ]


async def create_pending(db: AsyncSession, kind: str, params: dict[str, Any]) -> Artifact:
    """Create the artifact row before generation starts.

    The teacher sees the item appear in their library immediately, in a `generating` state,
    and a crash leaves a `failed` row with the error rather than nothing at all.
    """
    request = parse_request(kind, params)
    artifact = Artifact(
        kind=kind,
        title=getattr(request, "title", "") or _provisional_title(kind, request),
        status=ArtifactStatus.GENERATING,
        params=request.model_dump(mode="json"),
        source_document_ids=list(getattr(request, "document_ids", []) or []),
        course_id=getattr(request, "course_id", None),
    )
    db.add(artifact)
    await db.flush()
    return artifact


def _provisional_title(kind: str, request: BaseModel) -> str:
    topic = getattr(request, "topic", "") or getattr(request, "task_description", "")
    label = kind.replace("_", " ").title()
    return f"{label}: {topic[:80]}" if topic else label


async def generate(
    db: AsyncSession,
    artifact: Artifact,
    *,
    settings: Settings,
    provider: LLMProvider | None = None,
    progress=None,  # noqa: ANN001
) -> GenerationOutcome:
    """Run the generator for ``artifact`` and store its content."""
    started = time.perf_counter()
    provider = provider or build_provider(settings)
    request = parse_request(artifact.kind, artifact.params)
    report: dict[str, Any] = {}

    try:
        match artifact.kind:
            case ArtifactKind.SLIDES:
                content = await slides_gen.generate(
                    db, provider, settings, request, progress=progress
                )
            case ArtifactKind.NOTES:
                content = await notes_gen.generate(
                    db, provider, settings, request, progress=progress
                )
            case ArtifactKind.EXAM:
                content, check, audit = await exam_gen.generate(
                    db, provider, settings, request, progress=progress
                )
                report = _blueprint_report(check) | _answer_audit(audit)
                content = _apply_variants(content, request)
            case ArtifactKind.LESSON_PLAN:
                content = await lesson_plan_gen.generate(
                    db, provider, settings, request, progress=progress
                )
            case ArtifactKind.RUBRIC:
                content = await rubric_gen.generate(
                    db, provider, settings, request, progress=progress
                )
            case ArtifactKind.WORKSHEET:
                content = await worksheet_gen.generate(
                    db, provider, settings, request, progress=progress
                )
            case ArtifactKind.FLASHCARDS:
                content = await flashcards_gen.generate(
                    db, provider, settings, request, progress=progress
                )
            case ArtifactKind.GRADING:
                content = await grading_gen.grade(
                    db, provider, settings, request, progress=progress
                )
            case _:
                raise ValidationFailed(f"Cannot generate {artifact.kind!r}")

    except Exception as exc:
        # Record the failure in its own transaction. Writing it on `db` would be rolled
        # back along with the exception, leaving the artifact stuck on "generating"
        # forever with no explanation in the library.
        await _record_failure(artifact.id, exc)
        artifact.status = ArtifactStatus.FAILED
        artifact.error = f"{type(exc).__name__}: {exc}"[:4000]
        raise

    artifact.content = content.model_dump(mode="json")
    artifact.report = report
    artifact.title = _final_title(artifact, content)
    artifact.status = ArtifactStatus.READY
    artifact.error = ""
    artifact.model_used = provider.model
    artifact.generation_seconds = round(time.perf_counter() - started, 2)
    artifact.version = 1
    await db.flush()

    await bank_questions(db, artifact, content)
    return GenerationOutcome(artifact=artifact, report=report)


async def _record_failure(artifact_id: str, exc: Exception) -> None:
    """Persist a generation failure independently of the caller's transaction."""
    from app.db.session import session_scope

    detail = f"{type(exc).__name__}: {exc}"[:4000]
    try:
        async with session_scope() as failure_db:
            artifact = await failure_db.get(Artifact, artifact_id)
            if artifact is not None:
                artifact.status = ArtifactStatus.FAILED
                artifact.error = detail
    except Exception:  # pragma: no cover — never mask the original failure
        log.exception("Could not record the failure of artifact %s", artifact_id)


def _final_title(artifact: Artifact, content: BaseModel) -> str:
    if isinstance(content, QuestionPaper):
        return content.meta.exam_name or artifact.title
    return getattr(content, "title", "") or artifact.title


def _apply_variants(paper: QuestionPaper, request: ExamRequest) -> QuestionPaper:
    """Store the base paper; variants are derived deterministically on export.

    Keeping only the base paper means editing a question fixes it in every set at once —
    storing three copies would let them drift apart.
    """
    if request.variants > 1:
        paper.meta.variant_label = "Set A"
    return paper


def _answer_audit(audit) -> dict[str, Any]:  # noqa: ANN001
    """The independent answer check, for the UI to show above everything else.

    A disputed answer key outranks any blueprint drift: a paper weighted 60/40 instead of
    50/50 is imperfect, a paper with a wrong answer is harmful.
    """
    return {
        "answers_checked": audit.checked,
        "answers_agreed": audit.agreed,
        "answers_skipped": audit.skipped,
        "answer_disagreements": [d.model_dump(mode="json") for d in audit.disagreements],
        "answers_ok": audit.ok,
        "answers_summary": audit.summary(),
        "verifier_model": audit.verifier_model,
    }


def _blueprint_report(check) -> dict[str, Any]:  # noqa: ANN001
    return {
        "blueprint_matches": check.matches,
        "actual_marks": check.actual_marks,
        "planned_marks": check.planned_marks,
        "errors": check.errors,
        "warnings": check.warnings,
        "bloom_actual": check.bloom_actual,
        "bloom_planned": check.bloom_planned,
        "topic_actual": check.topic_actual,
        "topic_planned": check.topic_planned,
        "summary": check.summary(),
    }


async def bank_questions(db: AsyncSession, artifact: Artifact, content: BaseModel) -> int:
    """File every generated question into the reusable bank.

    This is what turns a term's worth of generation into an asset: next month's revision
    paper can be assembled from questions the teacher has already reviewed, with no model
    call at all.
    """
    if isinstance(content, QuestionPaper):
        questions = content.questions
    elif isinstance(content, Worksheet):
        questions = content.questions
    else:
        return 0

    document_id = (artifact.source_document_ids or [None])[0]
    db.add_all(
        BankQuestion(
            course_id=artifact.course_id,
            artifact_id=artifact.id,
            document_id=document_id,
            qtype=question.type.value,
            topic=question.topic,
            bloom=question.bloom.value,
            difficulty=question.difficulty.value,
            marks=question.marks,
            stem=question.text[:2000],
            payload=question.model_dump(mode="json"),
        )
        for question in questions
    )
    await db.flush()
    log.info("Banked %d questions from %s", len(questions), artifact.id)
    return len(questions)


async def adapt(
    db: AsyncSession,
    source_artifact: Artifact,
    params: dict[str, Any],
    *,
    settings: Settings,
    provider: LLMProvider | None = None,
    progress=None,  # noqa: ANN001
) -> Artifact:
    """Produce an adapted copy of an artifact as a new artifact."""
    from app.schemas.requests import AdaptRequest

    provider = provider or build_provider(settings)
    request = AdaptRequest.model_validate({**params, "artifact_id": source_artifact.id})

    content, kind = await adapt_gen.adapt(db, provider, settings, request, progress=progress)

    artifact = Artifact(
        kind=kind,
        title=_final_title(source_artifact, content),
        status=ArtifactStatus.READY,
        params={**source_artifact.params, "adapted_from": source_artifact.id, **params},
        content=content.model_dump(mode="json"),
        source_document_ids=list(source_artifact.source_document_ids or []),
        course_id=source_artifact.course_id,
        model_used=provider.model,
    )
    db.add(artifact)
    await db.flush()
    return artifact
