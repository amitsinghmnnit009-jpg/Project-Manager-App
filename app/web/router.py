"""HTML routes for the PGM web UI.

W1 deliverable: Jinja2 wired up, base template, portfolio skeleton, root
redirect. Subsequent steps (W2–W6) populate the screens with real data
by calling existing Phase 1 service functions directly (NOT via HTTP
self-loopback) — see PHASE2_FUNCTIONAL_REQUIREMENTS.md §6.
"""
from __future__ import annotations
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates


_WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _WEB_DIR / "templates"
STATIC_DIR = _WEB_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["web"], include_in_schema=False)


@router.get("/", summary="Root → portfolio")
def root():
    return RedirectResponse(url="/portfolio", status_code=302)


@router.get("/portfolio", summary="Portfolio (cross-project) view")
def portfolio(request: Request):
    from app.registry.projects import list_projects
    projects = list_projects()
    return templates.TemplateResponse(
        request=request,
        name="portfolio.html",
        context={"projects": projects},
    )
