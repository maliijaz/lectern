"""PowerPoint rendering.

Every slide is drawn onto a blank layout with explicit text boxes rather than filled into
inherited placeholders. Placeholder inheritance is where template-based generators break:
a layout that has two content placeholders in one template has one in another, and text
silently vanishes. Drawing explicitly means the deck looks the same everywhere, and
auto-fit is something we control rather than something PowerPoint guesses at.
"""

from __future__ import annotations

from pathlib import Path

from app.core.capabilities import require
from app.core.logging import get_logger
from app.render.theme import Theme, get_theme
from app.schemas.deck import Deck, Slide, SlideLayout

log = get_logger(__name__)

# 16:9 at 13.333 × 7.5 inches — the modern default.
SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5
MARGIN_IN = 0.7
CONTENT_TOP_IN = 1.75


def find_template(theme_key: str) -> Path | None:
    """An institutional ``.pptx`` to draw onto, if the teacher has supplied one.

    Drop ``templates/pptx/<theme>.pptx`` (or ``default.pptx``) into the repository and the
    school's master slide, fonts and logo are used instead of the built-in theme. See
    ``templates/README.md``.
    """
    from app.config import TEMPLATES_DIR

    for name in (f"{theme_key}.pptx", "default.pptx"):
        candidate = TEMPLATES_DIR / "pptx" / name
        if candidate.exists():
            return candidate
    return None


def render(deck: Deck, out_path: Path, *, template: Path | None = None) -> Path:
    """Write ``deck`` to ``out_path``. Returns the path written."""
    require("pptx")
    from pptx import Presentation
    from pptx.util import Inches

    theme = get_theme(deck.theme)
    template = template or find_template(theme.key)

    if template is not None and template.exists():
        # An institutional template: keep its masters and fonts, draw our content on top.
        presentation = Presentation(str(template))
        log.info("Rendering onto template %s", template.name)
    else:
        presentation = Presentation()
        presentation.slide_width = Inches(SLIDE_WIDTH_IN)
        presentation.slide_height = Inches(SLIDE_HEIGHT_IN)

    blank = _blank_layout(presentation)

    for index, slide in enumerate(deck.slides):
        _render_slide(presentation, blank, slide, theme, index, len(deck.slides), deck)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(out_path))
    log.info("Wrote %d slides to %s", len(deck.slides), out_path.name)
    return out_path


def _blank_layout(presentation):  # noqa: ANN001, ANN202
    """The emptiest layout available — index 6 in the default template."""
    layouts = presentation.slide_layouts
    for index in (6, 5):
        if index < len(layouts):
            return layouts[index]
    return layouts[0]


# --------------------------------------------------------------------------- helpers


def _rgb(hex_colour: str):  # noqa: ANN202
    from pptx.dml.color import RGBColor

    return RGBColor.from_string(hex_colour)


def _set_background(slide, theme: Theme) -> None:  # noqa: ANN001
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(theme.background)


def _textbox(slide, left, top, width, height):  # noqa: ANN001, ANN202
    from pptx.util import Inches

    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    return frame


def _write(
    frame,  # noqa: ANN001
    lines: list[str],
    *,
    theme: Theme,
    size: int,
    colour: str,
    bold: bool = False,
    font: str | None = None,
    bullet: bool = False,
    align: str = "left",
    space_after: int = 10,
    line_spacing: float = 1.1,
) -> None:
    """Fill a text frame. The first line reuses the frame's existing empty paragraph."""
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Pt

    alignment = {
        "left": PP_ALIGN.LEFT,
        "center": PP_ALIGN.CENTER,
        "right": PP_ALIGN.RIGHT,
    }[align]

    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = alignment
        paragraph.space_after = Pt(space_after)
        paragraph.line_spacing = line_spacing

        run = paragraph.add_run()
        # python-pptx has no bullet API; a real bullet glyph is both simpler and more
        # predictable than editing the underlying XML.
        run.text = f"•  {line}" if bullet else line
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = _rgb(colour)
        run.font.name = font or theme.body_font


def _fit_size(items: list[str], base: int, *, floor: int = 12) -> int:
    """Shrink body text as a slide gets fuller, instead of letting it overflow.

    Both the number of bullets and their length matter — six short phrases fit where four
    long ones do not.
    """
    if not items:
        return base
    longest = max(len(item) for item in items)
    size = base
    if len(items) > 5:
        size -= 3
    if len(items) > 7:
        size -= 3
    if longest > 60:
        size -= 2
    if longest > 100:
        size -= 3
    return max(floor, size)


