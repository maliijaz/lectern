"""Deterministic stub provider.

Synthesises a schema-valid instance from the JSON Schema it is handed, which lets the whole
pipeline — generators, validators, renderers, the CLI — be exercised in tests and in CI
with no model, no GPU and no network.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from app.llm.base import Completion, LLMProvider, Message, ProbeResult


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", "memory://fake")
        kwargs.setdefault("model", "fake-model")
        super().__init__(**kwargs)
        #: Canned replies popped in order; falls back to synthesis when empty.
        self.scripted: list[str] = list(kwargs.get("scripted") or [])
        #: Every call recorded, for assertions in tests.
        self.calls: list[dict[str, Any]] = []
        #: Per-schema builders keyed by the schema's title, e.g. {"QuestionBatch": fn}.
        #: Schema synthesis alone cannot satisfy cross-field validators (an MCQ needs
        #: options with exactly one correct), so tests that exercise a real generator
        #: register a builder that returns a plausible instance instead.
        self.responders: dict[str, Callable[[dict[str, Any], list[Message]], Any]] = dict(
            kwargs.get("responders") or {}
        )

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        stop: Sequence[str] | None = None,
    ) -> Completion:
        self.calls.append({"messages": [m.as_dict() for m in messages], "json_schema": json_schema})

        responder = self.responders.get(json_schema.get("title", "")) if json_schema else None

        if self.scripted:
            text = self.scripted.pop(0)
        elif responder is not None:
            text = json.dumps(responder(json_schema, list(messages)), default=str)
        elif json_schema is not None:
            seed = _seed_from(messages)
            text = json.dumps(synthesize(json_schema, json_schema, seed))
        else:
            text = f"[fake] {messages[-1].content[:200]}" if messages else "[fake]"

        return Completion(
            text=text,
            model=self.model,
            prompt_tokens=sum(len(m.content) // 4 for m in messages),
            completion_tokens=len(text) // 4,
            seconds=0.0,
            schema_enforced=json_schema is not None,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        result = await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        for word in result.text.split(" "):
            yield word + " "

    async def list_models(self) -> list[str]:
        return [self.model]

    async def probe(self) -> ProbeResult:
        return ProbeResult(
            True, "Fake provider — generates stub content, no model in use.", [self.model], 0.0
        )


def _seed_from(messages: Sequence[Message]) -> int:
    blob = "".join(m.content for m in messages).encode("utf-8", "ignore")
    return int.from_bytes(hashlib.sha256(blob).digest()[:4], "big")


def synthesize(schema: dict[str, Any], root: dict[str, Any], seed: int, depth: int = 0) -> Any:
    """Build a minimal instance satisfying ``schema``.

    Handles the subset of JSON Schema that Pydantic emits: $ref/$defs, enums, const,
    anyOf/oneOf, objects with required properties, arrays with minItems, and the scalar
    types. Unknown constructs degrade to ``None``.
    """
    if depth > 12:
        return None

    schema = _resolve(schema, root)

    if "const" in schema:
        return schema["const"]
    if enum := schema.get("enum"):
        return enum[seed % len(enum)]
    if "default" in schema and schema.get("type") not in ("object", "array"):
        return schema["default"]

    for key in ("anyOf", "oneOf"):
        if options := schema.get(key):
            # Prefer a non-null branch so required fields get real values.
            concrete = [o for o in options if _resolve(o, root).get("type") != "null"]
            chosen = (concrete or options)[0]
            return synthesize(chosen, root, seed, depth + 1)

    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0])

    if kind == "object" or "properties" in schema:
        props: dict[str, Any] = schema.get("properties", {})
        required = set(schema.get("required", []))
        # Include required fields plus a couple of optionals, so renderers see realistic input.
        keys = [k for k in props if k in required] + [k for k in props if k not in required][:3]
        return {
            key: synthesize(props[key], root, seed + i, depth + 1) for i, key in enumerate(keys)
        }

    if kind == "array":
        item_schema = schema.get("items") or {"type": "string"}
        count = max(int(schema.get("minItems", 0)), 2)
        count = min(count, int(schema.get("maxItems", count)))
        return [synthesize(item_schema, root, seed + i, depth + 1) for i in range(count)]

    if kind == "string":
        title = schema.get("title") or "text"
        return f"Sample {title.lower()} {seed % 97}"
    if kind == "integer":
        return int(schema.get("minimum", 1)) + (seed % 5)
    if kind == "number":
        return float(schema.get("minimum", 1)) + (seed % 5)
    if kind == "boolean":
        return bool(seed % 2)
    if kind == "null":
        return None

    return f"sample-{seed % 97}"


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``$ref`` into ``$defs``."""
    seen: set[str] = set()
    while (ref := schema.get("$ref")) and ref not in seen:
        seen.add(ref)
        target: Any = root
        for part in ref.lstrip("#/").split("/"):
            if not isinstance(target, dict):
                return schema
            target = target.get(part, {})
        if not isinstance(target, dict):
            return schema
        schema = target
    return schema
