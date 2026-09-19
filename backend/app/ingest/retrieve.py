"""Retrieval: find the passages a generator should work from.

Two things matter more here than raw relevance:

* **Diversity.** A deck built from twelve near-identical passages about one paragraph is
  useless. MMR re-ranking trades a little relevance for coverage of the whole chapter.
* **Provenance.** Every hit is converted into a :class:`Citation`, so anything generated
  from it can point back at the page.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.core.logging import get_logger
from app.ingest import embed
from app.ingest.store import SearchHit, VectorStore, get_store
from app.schemas.common import Citation

log = get_logger(__name__)


@dataclass
class RetrievedContext:
    """Passages assembled for a prompt, with the citations that back them."""

    hits: list[SearchHit]
    citations: list[Citation]
    text: str
    truncated: bool = False

    def __bool__(self) -> bool:
        return bool(self.hits)


def search(
    query: str,
    *,
    settings: Settings,
    document_ids: list[str] | None = None,
    top_k: int | None = None,
    store: VectorStore | None = None,
    diversity: float = 0.4,
) -> list[SearchHit]:
    """Semantic search with MMR re-ranking.

    ``diversity`` of 0 is pure relevance; 1 is pure novelty. 0.3–0.5 works well for
    building teaching material from a chapter.
    """
    store = store or get_store(settings.vector_dir)
    k = top_k or settings.retrieve_top_k

    vector = embed.embed_query(query, model_name=settings.embed_model, device=settings.embed_device)

    where = _document_filter(document_ids)
    # Over-fetch so MMR has candidates to choose between.
    candidates = store.query(vector, top_k=max(k * 4, k + 10), where=where)
    if not candidates:
        return []
    return _mmr(candidates, k, diversity)


def _document_filter(document_ids: list[str] | None) -> dict | None:
    if not document_ids:
        return None
    if len(document_ids) == 1:
        return {"document_id": document_ids[0]}
    return {"document_id": {"$in": document_ids}}


def _mmr(hits: list[SearchHit], k: int, diversity: float) -> list[SearchHit]:
    """Maximal Marginal Relevance over the retrieved texts.

    Similarity between candidates is approximated by token overlap rather than by
    re-embedding: it is cheap, and at this scale it separates "same paragraph" from
    "different section" perfectly well.
    """
    if diversity <= 0 or len(hits) <= k:
        return hits[:k]

    tokens = [set(h.text.lower().split()) for h in hits]
    selected: list[int] = [0]
    remaining = set(range(1, len(hits)))

    while len(selected) < k and remaining:
        best_index, best_score = None, float("-inf")
        for index in remaining:
            overlap = max(_jaccard(tokens[index], tokens[s]) for s in selected)
            score = (1 - diversity) * hits[index].score - diversity * overlap
            if score > best_score:
                best_index, best_score = index, score
        if best_index is None:
            break
        selected.append(best_index)
        remaining.discard(best_index)

    return [hits[i] for i in selected]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return intersection / (len(a) + len(b) - intersection)


def build_context(
    hits: list[SearchHit],
    *,
    max_chars: int = 24_000,
    document_titles: dict[str, str] | None = None,
) -> RetrievedContext:
    """Format hits into a prompt block and the matching citation list.

    Each passage is numbered and labelled with its source so the model can cite it by
    reference — which is what makes grounded generation checkable.
    """
    titles = document_titles or {}
    parts: list[str] = []
    citations: list[Citation] = []
    used = 0
    truncated = False

    for index, hit in enumerate(hits, start=1):
        citation = Citation(
            document_id=hit.document_id,
            document_title=titles.get(hit.document_id, hit.metadata.get("document_title", "")),
            chunk_id=hit.chunk_id,
            page=hit.page,
            section=hit.section,
            quote=hit.text[:200],
        )
        header = f"[{index}] {citation.label()}"
        block = f"{header}\n{hit.text}"

        if used + len(block) > max_chars:
            truncated = True
            break

        parts.append(block)
        citations.append(citation)
        used += len(block)

    return RetrievedContext(
        hits=hits[: len(parts)],
        citations=citations,
        text="\n\n---\n\n".join(parts),
        truncated=truncated,
    )


def gather(
    queries: list[str],
    *,
    settings: Settings,
    document_ids: list[str] | None = None,
    per_query: int | None = None,
    max_chars: int = 24_000,
    document_titles: dict[str, str] | None = None,
    store: VectorStore | None = None,
) -> RetrievedContext:
    """Run several queries and merge the results, best score per chunk.

    Generators use this to cover a topic from a few angles at once — "definition of X",
    "examples of X", "common errors with X" — instead of a single vague query.
    """
    merged: dict[str, SearchHit] = {}
    for query in queries:
        for hit in search(
            query,
            settings=settings,
            document_ids=document_ids,
            top_k=per_query,
            store=store,
        ):
            existing = merged.get(hit.chunk_id)
            if existing is None or hit.score > existing.score:
                merged[hit.chunk_id] = hit

    ordered = sorted(merged.values(), key=lambda h: h.score, reverse=True)
    return build_context(ordered, max_chars=max_chars, document_titles=document_titles)
