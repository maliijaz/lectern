"""Async engine, session factory and the FastAPI dependency."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _make_engine() -> AsyncEngine:
    settings = get_settings()
    url = settings.database_url
    # SQL echo is opt-in on its own flag: TA_DEBUG is about application behaviour, and
    # statement logging is far too noisy to tie to it.
    echo = os.getenv("TA_SQL_ECHO", "").lower() in ("1", "true", "yes")
    kwargs: dict = {"echo": echo, "future": True}

    if url.startswith("sqlite"):
        # SQLite needs WAL + a busy timeout to survive the worker pool writing while a
        # request reads. Without this, concurrent generation jobs hit "database is locked".
        kwargs["connect_args"] = {"timeout": 30}

    engine = create_async_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Commits on success, rolls back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Same contract as :func:`get_db`, for use outside a request (workers, CLI)."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create tables if they do not exist.

    Alembic owns schema changes; this is the first-run convenience so a fresh install
    works without the user running a migration by hand. It never alters existing tables.
    """
    from app.db.models import Base  # noqa: PLC0415 — avoids a circular import at module load

    _ensure_sqlite_directory()

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _ensure_sqlite_directory() -> None:
    """Create the folder the SQLite file lives in, if it is missing.

    ``Settings.ensure_dirs`` creates the data directory, but the database URL is set
    independently and can point elsewhere — which is exactly what a hosting platform's
    environment variables tend to do. When the folder is absent SQLite fails with
    "unable to open database file", which says nothing about the actual problem. One
    mkdir removes a whole class of confusing deployment failures.
    """
    from pathlib import Path

    url = get_settings().database_url
    if not url.startswith("sqlite"):
        return

    _, _, path = url.partition("///")
    path = path.split("?", 1)[0].lstrip("/")
    if not path or path == ":memory:":
        return

    target = Path(url.split("///", 1)[1].split("?", 1)[0])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:  # a read-only or otherwise unusable location: let SQLite report it
        pass


async def ping() -> bool:
    try:
        async with get_sessionmaker()() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
