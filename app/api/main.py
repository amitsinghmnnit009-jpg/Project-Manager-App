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
    # Step 9: start the APScheduler that triggers daily status recompute +
    # weekly aggregation→highlights pipeline per project. Wrapped in
    # try/except — a scheduler failure must not block the API serving
    # /health and other read-only routes.
    from app.scheduler import start_scheduler, stop_scheduler
    try:
        start_scheduler()
    except Exception as e:
        log.error(
            "scheduler failed to start",
            extra={"event": "startup_scheduler_failed",
                   "error": str(e), "type": type(e).__name__},
        )
    yield
    log.info("shutdown")
    try:
        stop_scheduler()
    except Exception as e:
        log.error(
            "scheduler failed to stop cleanly",
            extra={"event": "shutdown_scheduler_failed",
                   "error": str(e), "type": type(e).__name__},
        )


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


# Step 11 — wire all the public + admin routers. Order doesn't matter for
# routing (each has a distinct prefix), but we register projects first so
# OpenAPI/Swagger groups them in a sensible order.
from app.api.projects import router as projects_router
from app.api.status import router as status_router
from app.api.reports import router as reports_router
from app.api.admin import router as admin_router

app.include_router(projects_router)
app.include_router(status_router)
app.include_router(reports_router)
app.include_router(admin_router)
