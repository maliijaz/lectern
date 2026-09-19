"""Hardware detection and GPU-aware context sizing.

The numbers asserted here are not guesses. They were measured by loading qwen3:8b on an
8 GB RTX 4060 at each context size and reading Ollama's own reported GPU/CPU split:

    4K  -> 5.58 GB, 100% GPU
    8K  -> 6.19 GB, 100% GPU
    12K -> 7.20 GB,  87% GPU  (spills)
    16K -> 7.81 GB,  80% GPU  (spills badly)

The recommender has to reproduce that boundary, because getting it wrong is the difference
between a generation that runs on the GPU and one that pins every core.
"""

from __future__ import annotations

import pytest

from app.core import hardware
from app.core.hardware import GPUInfo, Hardware

# An 8 GB card, as reported by nvidia-smi.
RTX_4060 = Hardware(gpus=[GPUInfo(name="NVIDIA GeForce RTX 4060", total_mb=8188, free_mb=7551)])
# A 24 GB card, where nothing needs reducing.
RTX_3090 = Hardware(gpus=[GPUInfo(name="NVIDIA GeForce RTX 3090", total_mb=24576, free_mb=24000)])
NO_GPU = Hardware(gpus=[])

QWEN3_8B_GB = 5.2


# --------------------------------------------------------------------------- sizing


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(4096, 4096), (8192, 8192), (12288, 8192), (16384, 8192)],
)
def test_context_matches_the_measured_gpu_boundary(requested: int, expected: int) -> None:
    chosen, _ = hardware.recommend_context(
        QWEN3_8B_GB, hardware=RTX_4060, requested=requested, ceiling=requested
    )
    assert chosen == expected


def test_a_large_card_does_not_reduce_anything() -> None:
    chosen, reason = hardware.recommend_context(
        QWEN3_8B_GB, hardware=RTX_3090, requested=16384, ceiling=16384
    )
    assert chosen == 16384
    assert "Reduced" not in reason


def test_reduction_explains_itself_in_plain_language() -> None:
    _, reason = hardware.recommend_context(
        QWEN3_8B_GB, hardware=RTX_4060, requested=16384, ceiling=16384
    )
    # A teacher must be able to tell what happened and why from this sentence alone.
    assert "16,384" in reason and "8,192" in reason
    assert "RTX 4060" in reason
    assert "CPU" in reason


def test_model_too_large_for_the_card_says_so() -> None:
    chosen, reason = hardware.recommend_context(14.0, hardware=RTX_4060, requested=8192)
    assert chosen == 4096
    assert "not enough" in reason
    assert "smaller model" in reason


def test_without_a_gpu_the_context_is_held_down() -> None:
    chosen, reason = hardware.recommend_context(QWEN3_8B_GB, hardware=NO_GPU, requested=32768)
    assert chosen <= 8192
    assert "No GPU" in reason


def test_ceiling_is_never_exceeded() -> None:
    """Auto-sizing may only reduce the configured value, never raise it."""
    chosen, _ = hardware.recommend_context(1.0, hardware=RTX_3090, requested=4096, ceiling=4096)
    assert chosen == 4096


# --------------------------------------------------------------------------- model size


def test_size_comes_from_the_reported_bytes_when_available() -> None:
    # Names mislead: "mistral:7b-instruct" is 4.4 GB, not 7 × 0.6.
    assert hardware.model_size_gb("mistral:7b-instruct", 4_400_000_000) == pytest.approx(4.4)


def test_size_falls_back_to_parsing_the_name() -> None:
    assert hardware.model_size_gb("qwen3:8b") == pytest.approx(4.8)
    assert hardware.model_size_gb("gemma3:4b") == pytest.approx(2.4)


def test_unparseable_name_gets_a_sane_default() -> None:
    assert 3.0 <= hardware.model_size_gb("some-custom-model") <= 6.0


# --------------------------------------------------------------------------- devices


def test_explicit_device_is_respected(monkeypatch) -> None:
    """A teacher who pins the device must not be overridden by detection."""
    monkeypatch.setattr(hardware, "detect", lambda *_a, **_k: RTX_4060)
    assert hardware.resolve_embed_device("cpu") == "cpu"
    assert hardware.resolve_embed_device("cuda") == "cuda"


def test_auto_uses_cuda_when_torch_sees_a_gpu_with_room(monkeypatch) -> None:
    ready = Hardware(gpus=[GPUInfo("RTX 4060", 8188, 7551)], torch_cuda=True, torch_device="cuda")
    monkeypatch.setattr(hardware, "detect", lambda *_a, **_k: ready)
    assert hardware.resolve_embed_device("auto") == "cuda"


def test_auto_stays_on_cpu_when_the_gpu_is_full(monkeypatch) -> None:
    """The language model usually owns the VRAM; embedding must not OOM mid-ingest."""
    busy = Hardware(gpus=[GPUInfo("RTX 4060", 8188, 300)], torch_cuda=True, torch_device="cuda")
    monkeypatch.setattr(hardware, "detect", lambda *_a, **_k: busy)
    assert hardware.resolve_embed_device("auto") == "cpu"


def test_auto_without_cuda_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "detect", lambda *_a, **_k: NO_GPU)
    assert hardware.resolve_embed_device("auto") == "cpu"


# --------------------------------------------------------------------------- reporting


def test_summary_is_readable_in_both_cases() -> None:
    assert "RTX 4060" in RTX_4060.summary()
    assert "GB" in RTX_4060.summary()
    assert "No GPU" in NO_GPU.summary()


def test_report_has_the_keys_the_ui_reads() -> None:
    report = hardware.report()
    assert {
        "summary",
        "has_gpu",
        "gpu_name",
        "vram_total_mb",
        "vram_free_mb",
        "embeddings_device",
        "torch_cuda",
    } <= set(report)


def test_detection_never_raises_without_nvidia_smi(monkeypatch) -> None:
    """A machine with no NVIDIA tooling must still start."""
    monkeypatch.setattr(hardware.shutil, "which", lambda _name: None)
    hardware.detect.cache_clear()
    result = hardware.detect()
    assert result.gpus == []
    hardware.detect.cache_clear()
