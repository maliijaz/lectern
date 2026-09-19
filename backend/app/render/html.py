"""Self-contained HTML output.

One file with its CSS inlined, so a teacher can email it, open it offline, or upload it to
an LMS page without anything breaking. Print styles are included because the most common
thing anyone does with an HTML worksheet is print it.
"""

from __future__ import annotations

from html import escape

from app.render.theme import get_theme

_PRINT_CSS = """
@media print {
  body { padding: 0; max-width: none; }
  a { color: inherit; text-decoration: none; }
  h1, h2, h3 { break-after: avoid; }
  table, blockquote, .callout { break-inside: avoid; }
  .no-print { display: none; }
}
"""


def _stylesheet(theme_key: str) -> str:
    theme = get_theme(theme_key)
    return f"""
:root {{
  --bg: #{theme.background};
  --surface: #{theme.surface};
  --title: #{theme.title};
  --body: #{theme.body};
  --muted: #{theme.muted};
  --accent: #{theme.accent};
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0 auto; padding: 2.5rem 1.25rem; max-width: 46rem;
  font-family: {theme.body_font}, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 16px; line-height: 1.65;
  color: var(--body); background: var(--bg);
}}
h1, h2, h3, h4 {{
  font-family: {theme.title_font}, Georgia, serif;
  color: var(--title); line-height: 1.25; margin: 2rem 0 .6rem;
}}
h1 {{ font-size: 2rem; margin-top: 0; border-bottom: 3px solid var(--accent); padding-bottom: .4rem; }}
h2 {{ font-size: 1.4rem; }}
h3 {{ font-size: 1.15rem; }}
p, li {{ margin: .5rem 0; }}
ul, ol {{ padding-left: 1.4rem; }}
a {{ color: var(--accent); }}
code {{
  font-family: {theme.mono_font}, monospace; font-size: .9em;
  background: var(--surface); padding: .12em .35em; border-radius: 3px;
}}
pre {{ background: var(--surface); padding: 1rem; border-radius: 6px; overflow-x: auto; }}
blockquote, .callout {{
  margin: 1rem 0; padding: .8rem 1rem;
  background: var(--surface); border-left: 3px solid var(--accent); border-radius: 0 4px 4px 0;
}}
blockquote > :first-child, .callout > :first-child {{ margin-top: 0; }}
blockquote > :last-child, .callout > :last-child {{ margin-bottom: 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 1.2rem 0; font-size: .95em; }}
th, td {{ border: 1px solid color-mix(in srgb, var(--muted) 40%, transparent); padding: .5rem .65rem; text-align: left; vertical-align: top; }}
th {{ background: var(--surface); font-weight: 600; }}
hr {{ border: none; border-top: 1px solid color-mix(in srgb, var(--muted) 35%, transparent); margin: 2rem 0; }}
details {{ margin: 1rem 0; padding: .6rem .9rem; background: var(--surface); border-radius: 4px; }}
summary {{ cursor: pointer; font-weight: 600; }}
sub {{ color: var(--muted); font-size: .78em; }}
.meta {{ color: var(--muted); font-size: .9em; }}
{_PRINT_CSS}
"""


def markdown_to_html(markdown: str) -> str:
    """Markdown body to an HTML fragment."""
    try:
        from markdown_it import MarkdownIt

        parser = MarkdownIt("commonmark", {"html": True, "linkify": True, "typographer": True})
        parser.enable(["table", "strikethrough"])
        return parser.render(markdown)
    except ImportError:  # pragma: no cover — markdown-it is a core dependency
        return "<pre>" + escape(markdown) + "</pre>"


def render(
    markdown: str,
    *,
    title: str,
    subtitle: str = "",
    theme_key: str = "academic",
) -> str:
    """A complete, standalone HTML document."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{_stylesheet(theme_key)}</style>
</head>
<body>
{f'<p class="meta">{escape(subtitle)}</p>' if subtitle else ""}
{markdown_to_html(markdown)}
</body>
</html>
"""


def render_reveal(markdown: str, *, title: str, theme_key: str = "academic") -> str:
    """A reveal.js deck from the slide Markdown, for presenting in a browser.

    reveal.js is loaded from a CDN, so this one output needs a network connection —
    everything else the product produces works offline. The ``.pptx`` remains the offline
    path, and the UI says so.
    """
    theme = get_theme(theme_key)
    sections = [
        f"<section data-markdown><textarea data-template>\n{chunk.strip()}\n</textarea></section>"
        for chunk in markdown.split("\n---\n")
        if chunk.strip()
    ]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{escape(title)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/theme/{"black" if theme.dark else "white"}.css">
<style>
  .reveal {{ font-family: {theme.body_font}, sans-serif; }}
  .reveal h1, .reveal h2, .reveal h3 {{
    font-family: {theme.title_font}, Georgia, serif;
    color: #{theme.title}; text-transform: none;
  }}
  .reveal section {{ text-align: left; }}
</style>
</head>
<body>
<div class="reveal"><div class="slides">
{chr(10).join(sections)}
</div></div>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.js"></script>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@5/plugin/markdown/markdown.js"></script>
<script>Reveal.initialize({{ plugins: [ RevealMarkdown ], hash: true, slideNumber: 'c/t' }});</script>
</body>
</html>
"""
