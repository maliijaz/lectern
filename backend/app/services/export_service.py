"""Turning a stored artifact into downloadable files.

One registry declares which formats each artifact kind supports and how to produce them,
so the API, the CLI and the UI all agree on what is possible without any of them hard-coding
a list. Exports are recorded as ``ArtifactFile`` rows tagged with the artifact version they
came from, which is how the UI knows a download is stale after the teacher edits the content.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import UnsupportedFormat
from app.core.logging import get_logger
from app.db.models import Artifact, ArtifactFile, ArtifactKind
from app.jobs.worker import run_blocking
from app.render import anki as anki_render
from app.render import docx as docx_render
from app.render import html as html_render
from app.render import lms
from app.render import markdown as md
from app.render import pdf as pdf_render
from app.render import pptx as pptx_render
from app.schemas import ARTIFACT_SCHEMAS
from app.schemas.deck import Deck
from app.schemas.lesson import LessonPlan
from app.schemas.notes import LectureNotes
from app.schemas.paper import QuestionPaper
from app.schemas.rubric import Rubric
from app.schemas.study import FlashcardDeck, GradingResult, Worksheet

log = get_logger(__name__)

#: Artifact kinds that produce a separate answer-key file alongside the main document.
HAS_ANSWER_KEY = {ArtifactKind.EXAM, ArtifactKind.WORKSHEET}


@dataclass(frozen=True)
class ExportFormat:
    key: str
    label: str
    extension: str
    media_type: str
    kinds: frozenset[str]
    #: Optional capability required; the UI greys the format out when it is missing.
    capability: str = ""
    description: str = ""
    #: True when this format also yields an answer key for kinds that have one.
    supports_answer_key: bool = False


_ALL_DOCS = frozenset(
    {
        ArtifactKind.SLIDES,
        ArtifactKind.NOTES,
        ArtifactKind.EXAM,
        ArtifactKind.LESSON_PLAN,
        ArtifactKind.RUBRIC,
        ArtifactKind.WORKSHEET,
        ArtifactKind.FLASHCARDS,
        ArtifactKind.GRADING,
    }
)
_QUESTION_KINDS = frozenset({ArtifactKind.EXAM, ArtifactKind.WORKSHEET})
_PRINTABLE = frozenset(
    {
        ArtifactKind.NOTES,
        ArtifactKind.EXAM,
        ArtifactKind.LESSON_PLAN,
        ArtifactKind.RUBRIC,
        ArtifactKind.WORKSHEET,
        ArtifactKind.GRADING,
    }
)

FORMATS: tuple[ExportFormat, ...] = (
    ExportFormat(
        "pptx",
        "PowerPoint",
        ".pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        frozenset({ArtifactKind.SLIDES}),
        capability="pptx",
        description="Editable slide deck with speaker notes",
    ),
    ExportFormat(
        "docx",
        "Word",
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _PRINTABLE,
        capability="docx",
        supports_answer_key=True,
        description="Editable document, ready to print",
    ),
    ExportFormat(
        "pdf",
        "PDF",
        ".pdf",
        "application/pdf",
        _ALL_DOCS,
        capability="pdf",
        supports_answer_key=True,
        description="Print-ready, fixed layout",
    ),
    ExportFormat(
        "md",
        "Markdown",
        ".md",
        "text/markdown",
        _ALL_DOCS,
        supports_answer_key=True,
        description="Plain text, paste anywhere",
    ),
    ExportFormat(
        "html",
        "Web page",
        ".html",
        "text/html",
        _ALL_DOCS,
        supports_answer_key=True,
        description="Self-contained page for an LMS",
    ),
    ExportFormat(
        "reveal",
        "Browser slides",
        ".html",
        "text/html",
        frozenset({ArtifactKind.SLIDES}),
        description="reveal.js deck — present from a browser (needs internet)",
    ),
    ExportFormat(
        "narration",
        "Narration",
        ".zip",
        "application/zip",
        frozenset({ArtifactKind.SLIDES}),
        description="Spoken script, plus per-slide audio when Piper is installed",
    ),
    ExportFormat(
        "moodle_xml",
        "Moodle XML",
        ".xml",
        "application/xml",
        _QUESTION_KINDS,
        description="Import into a Moodle question bank",
    ),
    ExportFormat(
        "gift",
        "GIFT",
        ".txt",
        "text/plain",
        _QUESTION_KINDS,
        description="Moodle's plain-text question format",
    ),
    ExportFormat(
        "qti",
        "QTI 2.1",
        ".zip",
        "application/zip",
        _QUESTION_KINDS,
        description="Canvas, Blackboard, D2L and OpenEdX",
    ),
    ExportFormat(
        "csv",
        "CSV",
        ".csv",
        "text/csv",
        frozenset({ArtifactKind.EXAM, ArtifactKind.WORKSHEET, ArtifactKind.FLASHCARDS}),
        description="Spreadsheet, one row per item",
    ),
    ExportFormat(
        "forms_csv",
        "Google Forms CSV",
        ".csv",
        "text/csv",
        _QUESTION_KINDS,
        description="For Google Forms quiz importers",
    ),
    ExportFormat(
        "apkg",
        "Anki deck",
        ".apkg",
        "application/octet-stream",
        frozenset({ArtifactKind.FLASHCARDS}),
        capability="anki",
        description="Spaced-repetition deck",
    ),
    ExportFormat(
        "tsv",
        "Quizlet TSV",
        ".tsv",
        "text/tab-separated-values",
        frozenset({ArtifactKind.FLASHCARDS}),
        description="Paste into Quizlet",
    ),
    ExportFormat(
        "json",
        "JSON",
        ".json",
        "application/json",
        _ALL_DOCS,
        description="The raw structured content",
    ),
)

_BY_KEY = {f.key: f for f in FORMATS}


def formats_for(kind: str) -> list[ExportFormat]:
    return [f for f in FORMATS if kind in f.kinds]


def get_format(key: str) -> ExportFormat:
    fmt = _BY_KEY.get(key)
    if fmt is None:
        raise UnsupportedFormat(f"Unknown export format {key!r}. Available: {', '.join(_BY_KEY)}")
    return fmt


def available_formats(kind: str) -> list[dict]:
    """Formats for this kind, with availability, for the UI's export panel."""
    from app.core.capabilities import capability_report

    capabilities = capability_report()
    out = []
    for fmt in formats_for(kind):
        entry = capabilities.get(fmt.capability) if fmt.capability else None
        out.append(
            {
                "key": fmt.key,
                "label": fmt.label,
                "extension": fmt.extension,
                "description": fmt.description,
                "available": entry["available"] if entry else True,
                "install": "" if entry is None or entry["available"] else entry["install"],
                "supports_answer_key": fmt.supports_answer_key and kind in HAS_ANSWER_KEY,
            }
        )
    return out


