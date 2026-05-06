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
    # TODO Step 5: sync_projects_from_config()
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
