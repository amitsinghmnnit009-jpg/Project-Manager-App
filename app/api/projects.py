"""Projects router (Step 11) — list, detail, minimal task list.

Read-only routes for the PGM dashboard. The minimal task list per FR §C.2
is delegated to JIRA — we present what's currently active without
duplicating JIRA's full task UI.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

from app.api.schemas import ProjectDetail, ProjectSummary, TaskItem
from app.registry.projects import get_project_by_code, list_projects


router = APIRouter(prefix="/api/projects", tags=["projects"])


# ---------- List + detail ------------------------------------------------

def _to_summary(p: dict) -> ProjectSummary:
    return ProjectSummary(
        code=p["code"], name=p["name"], type=p.get("type") or "general",
        state=p.get("state") or "active",
        owning_tl=p.get("owning_tl") or "", owning_pgm=p.get("owning_pgm") or "",
        start_date=p.get("start_date"), planned_end_date=p.get("planned_end_date"),
    )


def _to_detail(p: dict) -> ProjectDetail:
    return ProjectDetail(
        code=p["code"], name=p["name"], type=p.get("type") or "general",
        state=p.get("state") or "active",
        owning_tl=p.get("owning_tl") or "", owning_pgm=p.get("owning_pgm") or "",
        start_date=p.get("start_date"), planned_end_date=p.get("planned_end_date"),
        description=p.get("description") or "",
        confluence_milestones_url=p.get("confluence_milestones_url") or "",
        confluence_fr_url=p.get("confluence_fr_url") or "",
        confluence_extra_pages=p.get("confluence_extra_pages") or [],
        jira_project_key=p.get("jira_project_key") or "",
        weekly_cutoff=p.get("weekly_cutoff") or "Mon 13:00",
        week_boundary=p.get("week_boundary") or "monday",
        recompute_cadence=p.get("recompute_cadence"),
        issue_types=p.get("issue_types") or [],
        chronic_threshold=p.get("chronic_threshold") or 3,
        holiday_calendar_id=p.get("holiday_calendar_id") or "default",
    )


@router.get("", response_model=list[ProjectSummary],
            summary="List all projects in the registry")
def api_list_projects():
    """List all projects (sorted by code)."""
    return [_to_summary(p) for p in list_projects()]


@router.get("/{code}", response_model=ProjectDetail,
            summary="Project detail by code")
def api_get_project(code: str):
    """Get full project metadata. 404 if no project with that code."""
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(
            status_code=404, detail=f"Project {code!r} not found in registry",
        )
    return _to_detail(project)


# ---------- Tasks (FR §C.2) ----------------------------------------------

@router.get("/{code}/tasks", response_model=list[TaskItem],
            summary="Currently ongoing JIRA tasks for the project")
def api_list_project_tasks(code: str):
    """Minimal task list per FR §C.2 — JIRA ID + title + Open-in-JIRA URL.

    Sourced from `JiraClient.get_project_snapshot()` (last-14-day recently-
    active tasks; capped at 30). Returns empty list when JIRA has no recent
    activity. Returns 502 on JIRA fetch failure (network / auth / 5xx).
    """
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(
            status_code=404, detail=f"Project {code!r} not found in registry",
        )

    try:
        from app.clients import get_jira_client
        client = get_jira_client()
        snap = client.get_project_snapshot(
            project["jira_project_key"], project.get("issue_types") or None,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"JIRA fetch failed: {type(e).__name__}: {e}",
        )

    base = client.base.rstrip("/")
    return [
        TaskItem(
            id=r["id"], title=r["title"], status=r["status"],
            last_activity=r.get("last_activity"),
            url=f"{base}/browse/{r['id']}",
        )
        for r in snap.recent_activity
    ]
