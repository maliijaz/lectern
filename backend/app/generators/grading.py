"""Grading assistance.

This feature is deliberately constrained. A model marking student work is useful as a first
pass and dangerous as a final word: it cannot see effort, context, or the conversation the
teacher had with that student last week, and it is confidently wrong on unusual answers.

So this module:

* marks **only** against a rubric or mark scheme the teacher supplied — never against its
  own invented standard,
* requires a quoted piece of the student's own work as evidence for every judgement,
* returns a confidence score and forces `needs_teacher_review` on whenever the marking is
  near a boundary or the evidence is thin,
* never produces a final grade without a teacher confirming it in the UI.

The output is framed as *a proposal with reasons*, so a teacher can check the reasoning in
seconds rather than re-marking from scratch.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.db.models import Artifact
from app.llm.base import LLMProvider, system, user
from app.llm.structured import generate_structured
from app.schemas.common import GenerationMeta
from app.schemas.paper import QuestionPaper
from app.schemas.requests import GradingRequest
from app.schemas.rubric import Rubric
from app.schemas.study import GradingResult

log = get_logger(__name__)

PERSONA = (
    "You are a teaching assistant marking a first pass. You mark strictly against the "
    "standard you are given and never against your own. For every judgement you quote the "
    "student's own words as evidence. When the work sits between two levels, you say so "
    "and hand it back rather than guessing."
)

STRICTNESS = {
    "lenient": (
        "Give the benefit of the doubt. If a point is present but clumsily expressed, credit it."
    ),
    "balanced": (
        "Credit what is demonstrably there. Do not infer understanding the student has not "
        "shown, and do not withhold credit for wording."
    ),
    "strict": (
        "Credit only what is explicitly and correctly stated. Vague or partial statements "
        "earn partial credit at most."
    ),
}

TONE = {
    "encouraging": "Warm and specific. Name what worked before what did not.",
    "neutral": "Plain and factual. No praise, no criticism — just what is there.",
    "direct": "Brief and clear about what is wrong and how to fix it.",
}

#: A score within this fraction of a level boundary is always flagged for review.
BOUNDARY_MARGIN = 0.05


async def grade(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: GradingRequest,
    *,
    progress=None,  # noqa: ANN001
) -> GradingResult:
    started = time.perf_counter()

    async def report(fraction: float, message: str) -> None:
        if progress is not None:
            await progress(fraction, message)

    if not request.student_work.strip():
        raise ValidationFailed("There is no student work to mark.")

    await report(0.15, "Loading the marking standard")
    standard, max_points, task_title = await _load_standard(db, request)

    if not standard:
        raise ValidationFailed(
            "Marking needs a standard to mark against. Attach a rubric or a question "
            "paper, or paste the mark scheme into the task description.",
        )

    await report(0.35, "Marking against the standard")
    prompt = f"""\
Mark this student's work against the standard below. Do not apply any standard other
than this one.

### THE TASK
{request.task_description or task_title or "(not stated)"}

### THE MARKING STANDARD
{standard}

### THE STUDENT'S WORK
{request.student_work[:20000]}

### HOW TO MARK
{STRICTNESS.get(request.strictness, STRICTNESS["balanced"])}

For each criterion:
- award a level and the points that go with it,
- in `justification`, say what in the work earned that level,
- in `evidence_quote`, quote the student's own words that support it. If you cannot find
  a quote, the criterion is not met — say so rather than inferring it.

Then:
- `strengths`: what the student actually did well, specifically. Not "good effort".
- `areas_to_improve`: what is missing or wrong, tied to the standard.
- `next_steps`: two or three things the student can do on their next attempt.
- `feedback_to_student`: written to the student, second person. {TONE.get(request.feedback_tone, TONE["encouraging"])}
- `confidence`: how sure you are, 0 to 1. Be honest — unusual, very short, or off-topic
  answers should score low.
- `needs_teacher_review` and `review_reason`: set these whenever the work is between two
  levels, argues something defensible that the standard did not anticipate, or you had to
  guess at what the student meant.

