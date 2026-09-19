"""Offline narration with Piper.

Produces one audio file per slide plus a plain-text script, packaged as a zip. That shape
rather than audio embedded in the ``.pptx`` is deliberate: PowerPoint's per-slide audio is
awkward to work with programmatically and worse to edit afterwards, whereas separate files
are immediately useful — a teacher can drop them into a screen recording, hand them to a
student who missed the lesson, or publish them as a podcast of the unit.

Piper is optional. Without it the script is still produced, which is most of the value.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from app.core.logging import get_logger
from app.schemas.deck import Deck, SlideLayout

log = get_logger(__name__)

#: Voices are ~60 MB ONNX files downloaded separately; see the README.
VOICE_ENV = "LECTERN_PIPER_VOICE"


def available() -> bool:
    return shutil.which("piper") is not None


def _voice_model() -> Path | None:
    """Find a Piper voice: the env var if set, otherwise any .onnx beside the binary."""
    import os

    if configured := os.getenv(VOICE_ENV):
        path = Path(configured)
        return path if path.exists() else None

    piper = shutil.which("piper")
    if piper is None:
        return None
    for candidate in sorted(Path(piper).parent.glob("*.onnx")):
        return candidate
    return None


def narration_script(deck: Deck) -> list[tuple[int, str, str]]:
    """The spoken script per slide: (number, heading, what to say).

    Speaker notes are what the teacher would actually say, so they are the narration.
    Where a slide has none, the bullets are read out as a fallback rather than leaving
    silence.
    """
    script: list[tuple[int, str, str]] = []
    for index, slide in enumerate(deck.slides, start=1):
        spoken = slide.speaker_notes.strip()
        if not spoken:
            if slide.layout == SlideLayout.TITLE:
                spoken = f"{deck.title}. {deck.subtitle}".strip(". ")
            elif slide.bullets:
                spoken = f"{slide.heading}. " + ". ".join(slide.bullets)
            else:
                spoken = slide.heading
        script.append((index, slide.heading or f"Slide {index}", _speakable(spoken)))
    return script


def _speakable(text: str) -> str:
    """Strip markup a speech engine would read aloud as punctuation noise."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def synthesize(text: str, out_path: Path) -> Path | None:
    """Render one utterance to a WAV, or return None if Piper is unavailable."""
    piper = shutil.which("piper")
    voice = _voice_model()
    if piper is None or voice is None:
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(  # noqa: S603 — fixed executable, text on stdin
            [piper, "--model", str(voice), "--output_file", str(out_path)],
            input=text,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("Piper failed: %s", exc)
        return None

    if result.returncode != 0 or not out_path.exists():
        log.info("Piper failed: %s", (result.stderr or "")[:300])
        return None
    return out_path


def render_narration(deck: Deck, out_path: Path) -> Path:
    """Package the narration script, and the audio where Piper is installed, as a zip.

    Always produces a usable file: without Piper it holds the script alone, with a note
    saying how to add the audio.
    """
    script = narration_script(deck)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text_lines = [f"{deck.title}", "=" * len(deck.title), ""]
    for number, heading, spoken in script:
        text_lines += [f"[Slide {number}] {heading}", spoken, ""]

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        produced: list[Path] = []

        if available() and _voice_model() is not None:
            for number, _heading, spoken in script:
                audio = synthesize(spoken, tmpdir / f"slide-{number:02d}.wav")
                if audio is not None:
                    produced.append(audio)
            log.info("Synthesised %d of %d slides", len(produced), len(script))
        else:
            text_lines += [
                "",
                "--",
                "Audio was not generated because Piper is not installed, or no voice model",
                "was found. Install Piper and a voice, set LECTERN_PIPER_VOICE to the .onnx file,",
                "then export the narration again.",
            ]

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("narration-script.txt", "\n".join(text_lines))
            for audio in produced:
                archive.write(audio, arcname=audio.name)

    return out_path
