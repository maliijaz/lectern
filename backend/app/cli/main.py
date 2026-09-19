"""The ``ta`` command line.

The CLI is not a thin wrapper over the API — it runs the same services in-process, which
means it works with the server stopped and is the right tool for batch work: ingesting a
term's worth of chapters overnight, or generating thirty differentiated worksheets from a
shell loop.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from app.core.logging import ensure_utf8_streams, setup_logging

app = typer.Typer(
    name="ta",
    help="Teacher Assistant — turn documents or topics into slides, notes and question papers.",
    no_args_is_help=True,
    add_completion=False,
)
docs_app = typer.Typer(help="Manage source documents.", no_args_is_help=True)
generate_app = typer.Typer(help="Generate teaching material.", no_args_is_help=True)
app.add_typer(docs_app, name="docs")
app.add_typer(generate_app, name="generate")

# Must run before Rich inspects the stream's encoding to decide what it can render.
ensure_utf8_streams()
console = Console()
T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    """Run an async service call with the database initialised."""

    async def wrapper() -> T:
        from app.config import get_settings
        from app.db.session import dispose_engine, init_db

        get_settings().ensure_dirs()
        await init_db()
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(wrapper())


def _fail(message: str) -> None:
    console.print(f"[bold red]✗[/] {message}")
    raise typer.Exit(1)


def _progress_reporter(task_label: str) -> tuple[Progress, Callable[[float, str], Awaitable[None]]]:
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("{task.percentage:>3.0f}%"),
        console=console,
    )
    task_id = progress.add_task(task_label, total=100)

    async def report(fraction: float, message: str) -> None:
        progress.update(task_id, completed=fraction * 100, description=message or task_label)

    return progress, report


# --------------------------------------------------------------------------- top level


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug logging."),
) -> None:
    setup_logging(verbose)


@app.command()
def status() -> None:
    """Show the configuration, the model connection and what is installed."""
    from app.core import hardware as hardware_module
    from app.core.capabilities import capability_report
    from app.db.session import session_scope
    from app.llm.registry import build_provider
    from app.services import settings_service

    async def check() -> tuple[Any, Any, int, str, dict]:
        async with session_scope() as db:
            settings = await settings_service.effective_settings(db)
        provider = build_provider(settings)
        result = await provider.probe()

        context, reason, placement = 0, "", {}
        if hasattr(provider, "effective_context"):
            try:
                context, reason = await provider.effective_context()
                placement = await provider.placement()
            except Exception:
                pass
        return settings, result, context, reason, placement

    settings, probe, context, context_reason, placement = _run(check())
    hardware = hardware_module.refresh()

    table = Table(title="Teacher Assistant", show_header=False, box=None, padding=(0, 2))
    table.add_row("Data directory", str(settings.data_dir))
    table.add_row("Provider", settings.llm_provider)
    table.add_row("Model", settings.llm_model)
    table.add_row("Endpoint", settings.llm_base_url)
    table.add_row("Hardware", hardware.summary())
    table.add_row("Embeddings on", hardware_module.resolve_embed_device(settings.embed_device))
    if context:
        table.add_row("Context", f"{context:,} tokens")
    if placement.get("loaded"):
        share = placement["gpu_share"]
        table.add_row(
            "Model placement",
            f"[green]{share:.0%} on GPU[/]"
            if placement["fully_on_gpu"]
            else f"[yellow]{share:.0%} on GPU, the rest on the CPU — expect it to be slow[/]",
        )
    table.add_row(
        "Connection",
        f"[green]{probe.message}[/]" if probe.ok else f"[red]{probe.message}[/]",
    )
    console.print(table)

    if context_reason:
        console.print(f"  [dim]{context_reason}[/]")

    features = Table(title="Features", show_header=True, header_style="bold")
    features.add_column("Feature")
    features.add_column("Status")
    features.add_column("Install if missing")
    for entry in capability_report(refresh=True).values():
        features.add_row(
            entry["label"],
            "[green]ready[/]" if entry["available"] else "[yellow]not installed[/]",
            "" if entry["available"] else escape(entry["install"]),
        )
    console.print(features)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = typer.Option(False, help="Restart on code changes (development only)."),
) -> None:
    """Start the web API."""
    import uvicorn

    console.print(f"[bold]Teacher Assistant[/] → http://{host}:{port}  (docs at /docs)")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


@app.command()
def models() -> None:
    """List the models the configured backend can serve."""
    from app.db.session import session_scope
    from app.llm.registry import build_provider
    from app.services import settings_service

    async def fetch() -> list[str]:
        async with session_scope() as db:
            settings = await settings_service.effective_settings(db)
        return await build_provider(settings).list_models()

    try:
        for name in _run(fetch()):
            console.print(f"  {name}")
    except Exception as exc:
        _fail(f"Could not list models: {exc}")


# --------------------------------------------------------------------------- documents


@docs_app.command("add")
def docs_add(
    paths: list[Path] = typer.Argument(..., help="Files to ingest."),
    subject: str = typer.Option("", help="Subject tag."),
    grade: str = typer.Option("", "--grade", help="Grade or class level."),
    no_index: bool = typer.Option(False, help="Parse only; skip building the search index."),
) -> None:
    """Parse and index one or more documents."""
    import shutil

    from app.config import get_settings
    from app.db.models import Document, DocumentStatus
    from app.db.session import session_scope
    from app.ingest.pipeline import ingest_document, sha256_of
    from app.services import settings_service

    async def ingest_one(path: Path) -> None:
        settings = get_settings()
        async with session_scope() as db:
            settings = await settings_service.effective_settings(db)
            digest = sha256_of(path)
            document = Document(
                original_name=path.name,
                title=path.stem,
                stored_path="",
                size_bytes=path.stat().st_size,
                sha256=digest,
                status=DocumentStatus.UPLOADED,
                subject=subject,
                grade_level=grade,
            )
            db.add(document)
            await db.flush()
            stored = settings.uploads_dir / f"{document.id}{path.suffix.lower()}"
            shutil.copy2(path, stored)
            document.stored_path = str(stored)
            document_id = document.id

        progress, report = _progress_reporter(path.name)
        with progress:
            result = await ingest_document(document_id, settings=settings, index=not no_index)
            await report(1.0, "done")

        console.print(
            f"[green]✓[/] {path.name} — {result.word_count:,} words, "
            f"{result.chunk_count} passages"
            + (" [dim](not indexed)[/]" if not result.indexed else "")
            + (" [yellow](OCR used)[/]" if result.used_ocr else "")
        )

    for path in paths:
        if not path.exists():
            console.print(f"[red]✗[/] {path} does not exist")
            continue
        try:
            _run(ingest_one(path))
        except Exception as exc:
            console.print(f"[red]✗[/] {path.name}: {exc}")


@docs_app.command("list")
def docs_list() -> None:
    """List ingested documents."""
    from sqlalchemy import desc, select

    from app.db.models import Document
    from app.db.session import session_scope

    async def fetch() -> list[Document]:
        async with session_scope() as db:
            rows = await db.execute(select(Document).order_by(desc(Document.created_at)))
            return list(rows.scalars().all())

    documents = _run(fetch())
    if not documents:
        console.print("[dim]No documents yet. Add one with `ta docs add <file>`.[/]")
        return

    table = Table(show_header=True, header_style="bold")
    for column in ("ID", "Name", "Status", "Pages", "Words", "Passages", "Subject"):
        table.add_column(column)
    for document in documents:
        table.add_row(
            document.id[:8],
            document.title or document.original_name,
            document.status,
            str(document.page_count or "—"),
            f"{document.word_count:,}",
            str(document.chunk_count),
            document.subject or "—",
        )
    console.print(table)


@docs_app.command("search")
def docs_search(
    query: str,
    top_k: int = typer.Option(5, "--top", help="How many passages to show."),
) -> None:
    """Search the document library."""
    from app.db.session import session_scope
    from app.ingest import retrieve
    from app.services import settings_service

    async def run() -> list:
        async with session_scope() as db:
            settings = await settings_service.effective_settings(db)
        return retrieve.search(query, settings=settings, top_k=top_k)

    try:
        hits = _run(run())
    except Exception as exc:
        _fail(f"Search failed: {exc}")
        return

    if not hits:
        console.print("[dim]Nothing matched.[/]")
        return

    for index, hit in enumerate(hits, start=1):
        label = hit.section or "—"
        page = f" · p.{hit.page}" if hit.page else ""
        console.print(
            Panel(
                hit.text[:600] + ("…" if len(hit.text) > 600 else ""),
                title=f"[{index}] {label}{page}  [dim]score {hit.score:.3f}[/]",
                border_style="blue",
            )
        )


@docs_app.command("remove")
def docs_remove(document_id: str) -> None:
    """Delete a document, its text and its index entries."""
    from app.db.models import Document
    from app.db.session import session_scope
    from app.ingest.pipeline import remove_document_index
    from app.services import settings_service

    async def run() -> str:
        async with session_scope() as db:
            settings = await settings_service.effective_settings(db)
            document = await _resolve(db, Document, document_id)
            name = document.title or document.original_name
            await remove_document_index(document.id, settings)
            for path in (document.stored_path, document.markdown_path):
                if path:
                    Path(path).unlink(missing_ok=True)
            await db.delete(document)
            return name

    console.print(f"[green]✓[/] Removed {_run(run())}")


async def _resolve(db, model, prefix: str):  # noqa: ANN001, ANN202
    """Look up by full id, or by a unique id prefix — nobody types a UUID."""
    from sqlalchemy import select

    found = await db.get(model, prefix)
    if found is not None:
        return found

    rows = (await db.execute(select(model).where(model.id.like(f"{prefix}%")))).scalars().all()
    if not rows:
        raise typer.BadParameter(f"No {model.__name__.lower()} matching {prefix!r}")
    if len(rows) > 1:
        raise typer.BadParameter(f"{prefix!r} matches {len(rows)} records — be more specific")
    return rows[0]


# --------------------------------------------------------------------------- generation


async def _generate(
    kind: str,
    params: dict[str, Any],
    out_dir: Path,
    formats: list[str] | None,
) -> None:
    from app.db.session import session_scope
    from app.services import export_service, generation_service, settings_service

    async with session_scope() as db:
        settings = await settings_service.effective_settings(db)
        artifact = await generation_service.create_pending(db, kind, params)
        await db.commit()

        progress, report = _progress_reporter(f"Generating {kind.replace('_', ' ')}")
        with progress:
            outcome = await generation_service.generate(
                db, artifact, settings=settings, progress=report
            )
            await db.commit()

            chosen = formats or export_service.default_formats(kind)
            results = await export_service.export_many(
                db, outcome.artifact, chosen, settings=settings, progress=report
            )
            await report(1.0, "done")

    console.print(f"\n[bold green]✓[/] {outcome.artifact.title}")
    if outcome.report.get("summary"):
        matched = outcome.report.get("blueprint_matches")
        console.print(f"  [{'green' if matched else 'yellow'}]{outcome.report['summary']}[/]")
        for warning in outcome.report.get("warnings", [])[:5]:
            console.print(f"  [yellow]![/] {warning}")

    out_dir.mkdir(parents=True, exist_ok=True)
    for fmt, value in results.items():
        if isinstance(value, str):
            console.print(f"  [red]✗[/] {fmt}: {value}")
            continue
        for record in value:
            destination = out_dir / record.filename
            destination.write_bytes(Path(record.path).read_bytes())
            console.print(f"  [green]→[/] {destination}")

    console.print(f"\n  [dim]Artifact id: {outcome.artifact.id}[/]")


def _audience(subject: str, grade: str, language: str, reading_level: str) -> dict[str, str]:
    return {
        "subject": subject,
        "grade_level": grade,
        "language": language,
        "reading_level": reading_level,
    }


_DOC_OPTION = typer.Option(None, "--doc", "-d", help="Source document id or id prefix. Repeatable.")
_OUT_OPTION = typer.Option(Path("./out"), "--out", "-o", help="Where to write the files.")
_FORMAT_OPTION = typer.Option(None, "--format", "-f", help="Export format. Repeatable.")


async def _resolve_docs(ids: list[str] | None) -> list[str]:
    if not ids:
        return []
    from app.db.models import Document
    from app.db.session import session_scope

    async with session_scope() as db:
        return [(await _resolve(db, Document, value)).id for value in ids]


@generate_app.command("slides")
def gen_slides(
    topic: str = typer.Option("", "--topic", "-t", help="Topic to build the deck from."),
    doc: list[str] = _DOC_OPTION,
    slides: int = typer.Option(12, "--slides", "-n", help="Number of slides."),
    minutes: int = typer.Option(0, help="Lecture length; overrides --slides."),
    theme: str = typer.Option(
        "academic", help="academic, minimal, chalkboard, warm, high_contrast"
    ),
    subject: str = typer.Option("", help="Subject."),
    grade: str = typer.Option("", help="Grade or class level."),
    language: str = typer.Option("English", help="Language to write in."),
    instructions: str = typer.Option("", "--instructions", "-i", help="Extra direction."),
    no_quiz: bool = typer.Option(False, help="Leave out check-for-understanding slides."),
    out: Path = _OUT_OPTION,
    fmt: list[str] = _FORMAT_OPTION,
) -> None:
    """Generate a slide deck."""
    params = {
        "topic": topic,
        "document_ids": _run(_resolve_docs(doc)),
        "slide_count": slides,
        "lecture_minutes": minutes,
        "theme": theme,
        "instructions": instructions,
        "include_quiz_slides": not no_quiz,
        "audience": _audience(subject, grade, language, ""),
    }
    _run(_generate("slides", params, out, fmt or None))


@generate_app.command("notes")
def gen_notes(
    topic: str = typer.Option("", "--topic", "-t"),
    doc: list[str] = _DOC_OPTION,
    depth: str = typer.Option("standard", help="outline, standard, detailed or textbook."),
    sections: int = typer.Option(8, "--sections", help="Maximum number of sections."),
    subject: str = typer.Option(""),
    grade: str = typer.Option(""),
    language: str = typer.Option("English"),
    reading_level: str = typer.Option("", help="Target reading level, e.g. 'Grade 7'."),
    instructions: str = typer.Option("", "--instructions", "-i"),
    out: Path = _OUT_OPTION,
    fmt: list[str] = _FORMAT_OPTION,
) -> None:
    """Generate lecture notes."""
    params = {
        "topic": topic,
        "document_ids": _run(_resolve_docs(doc)),
        "depth": depth,
        "max_sections": sections,
        "instructions": instructions,
        "audience": _audience(subject, grade, language, reading_level),
    }
    _run(_generate("notes", params, out, fmt or None))


@generate_app.command("exam")
def gen_exam(
    topic: str = typer.Option("", "--topic", "-t"),
    doc: list[str] = _DOC_OPTION,
    marks: float = typer.Option(50, "--marks", "-m", help="Total marks."),
    duration: int = typer.Option(60, help="Duration in minutes."),
    types: str = typer.Option(
        "mcq,short_answer,long_answer",
        "--types",
        help="Comma-separated question types.",
    ),
    variants: int = typer.Option(1, help="How many shuffled sets to produce."),
    name: str = typer.Option("", "--name", help="Exam name, e.g. 'Mid-Term Examination'."),
    institution: str = typer.Option("", help="School or university name."),
    subject: str = typer.Option(""),
    grade: str = typer.Option(""),
    instructions: str = typer.Option("", "--instructions", "-i"),
    out: Path = _OUT_OPTION,
    fmt: list[str] = _FORMAT_OPTION,
) -> None:
    """Generate a question paper with an answer key."""
    params = {
        "topic": topic,
        "document_ids": _run(_resolve_docs(doc)),
        "total_marks": marks,
        "duration_minutes": duration,
        "question_types": [t.strip() for t in types.split(",") if t.strip()],
        "variants": variants,
        "exam_name": name,
        "institution": institution,
        "instructions": instructions,
        "audience": _audience(subject, grade, "English", ""),
    }
    _run(_generate("exam", params, out, fmt or None))


@generate_app.command("worksheet")
def gen_worksheet(
    topic: str = typer.Option("", "--topic", "-t"),
    doc: list[str] = _DOC_OPTION,
    questions: int = typer.Option(12, "--questions", "-n"),
    difficulty: str = typer.Option("medium", help="easy, medium or hard."),
    subject: str = typer.Option(""),
    grade: str = typer.Option(""),
    out: Path = _OUT_OPTION,
    fmt: list[str] = _FORMAT_OPTION,
) -> None:
    """Generate a practice worksheet with an answer key."""
    params = {
        "topic": topic,
        "document_ids": _run(_resolve_docs(doc)),
        "question_count": questions,
        "difficulty": difficulty,
        "audience": _audience(subject, grade, "English", ""),
    }
    _run(_generate("worksheet", params, out, fmt or None))


@generate_app.command("lesson-plan")
def gen_lesson_plan(
    topic: str = typer.Option("", "--topic", "-t"),
    doc: list[str] = _DOC_OPTION,
    minutes: int = typer.Option(45, help="Period length."),
    template: str = typer.Option("generic", help="generic, 5e, hunter, gradual_release, inquiry."),
    subject: str = typer.Option(""),
    grade: str = typer.Option(""),
    out: Path = _OUT_OPTION,
    fmt: list[str] = _FORMAT_OPTION,
) -> None:
    """Generate a lesson plan."""
    params = {
        "topic": topic,
        "document_ids": _run(_resolve_docs(doc)),
        "duration_minutes": minutes,
        "template": template,
        "audience": _audience(subject, grade, "English", ""),
    }
    _run(_generate("lesson_plan", params, out, fmt or None))


@generate_app.command("rubric")
def gen_rubric(
    task: str = typer.Option(..., "--task", help="The assignment being assessed."),
    doc: list[str] = _DOC_OPTION,
    criteria: int = typer.Option(4, help="Number of criteria."),
    style: str = typer.Option("analytic", help="analytic, holistic or single_point."),
    points: float = typer.Option(0, help="Total points; 0 lets the model choose."),
    subject: str = typer.Option(""),
    grade: str = typer.Option(""),
    out: Path = _OUT_OPTION,
    fmt: list[str] = _FORMAT_OPTION,
) -> None:
    """Generate an assessment rubric."""
    params = {
        "topic": task,
        "task_description": task,
        "document_ids": _run(_resolve_docs(doc)),
        "criteria_count": criteria,
        "style": style,
        "total_points": points,
        "audience": _audience(subject, grade, "English", ""),
    }
    _run(_generate("rubric", params, out, fmt or None))


@generate_app.command("flashcards")
def gen_flashcards(
    topic: str = typer.Option("", "--topic", "-t"),
    doc: list[str] = _DOC_OPTION,
    cards: int = typer.Option(25, "--cards", "-n"),
    cloze: bool = typer.Option(True, help="Include fill-the-gap cards."),
    subject: str = typer.Option(""),
    grade: str = typer.Option(""),
    out: Path = _OUT_OPTION,
    fmt: list[str] = _FORMAT_OPTION,
) -> None:
    """Generate a flashcard deck (Anki .apkg and CSV)."""
    params = {
        "topic": topic,
        "document_ids": _run(_resolve_docs(doc)),
        "card_count": cards,
        "include_cloze": cloze,
        "audience": _audience(subject, grade, "English", ""),
    }
    _run(_generate("flashcards", params, out, fmt or None))


# --------------------------------------------------------------------------- artifacts


@app.command("list")
def list_artifacts(
    kind: str = typer.Option("", help="Filter by kind."),
    limit: int = typer.Option(20, help="How many to show."),
) -> None:
    """List generated material."""
    from sqlalchemy import desc, select

    from app.db.models import Artifact
    from app.db.session import session_scope

    async def fetch() -> list[Artifact]:
        async with session_scope() as db:
            stmt = select(Artifact).order_by(desc(Artifact.created_at)).limit(limit)
            if kind:
                stmt = stmt.where(Artifact.kind == kind)
            return list((await db.execute(stmt)).scalars().all())

    artifacts = _run(fetch())
    if not artifacts:
        console.print("[dim]Nothing generated yet.[/]")
        return

    table = Table(show_header=True, header_style="bold")
    for column in ("ID", "Kind", "Title", "Status", "Created"):
        table.add_column(column)
    for artifact in artifacts:
        table.add_row(
            artifact.id[:8],
            artifact.kind,
            (artifact.title or "")[:50],
            "[green]ready[/]" if artifact.status == "ready" else artifact.status,
            artifact.created_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@app.command("export")
def export_artifact(
    artifact_id: str,
    fmt: list[str] = typer.Option(..., "--format", "-f", help="Format. Repeatable."),
    out: Path = _OUT_OPTION,
) -> None:
    """Export an existing artifact to more formats."""
    from app.db.models import Artifact
    from app.db.session import session_scope
    from app.services import export_service, settings_service

    async def run() -> dict:
        async with session_scope() as db:
            settings = await settings_service.effective_settings(db)
            artifact = await _resolve(db, Artifact, artifact_id)
            return await export_service.export_many(db, artifact, list(fmt), settings=settings)

    out.mkdir(parents=True, exist_ok=True)
    for key, value in _run(run()).items():
        if isinstance(value, str):
            console.print(f"[red]✗[/] {key}: {value}")
            continue
        for record in value:
            destination = out / record.filename
            destination.write_bytes(Path(record.path).read_bytes())
            console.print(f"[green]→[/] {destination}")


@app.command("show")
def show_artifact(artifact_id: str) -> None:
    """Print an artifact's content as JSON."""
    from app.db.models import Artifact
    from app.db.session import session_scope

    async def run() -> dict:
        async with session_scope() as db:
            artifact = await _resolve(db, Artifact, artifact_id)
            return artifact.content

    console.print_json(json.dumps(_run(run()), ensure_ascii=False))


@app.command("formats")
def list_formats(kind: str = typer.Argument(..., help="Artifact kind, e.g. exam.")) -> None:
    """Show the export formats available for an artifact kind."""
    from app.services import export_service

    table = Table(show_header=True, header_style="bold")
    for column in ("Format", "Extension", "Available", "What it is for"):
        table.add_column(column)
    for entry in export_service.available_formats(kind):
        table.add_row(
            entry["key"],
            entry["extension"],
            "[green]yes[/]" if entry["available"] else f"[yellow]{escape(entry['install'])}[/]",
            entry["description"],
        )
    console.print(table)


if __name__ == "__main__":
    app()
