"""Visual themes for rendered output.

Themes are defined in code rather than shipped as binary ``.pptx`` template files. That
keeps the repository text-only and diffable, lets a teacher add a school theme by editing
one dict, and avoids the placeholder-mapping problems that come with reusing someone
else's template. A teacher who *does* have an institutional template can still supply it —
see ``app.render.pptx.render`` — and its master will be used instead.

Colours are chosen for projector legibility: high contrast, no thin light-on-light text,
and accents that survive a washed-out classroom projector.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    description: str

    background: str  # slide background
    surface: str  # cards, table fills, callout boxes
    title: str  # headings
    body: str  # body text
    muted: str  # captions, footers, page numbers
    accent: str  # rules, bullets, emphasis
    accent_alt: str  # second accent for two-column and charts

    title_font: str = "Calibri Light"
    body_font: str = "Calibri"
    mono_font: str = "Consolas"

    #: Point sizes, largest to smallest. Bullet text steps down as a slide gets fuller.
    size_title: int = 40
    size_heading: int = 30
    size_body: int = 20
    size_small: int = 14

    dark: bool = False
    chart_palette: tuple[str, ...] = field(
        default_factory=lambda: ("2563EB", "DC2626", "059669", "D97706", "7C3AED", "0891B2")
    )


THEMES: dict[str, Theme] = {
    "academic": Theme(
        key="academic",
        name="Academic",
        description="Navy and warm grey. Serious, prints well, reads from the back row.",
        background="FFFFFF",
        surface="F1F5F9",
        title="1E3A5F",
        body="1F2937",
        muted="64748B",
        accent="2563EB",
        accent_alt="0891B2",
        title_font="Georgia",
        body_font="Calibri",
    ),
    "minimal": Theme(
        key="minimal",
        name="Minimal",
        description="Black on white with a single accent. Nothing competes with the content.",
        background="FFFFFF",
        surface="F5F5F5",
        title="111111",
        body="2B2B2B",
        muted="767676",
        accent="E11D48",
        accent_alt="111111",
        title_font="Calibri Light",
        body_font="Calibri",
    ),
    "chalkboard": Theme(
        key="chalkboard",
        name="Chalkboard",
        description="Dark slate with chalk tones. Easy on the eyes in a darkened room.",
        background="1B2B2A",
        surface="243A38",
        title="F5F5DC",
        body="E8E6DC",
        muted="9BB0AD",
        accent="7FD1AE",
        accent_alt="F2C14E",
        dark=True,
        chart_palette=("7FD1AE", "F2C14E", "EF8354", "8AB6D6", "C98BDB", "E5E5E5"),
    ),
    "warm": Theme(
        key="warm",
        name="Warm",
        description="Cream and terracotta. Friendly — suits primary and middle years.",
        background="FDF8F3",
        surface="F5E9DD",
        title="8C3B1E",
        body="3B2F2A",
        muted="8A7A6F",
        accent="C75B39",
        accent_alt="3F7A6D",
        title_font="Georgia",
        body_font="Calibri",
    ),
    "high_contrast": Theme(
        key="high_contrast",
        name="High contrast",
        description="Maximum legibility. Built for large rooms and low-vision readers.",
        background="FFFFFF",
        surface="EEEEEE",
        title="000000",
        body="000000",
        muted="3A3A3A",
        accent="0000CC",
        accent_alt="AA0000",
        size_title=44,
        size_heading=34,
        size_body=24,
        size_small=16,
    ),
}

DEFAULT_THEME = "academic"


def get_theme(key: str | None) -> Theme:
    return THEMES.get((key or "").lower(), THEMES[DEFAULT_THEME])


def theme_list() -> list[dict[str, str]]:
    """Themes as the UI picker needs them."""
    return [
        {
            "key": t.key,
            "name": t.name,
            "description": t.description,
            "accent": f"#{t.accent}",
            "background": f"#{t.background}",
            "dark": str(t.dark).lower(),
        }
        for t in THEMES.values()
    ]
