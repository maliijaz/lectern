"""Logging setup. Rich handler when available, plain stdlib otherwise."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

# These libraries are chatty at INFO and drown out our own messages during ingestion.
_NOISY = ("httpx", "httpcore", "chromadb", "sentence_transformers", "urllib3", "PIL", "matplotlib")


def ensure_utf8_streams() -> None:
    """Make stdout and stderr able to carry the characters this product prints.

    A Windows console defaults to cp1252, which cannot encode a tick, an arrow or an
    em-dash — all of which appear in ordinary output. Without this, a successful
    generation ends in a UnicodeEncodeError instead of a result.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # a redirected or closed stream
            pass


def setup_logging(debug: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    ensure_utf8_streams()

    level = logging.DEBUG if debug else logging.INFO
    handler: logging.Handler
    try:
        from rich.logging import RichHandler

        handler = RichHandler(rich_tracebacks=True, show_path=debug, markup=False)
        fmt = "%(message)s"
    except ImportError:  # pragma: no cover
        handler = logging.StreamHandler()
        fmt = "%(asctime)s %(levelname)-7s %(name)s  %(message)s"

    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S", handlers=[handler])
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
