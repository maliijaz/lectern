"""Split parsed Markdown into retrievable passages.

Chunking is structure-aware rather than a blind sliding window: sections are the natural
unit of a textbook chapter, and keeping the heading path on every chunk is what lets a
generated exam question cite "Chapter 4 → Photosynthesis → Light reactions" instead of
"somewhere in the PDF".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Rough token estimate. Good enough for sizing chunks, and avoids pulling in a tokenizer
#: (and its model download) just to decide where to cut.
CHARS_PER_TOKEN = 4

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_CODE_FENCE_RE = re.compile(r"^```")
_PAGE_MARKER_RE = re.compile(r"<!--\s*page[:\s]*(\d+)\s*-->", re.IGNORECASE)


@dataclass
class Chunk:
    ordinal: int
    text: str
    heading: str = ""
    section_path: str = ""
    page_from: int | None = None
    page_to: int | None = None
    token_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.token_count:
            self.token_count = estimate_tokens(self.text)

    def with_context(self) -> str:
        """The text as it is embedded and shown to the model — prefixed with its heading
        path, so a passage retrieved out of context still says what it is about."""
        return f"{self.section_path}\n\n{self.text}" if self.section_path else self.text


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


@dataclass
class _Block:
    """A run of markdown under one heading."""

    lines: list[str] = field(default_factory=list)
    heading: str = ""
    path: list[str] = field(default_factory=list)
    page: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _split_into_blocks(markdown: str) -> list[_Block]:
    """Walk the document, tracking the heading stack and any page markers."""
    blocks: list[_Block] = []
    stack: list[tuple[int, str]] = []
    current = _Block()
    in_fence = False
    page: int | None = None

    for line in markdown.splitlines():
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            current.lines.append(line)
            continue

        if not in_fence:
            if marker := _PAGE_MARKER_RE.search(line):
                page = int(marker.group(1))
                if current.page is None:
                    current.page = page
                continue

            if heading := _HEADING_RE.match(line):
                if current.text:
                    blocks.append(current)
                level, title = len(heading.group(1)), heading.group(2)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                current = _Block(heading=title, path=[t for _, t in stack], page=page)
                continue

        current.lines.append(line)

    if current.text:
        blocks.append(current)
    return blocks


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Break an oversized block on paragraph boundaries, with overlap for continuity."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buffer = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            # A single paragraph larger than the budget (a long table or a wall of OCR
            # text): fall back to sentence boundaries.
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.extend(_split_on_sentences(paragraph, max_chars))
            continue

        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            pieces.append(buffer)
            tail = buffer[-overlap_chars:] if overlap_chars else ""
            buffer = f"{tail}\n\n{paragraph}".strip() if tail else paragraph

    if buffer.strip():
        pieces.append(buffer)
    return pieces


def _split_on_sentences(text: str, max_chars: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(buffer) + len(sentence) + 1 <= max_chars:
            buffer = f"{buffer} {sentence}".strip()
        else:
            if buffer:
                pieces.append(buffer)
            # A sentence longer than the budget only happens in broken OCR; hard-cut it.
            while len(sentence) > max_chars:
                pieces.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            buffer = sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_markdown(
    markdown: str,
    *,
    target_tokens: int = 512,
    overlap_tokens: int = 64,
    min_tokens: int = 40,
) -> list[Chunk]:
    """Chunk a Markdown document, preserving heading context.

    Small adjacent sections are merged so a chapter of one-line headings does not produce
    a hundred useless chunks.
    """
    max_chars = target_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    min_chars = min_tokens * CHARS_PER_TOKEN

    blocks = _split_into_blocks(markdown)
    if not blocks:
        return []

    merged = _merge_small_blocks(blocks, min_chars, max_chars)

    chunks: list[Chunk] = []
    for block in merged:
        text = block.text
        if not text:
            continue
        path = " › ".join(block.path)
        for piece in _split_long_text(text, max_chars, overlap_chars):
            if not piece.strip():
                continue
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=piece.strip(),
                    heading=block.heading,
                    section_path=path,
                    page_from=block.page,
                    page_to=block.page,
                )
            )
    return chunks


def _merge_small_blocks(blocks: list[_Block], min_chars: int, max_chars: int) -> list[_Block]:
    """Fold a too-short block into the next one when they share a parent heading."""
    merged: list[_Block] = []
    for block in blocks:
        if (
            merged
            and len(merged[-1].text) < min_chars
            and len(merged[-1].text) + len(block.text) <= max_chars
        ):
            previous = merged[-1]
            previous.lines.append("")
            if block.heading:
                previous.lines.append(f"**{block.heading}**")
            previous.lines.extend(block.lines)
            if previous.page is None:
                previous.page = block.page
            continue
        merged.append(block)
    return merged


def chunk_docling_document(
    doc_dict: dict[str, Any] | None,
    markdown: str,
    *,
    target_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[Chunk]:
    """Prefer Docling's own hybrid chunker, which carries real page numbers.

    Falls back to :func:`chunk_markdown` whenever Docling is absent or the document
    cannot be reconstructed — the result is the same shape either way.
    """
    if doc_dict is None:
        return chunk_markdown(markdown, target_tokens=target_tokens, overlap_tokens=overlap_tokens)

    try:
        from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
        from docling_core.types.doc.document import DoclingDocument
    except ImportError:
        return chunk_markdown(markdown, target_tokens=target_tokens, overlap_tokens=overlap_tokens)

    try:
        document = DoclingDocument.model_validate(doc_dict)
        chunker = HybridChunker(max_tokens=target_tokens)
        chunks: list[Chunk] = []
        for item in chunker.chunk(document):
            text = (getattr(item, "text", "") or "").strip()
            if not text:
                continue
            headings = list(getattr(getattr(item, "meta", None), "headings", None) or [])
            pages = _pages_of(item)
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=text,
                    heading=headings[-1] if headings else "",
                    section_path=" › ".join(headings),
                    page_from=min(pages) if pages else None,
                    page_to=max(pages) if pages else None,
                )
            )
        if chunks:
            return chunks
    except Exception:  # pragma: no cover — docling API drift
        pass

    return chunk_markdown(markdown, target_tokens=target_tokens, overlap_tokens=overlap_tokens)


def _pages_of(item: Any) -> list[int]:
    pages: list[int] = []
    meta = getattr(item, "meta", None)
    for doc_item in getattr(meta, "doc_items", None) or []:
        for prov in getattr(doc_item, "prov", None) or []:
            page_no = getattr(prov, "page_no", None)
            if isinstance(page_no, int):
                pages.append(page_no)
    return pages
