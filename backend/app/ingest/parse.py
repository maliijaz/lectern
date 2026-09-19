"""Turn an uploaded file into Markdown plus structure.

Docling does the heavy lifting: it understands page layout, reading order, tables and
figures, and falls back to OCR for scanned pages. When Docling is not installed the plain
text formats still work, so a minimal install can generate from a Markdown syllabus even
if it cannot open a scanned PDF.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import UnsupportedFormat
from app.core.logging import get_logger

log = get_logger(__name__)

#: Handled without Docling.
PLAIN_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".org", ".tex"}
STRUCTURED_SUFFIXES = {".csv", ".tsv", ".json"}
#: Handled by Docling.
RICH_SUFFIXES = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".html",
    ".htm",
    ".adoc",
    ".asciidoc",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".tif",
    ".bmp",
    ".webp",
}
SUPPORTED_SUFFIXES = PLAIN_TEXT_SUFFIXES | STRUCTURED_SUFFIXES | RICH_SUFFIXES

#: Suffixes that are images — always OCR'd, never expected to have a text layer.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


@dataclass
class ParsedDocument:
    markdown: str
    title: str = ""
    page_count: int = 0
    word_count: int = 0
    used_ocr: bool = False
    parser: str = ""
    #: Docling's structured representation, when available — kept for page provenance.
    doc_dict: dict[str, Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.word_count:
            self.word_count = len(self.markdown.split())


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def parse(path: Path, *, ocr_enabled: bool = True, ocr_languages: str = "eng") -> ParsedDocument:
    """Parse ``path`` into Markdown. Synchronous and CPU-bound — call via ``run_blocking``."""
    if not path.exists():
        raise UnsupportedFormat(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix in PLAIN_TEXT_SUFFIXES:
        return _parse_plain_text(path)
    if suffix in STRUCTURED_SUFFIXES:
        return _parse_structured(path)
    if suffix in RICH_SUFFIXES:
        return _parse_with_docling(path, ocr_enabled=ocr_enabled, ocr_languages=ocr_languages)

    raise UnsupportedFormat(
        f"Cannot read {path.suffix or 'files without an extension'}. Supported: "
        + ", ".join(sorted(SUPPORTED_SUFFIXES))
    )


def _first_heading(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            # A non-heading first line: use it if it reads like a title.
            if len(stripped) < 120:
                return stripped
            break
    return fallback


def _parse_plain_text(path: Path) -> ParsedDocument:
    text = path.read_text(encoding="utf-8", errors="replace")
    return ParsedDocument(
        markdown=text,
        title=_first_heading(text, path.stem),
        parser="plaintext",
    )


def _parse_structured(path: Path) -> ParsedDocument:
    """CSV/TSV/JSON become Markdown so the same chunker and prompts apply."""
    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UnsupportedFormat(f"{path.name} is not valid JSON: {exc}") from exc
        markdown = f"# {path.stem}\n\n```json\n{json.dumps(data, indent=2)[:200_000]}\n```"
        return ParsedDocument(markdown=markdown, title=path.stem, parser="json")

    delimiter = "\t" if suffix == ".tsv" else ","
    rows = list(csv.reader(io.StringIO(raw), delimiter=delimiter))
    if not rows:
        return ParsedDocument(markdown=f"# {path.stem}\n\n_(empty)_", title=path.stem, parser="csv")

    header, *body = rows
    lines = [
        f"# {path.stem}",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body[:5000]:
        padded = row + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in padded[: len(header)]) + " |")
    return ParsedDocument(markdown="\n".join(lines), title=path.stem, parser="csv")


def _parse_with_docling(path: Path, *, ocr_enabled: bool, ocr_languages: str) -> ParsedDocument:
    # Check availability without importing: Docling pulls in torch and takes seconds to
    # load, which is wasted work on the path where we only want to raise a friendly error.
    if importlib.util.find_spec("docling") is None:
        from app.core.errors import DependencyMissing

        raise DependencyMissing(
            f"Reading {path.suffix} files needs Docling, which is not installed.",
            install="pip install -e .[ingest]",
        )

    converter = _build_converter(path, ocr_enabled=ocr_enabled, ocr_languages=ocr_languages)
    log.info("Parsing %s with Docling", path.name)
    result = converter.convert(str(path))
    document = result.document

    markdown = document.export_to_markdown()
    doc_dict: dict[str, Any] | None = None
    try:
        doc_dict = document.export_to_dict()
    except Exception:  # pragma: no cover - version differences in docling-core
        log.debug("Could not export the Docling document dict", exc_info=True)

    page_count = len(getattr(document, "pages", {}) or {})
    used_ocr = path.suffix.lower() in IMAGE_SUFFIXES or _looks_ocred(markdown, page_count)

    return ParsedDocument(
        markdown=markdown,
        title=_docling_title(document) or _first_heading(markdown, path.stem),
        page_count=page_count,
        used_ocr=used_ocr,
        parser="docling",
        doc_dict=doc_dict,
    )


def _build_converter(path: Path, *, ocr_enabled: bool, ocr_languages: str):  # noqa: ANN202
    """Construct a converter, configuring OCR when we can.

    Docling's options API has moved between releases, so this degrades to the default
    converter rather than breaking on a version we were not written against.
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = ocr_enabled
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        if ocr_enabled and ocr_languages:
            with_languages = [lang.strip() for lang in ocr_languages.split(",") if lang.strip()]
            if with_languages and hasattr(pipeline_options, "ocr_options"):
                try:
                    pipeline_options.ocr_options.lang = with_languages
                except Exception:
                    log.debug("Could not set OCR languages", exc_info=True)

        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
    except Exception:
        log.info("Using Docling's default pipeline options", exc_info=True)
        from docling.document_converter import DocumentConverter

        return DocumentConverter()


def _docling_title(document: Any) -> str:
    for attr in ("name", "title"):
        value = getattr(document, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    origin = getattr(document, "origin", None)
    filename = getattr(origin, "filename", None)
    return Path(filename).stem if isinstance(filename, str) else ""


def _looks_ocred(markdown: str, page_count: int) -> bool:
    """A PDF that yields almost no text per page was almost certainly a scan."""
    if page_count <= 0:
        return False
    return len(markdown.split()) / page_count < 20
