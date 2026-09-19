"""The Ollama backend's GPU-aware behaviour.

These exercise the request Ollama actually receives, because the two settings that matter
most for speed — the context size and whether the model reasons out loud — are invisible
in the response. Getting either wrong costs a teacher minutes per generation and nothing
in the output would show it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.hardware import GPUInfo, Hardware
from app.llm.base import user
from app.llm.ollama import OllamaProvider

RTX_4060 = Hardware(gpus=[GPUInfo("NVIDIA GeForce RTX 4060", total_mb=8188, free_mb=7551)])


def _transport(handler) -> httpx.MockTransport:  # noqa: ANN001
    return httpx.MockTransport(handler)


def _provider(handler, **kwargs) -> OllamaProvider:  # noqa: ANN001
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen3:8b", **kwargs)
    provider._client = httpx.AsyncClient(
        base_url="http://localhost:11434", transport=_transport(handler)
    )
    return provider


def _make_handler(captured: list[dict], *, model_bytes: int = 5_200_000_000):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "size": model_bytes}]})
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:8b", "size": 6_190_000_000, "size_vram": 6_190_000_000}
                    ]
                },
            )
        if request.url.path == "/api/chat":
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": "{}"}, "model": "qwen3:8b"})
        return httpx.Response(404)

    return handler


# --------------------------------------------------------------------------- context


async def test_context_is_reduced_to_fit_the_card(monkeypatch) -> None:
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    captured: list[dict] = []
    provider = _provider(_make_handler(captured), num_ctx=16384, auto_context=True)
    await provider.chat([user("hi")])

    # 16K would put a fifth of this model on the CPU; 8K keeps all of it on the GPU.
    assert captured[0]["options"]["num_ctx"] == 8192


async def test_auto_sizing_can_be_turned_off(monkeypatch) -> None:
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    captured: list[dict] = []
    provider = _provider(_make_handler(captured), num_ctx=16384, auto_context=False)
    await provider.chat([user("hi")])

    assert captured[0]["options"]["num_ctx"] == 16384
    _, reason = await provider.effective_context()
    assert "auto-sizing is off" in reason


async def test_the_context_is_resolved_once_not_per_call(monkeypatch) -> None:
    """Resolving needs an HTTP round trip; doing it every call would be wasteful."""
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    tag_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tag_calls
        if request.url.path == "/api/tags":
            tag_calls += 1
            return httpx.Response(
                200, json={"models": [{"name": "qwen3:8b", "size": 5_200_000_000}]}
            )
        return httpx.Response(200, json={"message": {"content": "{}"}, "model": "qwen3:8b"})

    provider = _provider(handler, num_ctx=16384)
    for _ in range(3):
        await provider.chat([user("hi")])

    assert tag_calls == 1


async def test_a_backend_that_cannot_report_size_still_works(monkeypatch) -> None:
    """The size lookup is best-effort; failing it must not fail the generation."""
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"message": {"content": "{}"}, "model": "qwen3:8b"})

    provider = _provider(handler, num_ctx=16384)
    result = await provider.chat([user("hi")])
    assert result.model == "qwen3:8b"


# --------------------------------------------------------------------------- thinking


@pytest.mark.parametrize("thinking", [True, False])
async def test_thinking_is_sent_explicitly(monkeypatch, thinking: bool) -> None:
    """Measured at 47.5s with and 19.9s without, so it must not be left to the default."""
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    captured: list[dict] = []
    provider = _provider(_make_handler(captured), thinking=thinking)
    await provider.chat([user("hi")])

    assert captured[0]["think"] is thinking


async def test_thinking_defaults_to_off(monkeypatch) -> None:
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)
    captured: list[dict] = []
    provider = _provider(_make_handler(captured))
    await provider.chat([user("hi")])
    assert captured[0]["think"] is False


# --------------------------------------------------------------------------- placement


async def test_placement_reports_a_fully_resident_model(monkeypatch) -> None:
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)
    provider = _provider(_make_handler([]))

    placement = await provider.placement()
    assert placement["loaded"] is True
    assert placement["fully_on_gpu"] is True
    assert placement["gpu_share"] == pytest.approx(1.0)


async def test_placement_flags_a_model_that_spilled() -> None:
    """The failure this whole feature exists to make visible."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:8b", "size": 7_810_000_000, "size_vram": 6_290_000_000}
                    ]
                },
            )
        return httpx.Response(404)

    placement = await _provider(handler).placement()
    assert placement["fully_on_gpu"] is False
    assert placement["gpu_share"] == pytest.approx(0.805, abs=0.01)