def _accent_rule(slide, theme: Theme, *, top: float = 1.45, width: float = 2.0) -> None:  # noqa: ANN001
    """A short accent bar under the heading — the one decorative element in the deck."""
    from pptx.util import Inches, Pt

    line = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(MARGIN_IN),
        Inches(top),
        Inches(width),
        Pt(4),
    )
    line.fill.solid()
    line.fill.fore_color.rgb = _rgb(theme.accent)
    line.line.fill.background()
    line.shadow.inherit = False


def _heading(slide, text: str, theme: Theme) -> None:  # noqa: ANN001
    frame = _textbox(slide, MARGIN_IN, 0.55, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 1.0)
    size = theme.size_heading if len(text) < 60 else theme.size_heading - 6
    _write(
        frame,
        [text],
        theme=theme,
        size=size,
        colour=theme.title,
        bold=True,
        font=theme.title_font,
        space_after=0,
    )
    _accent_rule(slide, theme)


def _footer(slide, theme: Theme, number: int, total: int, deck: Deck) -> None:  # noqa: ANN001
    frame = _textbox(slide, MARGIN_IN, SLIDE_HEIGHT_IN - 0.55, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 0.35)
    label = deck.title[:60]
    _write(
        frame,
        [f"{label}    ·    {number} / {total}"],
        theme=theme,
        size=theme.size_small - 3,
        colour=theme.muted,
        space_after=0,
    )


def _notes(slide, text: str) -> None:  # noqa: ANN001
    if text.strip():
        slide.notes_slide.notes_text_frame.text = text.strip()


# --------------------------------------------------------------------------- layouts


def _render_slide(  # noqa: PLR0913
    presentation,  # noqa: ANN001
    layout,  # noqa: ANN001
    slide: Slide,
    theme: Theme,
    index: int,
    total: int,
    deck: Deck,
) -> None:
    target = presentation.slides.add_slide(layout)
    _set_background(target, theme)

    renderer = {
        SlideLayout.TITLE: _layout_title,
        SlideLayout.SECTION: _layout_section,
        SlideLayout.TWO_COLUMN: _layout_two_column,
        SlideLayout.QUOTE: _layout_quote,
        SlideLayout.DEFINITION: _layout_definition,
        SlideLayout.TABLE: _layout_table,
        SlideLayout.DIAGRAM: _layout_diagram,
        SlideLayout.IMAGE_TEXT: _layout_image_text,
        SlideLayout.QUIZ: _layout_quiz,
        SlideLayout.EXAMPLE: _layout_bullets,
        SlideLayout.SUMMARY: _layout_bullets,
        SlideLayout.QUESTIONS: _layout_bullets,
    }.get(slide.layout, _layout_bullets)

    renderer(target, slide, theme, deck)
    _notes(target, slide.speaker_notes)

    if slide.layout not in (SlideLayout.TITLE, SlideLayout.SECTION):
        _footer(target, theme, index + 1, total, deck)


def _layout_title(slide, data: Slide, theme: Theme, deck: Deck) -> None:  # noqa: ANN001
    frame = _textbox(slide, MARGIN_IN, 2.5, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 1.6)
    _write(
        frame,
        [data.heading or deck.title],
        theme=theme,
        size=theme.size_title,
        colour=theme.title,
        bold=True,
        font=theme.title_font,
        space_after=6,
    )
    _accent_rule(slide, theme, top=4.25, width=3.0)

    subtitle_lines = [line for line in (data.subheading or deck.subtitle, deck.presenter) if line]
    if subtitle_lines:
        frame = _textbox(slide, MARGIN_IN, 4.5, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 1.2)
        _write(
            frame,
            subtitle_lines,
            theme=theme,
            size=theme.size_body,
            colour=theme.muted,
            space_after=4,
        )


def _layout_section(slide, data: Slide, theme: Theme, _deck: Deck) -> None:  # noqa: ANN001
    frame = _textbox(slide, MARGIN_IN, 3.0, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 1.5)
    _write(
        frame,
        [data.heading],
        theme=theme,
        size=theme.size_title - 4,
        colour=theme.accent,
        bold=True,
        font=theme.title_font,
        align="center",
    )
    if data.subheading:
        frame = _textbox(slide, MARGIN_IN, 4.3, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 0.8)
        _write(
            frame,
            [data.subheading],
            theme=theme,
            size=theme.size_body,
            colour=theme.muted,
            align="center",
        )


