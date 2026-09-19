"""Assembling the source material a generator works from.

A teacher can start from a topic, from documents, or from both. This module resolves that
into one context block plus the citations that back it, choosing between three strategies:

* **Whole document.** When the sources are small enough to fit the context window, send
  them entire. Retrieval can only lose information a short chapter already gives for free.
* **Retrieval.** For larger sources, search with several framings of the request so the
  context covers the topic rather than one lucky paragraph.
* **Topic only.** No documents: the model works from its own knowledge, and the result is
  marked ungrounded so the UI can say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.db.models import Document
from app.ingest import retrieve
from app.jobs.worker import run_blocking
from app.schemas.common import Audience, Citation

log = get_logger(__name__)

#: Characters of source material to put in front of the model. Roughly a quarter of the
#: context window, leaving room for the schema, the instructions and the reply.
DEFAULT_BUDGET = 24_000


@dataclass
class SourceMaterial:
    """What a generator is given to work from."""

    text: str = ""
    citations: list[Citation] = field(default_factory=list)
    document_titles: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    grounded: bool = False
    strategy: str = "topic_only"
    truncated: bool = False

    def prompt_block(self, heading: str = "SOURCE MATERIAL") -> str:
        """The context as it appears in a prompt, or an explicit note that there is none."""
        if not self.text:
            return ""
        note = (
            "\n(Only part of the source is shown; work with what is here.)"
            if self.truncated
            else ""
        )
        return f"### {heading}{note}\n\n{self.text}\n\n### END OF SOURCE MATERIAL\n"

    def citation_rule(self) -> str:
        if not self.grounded:
            return ""
        return (
            "Base every statement on the source material above. Where a passage supports "
            "what you write, put its number in the citations field, e.g. [3]. If the "
            "source does not cover something the request asks for, leave it out rather "
            "than inventing it."
        )


async def load_documents(db: AsyncSession, document_ids: list[str]) -> list[Document]:
    if not document_ids:
        return []
    rows = (await db.execute(select(Document).where(Document.id.in_(document_ids)))).scalars().all()
    # Preserve the caller's ordering — it is the order the teacher picked.
    by_id = {d.id: d for d in rows}
    return [by_id[i] for i in document_ids if i in by_id]


async def collect(
    db: AsyncSession,
    *,
    settings: Settings,
    topic: str = "",
    document_ids: list[str] | None = None,
    queries: list[str] | None = None,
    budget: int = DEFAULT_BUDGET,
    prefer_whole_document: bool = True,
) -> SourceMaterial:
    """Resolve a request into source material.

    ``queries`` lets a generator ask from several angles ("definition of X", "examples of
    X", "common mistakes with X"); without it, the topic is used as a single query.
    """
    documents = await load_documents(db, document_ids or [])
    if not documents:
        return SourceMaterial(strategy="topic_only", grounded=False)

    titles = [d.title or d.original_name for d in documents]
    ids = [d.id for d in documents]

    if prefer_whole_document:
        whole = _read_whole(documents, budget)
        if whole is not None:
            text, citations = whole
            return SourceMaterial(
                text=text,
                citations=citations,
                document_titles=titles,
                document_ids=ids,
                grounded=True,
                strategy="whole_document",
            )

    search_terms = [q for q in (queries or []) if q.strip()] or [topic or titles[0]]
    context = await run_blocking(
        retrieve.gather,
        search_terms,
        settings=settings,
        document_ids=ids,
        max_chars=budget,
        document_titles={d.id: d.title or d.original_name for d in documents},
    )

    if not context.hits:
        # Indexed nothing, or the index is unavailable — fall back to the raw text so the
        # teacher still gets something grounded in their document.
        whole = _read_whole(documents, budget)
        if whole is not None:
            text, citations = whole
            return SourceMaterial(
                text=text,
                citations=citations,
                document_titles=titles,
                document_ids=ids,
                grounded=True,
                strategy="whole_document_fallback",
                truncated=True,
            )
        log.warning("No source material could be retrieved for documents %s", ids)
        return SourceMaterial(document_titles=titles, document_ids=ids, strategy="empty")

    return SourceMaterial(
        text=context.text,
        citations=context.citations,
        document_titles=titles,
        document_ids=ids,
        grounded=True,
        strategy="retrieval",
        truncated=context.truncated,
    )


def _read_whole(documents: list[Document], budget: int) -> tuple[str, list[Citation]] | None:
    """Read the parsed Markdown of every document if the total fits the budget."""
    parts: list[str] = []
    citations: list[Citation] = []
    total = 0

    for index, document in enumerate(documents, start=1):
        if not document.markdown_path:
            return None
        path = Path(document.markdown_path)
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        total += len(text)
        if total > budget:
            return None

        title = document.title or document.original_name
        parts.append(f"[{index}] {title}\n{text}")
        citations.append(Citation(document_id=document.id, document_title=title, quote=text[:200]))

    return ("\n\n---\n\n".join(parts), citations) if parts else None


def audience_block(audience: Audience) -> str:
    """The audience as a prompt fragment. Empty fields are omitted rather than sent blank —
    'Grade level: ' teaches the model nothing and wastes context."""
    lines = []
    if audience.grade_level:
        lines.append(f"- Level: {audience.grade_level}")
    if audience.subject:
        lines.append(f"- Subject: {audience.subject}")
    if audience.prior_knowledge:
        lines.append(f"- Already know: {audience.prior_knowledge}")
    if audience.reading_level:
        lines.append(f"- Write at this reading level: {audience.reading_level}")
    if audience.language and audience.language.lower() != "english":
        lines.append(f"- Write in: {audience.language}")
    return "### AUDIENCE\n" + "\n".join(lines) if lines else ""


def resolve_citations(indices: list[int] | list[str], available: list[Citation]) -> list[Citation]:
    """Turn the ``[3]`` markers a model emits into real citations.

    Models cite loosely — "3", "[3]", "passage 3" — so parse tolerantly and drop anything
    that does not resolve rather than attaching a citation that points nowhere.
    """
    resolved: list[Citation] = []
    seen: set[str] = set()

    for raw in indices:
        number: int | None = None
        if isinstance(raw, int):
            number = raw
        else:
            digits = "".join(ch for ch in str(raw) if ch.isdigit())
            if digits:
                number = int(digits)
        if number is None or not 1 <= number <= len(available):
            continue
        citation = available[number - 1]
        key = citation.chunk_id or f"{citation.document_id}:{citation.page}"
        if key in seen:
            continue
        seen.add(key)
        resolved.append(citation)

    return resolved