# --------------------------------------------------------------------------- rendering


@dataclass
class RenderedFile:
    role: str
    filename: str
    path: Path
    size_bytes: int = 0
    extras: dict = field(default_factory=dict)


def _slug(text: str, limit: int = 60) -> str:
    keep = [c if c.isalnum() or c in " -_" else " " for c in text]
    cleaned = "-".join("".join(keep).split())
    return (cleaned[:limit].strip("-") or "artifact").lower()


def render_artifact(
    artifact_kind: str,
    content: dict,
    fmt_key: str,
    out_dir: Path,
    *,
    base_name: str,
    theme_key: str = "academic",
    include_answer_key: bool = True,
) -> list[RenderedFile]:
    """Render one artifact into one format. Synchronous — call via ``run_blocking``.

    Returns every file produced: a paper export also yields its answer key, and a
    multi-variant exam yields a paper and key per set.
    """
    fmt = get_format(fmt_key)
    if artifact_kind not in fmt.kinds:
        raise UnsupportedFormat(f"{fmt.label} is not available for {artifact_kind} artifacts.")

    schema = ARTIFACT_SCHEMAS[artifact_kind]
    obj = schema.model_validate(content)
    out_dir.mkdir(parents=True, exist_ok=True)

    handler = _HANDLERS.get((artifact_kind, fmt.key)) or _HANDLERS.get(("*", fmt.key))
    if handler is None:
        raise UnsupportedFormat(f"No renderer for {artifact_kind} as {fmt.label}.")

    files = handler(obj, out_dir, base_name, fmt, theme_key, include_answer_key)
    for entry in files:
        entry.size_bytes = entry.path.stat().st_size if entry.path.exists() else 0
    return files


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _title_of(obj) -> str:  # noqa: ANN001
    if isinstance(obj, QuestionPaper):
        return obj.meta.exam_name or "Examination"
    if isinstance(obj, GradingResult):
        return obj.task_title or "Feedback"
    return getattr(obj, "title", "Document")


def _subtitle_of(obj) -> str:  # noqa: ANN001
    if isinstance(obj, QuestionPaper):
        return " · ".join(p for p in (obj.meta.course, obj.meta.grade_level) if p)
    return getattr(obj, "subtitle", "") or getattr(obj, "subject", "")


