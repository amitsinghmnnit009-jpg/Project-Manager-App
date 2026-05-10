"""Project status router (Step 11) — current, history, manual refresh.

Three endpoints under `/api/projects/{code}/status`:
- GET ""              → latest ProjectStatus row (404 when never computed)
- GET "/history"      → ProjectStatusHistory list (most recent first)
- POST "/refresh"     → trigger run_status_compute synchronously, return result
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.schemas import (
    ProjectStatusOut, StatusHistoryEntry, StatusRefreshResult,
)
from app.db import session_scope
from app.models import ProjectStatus, ProjectStatusHistory
from app.registry.projects import get_project_by_code


router = APIRouter(prefix="/api/projects/{code}/status", tags=["status"])


# ---------- Latest current status ---------------------------------------

@router.get("", response_model=ProjectStatusOut,
            summary="Latest computed status for a project")
def api_get_status(code: str):
    """Return the single ProjectStatus row for this project (one row per
    project — refreshed in place by the Status Engine).

    Returns 404 if the project hasn't been computed yet — caller can POST
    /refresh to trigger an immediate compute.
    """
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    with session_scope() as s:
        row = s.execute(
            select(ProjectStatus).where(ProjectStatus.project_id == project["id"])
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                404,
                f"No status computed yet for {code!r}. "
                f"Trigger via POST /api/projects/{code}/status/refresh",
            )
        return ProjectStatusOut(
            project_code=code,
            computed_at=row.computed_at,
            overall_health=row.overall_health,
            schedule_status=row.schedule_status,
            completion_pct=row.completion_pct,
            confidence=row.confidence,
            rationale=row.rationale,
            milestones=row.milestones_json or [],
            prompt_version=row.prompt_version,
            llm_mode_used=row.llm_mode_used,
        )


# ---------- History timeline --------------------------------------------

@router.get("/history", response_model=list[StatusHistoryEntry],
            summary="Status-change history (most recent first)")
def api_get_status_history(
    code: str,
    limit: int = Query(50, ge=1, le=500,
                       description="Max history entries to return"),
):
    """ProjectStatusHistory rows, most recent first. A history row is only
    appended when overall_health / schedule_status / completion_pct
    actually CHANGE (per FR §A.6.1) — so an unchanged daily compute won't
    appear here.
    """
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    with session_scope() as s:
        rows = s.execute(
            select(ProjectStatusHistory)
            .where(ProjectStatusHistory.project_id == project["id"])
            .order_by(ProjectStatusHistory.computed_at.desc())
            .limit(limit)
        ).scalars().all()
        return [
            StatusHistoryEntry(
                computed_at=r.computed_at,
                prior_health=r.prior_health, new_health=r.new_health,
                prior_schedule=r.prior_schedule, new_schedule=r.new_schedule,
                prior_completion_pct=r.prior_completion_pct,
                new_completion_pct=r.new_completion_pct,
                rationale=r.rationale, prompt_version=r.prompt_version,
            )
            for r in rows
        ]


# ---------- Manual refresh trigger --------------------------------------

@router.post("/refresh", response_model=StatusRefreshResult,
             summary="Trigger an immediate Status Engine recompute (synchronous)")
def api_refresh_status(code: str):
    """Synchronously runs the Status Engine. Returns the structured outcome.

    Same code path as the daily scheduled job + the `run-status` CLI. Persists
    ProjectStatus + (if changed) ProjectStatusHistory + AIComputeLog rows.
    """
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    from app.engines.status import run_status_compute
    res = run_status_compute(code)
    parsed = res.parsed or {}
    return StatusRefreshResult(
        success=res.success, error=res.error,
        overall_health=parsed.get("overall_health"),
        schedule_status=parsed.get("schedule_status"),
        completion_pct=parsed.get("completion_pct"),
        confidence=parsed.get("confidence"),
        rationale=parsed.get("rationale", ""),
        duration_seconds=res.duration_seconds,
        changed=res.changed,
        validation_issues=list(res.issues or []),
    )