async def test_placement_is_quiet_when_nothing_is_loaded() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json={"models": []}))
    assert await provider.placement() == {"loaded": False}


async def test_placement_survives_an_unreachable_backend() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await _provider(handler).placement() == {"loaded": False}


# --------------------------------------------------------------------------- errors


async def test_a_server_error_is_marked_transient() -> None:
    """The exact failure that lost forty minutes of a real run."""
    from app.core.errors import LLMError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "qwen3:8b", "size": 5_200_000_000}]}
            )
        return httpx.Response(500, text='{"error":"model runner has unexpectedly stopped"}')

    with pytest.raises(LLMError) as caught:
        await _provider(handler).chat([user("hi")])
    assert caught.value.transient is True


async def test_a_missing_model_is_not_transient() -> None:
    from app.core.errors import LLMError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404, text="not found")

    with pytest.raises(LLMError) as caught:
        await _provider(handler).chat([user("hi")])
    assert caught.value.transient is False
    assert "ollama pull" in caught.value.message


# --------------------------------------------------------------------------- self-correction


def _spilling_handler(captured: list[dict], gpu_share: float = 0.92):
    """Ollama reports the model partly on the CPU, whatever context we asked for."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "qwen3:8b", "size": 5_300_000_000}]}
            )
        if request.url.path == "/api/ps":
            total = 7_000_000_000
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3:8b",
                            "size": total,
                            "size_vram": int(total * gpu_share),
                        }
                    ]
                },
            )
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "{}"}, "model": "qwen3:8b"})

    return handler


async def test_a_spilling_model_shrinks_its_context(monkeypatch) -> None:
    """Estimating is not enough.

    The up-front estimate is calibrated on one architecture; a hybrid Mamba model of the
    same nominal size needs more memory per token and lands partly on the CPU. Reading the
    real placement and stepping down fixes it instead of merely warning about it.
    """
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    captured: list[dict] = []
    provider = _provider(_spilling_handler(captured), num_ctx=16384, auto_context=True)

    await provider.chat([user("one")])
    first = captured[0]["options"]["num_ctx"]

    await provider.chat([user("two")])
    second = captured[1]["options"]["num_ctx"]

    assert second < first, "it must use a smaller context after observing the spill"
    _, reason = await provider.effective_context()
    assert "on the CPU" in reason
    assert "Measured after loading" in reason


async def test_shrinking_stops_rather_than_spiralling(monkeypatch) -> None:
    """A model that will never fit must not shrink to nothing."""
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    captured: list[dict] = []
    provider = _provider(_spilling_handler(captured, gpu_share=0.5), num_ctx=16384)

    for _ in range(5):
        await provider.chat([user("hi")])

    sizes = [c["options"]["num_ctx"] for c in captured]
    assert len(set(sizes)) <= 3, f"stepped down too many times: {sizes}"
    assert min(sizes) >= 4096, "must not shrink below a usable context"


async def test_a_model_that_fits_is_left_alone(monkeypatch) -> None:
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    captured: list[dict] = []
    provider = _provider(_make_handler(captured), num_ctx=16384)

    await provider.chat([user("one")])
    await provider.chat([user("two")])

    sizes = {c["options"]["num_ctx"] for c in captured}
    assert sizes == {8192}, "a fully resident model must not be disturbed"


async def test_self_correction_respects_manual_sizing(monkeypatch) -> None:
    """If the teacher turned auto-sizing off, their number stands."""
    monkeypatch.setattr("app.core.hardware.detect", lambda *_a, **_k: RTX_4060)

    captured: list[dict] = []
    provider = _provider(_spilling_handler(captured), num_ctx=16384, auto_context=False)

    await provider.chat([user("one")])
    await provider.chat([user("two")])

    assert {c["options"]["num_ctx"] for c in captured} == {16384}