def _markdown_of(obj, *, answer_key: bool = False) -> str:  # noqa: ANN001
    """The Markdown for any artifact — the spine of the md, html and pdf exports."""
    if isinstance(obj, Deck):
        return md.deck_to_markdown(obj)
    if isinstance(obj, LectureNotes):
        return md.notes_to_markdown(obj)
    if isinstance(obj, QuestionPaper):
        return md.paper_to_markdown(obj, answer_key=answer_key)
    if isinstance(obj, Worksheet):
        return md.worksheet_to_markdown(obj, answer_key=answer_key)
    if isinstance(obj, Rubric):
        return md.rubric_to_markdown(obj)
    if isinstance(obj, LessonPlan):
        return md.lesson_plan_to_markdown(obj)
    if isinstance(obj, FlashcardDeck):
        return md.flashcards_to_markdown(obj)
    if isinstance(obj, GradingResult):
        return md.grading_to_markdown(obj)
    raise UnsupportedFormat(f"No Markdown renderer for {type(obj).__name__}")


def _wants_key(obj, include: bool) -> bool:  # noqa: ANN001
    return include and isinstance(obj, QuestionPaper | Worksheet)


# Each handler: (obj, out_dir, base, fmt, theme, include_key) -> list[RenderedFile]
Handler = Callable[..., list[RenderedFile]]


