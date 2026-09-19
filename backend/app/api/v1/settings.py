"""Read and change runtime settings, and probe the configured model backend."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RUNTIME_OVERRIDABLE
from app.db.session import get_db
from app.db.session import ping as db_ping
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    values: dict[str, Any]
    overridden: list[str]
    editable: list[str]


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ProbeResult(BaseModel):
    ok: bool
    provider: str
    base_url: str
    model: str
    message: str
    models_available: list[str] = Field(default_factory=list)
    latency_ms: float | None = None


@router.get("", response_model=SettingsOut)
async def read_settings(db: AsyncSession = Depends(get_db)) -> SettingsOut:
    effective = await settings_service.effective_settings(db)
    overrides = await settings_service.load_overrides(db)
    return SettingsOut(
        values=settings_service.redact(effective),
        overridden=sorted(overrides),
        editable=sorted(RUNTIME_OVERRIDABLE),
    )


@router.put("", response_model=SettingsOut)
async def update_settings(body: SettingsUpdate, db: AsyncSession = Depends(get_db)) -> SettingsOut:
    await settings_service.save_overrides(db, body.values)
    await db.commit()
    return await read_settings(db)


@router.post("/reset", response_model=SettingsOut)
async def reset_settings(db: AsyncSession = Depends(get_db)) -> SettingsOut:
    await settings_service.reset_overrides(db)
    await db.commit()
    return await read_settings(db)


@router.post("/probe", response_model=ProbeResult)
async def probe_llm(db: AsyncSession = Depends(get_db)) -> ProbeResult:
    """Check the configured model backend is reachable and list the models it offers."""
    from app.llm.registry import build_provider

    effective = await settings_service.effective_settings(db)
    provider = build_provider(effective)
    result = await provider.probe()
    return ProbeResult(
        ok=result.ok,
        provider=effective.llm_provider,
        base_url=effective.llm_base_url,
        model=effective.llm_model,
        message=result.message,
        models_available=result.models,
        latency_ms=result.latency_ms,
    )


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """One call the UI makes on load to decide what to warn the user about."""
    from app.core.capabilities import capability_report
    from app.core.hardware import report as hardware_report
    from app.llm.registry import build_provider

    effective = await settings_service.effective_settings(db)

    llm: dict[str, Any] = {
        "provider": effective.llm_provider,
        "model": effective.llm_model,
        "base_url": effective.llm_base_url,
        "context": effective.llm_num_ctx,
        "auto_context": effective.llm_auto_context,
    }

    # Where the model is actually running matters more than any other single fact about
    # performance, so it is part of the status the UI loads on every page.
    provider = build_provider(effective)
    if hasattr(provider, "effective_context"):
        try:
            llm["context"], llm["context_reason"] = await provider.effective_context()
            llm["placement"] = await provider.placement()
        except Exception:  # the backend being down is reported by /probe, not here
            llm["placement"] = {"loaded": False}

    return {
        "database": await db_ping(),
        "llm": llm,
        "hardware": hardware_report(),
        "capabilities": capability_report(),
    }
