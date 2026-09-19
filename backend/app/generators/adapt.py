"""Adapting existing content for a different audience.

Reading-level rewrites, translation, and differentiated variants all share one shape: take
an artifact the teacher already has, change one dimension of it, and keep everything else —
the structure, the question count, the marks — identical. Keeping the structure fixed is
what makes the result a *variant* a teacher can hand out alongside the original, rather
than a second artifact they have to re-check from scratch.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import NotFoundError, ValidationFailed
from app.core.logging import get_logger
from app.db.models import Artifact
from app.llm.base import LLMProvider, system, user
from app.llm.structured import generate_structured
from app.schemas import ARTIFACT_SCHEMAS
from app.schemas.requests import AdaptRequest

log = get_logger(__name__)

PERSONA = (
    "You adapt teaching material without changing what it teaches. You keep the structure, "
    "the number of items and the assessed content exactly as they are, and change only "
    "what you were asked to change."
)

#: Approximate sentence length and vocabulary guidance per band. Vague instructions like
#: "make it simpler" produce material that is merely shorter, not more readable.
READING_BANDS: dict[str, str] = {
    "grade 3": "Sentences under 10 words. Only everyday words. One idea per sentence.",
    "grade 5": "Sentences under 14 words. Explain any word longer than three syllables.",
    "grade 7": "Sentences under 18 words. Subject terms allowed if defined on first use.",
    "grade 9": "Sentences under 22 words. Subject terms used normally.",
    "grade 11": "Full academic register, but no unnecessary complexity.",
}

VARIANT_RULES: dict[str, str] = {
    "support": (
        "Produce the SUPPORT version. Keep every item, but add scaffolding: a worked first "
        "step, a sentence starter, a word bank, or a hint. Break multi-step questions into "
        "labelled parts. Do not reduce what is assessed — reduce what is in the way of it."
    ),
    "core": "Produce the CORE version: the material as it stands, lightly tightened.",
    "extension": (
        "Produce the EXTENSION version. Keep every item, but raise the demand: remove "
        "scaffolding, ask for justification where description was enough, and add a "
        "'why' or 'what if' to the closing items. Do not simply add more questions."
    ),
}


async def adapt(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: AdaptRequest,
    *,
    progress=None,  # noqa: ANN001
) -> tuple[Any, str]:
    """Return the adapted content object and the artifact kind it belongs to."""
    started = time.perf_counter()

    async def report(fraction: float, message: str) -> None:
        if progress is not None:
            await progress(fraction, message)

    artifact = await db.get(Artifact, request.artifact_id)
    if artifact is None:
        raise NotFoundError(f"No artifact {request.artifact_id}")

    schema = ARTIFACT_SCHEMAS.get(artifact.kind)
    if schema is None:
        raise ValidationFailed(f"{artifact.kind} artifacts cannot be adapted.")

    changes = _describe_changes(request)
    if not changes:
        raise ValidationFailed(
            "Say what to change: a reading level, a language, or a differentiated variant."
        )

    await report(0.3, "Rewriting")
    original = schema.model_validate(artifact.content)

    structure_rule = (
        "Keep the structure identical: the same sections in the same order, the same number "
        "of items, the same marks. Change only the wording."
        if request.preserve_structure
        else "You may restructure if it genuinely helps the target audience."
    )

    prompt = f"""\
Adapt the material below.

### WHAT TO CHANGE
{chr(10).join(f"- {c}" for c in changes)}

### WHAT NOT TO CHANGE
- The concepts taught and the facts stated.
- {structure_rule}
- Correct answers stay correct. If you reword a question, reword its answer to match.
- Any subject-specific term that the student is expected to learn stays; explain it rather
  than replacing it.

### THE MATERIAL
{original.model_dump_json(indent=2)[:40000]}

Return the complete adapted material in the same schema.
"""

    adapted = await generate_structured(
        provider,
        schema,
        [system(PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )

    _stamp_title(adapted, request)
    if hasattr(adapted, "meta"):
        adapted.meta = adapted.meta.model_copy(
            update={
                "model": provider.model,
                "provider": provider.name,
                "seconds": round(time.perf_counter() - started, 2),
            }
        )

    await report(1.0, "Adapted")
    return adapted, artifact.kind


def _describe_changes(request: AdaptRequest) -> list[str]:
    changes: list[str] = []

    if request.target_reading_level:
        band = READING_BANDS.get(request.target_reading_level.strip().lower(), "")
        changes.append(
            f"Rewrite at a {request.target_reading_level} reading level."
            + (f" {band}" if band else "")
        )

    if request.target_language:
        changes.append(
            f"Translate into {request.target_language}. Keep subject-specific terms "
            "accurate; where a term has no good equivalent, give the original in brackets."
        )

    if request.variant and request.variant in VARIANT_RULES:
        changes.append(VARIANT_RULES[request.variant])

    if request.simplify_vocabulary:
        changes.append(
            "Replace unnecessarily difficult words with plain equivalents. Keep subject "
            "terms and define them on first use."
        )

    if request.add_scaffolding:
        changes.append(
            "Add scaffolding: sentence starters, a first worked step, or a word bank where "
            "a student could get stuck."
        )

    return changes


def _stamp_title(content: Any, request: AdaptRequest) -> None:
    """Label the variant so it is not confused with the original on a printer."""
    labels = []
    if request.variant:
        labels.append(request.variant.title())
    if request.target_reading_level:
        labels.append(request.target_reading_level)
    if request.target_language:
        labels.append(request.target_language)

    if not labels:
        return

    suffix = f" ({' · '.join(labels)})"
    if hasattr(content, "title") and isinstance(content.title, str):
        if not content.title.endswith(suffix):
            content.title = content.title + suffix
    elif hasattr(content, "meta") and hasattr(content.meta, "exam_name"):
        content.meta.exam_name = content.meta.exam_name + suffix
