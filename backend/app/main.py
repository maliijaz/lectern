"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.db.session import dispose_engine, init_db

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.debug)
    settings.ensure_dirs()
    await init_db()

    from app.jobs.worker import start_workers, stop_workers

    await start_workers()
    log.info("%s ready — data in %s", settings.app_name, settings.data_dir)
    try:
        yield
    finally:
        await stop_workers()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Turn documents or topics into slides, lecture notes and question papers.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Added after CORS, so it runs *before* it: a rejected request should not be told
    # which origins are welcome. No-op unless LECTERN_ACCESS_KEY is set.
    if settings.access_key:
        from app.core.access import AccessKeyMiddleware

        app.add_middleware(AccessKeyMiddleware, access_key=settings.access_key)
        log.info("Access key required - set it as a header, or open the URL with ?key=")

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built web UI from the same process, when it has been built.

    In development the Vite server proxies to this API instead, so this is a no-op until
    someone runs `npm run build`. Serving both from one origin is what makes `ta serve`
    a complete install rather than half of one.
    """
    from app.config import PROJECT_ROOT

    dist = PROJECT_ROOT / "frontend" / "dist"
    index = dist / "index.html"
    if not index.exists():
        log.info("Web UI not built; API only. Run `npm run build` in frontend/ to serve it.")
        return

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        # A real file if it exists, otherwise index.html so client-side routes work on a
        # hard refresh. Registered last, so it never shadows the API routes.
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)

    log.info("Serving the web UI from %s", dist)


app = create_app()
