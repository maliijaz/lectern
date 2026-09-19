"""Flashcard generation.

Cards are generated against the rules of spaced repetition rather than by splitting a
glossary in half: one fact per card, the prompt phrased as a question, and no card whose
answer is a list — a card the student can only half-remember teaches them to half-remember.
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
from app.schemas.requests import FlashcardsRequest
from app.schemas.study import CardKind, Flashcard, FlashcardDeck

log = get_logger(__name__)

PERSONA = (
    "You build spaced-repetition decks. You know the rules: one idea per card, a prompt "
    "that has exactly one right answer, and no card that asks the learner to recall a list."
)


async def generate(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: FlashcardsRequest,
    *,
    progress=None,  # noqa: ANN001
) -> FlashcardDeck:
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
            f"definitions and key terms in {request.topic}",
            f"facts worth memorising about {request.topic}",
        ],
        budget=18_000,
    )

    await report(0.3, "Writing the cards")
    kinds = ["basic"]
    if request.include_cloze:
        kinds.append("cloze")
    if request.include_reversed:
        kinds.append("reversed")

    prompt = f"""\
Build a flashcard deck of {request.card_count} cards.

{f"TOPIC: {request.topic}" if request.topic else ""}
{source_mod.audience_block(request.audience)}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

Rules:
- One fact per card. If an answer needs "and", it is two cards.
- The front is a question or a term, never a topic heading. "What does rubisco do?" is a
  card; "Rubisco" alone is not.
- The back is the shortest complete answer. No paragraphs.
- Never write a card whose answer is a list of items — split it, or ask for one specific item.
- Vary what you ask about the same idea: its definition, its function, when it applies,
  how it differs from a neighbouring idea.
{"- Add an `example` to cards where a concrete instance makes the idea stick." if request.include_examples else ""}
- Use these card kinds: {", ".join(kinds)}.
{"- For `cloze` cards, write the full sentence in `front` with the hidden part wrapped as {{c1::the answer}}, and put the hidden text in `back`. The sentence must still make sense with the gap." if request.include_cloze else ""}
{"- Use `reversed` only where recall genuinely works both ways, such as a term and its definition. Never for a fact with a one-way relationship." if request.include_reversed else ""}
- Tag each card with its sub-topic.
{material.citation_rule()}
"""

    deck = await generate_structured(
        provider,
        FlashcardDeck,
        [system(PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )

    deck.title = request.title or deck.title or f"{request.topic} — flashcards"
    deck.subject = deck.subject or request.audience.subject
    deck.cards = _clean(deck.cards, request)
    deck.meta = GenerationMeta(
        model=provider.model,
        provider=provider.name,
        seconds=round(time.perf_counter() - started, 2),
        grounded=material.grounded,
        source_documents=material.document_titles,
    )

    await report(1.0, f"{len(deck.cards)} cards")
    return deck


def _clean(cards: list[Flashcard], request: FlashcardsRequest) -> list[Flashcard]:
    """Drop duplicates and unusable cards, and honour the kinds the teacher allowed."""
    seen: set[str] = set()
    kept: list[Flashcard] = []

    for card in cards:
        if not card.front.strip():
            continue

        key = card.front.strip().lower()
        if key in seen:
            continue
        seen.add(key)

        if card.kind == CardKind.CLOZE:
            if not request.include_cloze:
                card.kind = CardKind.BASIC
            elif "{{c" not in card.front:
                # A cloze card with no gap is just a basic card wearing a hat.
                card.kind = CardKind.BASIC
        if card.kind == CardKind.REVERSED and not request.include_reversed:
            card.kind = CardKind.BASIC

        if card.kind != CardKind.CLOZE and not card.back.strip():
            continue

        kept.append(card)
        if len(kept) >= request.card_count:
            break

    return kept