def _h_markdown(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    files = [
        RenderedFile("main", f"{base}.md", _write_text(out_dir / f"{base}.md", _markdown_of(obj)))
    ]
    if _wants_key(obj, include_key):
        files.append(
            RenderedFile(
                "answer_key",
                f"{base}-answer-key.md",
                _write_text(
                    out_dir / f"{base}-answer-key.md",
                    _markdown_of(obj, answer_key=True),
                ),
            )
        )
    return files


def _h_html(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    title, subtitle = _title_of(obj), _subtitle_of(obj)
    files = [
        RenderedFile(
            "main",
            f"{base}.html",
            _write_text(
                out_dir / f"{base}.html",
                html_render.render(
                    _markdown_of(obj), title=title, subtitle=subtitle, theme_key=theme
                ),
            ),
        )
    ]
    if _wants_key(obj, include_key):
        files.append(
            RenderedFile(
                "answer_key",
                f"{base}-answer-key.html",
                _write_text(
                    out_dir / f"{base}-answer-key.html",
                    html_render.render(
                        _markdown_of(obj, answer_key=True),
                        title=f"{title} — Answer Key",
                        subtitle=subtitle,
                        theme_key=theme,
                    ),
                ),
            )
        )
    return files


def _h_reveal(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    return [
        RenderedFile(
            "main",
            f"{base}-slides.html",
            _write_text(
                out_dir / f"{base}-slides.html",
                html_render.render_reveal(
                    md.deck_to_markdown(obj, include_notes=False),
                    title=obj.title,
                    theme_key=theme,
                ),
            ),
        )
    ]


def _h_narration(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    from app.media import tts

    return [
        RenderedFile(
            "main",
            f"{base}-narration.zip",
            tts.render_narration(obj, out_dir / f"{base}-narration.zip"),
            extras={"audio": tts.available()},
        )
    ]


def _h_pdf(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    title, subtitle = _title_of(obj), _subtitle_of(obj)
    landscape = isinstance(obj, Rubric)
    footer = obj.meta.institution if isinstance(obj, QuestionPaper) else subtitle

    files = [
        RenderedFile(
            "main",
            f"{base}.pdf",
            pdf_render.render_markdown_to_pdf(
                _markdown_of(obj),
                out_dir / f"{base}.pdf",
                title=title,
                subtitle=subtitle,
                footer=footer,
                theme_key=theme,
                landscape=landscape,
            ),
        )
    ]
    if _wants_key(obj, include_key):
        files.append(
            RenderedFile(
                "answer_key",
                f"{base}-answer-key.pdf",
                pdf_render.render_markdown_to_pdf(
                    _markdown_of(obj, answer_key=True),
                    out_dir / f"{base}-answer-key.pdf",
                    title=f"{title} — Answer Key",
                    subtitle=subtitle,
                    footer=footer,
                    theme_key=theme,
                ),
            )
        )
    return files


def _h_pptx(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    from app.config import get_settings
    from app.media import diagrams

    obj.theme = theme or obj.theme
    # Turn the model's figure specs into real images before the slides are laid out. A
    # no-op when mmdc and matplotlib are absent, in which case the slides show the figures'
    # alt text rather than an empty frame.
    diagrams.prepare_deck_figures(obj, get_settings().media_dir)
    return [RenderedFile("main", f"{base}.pptx", pptx_render.render(obj, out_dir / f"{base}.pptx"))]


def _h_docx(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    path = out_dir / f"{base}.docx"
    files: list[RenderedFile] = []

    if isinstance(obj, QuestionPaper):
        files.append(
            RenderedFile("main", path.name, docx_render.render_paper(obj, path, theme_key=theme))
        )
        if include_key:
            key_path = out_dir / f"{base}-answer-key.docx"
            files.append(
                RenderedFile(
                    "answer_key",
                    key_path.name,
                    docx_render.render_paper(obj, key_path, answer_key=True, theme_key=theme),
                )
            )
    elif isinstance(obj, Worksheet):
        files.append(
            RenderedFile(
                "main", path.name, docx_render.render_worksheet(obj, path, theme_key=theme)
            )
        )
        if include_key:
            key_path = out_dir / f"{base}-answer-key.docx"
            files.append(
                RenderedFile(
                    "answer_key",
                    key_path.name,
                    docx_render.render_worksheet(obj, key_path, answer_key=True, theme_key=theme),
                )
            )
    elif isinstance(obj, LectureNotes):
        files.append(
            RenderedFile("main", path.name, docx_render.render_notes(obj, path, theme_key=theme))
        )
    elif isinstance(obj, Rubric):
        files.append(
            RenderedFile("main", path.name, docx_render.render_rubric(obj, path, theme_key=theme))
        )
    elif isinstance(obj, LessonPlan):
        files.append(
            RenderedFile(
                "main", path.name, docx_render.render_lesson_plan(obj, path, theme_key=theme)
            )
        )
    else:
        raise UnsupportedFormat(f"No Word renderer for {type(obj).__name__}")

    return files


def _as_paper(obj):  # noqa: ANN001
    """Worksheets export to LMS formats by presenting as a paper."""
    if isinstance(obj, QuestionPaper):
        return obj
    from app.schemas.paper import PaperMeta, PaperSection

    return QuestionPaper(
        meta=PaperMeta(exam_name=obj.title, course=obj.subject, grade_level=obj.grade_level),
        sections=[
            PaperSection(title=s.title, instructions=s.instructions, questions=s.questions)
            for s in obj.sections
        ]
        + ([PaperSection(title="Challenge", questions=obj.challenge)] if obj.challenge else []),
    )


def _h_moodle(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    paper = _as_paper(obj)
    return [
        RenderedFile(
            "main",
            f"{base}-moodle.xml",
            _write_text(out_dir / f"{base}-moodle.xml", lms.to_moodle_xml(paper)),
        )
    ]


def _h_gift(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    return [
        RenderedFile(
            "main",
            f"{base}.gift.txt",
            _write_text(out_dir / f"{base}.gift.txt", lms.to_gift(_as_paper(obj))),
        )
    ]


def _h_qti(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    return [
        RenderedFile(
            "main",
            f"{base}-qti.zip",
            lms.to_qti_package(_as_paper(obj), out_dir / f"{base}-qti.zip"),
        )
    ]


def _h_csv(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    text = anki_render.to_csv(obj) if isinstance(obj, FlashcardDeck) else lms.to_csv(_as_paper(obj))
    return [RenderedFile("main", f"{base}.csv", _write_text(out_dir / f"{base}.csv", text))]


def _h_forms_csv(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    paper = _as_paper(obj)
    return [
        RenderedFile(
            "main",
            f"{base}-google-forms.csv",
            _write_text(out_dir / f"{base}-google-forms.csv", lms.to_google_forms_csv(paper)),
            extras={"skipped_types": lms.unsupported_in_forms(paper)},
        )
    ]


def _h_apkg(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    return [RenderedFile("main", f"{base}.apkg", anki_render.render(obj, out_dir / f"{base}.apkg"))]


def _h_tsv(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    return [
        RenderedFile(
            "main",
            f"{base}-quizlet.tsv",
            _write_text(out_dir / f"{base}-quizlet.tsv", anki_render.to_quizlet_tsv(obj)),
        )
    ]


def _h_json(obj, out_dir, base, fmt, theme, include_key):  # noqa: ANN001
    return [
        RenderedFile(
            "main",
            f"{base}.json",
            _write_text(
                out_dir / f"{base}.json",
                json.dumps(obj.model_dump(mode="json"), indent=2, ensure_ascii=False),
            ),
        )
    ]


_HANDLERS: dict[tuple[str, str], Handler] = {
    ("*", "md"): _h_markdown,
    ("*", "html"): _h_html,
    ("*", "pdf"): _h_pdf,
    ("*", "json"): _h_json,
    ("*", "docx"): _h_docx,
    ("*", "moodle_xml"): _h_moodle,
    ("*", "gift"): _h_gift,
    ("*", "qti"): _h_qti,
    ("*", "csv"): _h_csv,
    ("*", "forms_csv"): _h_forms_csv,
    ("*", "apkg"): _h_apkg,
    ("*", "tsv"): _h_tsv,
    (ArtifactKind.SLIDES, "pptx"): _h_pptx,
    (ArtifactKind.SLIDES, "reveal"): _h_reveal,
    (ArtifactKind.SLIDES, "narration"): _h_narration,
}


# --------------------------------------------------------------------------- persistence


async def export(
    db: AsyncSession,
    artifact: Artifact,
    fmt_key: str,
    *,
    settings: Settings,
    theme_key: str = "",
    include_answer_key: bool = True,
    force: bool = False,
) -> list[ArtifactFile]:
    """Render and record an export, reusing an existing up-to-date file unless ``force``."""
    fmt = get_format(fmt_key)

    if not force:
        existing = (
            (
                await db.execute(
                    select(ArtifactFile).where(
                        ArtifactFile.artifact_id == artifact.id,
                        ArtifactFile.fmt == fmt.key,
                        ArtifactFile.source_version == artifact.version,
                    )
                )
            )
            .scalars()
            .all()
        )
        if existing and all(Path(f.path).exists() for f in existing):
            log.debug("Reusing %s export of %s", fmt.key, artifact.id)
            return list(existing)

    theme = theme_key or artifact.params.get("theme") or "academic"
    base_name = _slug(artifact.title or artifact.kind)
    out_dir = settings.exports_dir / artifact.id

    rendered = await run_blocking(
        render_artifact,
        artifact.kind,
        artifact.content,
        fmt.key,
        out_dir,
        base_name=base_name,
        theme_key=theme,
        include_answer_key=include_answer_key,
    )

    # Replace any previous rows for this format so the list stays honest.
    stale = (
        (
            await db.execute(
                select(ArtifactFile).where(
                    ArtifactFile.artifact_id == artifact.id, ArtifactFile.fmt == fmt.key
                )
            )
        )
        .scalars()
        .all()
    )
    for row in stale:
        await db.delete(row)
    await db.flush()

    records = [
        ArtifactFile(
            artifact_id=artifact.id,
            fmt=fmt.key,
            role=entry.role,
            filename=entry.filename,
            path=str(entry.path),
            size_bytes=entry.size_bytes,
            source_version=artifact.version,
        )
        for entry in rendered
    ]
    db.add_all(records)
    await db.flush()
    # Commit each format as it completes, so the write lock is never held across the next
    # format's rendering and a partial export survives a later failure.
    await db.commit()

    log.info(
        "Exported %s as %s → %d file(s)", artifact.title or artifact.id, fmt.label, len(records)
    )
    return records


async def export_many(
    db: AsyncSession,
    artifact: Artifact,
    fmt_keys: list[str],
    *,
    settings: Settings,
    theme_key: str = "",
    include_answer_key: bool = True,
    progress=None,  # noqa: ANN001
) -> dict[str, list[ArtifactFile] | str]:
    """Export several formats, reporting per-format failures rather than aborting.

    A missing optional dependency should cost the teacher that one format, not the whole
    export.
    """
    results: dict[str, list[ArtifactFile] | str] = {}
    for index, key in enumerate(fmt_keys):
        if progress is not None:
            await progress((index + 1) / len(fmt_keys), f"Exporting {key}")
        try:
            results[key] = await export(
                db,
                artifact,
                key,
                settings=settings,
                theme_key=theme_key,
                include_answer_key=include_answer_key,
            )
        except Exception as exc:
            log.warning("Export %s failed for %s: %s", key, artifact.id, exc)
            results[key] = f"{type(exc).__name__}: {exc}"
    return results


def default_formats(kind: str) -> list[str]:
    """What to produce automatically when generation finishes."""
    return {
        ArtifactKind.SLIDES: ["pptx", "pdf"],
        ArtifactKind.NOTES: ["docx", "pdf"],
        ArtifactKind.EXAM: ["docx", "pdf"],
        ArtifactKind.WORKSHEET: ["docx", "pdf"],
        ArtifactKind.RUBRIC: ["docx", "pdf"],
        ArtifactKind.LESSON_PLAN: ["docx", "pdf"],
        ArtifactKind.FLASHCARDS: ["apkg", "csv"],
        ArtifactKind.GRADING: ["pdf"],
    }.get(kind, ["md"])
