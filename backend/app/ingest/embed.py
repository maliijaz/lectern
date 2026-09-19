"""Local sentence embeddings.

The model is small (bge-small is ~130 MB), runs on CPU, and is loaded once and reused.
It is downloaded on first use; after that everything works offline.
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

_model: Any = None
_model_key: tuple[str, str] | None = None
_lock = threading.Lock()

#: bge/e5 models expect an instruction prefix on the *query* side only. Without it,
#: retrieval quality drops noticeably, and it costs nothing to add.
_QUERY_PREFIXES = {
    "bge": "Represent this sentence for searching relevant passages: ",
    "e5": "query: ",
}


def _query_prefix(model_name: str) -> str:
    lowered = model_name.lower()
    for marker, prefix in _QUERY_PREFIXES.items():
        if marker in lowered:
            return prefix
    return ""


def _passage_prefix(model_name: str) -> str:
    return "passage: " if "e5" in model_name.lower() else ""


def load_model(model_name: str, device: str = "auto") -> Any:
    """Load (and cache) the embedding model. Blocking — call via ``run_blocking``."""
    from app.core.hardware import resolve_embed_device

    global _model, _model_key
    device = resolve_embed_device(device)
    key = (model_name, device)
    with _lock:
        if _model is not None and _model_key == key:
            return _model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            from app.core.errors import DependencyMissing

            raise DependencyMissing(
                "Semantic search needs sentence-transformers, which is not installed.",
                install="pip install -e .[ingest]",
            ) from exc

        log.info("Loading embedding model %s on %s (first run downloads it)", model_name, device)
        _model = SentenceTransformer(model_name, device=device)
        _model_key = key
        return _model


def embed_passages(
    texts: list[str],
    *,
    model_name: str,
    device: str = "cpu",
    batch_size: int = 32,
) -> list[list[float]]:
    """Embed document passages for storage."""
    if not texts:
        return []
    model = load_model(model_name, device)
    prefix = _passage_prefix(model_name)
    prepared = [prefix + t for t in texts] if prefix else texts
    vectors = model.encode(
        prepared,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [v.tolist() for v in vectors]


def embed_query(query: str, *, model_name: str, device: str = "auto") -> list[float]:
    """Embed a search query, with the model's query instruction prefix applied."""
    model = load_model(model_name, device)
    vector = model.encode(
        _query_prefix(model_name) + query,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return vector.tolist()


def dimensions(model_name: str, device: str = "auto") -> int:
    return int(load_model(model_name, device).get_sentence_embedding_dimension())


def unload() -> None:
    """Release the model — used by tests and by the CLI after a one-shot run."""
    global _model, _model_key
    with _lock:
        _model = None
        _model_key = None
