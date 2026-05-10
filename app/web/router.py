"""HTML routes for the PGM web UI.

W1: scaffold + portfolio skeleton.
W2 (this step): portfolio populated with status badges, needs-attention card,
    refresh-all button.

Subsequent steps (W3–W6) populate per-project detail, compare, admin.
HTML routes call existing Phase 1 service functions DIRECTLY (NOT via HTTP
self-loopback) — see PHASE2_FUNCTIONAL_REQUIREMENTS.md §6.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select


_WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _WEB_DIR / "templates"
STATIC_DIR = _WEB_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["web"], include_in_schema=False)


# Per PHASE2_FUNCTIONAL_REQUIREMENTS.md §10 — projects whose last status
# compute is older than this get a "stale" badge (no blocking behavior).
STALE_THRESHOLD_DAYS = 10

# Subset of overall_health values that surface in the needs-attention card
# (per FR §4.1 and the user-confirmed scoping decision: red+amber only).
ATTENTION_HEALTH_VALUES = {"Red", "Amber"}


def _is_stale(computed_at: Optional[datetime], days: int = STALE_THRESHOLD_DAYS) -> bool:
    """True iff `computed_at` is older than `days` days vs UTC now."""
    if computed_at is None:
        return False
    # ProjectStatus.computed_at is stored as naive UTC (datetime.utcnow). Make
    # the comparison aware-vs-aware to avoid a TypeError if one side ever has
    # tzinfo attached.
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - computed_at) >= timedelta(days=days)


def _load_portfolio_rows() -> list[dict]:
    """Return one dict per project, with status (or None) and stale flag.

    Shape:
      {
        "project":     ProjectSummary-shaped dict from list_projects(),
        "status":      {overall_health, schedule_status, completion_pct,
                        confidence, rationale, computed_at} or None,
        "is_stale":    bool,
      }
    """
    from app.db import session_scope
    from app.models import ProjectStatus
    from app.registry.projects import list_projects

    projects = list_projects()
    if not projects:
        return []

    project_ids = [p["id"] for p in projects]
    with session_scope() as s:
        status_rows = s.execute(
            select(ProjectStatus).where(ProjectStatus.project_id.in_(project_ids))
        ).scalars().all()
        # Snapshot fields BEFORE the session closes — otherwise the template
        # accesses detached ORM instances and SQLAlchemy raises.
        by_pid = {
            r.project_id: {
                "overall_health": r.overall_health,
                "schedule_status": r.schedule_status,
                "completion_pct": r.completion_pct,
                "confidence": r.confidence,
                "rationale": r.rationale or "",
                "computed_at": r.computed_at,
            }
            for r in status_rows
        }

    rows = []
    for p in projects:
        status = by_pid.get(p["id"])
        rows.append({
            "project": p,
            "status": status,
            "is_stale": _is_stale(status["computed_at"]) if status else False,
        })
    return rows


def _attention_rows(rows: list[dict]) -> list[dict]:
    """Subset of rows that should surface in the needs-attention card.

    Per scoping: any project whose latest overall_health is Red or Amber.
    Sorted Red first, then Amber, then by computed_at desc within each tier.
    """
    candidates = [
        r for r in rows
        if r["status"] and r["status"]["overall_health"] in ATTENTION_HEALTH_VALUES
    ]

    def sort_key(r: dict):
        # Red sorts before Amber (0 < 1). Newer computed_at first within tier.
        health_rank = 0 if r["status"]["overall_health"] == "Red" else 1
        # negate timestamp so newer (= larger) comes first under ascending sort
        ts = r["status"]["computed_at"] or datetime.min
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (health_rank, -ts.timestamp())

    return sorted(candidates, key=sort_key)


# ---------- Routes -------------------------------------------------------

@router.get("/", summary="Root → portfolio")
def root():
    return RedirectResponse(url="/portfolio", status_code=302)


@router.get("/portfolio", summary="Portfolio (cross-project) view")
def portfolio(request: Request):
    rows = _load_portfolio_rows()
    return templates.TemplateResponse(
        request=request,
        name="portfolio.html",
        context={
            "rows": rows,
            "attention_rows": _attention_rows(rows),
            "stale_threshold_days": STALE_THRESHOLD_DAYS,
        },
    )


@router.post("/portfolio/refresh-all",
             summary="Trigger Status Engine for every project (sequential)")
def portfolio_refresh_all():
    """Sequentially run run_status_compute(code) for every project, then ask
    htmx to do a full page reload via HX-Refresh.

    Per PHASE2_FUNCTIONAL_REQUIREMENTS.md §10 #5 — POC behavior: button is
    disabled in the UI for the duration of this call (~30–60s for 5 projects).
    No background task plumbing in this phase.
    """
    from app.engines.status import run_status_compute
    from app.registry.projects import list_projects

    for p in list_projects():
        try:
            run_status_compute(p["code"])
        except Exception:
            # Don't abort the whole refresh on one project's failure; the
            # error will already be in AIComputeLog and system.jsonl. Stale
            # badges + the Admin page surface broken projects.
            pass

    # htmx-aware response: tells htmx to do `window.location.reload()` on the
    # client side. For non-htmx clients (regular form post), an empty 204
    # would leave the user on a blank page — so include a redirect fallback.
    return Response(
        status_code=204,
        headers={"HX-Refresh": "true"},
    )
