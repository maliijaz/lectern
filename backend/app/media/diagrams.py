"""Turning a figure spec into an actual image.

The model describes a visual — Mermaid source for a flowchart, a small spec for a chart —
and this module renders it to PNG so the slide renderer has something to place. Both
backends are optional: without them a deck still renders, with the figure's alt text shown
as content rather than an empty box.

Images are cached by a hash of their source, so re-exporting a deck after editing one
bullet does not re-render every diagram.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.logging import get_logger
from app.schemas.common import Figure

log = get_logger(__name__)

#: Rendered wide enough to stay sharp on a projector without bloating the .pptx.
WIDTH_PX = 1600
HEIGHT_PX = 1000

#: Matches the chart palette in app.render.theme so figures do not clash with the deck.
CHART_COLOURS = ["#2563EB", "#DC2626", "#059669", "#D97706", "#7C3AED", "#0891B2"]


def _cache_key(kind: str, spec: str, theme_key: str) -> str:
    digest = hashlib.sha256(f"{kind}::{theme_key}::{spec}".encode()).hexdigest()
    return f"{kind}-{digest[:16]}.png"


def render_figure(figure: Figure, cache_dir: Path, *, theme_key: str = "academic") -> Path | None:
    """Render ``figure`` to a PNG, or return None if it cannot be rendered.

    Returning None rather than raising is deliberate: a missing diagram should cost one
    slide's illustration, never the whole export.
    """
    if not figure.spec.strip():
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / _cache_key(figure.kind, figure.spec, theme_key)
    if target.exists():
        return target

    try:
        if figure.kind == "chart":
            return _render_chart(figure.spec, target)
        if figure.kind in ("diagram", "timeline"):
            return _render_mermaid(figure.spec, target, theme_key)
    except Exception:
        log.warning("Could not render a %s figure; falling back to its alt text", figure.kind)
        log.debug("Figure render failure", exc_info=True)
    return None


def _render_mermaid(spec: str, target: Path, theme_key: str) -> Path | None:
    """Render Mermaid source with the Mermaid CLI, if it is installed."""
    mmdc = shutil.which("mmdc")
    if mmdc is None:
        log.info("mmdc is not installed; skipping the diagram")
        return None

    from app.render.theme import get_theme

    theme = get_theme(theme_key)
    config = {
        "theme": "base",
        "themeVariables": {
            "primaryColor": f"#{theme.surface}",
            "primaryTextColor": f"#{theme.body}",
            "primaryBorderColor": f"#{theme.accent}",
            "lineColor": f"#{theme.muted}",
            "fontFamily": theme.body_font,
            "fontSize": "18px",
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        source = tmpdir / "diagram.mmd"
        source.write_text(spec, encoding="utf-8")
        config_path = tmpdir / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        result = subprocess.run(  # noqa: S603 — fixed executable, temp-file arguments
            [
                mmdc,
                "-i",
                str(source),
                "-o",
                str(target),
                "-c",
                str(config_path),
                "-b",
                f"#{theme.background}",
                "-w",
                str(WIDTH_PX),
                "-H",
                str(HEIGHT_PX),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    if result.returncode != 0 or not target.exists():
        # Almost always invalid Mermaid from the model, which is recoverable — the slide
        # falls back to its alt text.
        log.info("mmdc failed: %s", (result.stderr or "")[:300])
        return None

    return target


def _render_chart(spec: str, target: Path) -> Path | None:
    """Render a chart from a small JSON spec.

    The spec the model produces looks like::

        {"type": "bar", "title": "...", "x_label": "...", "y_label": "...",
         "labels": ["A", "B"], "series": [{"name": "2024", "values": [1, 2]}]}
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # no display in a server process
        import matplotlib.pyplot as plt
    except ImportError:
        log.info("matplotlib is not installed; skipping the chart")
        return None

    try:
        data = json.loads(spec)
    except json.JSONDecodeError:
        log.info("Chart spec was not valid JSON")
        return None

    labels = [str(label) for label in data.get("labels", [])]
    series = data.get("series") or []
    if not labels or not series:
        return None

    chart_type = data.get("type", "bar")
    figure, axes = plt.subplots(figsize=(WIDTH_PX / 160, HEIGHT_PX / 160), dpi=160)

    try:
        if chart_type == "pie":
            values = series[0].get("values", [])
            axes.pie(values, labels=labels, autopct="%1.0f%%", colors=CHART_COLOURS)
            axes.axis("equal")
        elif chart_type == "line":
            for index, entry in enumerate(series):
                axes.plot(
                    labels,
                    entry.get("values", []),
                    marker="o",
                    label=entry.get("name", ""),
                    color=CHART_COLOURS[index % len(CHART_COLOURS)],
                    linewidth=2.2,
                )
        else:  # bar, grouped when there is more than one series
            width = 0.8 / max(1, len(series))
            positions = range(len(labels))
            for index, entry in enumerate(series):
                offset = [p + index * width for p in positions]
                axes.bar(
                    offset,
                    entry.get("values", []),
                    width=width,
                    label=entry.get("name", ""),
                    color=CHART_COLOURS[index % len(CHART_COLOURS)],
                )
            axes.set_xticks([p + 0.4 - width / 2 for p in positions])
            axes.set_xticklabels(labels)

        if title := data.get("title"):
            axes.set_title(title, fontsize=15, weight="bold")
        if x_label := data.get("x_label"):
            axes.set_xlabel(x_label, fontsize=12)
        if y_label := data.get("y_label"):
            axes.set_ylabel(y_label, fontsize=12)
        if len(series) > 1 and chart_type != "pie":
            axes.legend(frameon=False)

        if chart_type != "pie":
            axes.spines["top"].set_visible(False)
            axes.spines["right"].set_visible(False)
            axes.grid(axis="y", alpha=0.25)

        figure.tight_layout()
        figure.savefig(target, transparent=False)
        return target
    finally:
        plt.close(figure)


def prepare_deck_figures(deck, cache_dir: Path) -> int:
    """Render every figure in a deck and attach the paths for the PPTX renderer.

    Returns how many rendered. The renderer reads ``figure._rendered_path``; setting a
    private attribute keeps the rendered path out of the stored JSON, which should hold
    only the source of truth.
    """
    rendered = 0
    for slide in deck.slides:
        if slide.figure is None:
            continue
        path = render_figure(slide.figure, cache_dir, theme_key=deck.theme)
        if path is not None:
            object.__setattr__(slide.figure, "_rendered_path", str(path))
            rendered += 1
    if rendered:
        log.info("Rendered %d figure(s) for %r", rendered, deck.title)
    return rendered
