"""Lecture notes generation.

Same two-pass shape as slides, for the same reason, but the second pass runs one section
at a time: notes sections are long, and asking a local model for six of them in one reply
reliably produces three good ones and three stubs.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.generators import source as source_mod
from app.llm.base import LLMProvider, system, user
from app.llm.structured import generate_structured
from app.schemas.common import Depth, GenerationMeta, KeyTerm
from app.schemas.notes import LectureNotes, NoteSection, NotesOutline, SectionPlan
from app.schemas.requests import NotesRequest

log = get_logger(__name__)

NOTES_PERSONA = (
    "You are a teacher writing the notes you wish you had been given as a student. You "
    "explain the idea before the terminology, you use concrete examples, and you say "
    "plainly what students usually get wrong."
)

#: Words per section, by depth. The model needs a number, not an adjective — "detailed"
#: means nothing without a target, and an unanchored request produces three sentences.
DEPTH_WORDS: dict[Depth, tuple[int, str]] = {
    Depth.OUTLINE: (120, "a tight skeleton: the claims, with one line of support each"),
    Depth.STANDARD: (350, "a clear explanation a student can revise from"),
    Depth.DETAILED: (700, "a thorough treatment with worked reasoning and several examples"),
    Depth.TEXTBOOK: (
        1200,
        "textbook depth: derivations, edge cases, and connections to other topics",
    ),
}


async def generate(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: NotesRequest,
    *,
    progress=None,  # noqa: ANN001
) -> LectureNotes:
    started = time.perf_counter()

    async def report(fraction: float, message: str) -> None:
        if progress is not None:
            await progress(fraction, message)

    await report(0.05, "Gathering source material")
    material = await source_mod.collect(
        db,
        settings=settings,
        topic=request.topic,
        document_ids=request.document_ids,
        queries=_source_queries(request),
    )

    await report(0.15, "Planning the structure")
    outline = await _plan(provider, settings, request, material)

    sections: list[NoteSection] = []
    planned = outline.section_plan[: request.max_sections]
    for index, plan in enumerate(planned):
        await report(
            0.2 + 0.65 * index / max(1, len(planned)),
            f"Writing “{plan.heading}” ({index + 1} of {len(planned)})",
        )
        sections.append(
            await _write_section(
                provider, settings, request, material, outline, plan, written=sections
            )
        )

    await report(0.9, "Writing the summary and glossary")
    notes = LectureNotes(
        title=request.title or outline.title,
        subtitle=request.audience.subject,
        depth=request.depth,
        overview=outline.overview,
        objectives=outline.objectives,
        sections=sections,
        citations=material.citations,
        meta=GenerationMeta(
            model=provider.model,
            provider=provider.name,
            seconds=round(time.perf_counter() - started, 2),
            grounded=material.grounded,
            source_documents=material.document_titles,
        ),
    )

    closing = await _write_closing(provider, settings, request, notes)
    notes.summary = closing.summary
    notes.review_questions = closing.review_questions if request.include_review_questions else []
    notes.further_reading = closing.further_reading
    if request.include_glossary:
        notes.glossary = _collect_glossary(sections, closing.glossary)

    await report(1.0, f"{notes.word_count():,} words across {len(sections)} sections")
    return notes


def _source_queries(request: NotesRequest) -> list[str]:
    topic = request.topic or request.audience.subject or "the material"
    return [
        topic,
        f"definitions and key terms in {topic}",
        f"worked examples of {topic}",
        f"common errors and misconceptions in {topic}",
        f"why {topic} matters in practice",
    ]


async def _plan(
    provider: LLMProvider,
    settings: Settings,
    request: NotesRequest,
    material: source_mod.SourceMaterial,
) -> NotesOutline:
    prompt = f"""\
Plan a set of lecture notes.

{f"TOPIC: {request.topic}" if request.topic else ""}
{source_mod.audience_block(request.audience)}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

Produce:
- a title
- a one-paragraph overview that orients a reader who knows nothing about this yet
- three to six learning objectives, each starting with a measurable verb
- between 3 and {request.max_sections} sections, in the order they should be read

Sections must build on each other: no section may depend on something a later section
introduces. For each, give the heading, one sentence on what it must explain, its key
points, and whether a worked example belongs there.
{material.citation_rule()}
"""

    outline = await generate_structured(
        provider,
        NotesOutline,
        [system(NOTES_PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )
    if not outline.section_plan:
        raise ValueError("The model returned no section plan")
    return outline


async def _write_section(
    provider: LLMProvider,
    settings: Settings,
    request: NotesRequest,
    material: source_mod.SourceMaterial,
    outline: NotesOutline,
    plan: SectionPlan,
    *,
    written: list[NoteSection],
) -> NoteSection:
    target_words, depth_description = DEPTH_WORDS[request.depth]

    covered = (
        "\n".join(f"- {s.heading}" for s in written) if written else "(this is the first section)"
    )

    extras: list[str] = []
    if request.include_examples and plan.needs_example:
        extras.append(
            "Include one worked example in `examples`: the problem, the steps in order, "
            "the answer, and a line on why the method works."
        )
    if request.include_misconceptions:
        extras.append(
            "Include at least one callout of kind `misconception` naming a specific wrong "
            "idea students hold here, and what makes it wrong."
        )
    if request.include_glossary:
        extras.append("Put every term you introduce in `key_terms` with a plain definition.")

    prompt = f"""\
Write one section of the notes "{outline.title}".

SECTION: {plan.heading}
IT MUST EXPLAIN: {plan.covers}
KEY POINTS TO COVER: {"; ".join(plan.key_points) or "(use your judgement)"}

{source_mod.audience_block(request.audience)}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

SECTIONS ALREADY WRITTEN (do not repeat them, and you may refer back to them):
{covered}

Write roughly {target_words} words of `body` — {depth_description}.
Use Markdown in `body`: paragraphs, lists, **bold** for terms being defined. Do not use
headings inside `body`; the section heading is already set.
Explain the idea in plain language before you name it.
{chr(10).join(extras)}
{material.citation_rule()}
"""

    section = await generate_structured(
        provider,
        NoteSection,
        [system(NOTES_PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )
    if not section.heading:
        section.heading = plan.heading
    return section


async def _write_closing(
    provider: LLMProvider,
    settings: Settings,
    request: NotesRequest,
    notes: LectureNotes,
) -> LectureNotes:
    body = "\n\n".join(f"## {s.heading}\n{s.body[:600]}" for s in notes.sections)
    review_rule = (
        f"- {min(8, max(3, len(notes.sections) * 2))} review questions with brief model answers, "
        "spread across the sections and progressing from recall to application"
        if request.include_review_questions
        else "- an empty review_questions list"
    )

    prompt = f"""\
These are the notes you have written for "{notes.title}":

{body[:12000]}

Now write the closing material:
- a `summary` of 120–180 words that ties the sections together, not a list of headings
{review_rule}
- `further_reading`: three to five genuinely useful directions, described rather than
  cited as URLs
- `glossary`: every key term across all sections, defined once

Leave the other fields empty.
"""

    return await generate_structured(
        provider,
        LectureNotes,
        [system(NOTES_PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )


def _collect_glossary(sections: list[NoteSection], extra: list[KeyTerm]) -> list[KeyTerm]:
    """Merge per-section key terms with the closing pass, first definition winning."""
    merged: dict[str, KeyTerm] = {}
    for section in sections:
        for term in section.key_terms:
            merged.setdefault(term.term.strip().lower(), term)
    for term in extra:
        merged.setdefault(term.term.strip().lower(), term)
    return sorted(merged.values(), key=lambda t: t.term.lower())
