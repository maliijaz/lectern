"""Anki deck export.

Model and deck ids are derived from a hash of the title rather than randomised, so
re-exporting the same deck after an edit *updates* the teacher's existing Anki deck
instead of creating a duplicate beside it. That single detail is the difference between
this being useful over a term and being a nuisance.
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from app.core.capabilities import require
from app.core.logging import get_logger
from app.schemas.study import CardKind, FlashcardDeck

log = get_logger(__name__)

# Anki ids must be in the signed 32-bit-ish range it expects.
_ID_FLOOR = 1 << 30
_ID_RANGE = 1 << 31


def _stable_id(*parts: str) -> int:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).digest()
    return _ID_FLOOR + int.from_bytes(digest[:4], "big") % _ID_RANGE


_CARD_CSS = """
.card {
  font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  font-size: 20px;
  text-align: center;
  color: #1f2937;
  background: #ffffff;
  padding: 24px;
}
.hint { color: #6b7280; font-size: 15px; margin-top: 14px; font-style: italic; }
.example { color: #374151; font-size: 16px; margin-top: 16px; }
.source { color: #9ca3af; font-size: 12px; margin-top: 20px; }
hr#answer { border: none; border-top: 1px solid #e5e7eb; margin: 20px 0; }
"""


def _basic_model(genanki):  # noqa: ANN001, ANN202
    return genanki.Model(
        _stable_id("lectern", "basic"),
        "Lectern — Basic",
        fields=[
            {"name": "Front"},
            {"name": "Back"},
            {"name": "Hint"},
            {"name": "Example"},
            {"name": "Source"},
        ],
        templates=[
            {
                "name": "Recall",
                "qfmt": "{{Front}}{{#Hint}}<div class='hint'>{{Hint}}</div>{{/Hint}}",
                "afmt": (
                    "{{FrontSide}}<hr id='answer'>{{Back}}"
                    "{{#Example}}<div class='example'>{{Example}}</div>{{/Example}}"
                    "{{#Source}}<div class='source'>{{Source}}</div>{{/Source}}"
                ),
            }
        ],
        css=_CARD_CSS,
    )


def _reversed_model(genanki):  # noqa: ANN001, ANN202
    """Two cards per note — term→definition and definition→term."""
    return genanki.Model(
        _stable_id("lectern", "reversed"),
        "Lectern — Reversed",
        fields=[
            {"name": "Front"},
            {"name": "Back"},
            {"name": "Hint"},
            {"name": "Example"},
            {"name": "Source"},
        ],
        templates=[
            {
                "name": "Forward",
                "qfmt": "{{Front}}{{#Hint}}<div class='hint'>{{Hint}}</div>{{/Hint}}",
                "afmt": "{{FrontSide}}<hr id='answer'>{{Back}}",
            },
            {
                "name": "Reverse",
                "qfmt": "{{Back}}",
                "afmt": "{{FrontSide}}<hr id='answer'>{{Front}}",
            },
        ],
        css=_CARD_CSS,
    )


def _cloze_model(genanki):  # noqa: ANN001, ANN202
    return genanki.Model(
        _stable_id("lectern", "cloze"),
        "Lectern — Cloze",
        fields=[{"name": "Text"}, {"name": "Extra"}],
        templates=[
            {
                "name": "Cloze",
                "qfmt": "{{cloze:Text}}",
                "afmt": "{{cloze:Text}}<br>{{Extra}}",
            }
        ],
        css=_CARD_CSS,
        model_type=genanki.Model.CLOZE,
    )


def render(deck: FlashcardDeck, out_path: Path) -> Path:
    """Write an ``.apkg`` file."""
    require("anki")
    import genanki

    basic = _basic_model(genanki)
    reversed_model = _reversed_model(genanki)
    cloze = _cloze_model(genanki)

    anki_deck = genanki.Deck(_stable_id("deck", deck.title), deck.title)

    for card in deck.cards:
        source = card.citation.label() if card.citation else ""
        tags = [_tag(t) for t in [*card.tags, deck.subject, card.difficulty.value] if t]

        if card.kind == CardKind.CLOZE:
            text = card.front if "{{c" in card.front else _auto_cloze(card.front, card.back)
            note = genanki.Note(model=cloze, fields=[text, card.example or source], tags=tags)
        else:
            model = reversed_model if card.kind == CardKind.REVERSED else basic
            note = genanki.Note(
                model=model,
                fields=[card.front, card.back, card.hint, card.example, source],
                tags=tags,
            )
        anki_deck.add_note(note)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(anki_deck).write_to_file(str(out_path))
    log.info("Wrote %d Anki cards to %s", len(deck.cards), out_path.name)
    return out_path


def _auto_cloze(sentence: str, answer: str) -> str:
    """Turn a sentence plus its answer into cloze syntax when the model did not."""
    if answer and answer in sentence:
        return sentence.replace(answer, f"{{{{c1::{answer}}}}}", 1)
    return f"{sentence} {{{{c1::{answer}}}}}" if answer else sentence


def _tag(value: str) -> str:
    """Anki tags cannot contain spaces."""
    return value.strip().replace(" ", "_")


def to_quizlet_tsv(deck: FlashcardDeck) -> str:
    """Quizlet's paste-import format: term, tab, definition, newline."""
    lines = []
    for card in deck.cards:
        front = card.front.replace("\t", " ").replace("\n", " ")
        back = card.back.replace("\t", " ").replace("\n", " ")
        if card.example:
            back += f" — {card.example}"
        lines.append(f"{front}\t{back}")
    return "\n".join(lines) + "\n"


def to_csv(deck: FlashcardDeck) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["front", "back", "hint", "example", "tags", "difficulty", "kind"])
    for card in deck.cards:
        writer.writerow(
            [
                card.front,
                card.back,
                card.hint,
                card.example,
                " ".join(card.tags),
                card.difficulty.value,
                card.kind.value,
            ]
        )
    return buffer.getvalue()
