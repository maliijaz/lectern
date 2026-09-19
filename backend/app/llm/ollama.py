"""Ollama backend — the default, and the reason this product runs with no account and no key.

Ollama accepts a JSON Schema in the ``format`` field and constrains decoding to it, which
is what makes reliable structured output possible on small local models.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.core.errors import LLMError, LLMUnavailable
from app.core.logging import get_logger
from app.llm.base import Completion, LLMProvider, Message, ProbeResult

log = get_logger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: httpx.AsyncClient | None = None
        #: Size the context to the GPU rather than trusting the configured ceiling.
        self.auto_context: bool = bool(kwargs.get("auto_context", True))
        self.thinking: bool = bool(kwargs.get("thinking", False))
        #: Resolved once per process, then reused — it needs an HTTP round trip.
        self._resolved_ctx: int | None = None
        self._ctx_reason: str = ""
        #: How many times the context has been stepped down after observing a spill.
        self._shrinks: int = 0

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            )
        return self._client

    def _options(self, temperature: float | None, max_tokens: int | None) -> dict[str, Any]:
        return {
            "temperature": self.temperature if temperature is None else temperature,
            "num_predict": self.max_tokens if max_tokens is None else max_tokens,
            "num_ctx": self._resolved_ctx or self.num_ctx,
        }

    async def effective_context(self) -> tuple[int, str]:
        """The context size actually used, and why.

        Ollama does not refuse a context that will not fit — it quietly moves layers onto
        the CPU, and generation becomes several times slower with every core pinned. So
        the size is chosen from the model's real footprint and the card's real VRAM.
        """
        if self._resolved_ctx is not None:
            return self._resolved_ctx, self._ctx_reason

        if not self.auto_context:
            self._resolved_ctx = self.num_ctx
            self._ctx_reason = f"{self.num_ctx:,} tokens, as configured (auto-sizing is off)."
            return self._resolved_ctx, self._ctx_reason

        from app.core.hardware import model_size_gb, recommend_context

        size_bytes = 0
        try:
            size_bytes = await self._model_size_bytes()
        except Exception:
            log.debug("Could not read the model size from Ollama", exc_info=True)

        chosen, reason = recommend_context(
            model_size_gb(self.model, size_bytes), requested=self.num_ctx, ceiling=self.num_ctx
        )
        self._resolved_ctx, self._ctx_reason = chosen, reason
        if chosen != self.num_ctx:
            log.info("Context sized to %d tokens: %s", chosen, reason)
        return chosen, reason

    async def _correct_context_if_it_spilled(self) -> None:
        """Shrink the context when Ollama actually put part of the model on the CPU.

        The size chosen up front is a prediction from VRAM and model size, and predictions
        are calibrated on one architecture. Granite 4.2, for instance, is a hybrid
        Mamba/transformer and needs materially more memory per token than a same-sized
        Qwen, so the estimate puts it over the line. Rather than leave the teacher with a
        warning and a slow generation, read where the model really landed and step the
        context down until it fits. Costs one model reload, once.
        """
        if not self.auto_context or self._shrinks >= 2 or self._resolved_ctx is None:
            return

        placement = await self.placement()
        if not placement.get("loaded") or placement.get("fully_on_gpu"):
            return

        from app.core.hardware import _CONTEXT_STEPS

        smaller = [c for c in _CONTEXT_STEPS if c < self._resolved_ctx]
        if not smaller:
            return

        previous = self._resolved_ctx
        self._resolved_ctx = max(smaller)
        self._shrinks += 1
        self._ctx_reason = (
            f"Reduced from {previous:,} to {self._resolved_ctx:,} tokens: at {previous:,} "
            f"Ollama had put {1 - placement['gpu_share']:.0%} of the model on the CPU. "
            "Measured after loading, not estimated."
        )
        log.info(
            "Model spilled at %d tokens (%.0f%% on GPU); retrying at %d",
            previous,
            placement["gpu_share"] * 100,
            self._resolved_ctx,
        )

    async def _model_size_bytes(self) -> int:
        """The on-disk size Ollama reports for the configured model."""
        response = await self._http().get("/api/tags", timeout=10.0)
        response.raise_for_status()
        for entry in response.json().get("models", []):
            if entry.get("name") in (self.model, f"{self.model}:latest"):
                return int(entry.get("size", 0))
        return 0

    async def placement(self) -> dict[str, Any]:
        """How the loaded model is split between GPU and CPU, from Ollama itself.

        This is the ground truth the recommendation is trying to predict, and it is what
        the Settings page shows after a generation — a prediction a teacher cannot check
        is not much use.
        """
        try:
            response = await self._http().get("/api/ps", timeout=10.0)
            response.raise_for_status()
        except Exception:
            return {"loaded": False}

        for entry in response.json().get("models", []):
            if not entry.get("name", "").startswith(self.model.split(":")[0]):
                continue
            total = int(entry.get("size", 0))
            vram = int(entry.get("size_vram", 0))
            if not total:
                continue
            share = vram / total
            return {
                "loaded": True,
                "model": entry.get("name", self.model),
                "total_gb": round(total / 1e9, 2),
                "vram_gb": round(vram / 1e9, 2),
                "gpu_share": round(share, 3),
                "fully_on_gpu": share > 0.995,
            }
        return {"loaded": False}

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        stop: Sequence[str] | None = None,
    ) -> Completion:
        await self.effective_context()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": False,
            "options": self._options(temperature, max_tokens),
        }
        if stop:
            payload["options"]["stop"] = list(stop)
        if json_schema is not None:
            payload["format"] = json_schema
        # Sent unconditionally: models without a thinking mode ignore the field.
        payload["think"] = self.thinking

        started = time.perf_counter()
        try:
            response = await self._http().post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise LLMUnavailable(
                f"Cannot reach Ollama at {self.base_url}. Is it running? "
                "Start it with `ollama serve`.",
                transient=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama timed out after {self.timeout:.0f}s. Try a smaller model, "
                "a shorter document, or raise LECTERN_LLM_TIMEOUT."
            ) from exc
        except httpx.RemoteProtocolError as exc:
            # Ollama's model runner dropped the connection mid-response — usually memory
            # pressure part-way through a long job. Worth retrying.
            raise LLMError(f"Ollama closed the connection: {exc}", transient=True) from exc

        if response.status_code == 404:
            raise LLMError(
                f"Ollama does not have the model {self.model!r}. Pull it with "
                f"`ollama pull {self.model}`."
            )
        if response.status_code >= 400:
            # A 500 from Ollama is nearly always its runner crashing rather than a bad
            # request; the same call usually succeeds once the model has reloaded.
            raise LLMError(
                f"Ollama returned {response.status_code}: {response.text[:500]}",
                transient=response.status_code >= 500,
            )

        data = response.json()
        await self._correct_context_if_it_spilled()
        return Completion(
            text=data.get("message", {}).get("content", ""),
            model=data.get("model", self.model),
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            seconds=time.perf_counter() - started,
            schema_enforced=json_schema is not None,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        await self.effective_context()
        payload = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": True,
            "options": self._options(temperature, max_tokens),
        }
        try:
            async with self._http().stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise LLMError(f"Ollama returned {response.status_code}: {body[:500]}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        return
        except httpx.ConnectError as exc:
            raise LLMUnavailable(f"Cannot reach Ollama at {self.base_url}.") from exc

    async def list_models(self) -> list[str]:
        response = await self._http().get("/api/tags", timeout=10.0)
        response.raise_for_status()
        return sorted(m["name"] for m in response.json().get("models", []))

    async def probe(self) -> ProbeResult:
        started = time.perf_counter()
        try:
            models = await self.list_models()
        except httpx.ConnectError:
            return ProbeResult(
                False,
                f"No Ollama server at {self.base_url}. Install it from ollama.com, "
                "then run `ollama serve`.",
            )
        except Exception as exc:
            return ProbeResult(False, f"Could not reach Ollama: {exc}")

        latency = (time.perf_counter() - started) * 1000
        if not models:
            return ProbeResult(
                False,
                f"Ollama is running but has no models. Pull one with `ollama pull {self.model}`.",
                latency_ms=latency,
            )
        # Ollama reports "qwen3:8b"; accept a bare "qwen3" as a match for the :latest tag.
        if self.model not in models and f"{self.model}:latest" not in models:
            return ProbeResult(
                False,
                f"Model {self.model!r} is not pulled. Run `ollama pull {self.model}`, "
                f"or pick one of: {', '.join(models[:8])}",
                models,
                latency,
            )
        context, reason = await self.effective_context()
        placement = await self.placement()

        parts = [f"Ollama ready — {self.model}", reason]
        if placement.get("loaded"):
            if placement["fully_on_gpu"]:
                parts.append(f"Running entirely on the GPU ({placement['vram_gb']} GB).")
            else:
                parts.append(
                    f"Warning: only {placement['gpu_share']:.0%} of the model is on the "
                    f"GPU; the rest runs on the CPU and will be slow."
                )
        del context
        return ProbeResult(True, " ".join(parts), models, latency)

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
