"""Weekly reports router (Step 11) — list, latest, by-week, manual triggers.

Five endpoints under `/api/projects/{code}/reports`:
- GET ""                              → list weeks (optional from/to bounds)
- GET "/latest"                       → most recent WeeklyReport (full content)
- GET "/{week_of}"                    → specific week's WeeklyReport (full content)
- POST "/{week_of}/regenerate"        → trigger run_weekly_aggregation
- POST "/{week_of}/highlights/refresh"→ trigger run_highlights
"""
from __future__ import annotations
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.schemas import (
    AggregationRefreshResult, HighlightsRefreshResult,
    WeeklyReportDetail, WeeklyReportSummary,
)
from app.db import session_scope
from app.models import WeeklyReport
from app.registry.projects import get_project_by_code


router = APIRouter(prefix="/api/projects/{code}/reports", tags=["reports"])


# ---------- Internal helpers --------------------------------------------

def _to_summary(r: WeeklyReport, code: str) -> WeeklyReportSummary:
    return WeeklyReportSummary(
        project_code=code,
        week_of=r.week_of,
        generated_at=r.generated_at,
        regenerated_count=r.regenerated_count or 0,
        last_regenerated_at=r.last_regenerated_at,
        has_highlights=bool(r.prompt_version_highlights),
    )


def _to_detail(r: WeeklyReport, code: str) -> WeeklyReportDetail:
    return WeeklyReportDetail(
        project_code=code,
        week_of=r.week_of,
        generated_at=r.generated_at,
        regenerated_count=r.regenerated_count or 0,
        last_regenerated_at=r.last_regenerated_at,
        has_highlights=bool(r.prompt_version_highlights),
        content_markdown=r.content_markdown or "",
        prompt_version_aggregation=r.prompt_version_aggregation or "",
        prompt_version_highlights=r.prompt_version_highlights or "",
        llm_mode_used=r.llm_mode_used or "",
    )


# ---------- Read endpoints ----------------------------------------------

@router.get("", response_model=list[WeeklyReportSummary],
            summary="List weekly reports for a project (newest first)")
def api_list_reports(
    code: str,
    from_date: Optional[date] = Query(None, alias="from",
                                      description="Inclusive lower bound on week_of (YYYY-MM-DD)"),
    to_date: Optional[date] = Query(None, alias="to",
                                    description="Inclusive upper bound on week_of (YYYY-MM-DD)"),
    limit: int = Query(20, ge=1, le=200,
                       description="Max rows to return (default 20)"),
):
    """List the project's weekly reports — summary only (no markdown body).
    Use /latest or /{week_of} for the full content."""
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    with session_scope() as s:
        q = (
            select(WeeklyReport)
            .where(WeeklyReport.project_id == project["id"])
            .order_by(WeeklyReport.week_of.desc())
            .limit(limit)
        )
        if from_date:
            q = q.where(WeeklyReport.week_of >= from_date)
        if to_date:
            q = q.where(WeeklyReport.week_of <= to_date)
        rows = s.execute(q).scalars().all()
        return [_to_summary(r, code) for r in rows]


@router.get("/latest", response_model=WeeklyReportDetail,
            summary="Most recent weekly report (full content)")
def api_get_latest_report(code: str):
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    with session_scope() as s:
        row = s.execute(
            select(WeeklyReport)
            .where(WeeklyReport.project_id == project["id"])
            .order_by(WeeklyReport.week_of.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                404,
                f"No weekly reports yet for {code!r}. "
                f"Trigger one via POST /api/projects/{code}/reports/<MONDAY-DATE>/regenerate",
            )
        return _to_detail(row, code)


@router.get("/{week_of}", response_model=WeeklyReportDetail,
            summary="Weekly report for a specific Monday (full content)")
def api_get_report_for_week(code: str, week_of: date):
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    with session_scope() as s:
        row = s.execute(
            select(WeeklyReport).where(
                WeeklyReport.project_id == project["id"],
                WeeklyReport.week_of == week_of,
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                404, f"No weekly report for {code!r} week of {week_of}",
            )
        return _to_detail(row, code)


# ---------- Manual triggers ---------------------------------------------

@router.post("/{week_of}/regenerate", response_model=AggregationRefreshResult,
             summary="Trigger Aggregation Engine for a specific week (synchronous)")
def api_regenerate_report(code: str, week_of: date):
    """Synchronously runs the Aggregation Engine for the given week.
    Idempotent: existing rows for (project, week) are upserted with
    `regenerated_count` bumped per FR §B.3.4. Highlights section remains
    untouched — call /highlights/refresh separately to re-run it."""
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    from app.engines.aggregation import run_weekly_aggregation
    res = run_weekly_aggregation(code, week_of=week_of, regenerate=True)
    return AggregationRefreshResult(
        success=res.success, error=res.error, week_of=res.week_of,
        is_regeneration=res.is_regeneration,
        engineer_count=res.engineer_count,
        activity_records=res.activity_records,
        unmapped_authors_count=res.unmapped_authors_count,
        duration_seconds=res.duration_seconds,
        content_excerpt=(res.content_markdown or "")[:500],
    )


@router.post("/{week_of}/highlights/refresh", response_model=HighlightsRefreshResult,
             summary="Trigger Highlights Engine for a specific week (synchronous)")
def api_refresh_highlights(code: str, week_of: date):
    """Synchronously runs the Highlights Engine for the given week.
    Requires that this-week's WeeklyReport already exists (regenerate
    first if needed). first-week is a valid case — produces the standard
    'no prior week' message rather than failing."""
    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    from app.engines.highlights import run_highlights
    res = run_highlights(code, week_of=week_of)
    return HighlightsRefreshResult(
        success=res.success, error=res.error, week_of=res.week_of,
        is_first_week=res.is_first_week, last_week_of=res.last_week_of,
        duration_seconds=res.duration_seconds,
        highlights_excerpt=(res.highlights_section or "")[:500],
    )
