"""Rubric generation.

The hard part of a rubric is not the grid, it is writing descriptors that describe
*observable work* rather than restating the criterion with an adverb. "Excellent use of
evidence / Good use of evidence / Poor use of evidence" is not a rubric — it tells a
student nothing about how to move up a level. The prompt here is built almost entirely
around preventing that.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.generators import source as source_mod
from app.llm.base import LLMProvider, system, user
from app.llm.structured import generate_structured
from app.schemas.common import GenerationMeta
from app.schemas.requests import RubricRequest
from app.schemas.rubric import CriterionLevel, PerformanceLevel, Rubric, RubricStyle

log = get_logger(__name__)

PERSONA = (
    "You write assessment rubrics that students can use to improve their own work before "
    "it is marked. Every descriptor names something a marker could point to on the page."
)


async def generate(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: RubricRequest,
    *,
    progress=None,  # noqa: ANN001
) -> Rubric:
    started = time.perf_counter()

    async def report(fraction: float, message: str) -> None:
        if progress is not None:
            await progress(fraction, message)

    await report(0.1, "Gathering source material")
    material = await source_mod.collect(
        db,
        settings=settings,
        topic=request.topic or request.task_description,
        document_ids=request.document_ids,
        queries=[
            request.task_description or request.topic,
            f"assessment criteria for {request.topic}",
        ],
        budget=10_000,
    )

    await report(0.3, "Designing the rubric")
    levels = request.level_names or ["Exemplary", "Proficient", "Developing", "Beginning"]

    if request.style == RubricStyle.HOLISTIC:
        shape = (
            "Produce a HOLISTIC rubric: fill `holistic_descriptors` with one entry per "
            "level describing the work as a whole. Leave `criteria` empty."
        )
    elif request.style == RubricStyle.SINGLE_POINT:
        shape = (
            "Produce a SINGLE-POINT rubric: fill `criteria`, and for each give exactly one "
            "level named 'Meets the standard' describing the target. The 'not yet' and "
            "'exceeds' columns are left for the teacher to write on."
        )
    else:
        shape = (
            f"Produce an ANALYTIC rubric: {request.criteria_count} criteria, each with a "
            f"descriptor for every one of these levels: {', '.join(levels)}."
        )

    points = (
        f"The rubric totals {request.total_points:g} points; distribute them across criteria "
        "according to importance, not equally by default."
        if request.total_points
        else "Choose a sensible point value per criterion."
    )

    prompt = f"""\
Write a rubric.

TASK BEING ASSESSED: {request.task_description or request.topic}
{source_mod.audience_block(request.audience)}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

{shape}
{points}

Rules that matter more than anything else here:
- A descriptor must describe what is ON THE PAGE, not how good it is. Write "cites three
  or more sources, each linked to a specific claim", never "excellent use of sources".
- Adjacent levels must differ by something concrete a student could act on. If the only
  difference between two levels is an adverb, rewrite both.
- Describe what is present at each level, not what is missing. "Uses one source" beats
  "does not use enough sources".
- Criteria must not overlap. If two criteria would be scored from the same evidence, merge them.
- Criteria must be about this task specifically, not generic school-essay virtues.
{"- Also write `student_facing_summary`: the same standards in plain language, addressed to the student, under 120 words." if request.student_facing else "- Leave student_facing_summary empty."}

Level names to use, strongest first: {", ".join(levels)}
{material.citation_rule()}
"""

    rubric = await generate_structured(
        provider,
        Rubric,
        [system(PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )

    rubric.title = request.title or rubric.title
    rubric.style = request.style
    rubric.task_description = rubric.task_description or request.task_description
    rubric.subject = rubric.subject or request.audience.subject
    rubric.grade_level = rubric.grade_level or request.audience.grade_level
    rubric.levels = _normalise_levels(rubric, levels)
    _fill_missing_cells(rubric)

    if request.total_points:
        rubric.total_points = request.total_points

    rubric.meta = GenerationMeta(
        model=provider.model,
        provider=provider.name,
        seconds=round(time.perf_counter() - started, 2),
        grounded=material.grounded,
        source_documents=material.document_titles,
    )

    await report(1.0, f"{len(rubric.criteria)} criteria × {len(rubric.levels)} levels")
    return rubric


def _normalise_levels(rubric: Rubric, requested: list[str]) -> list[PerformanceLevel]:
    """Use the level names the teacher asked for, with descending weights."""
    existing = {level.name: level for level in rubric.levels}
    count = max(1, len(requested) - 1)
    return [
        PerformanceLevel(
            name=name,
            weight=round(1.0 - index / count, 2) if count else 1.0,
            description=existing.get(name).description if name in existing else "",
        )
        for index, name in enumerate(requested)
    ]


def _fill_missing_cells(rubric: Rubric) -> None:
    """Guarantee a full grid.

    A rubric with holes renders as blank table cells, which looks broken and is unusable.
    A placeholder that says so is honest and tells the teacher exactly what to fill in.
    """
    level_names = [level.name for level in rubric.levels]
    for criterion in rubric.criteria:
        present = {cell.level_name: cell for cell in criterion.levels}
        # Tolerate case and whitespace drift in the model's level names.
        lookup = {name.strip().lower(): cell for name, cell in present.items()}
        rebuilt: list[CriterionLevel] = []
        for name in level_names:
            cell = present.get(name) or lookup.get(name.strip().lower())
            rebuilt.append(
                cell.model_copy(update={"level_name": name})
                if cell
                else CriterionLevel(level_name=name, descriptor="— to be completed —")
            )
        criterion.levels = rebuilt
