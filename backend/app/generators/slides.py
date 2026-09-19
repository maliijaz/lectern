"""Slide deck generation.

Written as two passes, because one-shot deck generation fails in a specific, predictable
way: the model front-loads everything it knows, repeats itself around slide 8, and runs
dry before the end. Planning the whole deck first — titles, intent and rough content for
every slide — and only then writing each slide gives coverage that actually spans the
material. Slides are written in small batches so each call stays well inside the context
window of an 8B local model, and so progress is visible while it runs.
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
from app.schemas.common import GenerationMeta
from app.schemas.deck import Deck, DeckOutline, Slide, SlideLayout, SlideOutlineItem
from app.schemas.requests import SlidesRequest

log = get_logger(__name__)

#: Slides per content call. Four keeps each response small enough that a local model does
#: not start truncating, while still amortising the prompt.
BATCH_SIZE = 4

DECK_PERSONA = (
    "You are an experienced teacher preparing lecture slides. You know that slides are a "
    "visual aid, not a document: the slide carries the skeleton, the speaker notes carry "
    "the explanation. You never write a paragraph on a slide."
)

SLIDE_RULES = """\
Rules for every slide:
- The heading is a claim or a question, not a label. "Why cells need mitochondria" beats "Mitochondria".
- At most 6 bullets. Each is a short phrase under 15 words. No full sentences, no trailing full stops.
- Never repeat a point that appeared on an earlier slide.
- Speaker notes carry the actual teaching: the explanation, an example, and the question to ask.
- Estimate duration_minutes honestly — a dense slide takes longer than a title slide.
"""


class SlideBatch(BaseModel):
    """One content-pass response."""

    model_config = ConfigDict(extra="ignore")

    slides: list[Slide] = Field(default_factory=list)


async def generate(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: SlidesRequest,
    *,
    progress=None,  # noqa: ANN001 — optional async (fraction, message) callback
) -> Deck:
    """Produce a complete :class:`Deck`."""
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

    await report(0.15, "Planning the deck")
    outline = await _plan(provider, settings, request, material)

    slides: list[Slide] = []
    planned = outline.slides[: request.slide_count]
    total_batches = max(1, (len(planned) + BATCH_SIZE - 1) // BATCH_SIZE)

    for batch_index in range(total_batches):
        window = planned[batch_index * BATCH_SIZE : (batch_index + 1) * BATCH_SIZE]
        if not window:
            break
        await report(
            0.2 + 0.7 * batch_index / total_batches,
            f"Writing slides {batch_index * BATCH_SIZE + 1}–{batch_index * BATCH_SIZE + len(window)}"
            f" of {len(planned)}",
        )
        slides.extend(
            await _write_batch(
                provider, settings, request, material, outline, window, written=slides
            )
        )

    await report(0.95, "Assembling the deck")
    deck = Deck(
        title=request.title or outline.title,
        subtitle=outline.subtitle or request.audience.subject,
        presenter=request.presenter,
        objectives=outline.objectives if request.include_objectives else [],
        slides=_finalise(slides, request, outline),
        theme=request.theme,
        meta=GenerationMeta(
            model=provider.model,
            provider=provider.name,
            seconds=round(time.perf_counter() - started, 2),
            grounded=material.grounded,
            source_documents=material.document_titles,
        ),
    )
    await report(1.0, f"{len(deck.slides)} slides ready")
    return deck


def _source_queries(request: SlidesRequest) -> list[str]:
    """Several framings, so retrieval covers the topic instead of one paragraph."""
    topic = request.topic or request.audience.subject or "the material"
    return [
        topic,
        f"key concepts and definitions in {topic}",
        f"examples and applications of {topic}",
        f"common misconceptions about {topic}",
    ]


async def _plan(
    provider: LLMProvider,
    settings: Settings,
    request: SlidesRequest,
    material: source_mod.SourceMaterial,
) -> DeckOutline:
    wanted: list[str] = []
    if request.include_objectives:
        wanted.append("an opening slide stating the learning objectives")
    if request.include_quiz_slides:
        wanted.append(
            "two or three `quiz` slides spread through the deck, each checking the "
            "preceding section rather than sitting at the end"
        )
    if request.include_diagrams:
        wanted.append("at least one `diagram` slide where a visual explains better than words")
    if request.include_summary:
        wanted.append("a `summary` slide recapping the key points")

    prompt = f"""\
Plan a {request.slide_count}-slide lecture deck.

{f"TOPIC: {request.topic}" if request.topic else ""}
{source_mod.audience_block(request.audience)}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

Produce exactly {request.slide_count} entries, in teaching order: open with a `title` slide,
build understanding progressively, and close cleanly.

The deck must include:
{chr(10).join(f"- {w}" for w in wanted) or "- a clear beginning, middle and end"}

Available layouts: {", ".join(layout.value for layout in SlideLayout)}.

