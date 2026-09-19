"""Ingestion: parsing, chunking, retrieval and the upload endpoint.

Runs without Docling or sentence-transformers — Markdown parses natively and the vector
store is swapped for the in-memory one with a deterministic stub embedder.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.ingest import chunk as chunking
from app.ingest import parse

SAMPLE = """# Photosynthesis

Plants convert light energy into chemical energy.

## Light-dependent reactions

These occur in the thylakoid membrane. Water is split, releasing oxygen.
Chlorophyll absorbs photons and excites electrons.

## The Calvin cycle

Carbon dioxide is fixed into glucose in the stroma. This stage does not
require light directly, though it depends on the products of the light
reactions.

### Rubisco

Rubisco is the enzyme that fixes carbon dioxide. It is the most abundant
protein on Earth.
"""


# --------------------------------------------------------------------------- parsing


def test_markdown_parses_without_docling(tmp_path: Path) -> None:
    source = tmp_path / "chapter.md"
    source.write_text(SAMPLE, encoding="utf-8")

    parsed = parse.parse(source)
    assert parsed.parser == "plaintext"
    assert parsed.title == "Photosynthesis"
    assert parsed.word_count > 50


def test_csv_becomes_a_markdown_table(tmp_path: Path) -> None:
    source = tmp_path / "marks.csv"
    source.write_text("Name,Score\nAsha,91\nBilal,78\n", encoding="utf-8")

    parsed = parse.parse(source)
    assert "| Name | Score |" in parsed.markdown
    assert "| Asha | 91 |" in parsed.markdown


def test_unsupported_extension_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "notes.xyz"
    source.write_text("x", encoding="utf-8")
    from app.core.errors import UnsupportedFormat

    with pytest.raises(UnsupportedFormat):
        parse.parse(source)


# --------------------------------------------------------------------------- chunking


def test_chunks_carry_their_heading_path() -> None:
    chunks = chunking.chunk_markdown(SAMPLE, target_tokens=40, overlap_tokens=5, min_tokens=5)
    assert chunks

    paths = [c.section_path for c in chunks]
    assert any("Photosynthesis › The Calvin cycle" in p for p in paths)
    assert any("Rubisco" in p for p in paths)

    for c in chunks:
        assert c.text.strip()
        assert c.token_count > 0


def test_chunk_context_prefixes_the_section() -> None:
    chunks = chunking.chunk_markdown(SAMPLE, target_tokens=40, min_tokens=5)
    withctx = next(c for c in chunks if c.section_path)
    assert withctx.with_context().startswith(withctx.section_path)


def test_oversized_paragraph_is_split_on_sentences() -> None:
    long_text = "# T\n\n" + " ".join(f"Sentence number {i} about cells." for i in range(400))
    chunks = chunking.chunk_markdown(long_text, target_tokens=100, overlap_tokens=10)
    assert len(chunks) > 1
    assert all(c.token_count <= 160 for c in chunks)


def test_code_fences_are_not_treated_as_headings() -> None:
    text = "# Title\n\n```python\n# this is a comment, not a heading\nx = 1\n```\n\nAfter.\n"
    chunks = chunking.chunk_markdown(text, target_tokens=200, min_tokens=1)
    assert all(c.heading in ("Title", "") for c in chunks)


def test_empty_document_yields_no_chunks() -> None:
    assert chunking.chunk_markdown("") == []


# --------------------------------------------------------------------------- retrieval


class StubEmbedder:
    """Bag-of-words vectors over a fixed vocabulary — deterministic and dependency-free."""

    VOCAB = [
        "light",
        "calvin",
        "rubisco",
        "carbon",
        "oxygen",
        "glucose",
        "enzyme",
        "chlorophyll",
        "water",
        "stroma",
        "cycle",
        "energy",
    ]

    @classmethod
    def encode(cls, text: str) -> list[float]:
        lowered = text.lower()
        vector = [float(lowered.count(term)) for term in cls.VOCAB]
        norm = sum(v * v for v in vector) ** 0.5
        return [v / norm for v in vector] if norm else [0.0] * len(cls.VOCAB)


@pytest.fixture
def stub_index(monkeypatch):
    """An in-memory vector store populated from SAMPLE with stub embeddings."""
    from app.ingest import embed
    from app.ingest.store import InMemoryVectorStore

    monkeypatch.setattr(embed, "embed_query", lambda query, **_: StubEmbedder.encode(query))
    monkeypatch.setattr(
        embed, "embed_passages", lambda texts, **_: [StubEmbedder.encode(t) for t in texts]
    )

    store = InMemoryVectorStore()
    chunks = chunking.chunk_markdown(SAMPLE, target_tokens=60, min_tokens=5)
    store.add(
        ids=[f"c{c.ordinal}" for c in chunks],
        embeddings=[StubEmbedder.encode(c.with_context()) for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[
            {
                "document_id": "doc-1",
                "document_title": "Biology Chapter 4",
                "section_path": c.section_path,
                "heading": c.heading,
                "page_from": 12,
            }
            for c in chunks
        ],
    )
    return store


def test_search_finds_the_relevant_section(stub_index) -> None:
    from app.config import get_settings
    from app.ingest import retrieve

    hits = retrieve.search(
        "Which enzyme fixes carbon dioxide?",
        settings=get_settings(),
        top_k=3,
        store=stub_index,
    )
    assert hits
    assert "Rubisco" in " ".join(h.text for h in hits)


def test_build_context_produces_numbered_passages_and_citations(stub_index) -> None:
    from app.config import get_settings
    from app.ingest import retrieve

    hits = retrieve.search("carbon dioxide", settings=get_settings(), top_k=3, store=stub_index)
    context = retrieve.build_context(hits)

    assert context.text.startswith("[1] ")
    assert len(context.citations) == len(context.hits)
    first = context.citations[0]
    assert first.document_title == "Biology Chapter 4"
    assert first.page == 12
    assert "Biology Chapter 4" in first.label()


def test_context_respects_the_character_budget(stub_index) -> None:
    from app.config import get_settings
    from app.ingest import retrieve

    hits = retrieve.search("light", settings=get_settings(), top_k=5, store=stub_index)
    context = retrieve.build_context(hits, max_chars=120)
    assert context.truncated
    assert len(context.text) <= 200


def test_gather_merges_multiple_queries(stub_index) -> None:
    from app.config import get_settings
    from app.ingest import retrieve

    context = retrieve.gather(
        ["light reactions", "Calvin cycle", "rubisco enzyme"],
        settings=get_settings(),
        per_query=2,
        store=stub_index,
    )
    assert len(context.citations) >= 2
    # Merging must not duplicate a chunk that several queries both matched.
    assert len({c.chunk_id for c in context.citations}) == len(context.citations)


# --------------------------------------------------------------------------- upload API


def test_upload_parses_and_indexes_a_markdown_file(client, monkeypatch) -> None:
    from app.ingest import embed
    from app.ingest import store as store_module
    from app.ingest.store import InMemoryVectorStore

    monkeypatch.setattr(
        embed, "embed_passages", lambda texts, **_: [StubEmbedder.encode(t) for t in texts]
    )
    store_module.set_store(InMemoryVectorStore())

    response = client.post(
        "/api/v1/documents",
        files={"file": ("chapter.md", SAMPLE.encode(), "text/markdown")},
        data={"subject": "Biology", "grade_level": "Grade 10"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    document_id = body["document"]["id"]
    assert body["job_id"]

    deadline = time.time() + 30
    while time.time() < deadline:
        document = client.get(f"/api/v1/documents/{document_id}").json()
        if document["status"] in ("ready", "failed"):
            break
        time.sleep(0.1)

    assert document["status"] == "ready", document["error"]
    assert document["chunk_count"] > 0
    assert document["word_count"] > 50
    assert document["subject"] == "Biology"

    chunks = client.get(f"/api/v1/documents/{document_id}/chunks").json()
    assert chunks and chunks[0]["section_path"]

    markdown = client.get(f"/api/v1/documents/{document_id}/markdown").json()
    assert "Calvin cycle" in markdown["markdown"]

    store_module.set_store(None)


def test_duplicate_upload_is_deduplicated(client, monkeypatch) -> None:
    from app.ingest import embed
    from app.ingest import store as store_module
    from app.ingest.store import InMemoryVectorStore

    monkeypatch.setattr(
        embed, "embed_passages", lambda texts, **_: [StubEmbedder.encode(t) for t in texts]
    )
    store_module.set_store(InMemoryVectorStore())

    payload = {"file": ("dup.md", b"# Same content\n\nIdentical bytes.", "text/markdown")}
    first = client.post("/api/v1/documents", files=payload).json()
    second = client.post(
        "/api/v1/documents",
        files={"file": ("other-name.md", b"# Same content\n\nIdentical bytes.", "text/markdown")},
    ).json()

    assert second["duplicate_of"] == first["document"]["id"]
    assert second["job_id"] is None

    store_module.set_store(None)


def test_upload_rejects_an_unsupported_type(client) -> None:
    response = client.post(
        "/api/v1/documents", files={"file": ("virus.exe", b"MZ", "application/octet-stream")}
    )
    assert response.status_code == 415
