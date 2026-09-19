"""Narration: the script is always produced; audio is a bonus when Piper is installed."""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.media import tts
from app.schemas.deck import Deck, Slide, SlideLayout


def _deck() -> Deck:
    return Deck(
        title="Photosynthesis",
        subtitle="Biology · Grade 10",
        slides=[
            Slide(layout=SlideLayout.TITLE, heading="Photosynthesis"),
            Slide(
                layout=SlideLayout.BULLETS,
                heading="Light reactions",
                bullets=["Photons excite electrons", "Water is split"],
                speaker_notes="Explain that **chlorophyll** absorbs light, then ask `why green`.",
            ),
            Slide(
                layout=SlideLayout.BULLETS,
                heading="The Calvin cycle",
                bullets=["Carbon is fixed", "Glucose is built"],
            ),
        ],
    )


def test_script_covers_every_slide() -> None:
    script = tts.narration_script(_deck())
    assert len(script) == 3
    assert [number for number, _, _ in script] == [1, 2, 3]


def test_speaker_notes_are_the_narration() -> None:
    script = tts.narration_script(_deck())
    assert "chlorophyll absorbs light" in script[1][2]


def test_markup_is_stripped_so_it_is_not_read_aloud() -> None:
    script = tts.narration_script(_deck())
    spoken = script[1][2]
    assert "**" not in spoken
    assert "`" not in spoken


def test_slides_without_notes_fall_back_to_their_bullets() -> None:
    """Silence on a slide is worse than reading the bullets."""
    script = tts.narration_script(_deck())
    assert "Carbon is fixed" in script[2][2]
    # The title slide speaks the deck title and subtitle.
    assert "Photosynthesis" in script[0][2]


def test_narration_zip_always_contains_a_script(tmp_path: Path) -> None:
    out = tts.render_narration(_deck(), tmp_path / "narration.zip")
    assert out.exists()

    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        assert "narration-script.txt" in names
        text = archive.read("narration-script.txt").decode()

    assert "[Slide 1]" in text
    assert "[Slide 3]" in text
    assert "chlorophyll absorbs light" in text

    if not tts.available():
        # It must say why there is no audio rather than leaving the teacher guessing.
        assert "Piper is not installed" in text
        assert not [n for n in names if n.endswith(".wav")]


def test_narration_is_offered_only_for_slide_decks() -> None:
    from app.services import export_service

    assert any(f["key"] == "narration" for f in export_service.available_formats("slides"))
    assert not any(f["key"] == "narration" for f in export_service.available_formats("exam"))
