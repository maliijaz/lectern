"""Provider-agnostic language model interface.

Everything above this layer — generators, the repair loop, the CLI — talks only to
:class:`LLMProvider`. Swapping Ollama for a hosted OpenAI-compatible endpoint is a config
change, not a code change.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def system(content: str) -> Message:
    return Message("system", content)


def user(content: str) -> Message:
    return Message("user", content)


def assistant(content: str) -> Message:
    return Message("assistant", content)


@dataclass(slots=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    #: True when the backend enforced the JSON schema itself rather than us prompting for it.
    schema_enforced: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(slots=True)
class ProbeResult:
    ok: bool
    message: str
    models: list[str] = field(default_factory=list)
    latency_ms: float | None = None


class LLMProvider(abc.ABC):
    """Base class for model backends."""

    name: str = "base"

    def __init__(self, *, base_url: str, model: str, api_key: str = "", **options: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature: float = options.get("temperature", 0.4)
        self.max_tokens: int = options.get("max_tokens", 4096)
        self.num_ctx: int = options.get("num_ctx", 8192)
        self.timeout: float = options.get("timeout", 600)
        # Accepted by every provider so the registry can pass one uniform kwarg set;
        # only the Ollama backend can act on it.
        self.auto_context: bool = bool(options.get("auto_context", True))
        self.thinking: bool = bool(options.get("thinking", False))

    @abc.abstractmethod
    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        stop: Sequence[str] | None = None,
    ) -> Completion:
        """One completion.

        When ``json_schema`` is given the provider should constrain decoding to it if the
        backend supports that, and set ``Completion.schema_enforced`` accordingly. Callers
        must still validate the result — see :mod:`app.llm.structured`.
        """

    @abc.abstractmethod
    async def stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas as they arrive."""

    @abc.abstractmethod
    async def list_models(self) -> list[str]:
        """Model names this backend can serve."""

    async def probe(self) -> ProbeResult:
        """Check reachability. Overridden by providers that can say something more useful."""
        import time

        started = time.perf_counter()
        try:
            models = await self.list_models()
        except Exception as exc:
            return ProbeResult(False, f"Could not reach {self.base_url}: {exc}")

        latency = (time.perf_counter() - started) * 1000
        if models and self.model not in models:
            return ProbeResult(
                False,
                f"Connected, but the model {self.model!r} is not available. "
                f"Found: {', '.join(models[:10]) or 'none'}",
                models,
                latency,
            )
        return ProbeResult(True, f"Connected to {self.name} — {self.model}", models, latency)

    async def close(self) -> None:
        """Release connections. Safe to call more than once.

        Not abstract: a provider with no connections to release (the stub) should not have
        to implement an empty method.
        """
        return None

    async def __aenter__(self) -> LLMProvider:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
