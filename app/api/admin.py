"""Admin / observability router (Step 11) — config + logs + scheduler.

Five endpoints under `/admin`:
- GET /config                  → safe (non-secret) subset of config
- GET /logs/ai-computes        → AIComputeLog rows (paged)
- GET /logs/reminders          → ReminderLog rows (paged)
- GET /logs/sync               → SyncLog rows (paged)
- GET /scheduler/jobs          → currently registered APScheduler jobs

No write endpoints in Phase 1 — config edits go through editing
config.json + restart, per the deliberate "small attack surface" stance
of the no-auth Phase 1 deployment.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.schemas import (
    AdminConfigSafe, AIComputeLogEntry, ReminderLogEntry,
    SchedulerJobInfo, SyncLogEntry,
)
from app.config import get_config
from app.db import session_scope
from app.models import AIComputeLog, Project, ReminderLog, SyncLog


router = APIRouter(prefix="/admin", tags=["admin"])


# ---------- Config (read-only, no secrets) ------------------------------

@router.get("/config", response_model=AdminConfigSafe,
            summary="Safe subset of runtime config (no tokens or API keys)")
def api_get_config():
    """Read-only view of selected config — anything sensitive (JIRA token,
    Confluence token, OpenAI api_key, custom_headers, SMTP credentials) is
    excluded by construction (see schemas.AdminConfigSafe)."""
    cfg = get_config()
    return AdminConfigSafe(
        llm_provider=cfg.llm.provider,
        llm_temperature=cfg.llm.temperature,
        jira_base_url=cfg.jira.base_url,
        jira_api_version=cfg.jira.api_version,
        confluence_base_url=cfg.confluence.base_url,
        database_url=cfg.database.url,
        logging_directory=cfg.logging.directory,
        logging_level=cfg.logging.level,
        scheduler_status_recompute_cadence=cfg.scheduler.status_recompute_cadence,
        scheduler_daily_status_hour=cfg.scheduler.daily_status_hour,
        scheduler_weekly_aggregation_offset_minutes=cfg.scheduler.weekly_aggregation_offset_minutes,
        scheduler_timezone=cfg.scheduler.timezone,
        scheduler_misfire_grace_seconds=cfg.scheduler.misfire_grace_seconds,
        reminders_hours_before_cutoff=cfg.reminders.hours_before_cutoff,
        reminders_hours_after_cutoff=cfg.reminders.hours_after_cutoff,
        reports_retention_weeks=cfg.reports.retention_weeks,
        api_host=cfg.api.host, api_port=cfg.api.port,
        project_count=len(cfg.projects),
    )


# ---------- AI compute log ----------------------------------------------

@router.get("/logs/ai-computes", response_model=list[AIComputeLogEntry],
            summary="Recent AIComputeLog rows (compact LLM audit)")
def api_list_ai_computes(
    project_code: Optional[str] = Query(
        None, description="Filter to one project (joins on projects.code)",
    ),
    limit: int = Query(50, ge=1, le=500),
):
    with session_scope() as s:
        q = (
            select(AIComputeLog, Project.code)
            .join(Project, Project.id == AIComputeLog.project_id, isouter=True)
            .order_by(AIComputeLog.started_at.desc())
            .limit(limit)
        )
        if project_code:
            q = q.where(Project.code == project_code)
        rows = s.execute(q).all()
        return [
            AIComputeLogEntry(
                id=row[0].id, project_id=row[0].project_id,
                project_code=row[1],
                prompt_name=row[0].prompt_name,
                prompt_version=row[0].prompt_version,
                started_at=row[0].started_at, finished_at=row[0].finished_at,
                success_flag=row[0].success_flag, llm_mode=row[0].llm_mode,
                response_excerpt=row[0].response_excerpt or "",
                error_text=row[0].error_text or "",
            )
            for row in rows
        ]


# ---------- Reminder log ------------------------------------------------

@router.get("/logs/reminders", response_model=list[ReminderLogEntry],
            summary="Recent ReminderLog rows (mock-sent emails)")
def api_list_reminders(
    project_code: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    with session_scope() as s:
        q = (
            select(ReminderLog, Project.code)
            .join(Project, Project.id == ReminderLog.project_id, isouter=True)
            .order_by(ReminderLog.sent_at.desc())
            .limit(limit)
        )
        if project_code:
            q = q.where(Project.code == project_code)
        rows = s.execute(q).all()
        return [
            ReminderLogEntry(
                id=row[0].id,
                engineer_knox_id=row[0].engineer_knox_id,
                engineer_name=row[0].engineer_name or "",
                project_id=row[0].project_id,
                project_code=row[1],
                week_of=row[0].week_of,
                type=row[0].type, sent_at=row[0].sent_at,
                channel=row[0].channel or "", status=row[0].status or "",
            )
            for row in rows
        ]


# ---------- Sync log ----------------------------------------------------

@router.get("/logs/sync", response_model=list[SyncLogEntry],
            summary="Recent SyncLog rows (JIRA / Confluence ingestion)")
def api_list_sync(
    source: Optional[str] = Query(
        None, description="Filter to 'jira' or 'confluence'",
    ),
    limit: int = Query(50, ge=1, le=500),
):
    with session_scope() as s:
        q = (
            select(SyncLog, Project.code)
            .join(Project, Project.id == SyncLog.project_id, isouter=True)
            .order_by(SyncLog.started_at.desc())
            .limit(limit)
        )
        if source:
            q = q.where(SyncLog.source == source)
        rows = s.execute(q).all()
        return [
            SyncLogEntry(
                id=row[0].id, source=row[0].source,
                project_id=row[0].project_id, project_code=row[1],
                started_at=row[0].started_at, finished_at=row[0].finished_at,
                success_flag=row[0].success_flag,
                error_text=row[0].error_text or "",
            )
            for row in rows
        ]


# ---------- Scheduler jobs ----------------------------------------------

@router.get("/scheduler/jobs", response_model=list[SchedulerJobInfo],
            summary="Currently registered APScheduler jobs (with next run times)")
def api_list_scheduler_jobs():
    """Mirrors the `scheduler-status` CLI but as JSON. Returns empty list
    if the scheduler isn't running (e.g. server started without any
    projects in the DB)."""
    from app.scheduler import get_scheduler

    sched = get_scheduler()
    if sched is None:
        return []

    cfg = get_config()
    import pytz
    tz = pytz.timezone(cfg.scheduler.timezone)
    now = datetime.now(tz)

    out: list[SchedulerJobInfo] = []
    for job in sched.get_jobs():
        # APScheduler 3.x: next_run_time. 4.x: next_fire_time. Defensive
        # fallback to computing from the trigger directly.
        next_run = (
            getattr(job, "next_run_time", None)
            or getattr(job, "next_fire_time", None)
        )
        if next_run is None:
            try:
                next_run = job.trigger.get_next_fire_time(None, now)
            except Exception:
                next_run = None
        out.append(SchedulerJobInfo(
            id=job.id, name=job.name,
            next_run=next_run.isoformat() if next_run else None,
            trigger=str(job.trigger),
        ))
    return sorted(out, key=lambda j: j.id)
