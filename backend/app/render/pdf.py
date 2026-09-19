"""PDF rendering through Typst.

Typst was chosen over LaTeX and over HTML-to-PDF for concrete reasons: it compiles a
document in a few hundred milliseconds instead of several seconds, the whole toolchain is
a 30 MB Python wheel rather than a 1 GB TeX installation, and unlike WeasyPrint it has real
typesetting — proper page breaks, running headers, and widow control, which matter when a
teacher prints thirty copies of an exam.

Every artifact reaches PDF through the same route: schema → Markdown (``app.render.markdown``)
→ Typst → PDF. One converter, tested once, serves every document type.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.capabilities import require
from app.core.logging import get_logger
from app.render.theme import Theme, get_theme

log = get_logger(__name__)

_INLINE_RE = re.compile(r"(\*\*.+?\*\*|(?<!\*)\*[^*\n]+?\*(?!\*)|`[^`\n]+?`)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_RULE_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_SEPARATOR_RE = re.compile(r"^[\s:|-]+$")
_HTML_TAG_RE = re.compile(r"</?(?:details|summary|sub|sup|br|b|i|em|strong)\s*/?>")


def escape(text: str) -> str:
    """Escape Typst's markup characters so model prose cannot break the document."""
    out = text.replace("\\", "\\\\")
    for char in ("#", "$", "@", "<", ">", "*", "_", "`", "[", "]"):
        out = out.replace(char, f"\\{char}")
    return out


def _inline(text: str) -> str:
    """Convert inline Markdown emphasis to Typst, escaping everything else."""
    pieces: list[str] = []
    for piece in _INLINE_RE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            pieces.append(f"*{escape(piece[2:-2])}*")
        elif piece.startswith("*") and piece.endswith("*"):
            pieces.append(f"_{escape(piece[1:-1])}_")
        elif piece.startswith("`") and piece.endswith("`"):
            pieces.append(f"`{piece[1:-1]}`")
        else:
            pieces.append(escape(piece))
    return "".join(pieces)


def markdown_to_typst(markdown: str) -> str:
    """Convert the Markdown subset our renderers emit into Typst body markup."""
    lines = _HTML_TAG_RE.sub("", markdown).splitlines()
    out: list[str] = []
    index = 0
    in_code = False

    while index < len(lines):
        line = lines[index].rstrip()

        if line.strip().startswith("```"):
            in_code = not in_code
            out.append("```" if in_code else "```")
            index += 1
            continue
        if in_code:
            out.append(line)
            index += 1
            continue

        if not line.strip():
            out.append("")
            index += 1
            continue

        if _RULE_RE.match(line):
            out.append("#line(length: 100%, stroke: 0.5pt + luma(200))")
            index += 1
            continue

        if table := _consume_table(lines, index):
            block, index = table
            out.append(block)
            continue

        if heading := _HEADING_RE.match(line):
            level = len(heading.group(1))
            out.append(f"{'=' * level} {_inline(heading.group(2))}")
            index += 1
            continue

        if bullet := _BULLET_RE.match(line):
            depth = len(bullet.group(1)) // 2
            out.append(f"{'  ' * depth}- {_inline(bullet.group(2))}")
            index += 1
            continue

        if numbered := _NUMBERED_RE.match(line):
            depth = len(numbered.group(1)) // 2
            out.append(f"{'  ' * depth}+ {_inline(numbered.group(2))}")
            index += 1
            continue

        if quote := _QUOTE_RE.match(line):
            out.append(f"#callout[{_inline(quote.group(1))}]")
            index += 1
            continue

        out.append(_inline(line))
        index += 1

    return "\n".join(out)


def _consume_table(lines: list[str], start: int) -> tuple[str, int] | None:
    """If a Markdown pipe table begins at ``start``, convert it and report where it ended."""
    if not _TABLE_ROW_RE.match(lines[start]):
        return None

    rows: list[list[str]] = []
    index = start
    while index < len(lines) and _TABLE_ROW_RE.match(lines[index]):
        inner = _TABLE_ROW_RE.match(lines[index]).group(1)
        if not _SEPARATOR_RE.match(inner):
            rows.append([c.strip() for c in inner.split("|")])
        index += 1

    if not rows:
        return None

    columns = max(len(r) for r in rows)
    cells: list[str] = []
    for row_index, row in enumerate(rows):
        padded = row + [""] * (columns - len(row))
        for cell in padded:
            body = _inline(cell.replace("<br>", " ").replace("<br/>", " "))
            cells.append(f"  [{'*' + body + '*' if row_index == 0 else body}],")

    block = (
        f"#table(\n"
        f"  columns: {columns},\n"
        f"  stroke: 0.5pt + luma(200),\n"
        f"  inset: 6pt,\n"
        f"  fill: (_, row) => if row == 0 {{ luma(238) }} else {{ white }},\n"
        + "\n".join(cells)
        + "\n)"
    )
    return block, index


