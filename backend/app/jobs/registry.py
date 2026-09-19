"""Job handler registry and the context passed to every handler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.logging import get_logger
from app.db.models import JobStatus
from app.jobs import events

log = get_logger(__name__)


class Cancelled(Exception):
    """Raised inside a handler when the user cancels the job."""


@dataclass
class JobContext:
    """Handed to each handler. Use :meth:`progress` liberally — it is what the teacher sees
    while a ten-minute generation runs."""

    job_id: str
    kind: str
    params: dict[str, Any]
    _cancelled: bool = field(default=False, repr=False)
    # Sub-range of the overall 0..1 bar that nested steps report into.
    _span: tuple[float, float] = field(default=(0.0, 1.0), repr=False)

    async def progress(self, fraction: float, message: str = "") -> None:
        lo, hi = self._span
        scaled = lo + max(0.0, min(1.0, fraction)) * (hi - lo)
        from app.jobs.store import update_job  # local import: avoids a cycle

        await update_job(self.job_id, progress=scaled, message=message)
        events.publish(
            self.job_id,
            {
                "type": "progress",
                "status": JobStatus.RUNNING,
                "progress": scaled,
                "message": message,
            },
        )

    def span(self, lo: float, hi: float) -> JobContext:
        """A child context whose 0..1 maps onto ``lo..hi`` of this job's bar."""
        child = JobContext(self.job_id, self.kind, self.params)
        child._span = (
            self._span[0] + lo * (self._span[1] - self._span[0]),
            self._span[0] + hi * (self._span[1] - self._span[0]),
        )
        child._cancelled = self._cancelled
        return child

    def cancel(self) -> None:
        self._cancelled = True

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise Cancelled(f"Job {self.job_id} was cancelled")


class JobHandler(Protocol):
    async def __call__(self, ctx: JobContext) -> dict[str, Any]: ...


_handlers: dict[str, JobHandler] = {}


def job(kind: str) -> Callable[[JobHandler], JobHandler]:
    """Register an async handler under ``kind``.

    Handlers receive a :class:`JobContext` and return a JSON-serialisable result dict
    stored on the job row.
    """

    def decorator(fn: JobHandler) -> JobHandler:
        if kind in _handlers:
            raise RuntimeError(f"Duplicate job handler for {kind!r}")
        _handlers[kind] = fn
        return fn

    return decorator


def get_handler(kind: str) -> JobHandler | None:
    _ensure_handlers_imported()
    return _handlers.get(kind)


def known_kinds() -> list[str]:
    _ensure_handlers_imported()
    return sorted(_handlers)


_imported = False


def _ensure_handlers_imported() -> None:
    """Import the modules that define handlers, once, on first use."""
    global _imported
    if _imported:
        return
    _imported = True
    for module in ("app.jobs.handlers",):
        try:
            __import__(module)
        except Exception:  # pragma: no cover - surfaced at job dispatch time
            log.exception("Failed importing job handlers from %s", module)


__all__ = ["Cancelled", "JobContext", "JobHandler", "get_handler", "job", "known_kinds"]