def _layout_bullets(slide, data: Slide, theme: Theme, _deck: Deck) -> None:  # noqa: ANN001
    _heading(slide, data.heading, theme)
    if not data.bullets:
        return
    frame = _textbox(
        slide,
        MARGIN_IN,
        CONTENT_TOP_IN,
        SLIDE_WIDTH_IN - 2 * MARGIN_IN,
        SLIDE_HEIGHT_IN - CONTENT_TOP_IN - 0.8,
    )
    _write(
        frame,
        data.bullets,
        theme=theme,
        size=_fit_size(data.bullets, theme.size_body),
        colour=theme.body,
        bullet=True,
        space_after=14,
    )


def _layout_two_column(slide, data: Slide, theme: Theme, _deck: Deck) -> None:  # noqa: ANN001
    _heading(slide, data.heading, theme)
    columns = data.columns[:2]
    if not columns:
        return

    usable = SLIDE_WIDTH_IN - 2 * MARGIN_IN
    column_width = (usable - 0.5) / 2

    for position, column in enumerate(columns):
        left = MARGIN_IN + position * (column_width + 0.5)
        top = CONTENT_TOP_IN

        if column.heading:
            frame = _textbox(slide, left, top, column_width, 0.5)
            _write(
                frame,
                [column.heading],
                theme=theme,
                size=theme.size_body + 2,
                colour=theme.accent if position == 0 else theme.accent_alt,
                bold=True,
                space_after=4,
            )
            top += 0.65

        frame = _textbox(slide, left, top, column_width, SLIDE_HEIGHT_IN - top - 0.8)
        _write(
            frame,
            column.bullets,
            theme=theme,
            size=_fit_size(column.bullets, theme.size_body - 2),
            colour=theme.body,
            bullet=True,
            space_after=10,
        )


def _layout_quote(slide, data: Slide, theme: Theme, _deck: Deck) -> None:  # noqa: ANN001
    frame = _textbox(slide, 1.4, 2.2, SLIDE_WIDTH_IN - 2.8, 2.6)
    _write(
        frame,
        [f"“{data.quote}”"],
        theme=theme,
        size=theme.size_heading - 2,
        colour=theme.title,
        font=theme.title_font,
        align="center",
        line_spacing=1.25,
    )
    if data.quote_attribution:
        frame = _textbox(slide, 1.4, 5.0, SLIDE_WIDTH_IN - 2.8, 0.6)
        _write(
            frame,
            [f"— {data.quote_attribution}"],
            theme=theme,
            size=theme.size_body - 2,
            colour=theme.muted,
            align="center",
        )


def _layout_definition(slide, data: Slide, theme: Theme, _deck: Deck) -> None:  # noqa: ANN001
    from pptx.util import Inches

    _heading(slide, data.heading, theme)

    card = slide.shapes.add_shape(
        1,
        Inches(MARGIN_IN),
        Inches(CONTENT_TOP_IN + 0.2),
        Inches(SLIDE_WIDTH_IN - 2 * MARGIN_IN),
        Inches(2.6),
    )
    card.fill.solid()
    card.fill.fore_color.rgb = _rgb(theme.surface)
    card.line.color.rgb = _rgb(theme.accent)
    card.line.width = Inches(0.02)
    card.shadow.inherit = False

    frame = card.text_frame
    frame.word_wrap = True
    frame.margin_left = Inches(0.4)
    frame.margin_top = Inches(0.3)
    _write(
        frame,
        data.bullets or [data.subheading],
        theme=theme,
        size=theme.size_body + 2,
        colour=theme.body,
        line_spacing=1.3,
    )


def _layout_table(slide, data: Slide, theme: Theme, _deck: Deck) -> None:  # noqa: ANN001
    from pptx.util import Inches, Pt

    _heading(slide, data.heading, theme)
    rows = _parse_markdown_table(data.table_markdown)
    if not rows:
        _layout_bullets(slide, data, theme, _deck)
        return

    row_count, column_count = len(rows), max(len(r) for r in rows)
    height = min(SLIDE_HEIGHT_IN - CONTENT_TOP_IN - 0.8, 0.45 * row_count + 0.2)
    shape = slide.shapes.add_table(
        row_count,
        column_count,
        Inches(MARGIN_IN),
        Inches(CONTENT_TOP_IN),
        Inches(SLIDE_WIDTH_IN - 2 * MARGIN_IN),
        Inches(height),
    )
    table = shape.table
    font_size = Pt(max(11, theme.size_body - 4 - max(0, column_count - 4)))

    for row_index, row in enumerate(rows):
        for column_index in range(column_count):
            cell = table.cell(row_index, column_index)
            cell.text = row[column_index] if column_index < len(row) else ""
            is_header = row_index == 0
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(theme.accent if is_header else theme.background)
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = font_size
                    run.font.bold = is_header
                    run.font.name = theme.body_font
                    run.font.color.rgb = _rgb(theme.background if is_header else theme.body)