def _preamble(
    theme: Theme,
    *,
    title: str,
    subtitle: str,
    footer: str,
    landscape: bool,
    page_numbers: bool,
) -> str:
    """Page setup, fonts and the helpers the body markup calls."""
    # Each entry carries its own trailing comma so they can be composed in any combination.
    page_options = ['paper: "a4"', "margin: (x: 2cm, y: 2cm)"]
    if page_numbers:
        page_options += ['numbering: "1 / 1"', "number-align: center"]
    else:
        page_options.append("numbering: none")
    if landscape:
        page_options.append("flipped: true")
    page_setup = ",\n  ".join(page_options)
    header = (
        f"#set page(header: context {{ if counter(page).get().first() > 1 [ "
        f'#set text(8pt, fill: rgb("#{theme.muted}")); {_typst_string(footer)} '
        f"#h(1fr) {_typst_string(title)} ] }})"
        if footer or title
        else ""
    )

    return f"""\
#set document(title: {_typst_string(title)})
#set page(
  {page_setup}
)
#set text(font: ("{theme.body_font}", "Liberation Sans", "DejaVu Sans"), size: 10.5pt, fill: rgb("#{theme.body}"))
#set par(justify: false, leading: 0.65em, spacing: 1.1em)
#set heading(numbering: none)

#show heading.where(level: 1): it => block(
  above: 1.4em, below: 0.7em,
  text(size: 17pt, weight: "bold", fill: rgb("#{theme.title}"), it.body)
)
#show heading.where(level: 2): it => block(
  above: 1.1em, below: 0.5em,
  text(size: 13pt, weight: "bold", fill: rgb("#{theme.title}"), it.body)
)
#show heading.where(level: 3): it => block(
  above: 0.9em, below: 0.4em,
  text(size: 11.5pt, weight: "bold", fill: rgb("#{theme.body}"), it.body)
)
#show link: it => text(fill: rgb("#{theme.accent}"), it)
#show raw: it => text(font: ("{theme.mono_font}", "DejaVu Sans Mono"), size: 9.5pt, it)

#let callout(body) = block(
  width: 100%, inset: 9pt, radius: 3pt,
  fill: rgb("#{theme.surface}"),
  stroke: (left: 2.5pt + rgb("#{theme.accent}")),
  body
)

{header}

#block(below: 1.6em)[
  #text(size: 22pt, weight: "bold", fill: rgb("#{theme.title}"), {_typst_string(title)})
  {f'#linebreak() #text(size: 11pt, fill: rgb("#{theme.muted}"), {_typst_string(subtitle)})' if subtitle else ""}
  #v(0.4em)
  #line(length: 100%, stroke: 1.5pt + rgb("#{theme.accent}"))
]
"""


def _typst_string(value: str) -> str:
    """A Typst string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_typst(
    markdown: str,
    *,
    title: str,
    subtitle: str = "",
    footer: str = "",
    theme_key: str = "academic",
    landscape: bool = False,
    page_numbers: bool = True,
    strip_leading_title: bool = True,
) -> str:
    """Assemble a complete Typst document from Markdown."""
    theme = get_theme(theme_key)

    body = markdown
    if strip_leading_title:
        # The preamble already prints the title; keeping the Markdown H1 duplicates it.
        body = re.sub(r"^#\s+.*\n+", "", body, count=1)
        if subtitle:
            body = re.sub(r"^\*[^*\n]+\*\n+", "", body, count=1)

    return (
        _preamble(
            theme,
            title=title,
            subtitle=subtitle,
            footer=footer,
            landscape=landscape,
            page_numbers=page_numbers,
        )
        + "\n"
        + markdown_to_typst(body)
        + "\n"
    )


def render_markdown_to_pdf(
    markdown: str,
    out_path: Path,
    *,
    title: str,
    subtitle: str = "",
    footer: str = "",
    theme_key: str = "academic",
    landscape: bool = False,
    page_numbers: bool = True,
) -> Path:
    """Compile Markdown to a PDF. Returns the path written."""
    require("pdf")
    import typst

    source = build_typst(
        markdown,
        title=title,
        subtitle=subtitle,
        footer=footer,
        theme_key=theme_key,
        landscape=landscape,
        page_numbers=page_numbers,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    typst_path = out_path.with_suffix(".typ")
    typst_path.write_text(source, encoding="utf-8")

    try:
        typst.compile(str(typst_path), output=str(out_path))
    except Exception as exc:
        # Keep the .typ file on failure — it is the only way to debug a compile error.
        log.error("Typst compilation failed for %s: %s", out_path.name, exc)
        raise
    else:
        typst_path.unlink(missing_ok=True)

    log.info("Wrote %s (%d KB)", out_path.name, out_path.stat().st_size // 1024)
    return out_path
