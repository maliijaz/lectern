"""Lesson plan generation.

Single pass — a lesson plan is small enough to hold in one response, and splitting it would
break the thing that makes a plan coherent: the activities have to add up to the period
length and build towards the objectives. What this module adds after generation is the
arithmetic the model is bad at, namely making the timings actually sum correctly.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.generators import source as source_mod
from app.llm.base import LLMProvider, system, user
from app.llm.structured import generate_structured
from app.schemas.common import GenerationMeta, bloom_verb_hint
from app.schemas.lesson import LessonPlan, PlanTemplate
from app.schemas.requests import LessonPlanRequest

log = get_logger(__name__)

PERSONA = (
    "You are a head of department who writes lesson plans other teachers can pick up and "
    "teach cold. You are specific about what the teacher says and what students do, you "
    "budget time realistically, and you never plan more than fits the period."
)

TEMPLATE_SHAPE: dict[PlanTemplate, str] = {
    PlanTemplate.GENERIC: (
        "Structure: starter → direct instruction → guided practice → independent practice "
        "→ plenary."
    ),
    PlanTemplate.FIVE_E: (
        "Use the 5E model. Name the activities exactly: Engage, Explore, Explain, "
        "Elaborate, Evaluate — in that order."
    ),
    PlanTemplate.HUNTER: (
        "Use Madeline Hunter's sequence: anticipatory set, objective and purpose, input, "
        "modelling, checking for understanding, guided practice, independent practice."
    ),
    PlanTemplate.GRR: (
        "Use gradual release: 'I do' (modelling), 'We do' (guided), 'You do together' "
        "(collaborative), 'You do alone' (independent)."
    ),
    PlanTemplate.INQUIRY: (
        "Structure around a driving question: provocation, student questioning, "
        "investigation, synthesis, reflection."
    ),
}


async def generate(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: LessonPlanRequest,
    *,
    progress=None,  # noqa: ANN001
) -> LessonPlan:
    started = time.perf_counter()

    async def report(fraction: float, message: str) -> None:
        if progress is not None:
            await progress(fraction, message)

    await report(0.1, "Gathering source material")
    material = await source_mod.collect(
        db,
        settings=settings,
        topic=request.topic,
        document_ids=request.document_ids,
        queries=[
            request.topic,
            f"key concepts in {request.topic}",
            f"activities and examples for teaching {request.topic}",
        ],
        budget=14_000,
    )

    await report(0.3, "Writing the plan")
    materials = (
        f"AVAILABLE IN THE ROOM: {', '.join(request.available_materials)}"
        if request.available_materials
        else "Assume only a board and printed handouts are available."
    )
    differentiation_rule = (
        "Fill `differentiation` properly: concrete scaffolds under `support`, what the "
        "majority does under `core`, genuine extension (not 'more of the same') under "
        "`extension`, and specific help under `language_support`."
        if request.include_differentiation
        else "Leave differentiation empty."
    )

    prompt = f"""\
Write a lesson plan.

{f"TOPIC: {request.topic}" if request.topic else ""}
PERIOD LENGTH: {request.duration_minutes} minutes
CLASS SIZE: {request.class_size}
{source_mod.audience_block(request.audience)}
{materials}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

{TEMPLATE_SHAPE[request.template]}

Requirements:
- 2 to 4 learning objectives, each starting with a measurable verb.
- Success criteria written as "I can ..." statements a student can self-assess against.
- Activity minutes must sum to {request.duration_minutes}. Allow time for settling and
  packing up — do not plan {request.duration_minutes} minutes of content into a
  {request.duration_minutes} minute period.
- For each activity say exactly what the teacher does and exactly what students do at the
  same time. "Discuss the topic" is not a plan; "Pairs list three causes on a mini-whiteboard,
  teacher circulates and collects two to share" is.
- Give at least two activities a specific `check_for_understanding` question.
- Name at least two misconceptions students are likely to bring, and how the lesson addresses them.
{differentiation_rule}
{"- Set meaningful homework that consolidates rather than extends." if request.include_homework else "- Leave homework empty."}

Verbs by cognitive level:
{bloom_verb_hint()}
{material.citation_rule()}
"""

    plan = await generate_structured(
        provider,
        LessonPlan,
        [system(PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )

    plan.title = request.title or plan.title
    plan.duration_minutes = request.duration_minutes
    plan.template = request.template
    plan.subject = plan.subject or request.audience.subject
    plan.grade_level = plan.grade_level or request.audience.grade_level
    plan.date = request.date or plan.date
    plan.citations = material.citations
    plan.meta = GenerationMeta(
        model=provider.model,
        provider=provider.name,
        seconds=round(time.perf_counter() - started, 2),
        grounded=material.grounded,
        source_documents=material.document_titles,
    )

    _fit_timings(plan)
    await report(1.0, f"{len(plan.activities)} activities over {plan.duration_minutes} minutes")
    return plan


def _fit_timings(plan: LessonPlan) -> None:
    """Scale activity minutes so they sum to the period length.

    Models routinely produce a "45 minute lesson" whose parts add to 60. Rescaling
    proportionally preserves the shape of the lesson — the relative weight the model gave
    each phase — while making the plan usable as written.
    """
    if not plan.activities or plan.duration_minutes <= 0:
        return

    planned = sum(a.minutes for a in plan.activities)
    if planned <= 0 or planned == plan.duration_minutes:
        return

    scale = plan.duration_minutes / planned
    for activity in plan.activities:
        activity.minutes = max(1, round(activity.minutes * scale))

    # Rounding leaves a residue; put it on the longest activity, which absorbs it best.
    drift = plan.duration_minutes - sum(a.minutes for a in plan.activities)
    if drift:
        longest = max(plan.activities, key=lambda a: a.minutes)
        longest.minutes = max(1, longest.minutes + drift)

    log.debug("Rescaled activity timings from %d to %d minutes", planned, plan.duration_minutes)