Never invent content the student did not write. If the work is blank or off-topic, say so
and award zero with a clear reason.
"""

    result = await generate_structured(
        provider,
        GradingResult,
        [system(PERSONA), user(prompt)],
        temperature=min(0.2, settings.llm_temperature),  # marking should be reproducible
        max_attempts=settings.llm_max_repair_attempts,
    )

    result.student_identifier = request.student_identifier or result.student_identifier
    result.task_title = task_title or result.task_title
    _reconcile_totals(result, max_points or request.max_points)
    _apply_review_policy(result)

    result.meta = GenerationMeta(
        model=provider.model,
        provider=provider.name,
        seconds=round(time.perf_counter() - started, 2),
        grounded=True,
        source_documents=[task_title] if task_title else [],
    )

    await report(
        1.0,
        f"{result.total_points:g}/{result.max_points:g} "
        + ("— flagged for review" if result.needs_teacher_review else "— provisional"),
    )
    return result


async def _load_standard(db: AsyncSession, request: GradingRequest) -> tuple[str, float, str]:
    """Render the rubric or mark scheme the teacher attached into prompt text."""
    from app.render import markdown as md

    if request.rubric_artifact_id:
        artifact = await db.get(Artifact, request.rubric_artifact_id)
        if artifact is None:
            raise ValidationFailed(f"No rubric artifact {request.rubric_artifact_id}")
        rubric = Rubric.model_validate(artifact.content)
        return md.rubric_to_markdown(rubric), rubric.total_points, rubric.title

    if request.question_artifact_id:
        artifact = await db.get(Artifact, request.question_artifact_id)
        if artifact is None:
            raise ValidationFailed(f"No question paper artifact {request.question_artifact_id}")
        paper = QuestionPaper.model_validate(artifact.content)
        return (
            md.paper_to_markdown(paper, answer_key=True),
            paper.total_marks,
            paper.meta.exam_name,
        )

    # Last resort: the teacher pasted the scheme into the task description.
    if request.task_description.strip():
        return request.task_description, request.max_points, ""

    return "", request.max_points, ""


def _reconcile_totals(result: GradingResult, max_points: float) -> None:
    """Make the arithmetic right. Models add up criterion scores wrongly often enough that
    a teacher would lose trust in the whole feature over it."""
    if result.scores:
        computed = sum(s.points for s in result.scores)
        computed_max = sum(s.max_points for s in result.scores)
        if abs(result.total_points - computed) > 0.01:
            log.info("Correcting grading total from %s to %s", result.total_points, computed)
            result.total_points = round(computed, 2)
        if computed_max > 0 and not result.max_points:
            result.max_points = computed_max

    if max_points and not result.max_points:
        result.max_points = max_points

    # A score above the maximum is always a model error, never a real outcome.
    if result.max_points and result.total_points > result.max_points:
        result.total_points = result.max_points


def _apply_review_policy(result: GradingResult) -> None:
    """Force a teacher check whenever the marking is not clearly safe.

    The model's own `needs_teacher_review` is trusted to flag *more*, never to clear a flag
    this policy raises.
    """
    reasons: list[str] = []

    if result.confidence < 0.7:
        reasons.append(f"the model's confidence is only {result.confidence:.0%}")

    missing_evidence = [s.criterion for s in result.scores if not s.evidence_quote.strip()]
    if missing_evidence:
        reasons.append("no quoted evidence for " + ", ".join(missing_evidence[:3]))

    if result.max_points:
        share = result.total_points / result.max_points
        for boundary in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            if abs(share - boundary) <= BOUNDARY_MARGIN:
                reasons.append(f"the score sits on the {boundary:.0%} boundary")
                break

    if not result.scores:
        reasons.append("no per-criterion breakdown was produced")

    if reasons:
        result.needs_teacher_review = True
        existing = result.review_reason.strip().rstrip(".")
        combined = "; ".join(reasons)
        result.review_reason = f"{existing}; {combined}" if existing else combined.capitalize()