For each slide give a heading, the layout, one sentence of intent, and two to four key
points it will cover. Do not write the slide content yet — this is the plan.
{material.citation_rule()}
"""

    outline = await generate_structured(
        provider,
        DeckOutline,
        [system(DECK_PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )

    if not outline.slides:
        raise ValueError("The model returned an empty deck outline")
    return outline


async def _write_batch(
    provider: LLMProvider,
    settings: Settings,
    request: SlidesRequest,
    material: source_mod.SourceMaterial,
    outline: DeckOutline,
    window: list[SlideOutlineItem],
    *,
    written: list[Slide],
) -> list[Slide]:
    """Write the slides for one window of the outline."""
    already = (
        "\n".join(f"- {s.heading}: {'; '.join(s.bullets[:3])}" for s in written[-6:])
        if written
        else "(none yet — these are the first slides)"
    )

    plan_text = "\n\n".join(
        f"Slide {i}:\n  heading: {item.heading}\n  layout: {item.layout.value}\n"
        f"  intent: {item.intent}\n  key points: {'; '.join(item.key_points)}\n"
        f"  minutes: {item.duration_minutes}"
        for i, item in enumerate(window, start=len(written) + 1)
    )

    diagram_rule = (
        (
            "Figures — use one only where the picture carries meaning the bullets cannot:\n"
            "- A process, hierarchy or relationship: set `figure.kind` to 'diagram' and put "
            "valid Mermaid source in `figure.spec` (flowchart, graph or mindmap). Keep it to "
            "eight nodes at most; a diagram nobody can read from the back row is worse than "
            "a list.\n"
            "- Numbers worth comparing: set `figure.kind` to 'chart' and put a JSON spec in "
            '`figure.spec`: {"type": "bar"|"line"|"pie", "title": "...", "x_label": "...", '
            '"y_label": "...", "labels": ["A","B"], "series": [{"name": "...", '
            '"values": [1,2]}]}. Only use real figures from the source material — never '
            "invent data.\n"
            "- Always write `alt_text` describing what the figure shows.\n"
        )
        if request.include_diagrams
        else "Do not specify figures.\n"
    )
    notes_rule = "" if request.include_speaker_notes else "Leave speaker_notes empty.\n"

    prompt = f"""\
Deck: "{outline.title}"
{source_mod.audience_block(request.audience)}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

ALREADY COVERED (do not repeat any of this):
{already}

Write the full content for exactly these {len(window)} slides, following the plan:

{plan_text}

{SLIDE_RULES}
{diagram_rule}{notes_rule}
For a `quiz` slide, put the question in the heading and the options in bullets, and put
the correct answer with its explanation in speaker_notes.
{material.citation_rule()}

Return the slides in the same order as the plan.
"""

    batch = await generate_structured(
        provider,
        SlideBatch,
        [system(DECK_PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )

    slides = batch.slides[: len(window)]
    # A model that returns fewer slides than asked leaves gaps in the deck; fill them from
    # the plan rather than silently shipping a short deck.
    for item in window[len(slides) :]:
        log.warning("Model skipped slide %r; falling back to its plan", item.heading)
        slides.append(
            Slide(
                layout=item.layout,
                heading=item.heading,
                bullets=item.key_points,
                speaker_notes=item.intent,
                duration_minutes=item.duration_minutes,
            )
        )

    for slide, item in zip(slides, window, strict=False):
        if not slide.heading:
            slide.heading = item.heading
        if slide.duration_minutes <= 0:
            slide.duration_minutes = item.duration_minutes
        if material.citations:
            slide.citations = (
                source_mod.resolve_citations(
                    [c.chunk_id or c.document_id for c in slide.citations], material.citations
                )
                or slide.citations
            )

    return slides


def _finalise(slides: list[Slide], request: SlidesRequest, outline: DeckOutline) -> list[Slide]:
    """Guarantee the structural slides exist, whatever the model returned."""
    if not slides:
        return slides

    if slides[0].layout != SlideLayout.TITLE:
        slides.insert(
            0,
            Slide(
                layout=SlideLayout.TITLE,
                heading=request.title or outline.title,
                subheading=outline.subtitle or request.audience.subject,
                duration_minutes=0.5,
            ),
        )

    if request.include_objectives and outline.objectives:
        has_objectives = any(
            "objective" in s.heading.lower() or "learn" in s.heading.lower() for s in slides[:3]
        )
        if not has_objectives:
            slides.insert(
                1,
                Slide(
                    layout=SlideLayout.BULLETS,
                    heading="What you will be able to do",
                    bullets=[o.text for o in outline.objectives[:6]],
                    speaker_notes=(
                        "Read these aloud and return to them at the end so learners can "
                        "judge their own progress."
                    ),
                    duration_minutes=1.0,
                ),
            )

    if request.include_summary and slides[-1].layout not in (
        SlideLayout.SUMMARY,
        SlideLayout.QUESTIONS,
    ):
        slides.append(
            Slide(
                layout=SlideLayout.SUMMARY,
                heading="Key points",
                bullets=[s.heading for s in slides if s.layout == SlideLayout.BULLETS][:6],
                speaker_notes="Recap, then invite questions.",
                duration_minutes=2.0,
            )
        )

    return slides
