"""Which optional dependencies are actually installed.

The product degrades feature by feature rather than failing to start: without Docling you
can still generate from a topic, without matplotlib you get slides with no charts. The UI
reads this report to grey out what is unavailable and show the exact install command.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    #: What stops working without it.
    enables: str
    #: Shell command that installs it.
    install: str
    modules: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()
    extras: dict[str, Any] = field(default_factory=dict)

    def available(self) -> bool:
        mods = all(importlib.util.find_spec(m) is not None for m in self.modules)
        exes = all(shutil.which(e) is not None for e in self.executables)
        return mods and exes


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="ingest",
        label="Document parsing",
        enables="Uploading PDFs, Word and PowerPoint files as source material",
        install="pip install -e .[ingest]",
        modules=("docling",),
    ),
    Capability(
        key="retrieval",
        label="Semantic search",
        enables="Grounding generated content in your documents with citations",
        install="pip install -e .[ingest]",
        modules=("chromadb", "sentence_transformers"),
    ),
    Capability(
        key="pptx",
        label="PowerPoint export",
        enables="Exporting slide decks as .pptx",
        install="pip install python-pptx",
        modules=("pptx",),
    ),
    Capability(
        key="docx",
        label="Word export",
        enables="Exporting notes and papers as .docx",
        install="pip install python-docx",
        modules=("docx",),
    ),
    Capability(
        key="pdf",
        label="PDF export",
        enables="Exporting papers, notes and worksheets as print-ready PDF",
        install="pip install typst",
        modules=("typst",),
    ),
    Capability(
        key="anki",
        label="Anki export",
        enables="Exporting flashcards as an .apkg deck",
        install="pip install genanki",
        modules=("genanki",),
    ),
    Capability(
        key="charts",
        label="Charts and figures",
        enables="Generated charts and figures inside slides and notes",
        install="pip install -e .[media]",
        modules=("matplotlib", "PIL"),
    ),
    Capability(
        key="diagrams",
        label="Diagrams",
        enables="Mermaid flowcharts and concept maps on slides",
        install="npm install -g @mermaid-js/mermaid-cli",
        executables=("mmdc",),
    ),
    Capability(
        key="tts",
        label="Narration audio",
        # The narration *script* is always produced; only the spoken audio needs Piper.
        enables="Spoken audio for each slide (the written script works without it)",
        install="Install Piper and a voice, then set TA_PIPER_VOICE to the .onnx file",
        executables=("piper",),
    ),
)


@lru_cache
def _cached_report() -> dict[str, dict[str, Any]]:
    return {
        cap.key: {
            "label": cap.label,
            "available": cap.available(),
            "enables": cap.enables,
            "install": cap.install,
        }
        for cap in CAPABILITIES
    }


def capability_report(refresh: bool = False) -> dict[str, dict[str, Any]]:
    if refresh:
        _cached_report.cache_clear()
    return _cached_report()


def has(key: str) -> bool:
    entry = capability_report().get(key)
    return bool(entry and entry["available"])


def require(key: str) -> None:
    """Raise a user-facing error naming the install command if a capability is missing."""
    from app.core.errors import DependencyMissing

    entry = capability_report().get(key)
    if entry is None:
        raise DependencyMissing(f"Unknown capability {key!r}")
    if not entry["available"]:
        raise DependencyMissing(
            f"{entry['label']} is not installed. It enables: {entry['enables']}.",
            install=entry["install"],
        )
