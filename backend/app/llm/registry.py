"""Builds the configured provider.

Providers are cached per configuration signature so the HTTP connection pool is reused
across requests, and rebuilt automatically when the user changes settings in the UI.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm.base import LLMProvider
from app.llm.fake import FakeProvider
from app.llm.ollama import OllamaProvider
from app.llm.openai_compat import OpenAICompatProvider

log = get_logger(__name__)

PROVIDERS: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "openai_compat": OpenAICompatProvider,
    "fake": FakeProvider,
}

_cache: dict[tuple, LLMProvider] = {}
_lock = asyncio.Lock()


def _signature(settings: Settings) -> tuple:
    return (
        settings.llm_provider,
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_api_key,
        settings.llm_temperature,
        settings.llm_max_tokens,
        settings.llm_num_ctx,
        settings.llm_auto_context,
        settings.llm_thinking,
        settings.llm_timeout,
    )


def build_provider(settings: Settings | None = None, **overrides: Any) -> LLMProvider:
    """Get (or create) the provider for these settings."""
    settings = settings or get_settings()
    cls = PROVIDERS.get(settings.llm_provider)
    if cls is None:
        raise AppError(
            f"Unknown LLM provider {settings.llm_provider!r}. Choose one of: {', '.join(PROVIDERS)}"
        )

    signature = (*_signature(settings), tuple(sorted(overrides.items())))
    if (cached := _cache.get(signature)) is not None:
        return cached

    provider = cls(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        num_ctx=settings.llm_num_ctx,
        auto_context=settings.llm_auto_context,
        thinking=settings.llm_thinking,
        timeout=settings.llm_timeout,
        **overrides,
    )
    _cache[signature] = provider
    log.debug("Built %s provider for %s", settings.llm_provider, settings.llm_model)
    return provider


def build_for_model(settings: Settings, model: str) -> LLMProvider:
    """A provider for a specific model, otherwise configured identically.

    Used to give the answer-key verifier different weights from the generator, so that a
    mistake baked into one model's training is not confirmed by asking it twice.
    """
    if not model or model == settings.llm_model:
        return build_provider(settings)
    return build_provider(settings.model_copy(update={"llm_model": model}))


async def close_all() -> None:
    async with _lock:
        for provider in list(_cache.values()):
            try:
                await provider.close()
            except Exception:  # pragma: no cover
                log.debug("Error closing provider", exc_info=True)
        _cache.clear()


def register(name: str, cls: type[LLMProvider]) -> None:
    """Add a provider at runtime (used by tests and by anyone extending the product)."""
    PROVIDERS[name] = cls
