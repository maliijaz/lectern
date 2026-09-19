"""Vector storage behind a narrow protocol.

ChromaDB is the default because it is embedded, needs no server and installs in one line.
The :class:`VectorStore` protocol is deliberately small so swapping in LanceDB, Qdrant or
pgvector later is a single new file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core.logging import get_logger

log = get_logger(__name__)

COLLECTION = "documents"


@dataclass
class SearchHit:
    chunk_id: str
    document_id: str
    text: str
    score: float
    metadata: dict[str, Any]

    @property
    def page(self) -> int | None:
        value = self.metadata.get("page_from")
        return int(value) if isinstance(value, int | float) else None

    @property
    def section(self) -> str:
        return str(self.metadata.get("section_path") or self.metadata.get("heading") or "")


class VectorStore(Protocol):
    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None: ...

    def query(
        self,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[SearchHit]: ...

    def delete_document(self, document_id: str) -> None: ...

    def count(self) -> int: ...


class ChromaVectorStore:
    """Embedded Chroma collection. One collection holds every document, filtered by
    ``document_id`` metadata — which makes cross-document generation a single query."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._collection: Any = None

    def _get(self) -> Any:
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError as exc:
            from app.core.errors import DependencyMissing

            raise DependencyMissing(
                "Semantic search needs ChromaDB, which is not installed.",
                install="pip install -e .[ingest]",
            ) from exc

        client = chromadb.PersistentClient(
            path=str(self.directory),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        # Cosine matches the normalised embeddings produced in app.ingest.embed.
        self._collection = client.get_or_create_collection(
            name=COLLECTION, metadata={"hnsw:space": "cosine"}
        )
        return self._collection

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not ids:
            return
        # Chroma rejects None values in metadata, so drop empty keys rather than fail.
        cleaned = [{k: v for k, v in m.items() if v is not None} for m in metadatas]
        self._get().upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=cleaned)

    def query(
        self,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        result = self._get().query(
            query_embeddings=[embedding],
            n_results=max(1, top_k),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        return _hits_from_chroma(result)

    def delete_document(self, document_id: str) -> None:
        self._get().delete(where={"document_id": document_id})

    def count(self) -> int:
        return int(self._get().count())

    def reset(self) -> None:
        collection = self._get()
        existing = collection.get(include=[])
        if ids := existing.get("ids"):
            collection.delete(ids=ids)


def _hits_from_chroma(result: dict[str, Any]) -> list[SearchHit]:
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    hits: list[SearchHit] = []
    for index, chunk_id in enumerate(ids):
        metadata = metadatas[index] if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else 1.0
        hits.append(
            SearchHit(
                chunk_id=chunk_id,
                document_id=str((metadata or {}).get("document_id", "")),
                text=documents[index] if index < len(documents) else "",
                # Chroma returns cosine *distance*; turn it back into a similarity.
                score=1.0 - float(distance),
                metadata=metadata or {},
            )
        )
    return hits


class InMemoryVectorStore:
    """Dependency-free store used by the test suite and by `--no-index` CLI runs."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[list[float], str, dict[str, Any]]] = {}

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for chunk_id, vector, text, metadata in zip(
            ids, embeddings, documents, metadatas, strict=True
        ):
            self._rows[chunk_id] = (vector, text, metadata)

    def query(
        self,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        hits = []
        for chunk_id, (vector, text, metadata) in self._rows.items():
            if where and any(metadata.get(k) != v for k, v in where.items()):
                continue
            hits.append(
                SearchHit(
                    chunk_id=chunk_id,
                    document_id=str(metadata.get("document_id", "")),
                    text=text,
                    score=_cosine(embedding, vector),
                    metadata=metadata,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    def delete_document(self, document_id: str) -> None:
        self._rows = {k: v for k, v in self._rows.items() if v[2].get("document_id") != document_id}

    def count(self) -> int:
        return len(self._rows)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


_store: VectorStore | None = None


def get_store(directory: Path | None = None) -> VectorStore:
    """The process-wide store. Chroma unless overridden for tests."""
    global _store
    if _store is None:
        from app.config import get_settings

        _store = ChromaVectorStore(directory or get_settings().vector_dir)
    return _store


def set_store(store: VectorStore | None) -> None:
    global _store
    _store = store
