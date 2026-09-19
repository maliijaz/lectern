"""Any OpenAI-compatible ``/v1/chat/completions`` endpoint.

Covers llama.cpp's server, vLLM, LM Studio, text-generation-webui, LiteLLM proxies and
hosted providers. Structured output support varies, so this provider tries strict
``json_schema`` first and falls back to plain JSON mode when the server rejects it.
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


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: httpx.AsyncClient | None = None
        # Remembered per instance so we stop paying for a failed strict attempt every call.
        self._supports_strict_schema: bool | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            base = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
            self._client = httpx.AsyncClient(
                base_url=base,
                headers=headers,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            )
        return self._client

    def _payload(
        self,
        messages: Sequence[Message],
        temperature: float | None,
        max_tokens: int | None,
        stop: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if stop:
            payload["stop"] = list(stop)
        return payload

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        stop: Sequence[str] | None = None,
    ) -> Completion:
        payload = self._payload(messages, temperature, max_tokens, stop)
        strict = json_schema is not None and self._supports_strict_schema is not False

        if json_schema is not None:
            if strict:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": json_schema.get("title", "response"),
                        "schema": json_schema,
                        "strict": True,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        data = await self._post(payload)

        if data is None and strict:
            # Server rejected strict schema mode — remember that and retry in JSON mode.
            log.info("Endpoint rejected json_schema response_format; falling back to json_object")
            self._supports_strict_schema = False
            payload["response_format"] = {"type": "json_object"}
            data = await self._post(payload)

        if data is None:
            raise LLMError("The endpoint rejected the request in both strict and JSON mode.")
        if json_schema is not None and strict:
            self._supports_strict_schema = True

        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return Completion(
            text=(choice.get("message") or {}).get("content") or "",
            model=data.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            seconds=time.perf_counter() - started,
            schema_enforced=bool(json_schema is not None and strict),
        )

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST a completion. Returns None on 400/422, meaning "the payload was refused"."""
        try:
            response = await self._http().post("/chat/completions", json=payload)
        except httpx.ConnectError as exc:
            raise LLMUnavailable(f"Cannot reach the endpoint at {self.base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"The endpoint timed out after {self.timeout:.0f}s.") from exc

        if response.status_code in (400, 422):
            return None
        if response.status_code == 401:
            raise LLMError("Authentication failed — check the API key in Settings.")
        if response.status_code == 429:
            raise LLMError(
                "Rate limited by the endpoint. Wait a moment and try again.", transient=True
            )
        if response.status_code >= 400:
            raise LLMError(
                f"Endpoint returned {response.status_code}: {response.text[:500]}",
                transient=response.status_code >= 500,
            )
        return response.json()

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, temperature, max_tokens) | {"stream": True}
        try:
            async with self._http().stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise LLMError(f"Endpoint returned {response.status_code}: {body[:500]}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                    if piece := delta.get("content"):
                        yield piece
        except httpx.ConnectError as exc:
            raise LLMUnavailable(f"Cannot reach the endpoint at {self.base_url}.") from exc

    async def list_models(self) -> list[str]:
        response = await self._http().get("/models", timeout=10.0)
        response.raise_for_status()
        return sorted(m["id"] for m in response.json().get("data", []) if "id" in m)

    async def probe(self) -> ProbeResult:
        started = time.perf_counter()
        try:
            models = await self.list_models()
        except httpx.ConnectError:
            return ProbeResult(False, f"No server responding at {self.base_url}.")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                return ProbeResult(False, "Authentication failed — check the API key.")
            # Some servers do not implement /models; a completion is the real test.
            return await self._probe_by_completion(started)
        except Exception as exc:
            return ProbeResult(False, f"Could not reach the endpoint: {exc}")

        latency = (time.perf_counter() - started) * 1000
        if models and self.model not in models:
            return ProbeResult(
                False,
                f"Connected, but {self.model!r} is not offered. Available: {', '.join(models[:8])}",
                models,
                latency,
            )
        return ProbeResult(True, f"Connected — {self.model}", models, latency)

    async def _probe_by_completion(self, started: float) -> ProbeResult:
        try:
            await self.chat([Message("user", "ping")], max_tokens=1)
        except Exception as exc:
            return ProbeResult(False, f"Endpoint did not answer a test completion: {exc}")
        return ProbeResult(
            True,
            f"Connected — {self.model} (the server does not list models)",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
