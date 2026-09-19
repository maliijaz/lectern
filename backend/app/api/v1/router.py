"""Aggregates the v1 routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import artifacts, courses, documents, jobs, settings

api_router = APIRouter()
api_router.include_router(settings.router)
api_router.include_router(jobs.router)
api_router.include_router(documents.router)
api_router.include_router(artifacts.router)
api_router.include_router(artifacts.bank_router)
api_router.include_router(courses.router)
