"""Worksheet generation.

A worksheet is not a short exam. An exam sorts students; a worksheet teaches them. So the
generation asks for a worked example first, orders questions from easy to hard, and puts
scaffolding on the early items — a student working alone at home has no one to ask.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.generators import source as source_mod
from app.llm.base import LLMProvider, system, user
from app.llm.structured import generate_structured
from app.schemas.common import Difficulty, GenerationMeta
from app.schemas.paper import Question
from app.schemas.requests import WorksheetRequest
from app.schemas.study import Worksheet, WorksheetSection

log = get_logger(__name__)

PERSONA = (
    "You write practice worksheets for students working on their own. You model the method "
    "before you ask for it, you start easy so the student gets going, and you make the "
    "first question of each type almost identical to your worked example."
)

_DIFFICULTY_ORDER = {Difficulty.EASY: 0, Difficulty.MEDIUM: 1, Difficulty.HARD: 2}


class _Draft(BaseModel):
    """What the model returns; assembled into a Worksheet afterwards."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    warm_up: list[str] = Field(default_factory=list)
    worked_example: str = ""
    questions: list[Question] = Field(default_factory=list)
    challenge: list[Question] = Field(default_factory=list)
    reflection_prompt: str = ""


async def generate(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: WorksheetRequest,
    *,
    progress=None,  # noqa: ANN001
) -> Worksheet:
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
            f"worked examples of {request.topic}",
            f"practice problems on {request.topic}",
        ],
        budget=16_000,
    )

    await report(0.3, "Writing the questions")
    types = ", ".join(t.value for t in request.question_types)

    prompt = f"""\
Write a practice worksheet.

{f"TOPIC: {request.topic}" if request.topic else ""}
{source_mod.audience_block(request.audience)}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

Produce:
- 2 or 3 `warm_up` prompts: quick recall that activates what the student already knows.
{"- A `worked_example`: one problem solved in full, one step per line, with a sentence saying why each step follows. This is the model the student will imitate." if request.include_worked_example else ""}
- {request.question_count} questions using only these types: {types}.
{"- Ordered from easiest to hardest, so the student builds momentum." if request.graduated else ""}
{"- 2 `challenge` questions that go beyond the lesson, for students who finish early." if request.include_challenge else "- No challenge questions."}
- A `reflection_prompt` asking the student something about their own understanding, not about the content.

Every question needs a complete answer and, where marks are split, a mark scheme —
the teacher will print an answer key from this.
Put a `hint` on the first two questions only: a nudge, not the answer.
Target difficulty: {request.difficulty.value}.
{material.citation_rule()}
"""

    draft = await generate_structured(
        provider,
        _Draft,
        [system(PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )

    questions = draft.questions[: request.question_count]
    if request.graduated:
        questions.sort(key=lambda q: (_DIFFICULTY_ORDER[q.difficulty], q.marks))

    worksheet = Worksheet(
        title=request.title or draft.title or f"{request.topic} — practice",
        subject=request.audience.subject,
        grade_level=request.audience.grade_level,
        warm_up=draft.warm_up,
        worked_example=draft.worked_example if request.include_worked_example else "",
        sections=[
            WorksheetSection(
                title="Practice",
                instructions="Work through these in order. Show your working.",
                questions=questions,
                answer_lines=request.answer_lines,
            )
        ],
        challenge=draft.challenge[:2] if request.include_challenge else [],
        reflection_prompt=draft.reflection_prompt,
        estimated_minutes=request.estimated_minutes or _estimate_minutes(questions),
        citations=material.citations,
        meta=GenerationMeta(
            model=provider.model,
            provider=provider.name,
            seconds=round(time.perf_counter() - started, 2),
            grounded=material.grounded,
            source_documents=material.document_titles,
        ),
    )

    await report(1.0, f"{len(worksheet.questions)} questions")
    return worksheet


def _estimate_minutes(questions: list[Question]) -> int:
    """Students working alone take longer than the exam estimate — add a margin."""
    return max(5, round(sum(q.estimated_minutes for q in questions) * 1.4))