def _layout_diagram(slide, data: Slide, theme: Theme, deck: Deck) -> None:  # noqa: ANN001
    """Place a pre-rendered figure image, or fall back to describing it.

    Image generation happens before rendering (see :mod:`app.media.diagrams`) and the
    result is stashed on ``figure.extra_image``. A deck rendered without the media extras
    still makes sense: the alt text becomes visible content rather than an empty slide.
    """
    _heading(slide, data.heading, theme)
    figure = data.figure
    image_path = getattr(figure, "_rendered_path", None) if figure else None

    if image_path and Path(image_path).exists():
        from pptx.util import Inches

        slide.shapes.add_picture(
            str(image_path),
            Inches(MARGIN_IN + 1.0),
            Inches(CONTENT_TOP_IN),
            height=Inches(SLIDE_HEIGHT_IN - CONTENT_TOP_IN - 1.0),
        )
        if figure and figure.caption:
            frame = _textbox(
                slide, MARGIN_IN, SLIDE_HEIGHT_IN - 1.0, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 0.4
            )
            _write(
                frame,
                [figure.caption],
                theme=theme,
                size=theme.size_small,
                colour=theme.muted,
                align="center",
            )
        return

    description = [figure.alt_text or figure.caption] if figure else []
    _write(
        _textbox(slide, MARGIN_IN, CONTENT_TOP_IN, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 3.0),
        [line for line in (description + data.bullets) if line],
        theme=theme,
        size=theme.size_body,
        colour=theme.body,
        bullet=True,
    )


def _layout_image_text(slide, data: Slide, theme: Theme, deck: Deck) -> None:  # noqa: ANN001
    _heading(slide, data.heading, theme)
    figure = data.figure
    image_path = getattr(figure, "_rendered_path", None) if figure else None
    half = (SLIDE_WIDTH_IN - 2 * MARGIN_IN - 0.5) / 2

    if image_path and Path(image_path).exists():
        from pptx.util import Inches

        slide.shapes.add_picture(
            str(image_path),
            Inches(MARGIN_IN),
            Inches(CONTENT_TOP_IN),
            width=Inches(half),
        )
        text_left = MARGIN_IN + half + 0.5
    else:
        text_left = MARGIN_IN

    frame = _textbox(
        slide,
        text_left,
        CONTENT_TOP_IN,
        SLIDE_WIDTH_IN - text_left - MARGIN_IN,
        SLIDE_HEIGHT_IN - CONTENT_TOP_IN - 0.8,
    )
    _write(
        frame,
        data.bullets,
        theme=theme,
        size=_fit_size(data.bullets, theme.size_body),
        colour=theme.body,
        bullet=True,
        space_after=12,
    )


def _layout_quiz(slide, data: Slide, theme: Theme, _deck: Deck) -> None:  # noqa: ANN001
    from pptx.util import Inches

    badge = slide.shapes.add_shape(1, Inches(MARGIN_IN), Inches(0.35), Inches(1.6), Inches(0.38))
    badge.fill.solid()
    badge.fill.fore_color.rgb = _rgb(theme.accent)
    badge.line.fill.background()
    badge.shadow.inherit = False
    _write(
        badge.text_frame,
        ["CHECK YOUR UNDERSTANDING"],
        theme=theme,
        size=9,
        colour=theme.background,
        bold=True,
        align="center",
        space_after=0,
    )

    frame = _textbox(slide, MARGIN_IN, 1.05, SLIDE_WIDTH_IN - 2 * MARGIN_IN, 1.2)
    _write(
        frame,
        [data.heading],
        theme=theme,
        size=theme.size_heading - 4,
        colour=theme.title,
        bold=True,
        font=theme.title_font,
    )

    if data.bullets:
        frame = _textbox(slide, MARGIN_IN + 0.4, 2.8, SLIDE_WIDTH_IN - 2 * MARGIN_IN - 0.4, 3.4)
        _write(
            frame,
            [f"{chr(ord('A') + i)}.  {option}" for i, option in enumerate(data.bullets)],
            theme=theme,
            size=_fit_size(data.bullets, theme.size_body),
            colour=theme.body,
            space_after=16,
        )


def _parse_markdown_table(markdown: str) -> list[list[str]]:
    """Parse a pipe table, skipping the ``|---|`` separator row."""
    rows: list[list[str]] = []
    for line in markdown.strip().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells if c):
            continue
        rows.append(cells)
    return rows
