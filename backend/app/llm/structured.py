"""Reliable structured output: schema-constrained generation with a validation repair loop.

This is the load-bearing piece of the whole product. Every generator asks the model for a
Pydantic model instance rather than for prose or markup, and gets back something that has
already been validated — so renderers can assume well-formed input.

Four defences, in order:

1. **Constrain decoding.** Pass the JSON Schema to the backend (Ollama ``format``,
   OpenAI ``response_format``) so invalid tokens are never sampled.
2. **Extract robustly.** Reasoning models wrap answers in ``<think>`` blocks and chat models
   like code fences; strip both before parsing.
3. **Repair.** On a validation failure, show the model its own output and the exact
   pydantic errors and ask for a corrected document. Repeat up to a budget.
4. **Ride out a flaky backend.** A local model runner can crash or drop a connection
   part-way through a long job; a question paper is twenty sequential calls, so one
   hiccup would otherwise discard everything generated so far. Transient failures are
   retried with backoff, and the model is given a moment to reload.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import LLMError, StructuredOutputError
from app.core.logging import get_logger
from app.llm.base import Completion, LLMProvider, Message, assistant, system, user

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# Reasoning models (Qwen3, DeepSeek-R1 and friends) emit a scratchpad before the answer.
_THINK_RE = re.compile(r"<think>.*?</think>|<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Remove reasoning scratchpads, including an unterminated one at the start."""
    text = _THINK_RE.sub("", text)
    # An output truncated mid-thought leaves a dangling opener; drop everything before it.
    if (idx := text.lower().rfind("</think>")) != -1:
        text = text[idx + len("</think>") :]
    return text.strip()


def extract_json(text: str) -> str:
    """Pull the JSON document out of a model reply.

    Handles bare JSON, fenced JSON, and JSON surrounded by commentary.
    """
    text = strip_reasoning(text)

    if match := _FENCE_RE.search(text):
        candidate = match.group(1).strip()
        if candidate:
            return candidate

    stripped = text.strip()
    if stripped[:1] in "[{":
        return stripped

    # Fall back to the outermost balanced brace/bracket span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            return text[start : end + 1]

    return stripped


def parse_json(text: str) -> Any:
    """Parse model JSON, tolerating the two mistakes small models actually make."""
    payload = extract_json(text)
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        pass

    # Trailing commas before a closing brace/bracket.
    repaired = re.sub(r",\s*([}\]])", r"\1", payload)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Unescaped newlines inside string literals.
    repaired = re.sub(r'(?<!\\)\n(?=[^"]*"(?:[^"]*"[^"]*")*[^"]*$)', r"\\n", repaired)
    return json.loads(repaired)  # raises, and the caller turns it into a repair round


def _format_errors(exc: ValidationError, limit: int = 12) -> str:
    lines = []
    for err in exc.errors()[:limit]:
        location = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"- {location}: {err['msg']}")
    if len(exc.errors()) > limit:
        lines.append(f"- ...and {len(exc.errors()) - limit} more")
    return "\n".join(lines)


def schema_instructions(schema: type[BaseModel]) -> str:
    """A system message describing the target schema, for backends that cannot enforce it."""
    return (
        "Reply with a single JSON document and nothing else — no prose, no code fence, "
        "no explanation. It must validate against this JSON Schema:\n\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}"
    )


#: How many times to retry a transient backend failure, and the base backoff. A crashed
#: Ollama runner takes a few seconds to reload the model, so the first wait is generous.
TRANSIENT_RETRIES = 3
RETRY_BASE_DELAY = 4.0


async def call_with_retry(
    provider: LLMProvider,
    conversation: Sequence[Message],
    *,
    json_schema: dict[str, Any] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    retries: int = TRANSIENT_RETRIES,
    on_retry: Callable[[int, str], Awaitable[None]] | None = None,
) -> Completion:
    """One completion, retrying failures that a retry can actually fix.

    Only :attr:`LLMError.transient` failures are retried — a crashed runner, a dropped
    connection, a rate limit. A missing model or a bad key fails immediately, because
    waiting four seconds and asking again would just waste the teacher's time.
    """
    last: LLMError | None = None

    for attempt in range(retries + 1):
        try:
            return await provider.chat(
                conversation,
                json_schema=json_schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError as exc:
            if not exc.transient or attempt == retries:
                raise
            last = exc
            delay = RETRY_BASE_DELAY * (2**attempt)
            log.warning(
                "Transient model backend failure (attempt %d/%d), retrying in %.0fs: %s",
                attempt + 1,
                retries + 1,
                delay,
                exc.message,
            )
            if on_retry is not None:
                await on_retry(attempt + 1, exc.message)
            await asyncio.sleep(delay)

    raise last or LLMError("The model backend failed repeatedly.")


async def generate_structured(
    provider: LLMProvider,
    schema: type[T],
    messages: Sequence[Message],
    *,
    max_attempts: int = 3,
    temperature: float | None = None,
    max_tokens: int | None = None,
    on_attempt: Callable[[int, str], Awaitable[None]] | None = None,
) -> T:
    """Ask the model for an instance of ``schema`` and return it validated.

    Raises :class:`StructuredOutputError` if the repair budget runs out.
    """
    json_schema = schema.model_json_schema()
    # A self-referential model (NoteSection contains subsections) serialises as a bare
    # `$ref` wrapper with no title of its own. Backends use the title to name the schema,
    # so put it back.
    json_schema.setdefault("title", schema.__name__)

    conversation: list[Message] = list(messages)

    # Belt and braces: describe the schema in the prompt too. Constrained decoding keeps
    # the output parseable, but the description is what keeps it *sensible* — small models
    # fill required fields with junk unless they know what the fields mean.
    conversation.insert(0, system(schema_instructions(schema)))

    last_error = ""
    last_text = ""

    for attempt in range(1, max_attempts + 1):
        if on_attempt is not None:
            await on_attempt(attempt, last_error)

        completion: Completion = await call_with_retry(
            provider,
            conversation,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        last_text = completion.text

        if not last_text.strip():
            last_error = "The model returned an empty response."
            conversation += [
                assistant(""),
                user("You returned nothing. Reply with the complete JSON document."),
            ]
            continue

        try:
            data = parse_json(last_text)
        except json.JSONDecodeError as exc:
            last_error = f"Invalid JSON: {exc}"
            log.warning("Attempt %d/%d — unparseable JSON: %s", attempt, max_attempts, exc)
            conversation += [
                assistant(last_text[:4000]),
                user(
                    f"That was not valid JSON ({exc}). Return the corrected, complete JSON "
                    "document only."
                ),
            ]
            continue

        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            last_error = _format_errors(exc)
            log.warning(
                "Attempt %d/%d — schema validation failed:\n%s", attempt, max_attempts, last_error
            )
            conversation += [
                assistant(json.dumps(data)[:6000]),
                user(
                    "That JSON does not satisfy the schema. Fix exactly these problems and "
                    f"return the complete corrected document:\n{last_error}"
                ),
            ]

    raise StructuredOutputError(
        f"The model could not produce valid {schema.__name__} after {max_attempts} attempts. "
        f"Last problem:\n{last_error}",
        detail={"schema": schema.__name__, "last_output": last_text[:2000]},
    )


async def generate_text(
    provider: LLMProvider,
    messages: Sequence[Message],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Plain prose completion with reasoning scratchpads stripped."""
    completion = await call_with_retry(
        provider, messages, temperature=temperature, max_tokens=max_tokens
    )
    return strip_reasoning(completion.text)
