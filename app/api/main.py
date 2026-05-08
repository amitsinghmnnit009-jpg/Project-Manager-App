"""FastAPI app entrypoint.

Minimal in scaffold — just /health for now. Routers are wired in Step 11.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app import __version__
from app.config import get_config
from app.db import init_database
from app.utils.logging import system_log


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = system_log()
    log.info("startup", extra={"version": __version__})
    init_database()
    # Step 5: sync projects from config.json into the DB on every start.
    # Idempotent — safe whether the DB is empty or already has rows.
    from app.registry.projects import sync_projects_from_config
    from app.registry.engineers import load_engineer_mapping
    try:
        report = sync_projects_from_config()
        log.info("registry sync at startup", extra={"event": "startup_sync", **report})
    except Exception as e:
        # Don't crash the app on a bad config — surface as a warning so /health
        # still answers. The operator can fix config.json and restart.
        log.error(
            "project registry sync failed at startup",
            extra={"event": "startup_sync_failed", "error": str(e),
                   "type": type(e).__name__},
        )
    # Pre-warm the engineer mapping cache so the first request that needs it
    # doesn't pay the file-read latency.
    try:
        load_engineer_mapping()
    except Exception as e:
        log.error(
            "engineer mapping pre-load failed at startup",
            extra={"event": "startup_engineers_failed", "error": str(e),
                   "type": type(e).__name__},
        )
    # TODO Step 9: start_scheduler()
    yield
    log.info("shutdown")
    # TODO Step 9: stop_scheduler()


app = FastAPI(
    title="Project-Manager-App",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
def health():
    cfg = get_config()
    return {
        "status": "ok",
        "version": __version__,
        "llm_provider": cfg.llm.provider,
        "projects_configured": len(cfg.projects),
    }


# TODO Step 11: include_router(projects_router), reports_router, status_router, admin_router
