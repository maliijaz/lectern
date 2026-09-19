"""Runtime-editable settings.

:class:`app.config.Settings` holds the defaults from ``.env``. The Settings page writes
overrides into the ``app_settings`` table; this module merges the two and hands the rest of
the app a single resolved ``Settings`` object.

The merged result is cached in-process and invalidated on write, so a generation worker
does not hit the database for every LLM call.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RUNTIME_OVERRIDABLE, SECRET_FIELDS, Settings, get_settings
from app.core.errors import ValidationFailed
from app.db.models import AppSetting

_cache: Settings | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


async def load_overrides(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.execute(select(AppSetting))).scalars().all()
    return {r.key: r.value.get("v") for r in rows if r.key in RUNTIME_OVERRIDABLE}


async def effective_settings(db: AsyncSession) -> Settings:
    """Env defaults with database overrides applied on top."""
    global _cache
    if _cache is not None:
        return _cache

    base = get_settings()
    overrides = await load_overrides(db)
    if not overrides:
        _cache = base
        return base

    try:
        merged = base.model_copy(update=_coerce(base, overrides))
    except ValidationError as exc:  # a stored override no longer type-checks
        raise ValidationFailed(
            "Stored settings are invalid; reset them from the Settings page.",
            # include_context=False keeps the original exception object out of the payload,
            # which would otherwise make the response unserialisable.
            detail={"errors": exc.errors(include_url=False, include_context=False)},
        ) from exc

    _cache = merged
    return merged


def cached_settings() -> Settings:
    """Best-effort accessor for code paths with no session handy (e.g. sync renderers).

    Falls back to env defaults until something has warmed the cache.
    """
    return _cache or get_settings()


def _coerce(base: Settings, values: dict[str, Any]) -> dict[str, Any]:
    """Validate override values against the field types declared on Settings."""
    fields = type(base).model_fields
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in RUNTIME_OVERRIDABLE or key not in fields:
            continue
        if value is None or value == "":
            continue  # empty means "keep the env default"
        out[key] = value
    # Round-trip through the model so pydantic does the type coercion and raises on bad input.
    return type(base)(**{**base.model_dump(), **out}).model_dump(include=set(out))


async def save_overrides(db: AsyncSession, values: dict[str, Any]) -> dict[str, Any]:
    """Upsert overrides. A key mapped to ``None`` or ``""`` is cleared."""
    unknown = set(values) - RUNTIME_OVERRIDABLE
    if unknown:
        raise ValidationFailed(
            f"These settings are not editable at runtime: {', '.join(sorted(unknown))}",
            detail={"editable": sorted(RUNTIME_OVERRIDABLE)},
        )

    base = get_settings()
    _coerce(base, {k: v for k, v in values.items() if v not in (None, "")})  # validate early

    for key, value in values.items():
        if value in (None, ""):
            await db.execute(delete(AppSetting).where(AppSetting.key == key))
            continue
        row = await db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value={"v": value}))
        else:
            row.value = {"v": value}

    await db.flush()
    invalidate_cache()
    return await load_overrides(db)


async def reset_overrides(db: AsyncSession) -> None:
    await db.execute(delete(AppSetting))
    await db.flush()
    invalidate_cache()


def redact(settings: Settings) -> dict[str, Any]:
    """Settings as a dict safe to send to a client — secrets replaced by a presence flag."""
    data = settings.model_dump(mode="json")
    for key in SECRET_FIELDS:
        if key in data:
            data[key] = "********" if data[key] else ""
    return data
