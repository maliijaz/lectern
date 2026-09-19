"""What this machine can actually run on.

The single biggest determinant of whether this product is pleasant or painful to use is
whether the model fits entirely in GPU memory. Ollama silently splits a model across GPU
and CPU when it does not fit, and the result is not a warning — it is a generation that
takes several times as long and pins every core. On an 8 GB card, qwen3:8b fits at an 8K
context and spills at 12K, and nothing in the interface would otherwise tell a teacher so.

So the product measures it, sizes the context to fit, and says what it found.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.logging import get_logger

log = get_logger(__name__)

#: Headroom to leave free in VRAM. Compute buffers, the desktop and any other process need
#: room; sizing to the last megabyte means the model spills the moment anything else runs.
VRAM_HEADROOM_MB = 900

#: KV-cache and compute-buffer cost per 1K tokens of context, in MB, by model size in
#: billions of parameters.
#:
#: Measured rather than derived. Loading qwen3:8b on an 8 GB RTX 4060 and reading Ollama's
#: own reported footprint gave: 4K ctx -> 5.58 GB, 8K -> 6.19 GB, 12K -> 7.20 GB,
#: 16K -> 7.81 GB. That is a straight line at ~185 MB per 1K tokens, with weights
#: accounting for the remaining ~4.8 GB. Architectural maths under-predicts this by more
#: than half, because Ollama's compute buffers are not free. The other sizes are scaled
#: from that measurement; recalibrate the same way if they prove wrong.
_KV_MB_PER_1K = {3: 70.0, 8: 185.0, 14: 300.0, 32: 600.0}

#: Context sizes worth choosing between. Quality barely improves above 16K for this
#: workload, and every doubling costs real VRAM.
_CONTEXT_STEPS = (4096, 8192, 12288, 16384, 24576, 32768)

#: A 4-bit quantised model occupies roughly this many GB per billion parameters.
_GB_PER_BILLION = 0.6


@dataclass
class GPUInfo:
    name: str
    total_mb: int
    free_mb: int


@dataclass
class Hardware:
    gpus: list[GPUInfo] = field(default_factory=list)
    #: True when PyTorch can use CUDA — governs whether embeddings can run on the GPU.
    torch_cuda: bool = False
    torch_device: str = "cpu"

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpus)

    @property
    def primary(self) -> GPUInfo | None:
        return max(self.gpus, key=lambda g: g.total_mb) if self.gpus else None

    def summary(self) -> str:
        gpu = self.primary
        if gpu is None:
            return "No GPU detected — generation will run on the CPU."
        return f"{gpu.name} — {gpu.total_mb / 1024:.1f} GB VRAM, {gpu.free_mb / 1024:.1f} GB free"


def _detect_nvidia() -> list[GPUInfo]:
    """Read NVIDIA GPUs from nvidia-smi. Empty list when there are none."""
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        result = subprocess.run(  # noqa: S603 — fixed executable, no user input
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    gpus: list[GPUInfo] = []
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            gpus.append(GPUInfo(name=parts[0], total_mb=int(parts[1]), free_mb=int(parts[2])))
        except ValueError:
            continue
    return gpus


def _detect_torch() -> tuple[bool, str]:
    """Whether PyTorch sees an accelerator, without importing it when it is absent."""
    import importlib.util

    if importlib.util.find_spec("torch") is None:
        return False, "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return True, "cuda"
        # Apple Silicon: embeddings run well on MPS even though Ollama handles the LLM.
        backend = getattr(torch.backends, "mps", None)
        if backend is not None and backend.is_available():
            return False, "mps"
    except Exception:  # pragma: no cover — a broken torch install must not break startup
        log.debug("Could not query torch for accelerators", exc_info=True)
    return False, "cpu"


@lru_cache
def detect(refresh_token: int = 0) -> Hardware:
    """Inspect the machine. Cached; callers that need current free VRAM use `refresh`."""
    del refresh_token
    torch_cuda, torch_device = _detect_torch()
    hardware = Hardware(gpus=_detect_nvidia(), torch_cuda=torch_cuda, torch_device=torch_device)
    log.info("Hardware: %s (embeddings on %s)", hardware.summary(), hardware.torch_device)
    return hardware


def refresh() -> Hardware:
    """Re-measure. Free VRAM moves as models load and unload."""
    detect.cache_clear()
    return detect()


def resolve_embed_device(configured: str) -> str:
    """Turn an ``embed_device`` setting of "auto" into a real device.

    Embeddings are small and batched — the shape a GPU is good at — so indexing a long
    textbook goes from minutes to seconds on CUDA. But the language model usually holds
    most of the VRAM, so the GPU is only claimed when there is room left for it.
    """
    if configured and configured != "auto":
        return configured

    hardware = detect()
    if hardware.torch_cuda:
        gpu = hardware.primary
        # The embedding model is ~130 MB plus activations. If the LLM has already filled
        # the card, stay on the CPU rather than failing mid-ingest with an OOM.
        if gpu is None or gpu.free_mb > 700:
            return "cuda"
        log.info("GPU has only %d MB free; embedding on the CPU instead", gpu.free_mb)
        return "cpu"
    return hardware.torch_device


def model_size_gb(model_name: str, size_bytes: int = 0) -> float:
    """Best estimate of a model's on-disk size in GB.

    Uses the size the backend reports when there is one — it is the only reliable signal,
    since names mislead ("mistral:7b-instruct" is 4.4 GB, "qwen3:8b" is 5.2 GB). Falls
    back to reading a parameter count out of the name.
    """
    if size_bytes:
        return size_bytes / 1e9
    if match := re.search(r"(\d+(?:\.\d+)?)\s*[bB]\b", model_name):
        return float(match.group(1)) * _GB_PER_BILLION
    return 8 * _GB_PER_BILLION


def recommend_context(
    size_gb: float,
    *,
    hardware: Hardware | None = None,
    requested: int = 0,
    ceiling: int = 16384,
) -> tuple[int, str]:
    """The largest context that keeps the whole model on the GPU.

    Returns the size and a sentence explaining the choice, shown verbatim in the UI — a
    teacher deserves to know why the number is what it is.
    """
    hardware = hardware or detect()
    gpu = hardware.primary

    if gpu is None:
        chosen = min(requested or 8192, 8192)
        return chosen, (
            f"No GPU detected, so generation runs on the CPU. Context held to "
            f"{chosen:,} tokens to keep it from being unbearably slow."
        )

    budget_mb = gpu.total_mb - VRAM_HEADROOM_MB - size_gb * 1024
    if budget_mb <= 0:
        return 4096, (
            f"{gpu.name} has {gpu.total_mb / 1024:.1f} GB, not enough for a "
            f"{size_gb:.1f} GB model to fit on the GPU. It will run partly on the CPU and "
            "be slow — a smaller model would be much faster here."
        )

    billions = size_gb / _GB_PER_BILLION
    per_1k = _KV_MB_PER_1K[min(_KV_MB_PER_1K, key=lambda k: abs(k - billions))]
    affordable = int(budget_mb / per_1k * 1024)

    options = [c for c in _CONTEXT_STEPS if c <= min(affordable, ceiling)]
    chosen = max(options) if options else 4096

    if requested and requested <= chosen:
        return requested, f"{requested:,} tokens fits on {gpu.name} alongside the model."
    if requested:
        return chosen, (
            f"Reduced from {requested:,} to {chosen:,} tokens so the whole model stays on "
            f"{gpu.name}. Above this it spills onto the CPU, which is several times slower "
            "and pins every core."
        )
    return chosen, f"{chosen:,} tokens — the most that keeps this model entirely on {gpu.name}."


def report() -> dict:
    """Hardware as the Settings page and `ta status` present it."""
    hardware = refresh()
    gpu = hardware.primary
    return {
        "summary": hardware.summary(),
        "has_gpu": hardware.has_gpu,
        "gpu_name": gpu.name if gpu else "",
        "vram_total_mb": gpu.total_mb if gpu else 0,
        "vram_free_mb": gpu.free_mb if gpu else 0,
        "embeddings_device": resolve_embed_device("auto"),
        "torch_cuda": hardware.torch_cuda,
    }
