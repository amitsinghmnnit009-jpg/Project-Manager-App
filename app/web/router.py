"""HTML routes for the PGM web UI.

W1: scaffold + portfolio skeleton.
W2 (this step): portfolio populated with status badges, needs-attention card,
    refresh-all button.

Subsequent steps (W3–W6) populate per-project detail, compare, admin.
HTML routes call existing Phase 1 service functions DIRECTLY (NOT via HTTP
self-loopback) — see PHASE2_FUNCTIONAL_REQUIREMENTS.md §6.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import re

import markdown as _md
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import select


_WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _WEB_DIR / "templates"
STATIC_DIR = _WEB_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# Jinja `markdown` filter — converts the AI-generated WeeklyReport
# content_markdown to HTML for inline rendering on the project detail page.
# Per FR §5.3: standard markdown + fenced_code + tables.
#
# The python-markdown library DOES pass raw HTML through (its `safe_mode`
# was removed in v3). For POC we apply a tiny regex stripper as defense in
# depth — strips <script> tags and on*= inline event handlers. AI-generated
# weekly reports aren't expected to contain HTML, so this is belt-and-
# suspenders only. A production deployment with multiple users / external
# input should add `bleach` for full sanitization (Phase 3 / Theme 8).

_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*/?>", re.IGNORECASE)
_INLINE_HANDLER_RE = re.compile(
    r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)


def _strip_dangerous_html(html: str) -> str:
    html = _SCRIPT_BLOCK_RE.sub("", html)
    html = _SCRIPT_TAG_RE.sub("", html)
    html = _INLINE_HANDLER_RE.sub("", html)
    return html


def _md_to_html(text: Optional[str]) -> Markup:
    if not text:
        return Markup("")
    html = _md.markdown(text, extensions=["fenced_code", "tables"])
    return Markup(_strip_dangerous_html(html))


templates.env.filters["markdown"] = _md_to_html

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


# ========================================================================
# W3 — Project detail page
# ========================================================================

# Number of trailing status-history points to render in the sparkline.
SPARKLINE_POINTS = 8

# How many past weekly reports to list under the report card (W4).
PAST_REPORTS_LIMIT = 20


def _load_report(project_id: int, week_of: Optional[date] = None) -> Optional[dict]:
    """Latest WeeklyReport row for a project (or for a specific week if
    week_of is given). Snapshots fields before session closes."""
    from app.db import session_scope
    from app.models import WeeklyReport

    with session_scope() as s:
        q = (
            select(WeeklyReport)
            .where(WeeklyReport.project_id == project_id)
        )
        if week_of is not None:
            q = q.where(WeeklyReport.week_of == week_of)
        else:
            q = q.order_by(WeeklyReport.week_of.desc())
        row = s.execute(q.limit(1)).scalar_one_or_none()
        if row is None:
            return None
        return {
            "week_of": row.week_of,
            "generated_at": row.generated_at,
            "regenerated_count": row.regenerated_count or 0,
            "last_regenerated_at": row.last_regenerated_at,
            "content_markdown": row.content_markdown or "",
            "prompt_version_aggregation": row.prompt_version_aggregation or "",
            "prompt_version_highlights": row.prompt_version_highlights or "",
            "llm_mode_used": row.llm_mode_used or "",
            "has_highlights": bool(row.prompt_version_highlights),
        }


def _load_past_reports(project_id: int, limit: int) -> list[dict]:
    """List of past weekly reports (newest first), summary fields only —
    no markdown body. Used to populate the past-reports list."""
    from app.db import session_scope
    from app.models import WeeklyReport

    with session_scope() as s:
        rows = s.execute(
            select(WeeklyReport)
            .where(WeeklyReport.project_id == project_id)
            .order_by(WeeklyReport.week_of.desc())
            .limit(limit)
        ).scalars().all()
        return [
            {
                "week_of": r.week_of,
                "generated_at": r.generated_at,
                "regenerated_count": r.regenerated_count or 0,
                "has_highlights": bool(r.prompt_version_highlights),
            }
            for r in rows
        ]


def _render_report_card(request: Request, project: dict,
                        report: Optional[dict]) -> "Response":
    """Render the report-card partial. Used both inline on the project
    detail page and as the htmx swap target when switching weeks or
    re-running engines."""
    return templates.TemplateResponse(
        request=request,
        name="partials/report_card.html",
        context={"project": project, "report": report},
    )


def _load_status_history(project_id: int, limit: int) -> list[dict]:
    """Latest N ProjectStatusHistory rows, oldest-first for left-to-right
    sparkline rendering. Snapshots fields before session closes."""
    from app.db import session_scope
    from app.models import ProjectStatusHistory

    with session_scope() as s:
        rows = s.execute(
            select(ProjectStatusHistory)
            .where(ProjectStatusHistory.project_id == project_id)
            .order_by(ProjectStatusHistory.computed_at.desc())
            .limit(limit)
        ).scalars().all()
        snapshot = [
            {
                "computed_at": r.computed_at,
                "new_health": r.new_health,
                "new_schedule": r.new_schedule,
                "new_completion_pct": r.new_completion_pct,
            }
            for r in rows
        ]
    snapshot.reverse()  # oldest first → left edge of sparkline
    return snapshot


def _load_status(project_id: int) -> Optional[dict]:
    """Latest ProjectStatus row for a project (None if never computed)."""
    from app.db import session_scope
    from app.models import ProjectStatus

    with session_scope() as s:
        row = s.execute(
            select(ProjectStatus).where(ProjectStatus.project_id == project_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "overall_health": row.overall_health,
            "schedule_status": row.schedule_status,
            "completion_pct": row.completion_pct,
            "confidence": row.confidence,
            "rationale": row.rationale or "",
            "computed_at": row.computed_at,
            "milestones": list(row.milestones_json or []),
            "prompt_version": row.prompt_version or "",
            "llm_mode_used": row.llm_mode_used or "",
        }


@router.get("/projects/{code}", summary="Project detail")
def project_detail(request: Request, code: str):
    from fastapi import HTTPException
    from app.registry.projects import get_project_by_code

    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {code!r} not found in registry",
        )

    status = _load_status(project["id"])
    history = _load_status_history(project["id"], SPARKLINE_POINTS)
    is_stale = _is_stale(status["computed_at"]) if status else False
    latest_report = _load_report(project["id"])
    past_reports = _load_past_reports(project["id"], PAST_REPORTS_LIMIT)

    return templates.TemplateResponse(
        request=request,
        name="project_detail.html",
        context={
            "project": project,
            "status": status,
            "history": history,
            "is_stale": is_stale,
            "stale_threshold_days": STALE_THRESHOLD_DAYS,
            "report": latest_report,
            "past_reports": past_reports,
        },
    )


# ---- W4: report card fragment + triggers ------------------------------

@router.get("/projects/{code}/reports/{week_of}/fragment",
            summary="Report card partial for a specific week (htmx swap target)")
def project_report_fragment(request: Request, code: str, week_of: date):
    from fastapi import HTTPException
    from app.registry.projects import get_project_by_code

    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    report = _load_report(project["id"], week_of=week_of)
    if report is None:
        raise HTTPException(
            404,
            f"No weekly report for {code!r} week of {week_of}",
        )
    return _render_report_card(request, project, report)


@router.post("/projects/{code}/reports/{week_of}/regenerate",
             summary="Re-run Aggregation Engine for a specific week, return fragment")
def project_report_regenerate(request: Request, code: str, week_of: date):
    from fastapi import HTTPException
    from app.engines.aggregation import run_weekly_aggregation
    from app.registry.projects import get_project_by_code

    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    try:
        run_weekly_aggregation(code, week_of=week_of, regenerate=True)
    except Exception:
        # Same swallow-and-continue pattern as refresh-all / refresh-status.
        # The error is in AIComputeLog + system.jsonl already.
        pass

    report = _load_report(project["id"], week_of=week_of)
    return _render_report_card(request, project, report)


@router.post("/projects/{code}/reports/{week_of}/highlights/refresh",
             summary="Re-run Highlights Engine for a specific week, return fragment")
def project_highlights_refresh(request: Request, code: str, week_of: date):
    from fastapi import HTTPException
    from app.engines.highlights import run_highlights
    from app.registry.projects import get_project_by_code

    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    try:
        run_highlights(code, week_of=week_of)
    except Exception:
        pass

    report = _load_report(project["id"], week_of=week_of)
    return _render_report_card(request, project, report)


# ========================================================================
# W5 — Compare weeks (single project, 2–4 weeks side by side)
# ========================================================================

# Number of compare-column slots in the UI. Matches FR §4.3 "2–4 columns".
COMPARE_MAX_COLUMNS = 4
COMPARE_DEFAULT_COLUMNS = 2

# Match a "## Highlights..." heading at the start of a line, case-insensitive.
# Lets variants like "## Highlights vs prior week" match.
_HIGHLIGHTS_HEADING_RE = re.compile(r"^##\s+highlight", re.IGNORECASE | re.MULTILINE)
_NEXT_H2_RE = re.compile(r"^##\s+", re.MULTILINE)


def _extract_highlights_section(markdown: Optional[str]) -> Optional[str]:
    """Return the '## Highlights...' section of a weekly report's markdown,
    or None if no such section exists. Used by the compare view to show
    only the highlights block per column.

    Per FR §4.3: split the markdown by `## ` headings; return the section
    whose heading starts with "Highlight" (so "Highlights" and
    "Highlights vs prior week" both match)."""
    if not markdown:
        return None
    m = _HIGHLIGHTS_HEADING_RE.search(markdown)
    if not m:
        return None
    section_start = m.start()
    # Scan for the next ## heading after this one
    after = markdown[m.end():]
    next_match = _NEXT_H2_RE.search(after)
    if next_match:
        section_end = m.end() + next_match.start()
        return markdown[section_start:section_end].rstrip()
    return markdown[section_start:].rstrip()


def _load_status_at_week(project_id: int, week_of: date) -> Optional[dict]:
    """Latest ProjectStatusHistory entry whose computed_at falls at or
    before the END of the given week (week_of + 6 days). Approximates
    "what did the project look like at the end of week X?".

    Returns the snapshotted fields (none if no history before that point)."""
    from app.db import session_scope
    from app.models import ProjectStatusHistory

    week_end = datetime.combine(week_of + timedelta(days=6), datetime.max.time())
    with session_scope() as s:
        row = s.execute(
            select(ProjectStatusHistory)
            .where(
                ProjectStatusHistory.project_id == project_id,
                ProjectStatusHistory.computed_at <= week_end,
            )
            .order_by(ProjectStatusHistory.computed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "computed_at": row.computed_at,
            "new_health": row.new_health,
            "new_schedule": row.new_schedule,
            "new_completion_pct": row.new_completion_pct,
        }


def _parse_weeks_param(weeks: str) -> list[date]:
    """Parse a comma-separated YYYY-MM-DD string into a list of dates.
    Bad entries are skipped silently (POC — no error UI for malformed input)."""
    out: list[date] = []
    for token in (weeks or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(date.fromisoformat(token))
        except ValueError:
            continue
    return out


@router.get("/projects/{code}/compare",
            summary="Compare 2–4 weeks of a single project side-by-side")
def project_compare(request: Request, code: str, weeks: str = ""):
    from fastapi import HTTPException
    from app.registry.projects import get_project_by_code

    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(404, f"Project {code!r} not found in registry")

    available = _load_past_reports(project["id"], PAST_REPORTS_LIMIT)
    available_weeks = [r["week_of"] for r in available]

    selected = _parse_weeks_param(weeks)
    if not selected:
        # Default — last N weeks for which a report exists. Reverse so the
        # display reads oldest-on-the-left, newest-on-the-right (chronological).
        selected = list(reversed(available_weeks[:COMPARE_DEFAULT_COLUMNS]))
    selected = selected[:COMPARE_MAX_COLUMNS]

    columns = []
    for w in selected:
        report = _load_report(project["id"], week_of=w)
        status = _load_status_at_week(project["id"], w)
        highlights = _extract_highlights_section(report["content_markdown"]) if report else None
        columns.append({
            "week_of": w,
            "report": report,
            "status": status,
            "highlights": highlights,
        })

    return templates.TemplateResponse(
        request=request,
        name="compare.html",
        context={
            "project": project,
            "available_weeks": available_weeks,
            "selected": selected,
            "columns": columns,
            "max_columns": COMPARE_MAX_COLUMNS,
        },
    )


# ========================================================================
# W6 — Admin / observability page
# ========================================================================

# Default rows per log fragment + "Show N more" increment.
ADMIN_LOG_PAGE_SIZE = 50

# Allowed log-tab values; validated at the route to prevent template lookup
# of arbitrary fragments.
ADMIN_LOG_TYPES = ("ai-computes", "reminders", "sync")


def _load_admin_config_safe() -> dict:
    """Subset of runtime config that's safe to display (no tokens / keys).
    Mirrors the AdminConfigSafe pydantic model in app/api/schemas.py."""
    from app.config import get_config
    cfg = get_config()
    return {
        "llm_provider": cfg.llm.provider,
        "llm_temperature": cfg.llm.temperature,
        "jira_base_url": cfg.jira.base_url,
        "jira_api_version": cfg.jira.api_version,
        "confluence_base_url": cfg.confluence.base_url,
        "database_url": cfg.database.url,
        "logging_directory": cfg.logging.directory,
        "logging_level": cfg.logging.level,
        "scheduler_status_recompute_cadence": cfg.scheduler.status_recompute_cadence,
        "scheduler_daily_status_hour": cfg.scheduler.daily_status_hour,
        "scheduler_weekly_aggregation_offset_minutes": cfg.scheduler.weekly_aggregation_offset_minutes,
        "scheduler_timezone": cfg.scheduler.timezone,
        "scheduler_misfire_grace_seconds": cfg.scheduler.misfire_grace_seconds,
        "reminders_hours_before_cutoff": cfg.reminders.hours_before_cutoff,
        "reminders_hours_after_cutoff": cfg.reminders.hours_after_cutoff,
        "reports_retention_weeks": cfg.reports.retention_weeks,
        "api_host": cfg.api.host,
        "api_port": cfg.api.port,
        "project_count": len(cfg.projects),
    }


def _load_scheduler_jobs() -> list[dict]:
    """Currently registered APScheduler jobs (snapshotted dicts).
    Returns [] when the scheduler isn't running (e.g. test mode)."""
    from app.config import get_config
    from app.scheduler import get_scheduler

    sched = get_scheduler()
    if sched is None:
        return []

    cfg = get_config()
    import pytz
    tz = pytz.timezone(cfg.scheduler.timezone)
    now = datetime.now(tz)

    out: list[dict] = []
    for job in sched.get_jobs():
        next_run = (
            getattr(job, "next_run_time", None)
            or getattr(job, "next_fire_time", None)
        )
        if next_run is None:
            try:
                next_run = job.trigger.get_next_fire_time(None, now)
            except Exception:
                next_run = None
        out.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run,
            "trigger": str(job.trigger),
        })
    return sorted(out, key=lambda j: j["id"])


def _load_admin_log_rows(log_type: str, limit: int) -> list[dict]:
    """Snapshot of the most recent N rows from one of the admin log tables.
    Returns dicts (not ORM instances) so the template doesn't access
    detached SQLAlchemy state."""
    from app.db import session_scope
    from app.models import AIComputeLog, Project, ReminderLog, SyncLog

    with session_scope() as s:
        if log_type == "ai-computes":
            rows = s.execute(
                select(AIComputeLog, Project.code)
                .join(Project, Project.id == AIComputeLog.project_id, isouter=True)
                .order_by(AIComputeLog.started_at.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": row[0].id,
                    "project_code": row[1] or "—",
                    "prompt_name": row[0].prompt_name,
                    "prompt_version": row[0].prompt_version,
                    "started_at": row[0].started_at,
                    "finished_at": row[0].finished_at,
                    "success_flag": row[0].success_flag,
                    "llm_mode": row[0].llm_mode,
                    "response_excerpt": (row[0].response_excerpt or "")[:160],
                    "error_text": (row[0].error_text or "")[:160],
                }
                for row in rows
            ]
        if log_type == "reminders":
            rows = s.execute(
                select(ReminderLog, Project.code)
                .join(Project, Project.id == ReminderLog.project_id, isouter=True)
                .order_by(ReminderLog.sent_at.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": row[0].id,
                    "engineer_knox_id": row[0].engineer_knox_id,
                    "engineer_name": row[0].engineer_name or "",
                    "project_code": row[1] or "—",
                    "week_of": row[0].week_of,
                    "type": row[0].type,
                    "sent_at": row[0].sent_at,
                    "channel": row[0].channel or "",
                    "status": row[0].status or "",
                }
                for row in rows
            ]
        if log_type == "sync":
            rows = s.execute(
                select(SyncLog, Project.code)
                .join(Project, Project.id == SyncLog.project_id, isouter=True)
                .order_by(SyncLog.started_at.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": row[0].id,
                    "source": row[0].source,
                    "project_code": row[1] or "—",
                    "started_at": row[0].started_at,
                    "finished_at": row[0].finished_at,
                    "success_flag": row[0].success_flag,
                    "error_text": (row[0].error_text or "")[:160],
                }
                for row in rows
            ]
        return []


@router.get("/admin", summary="Admin / observability page")
def admin_page(request: Request, tab: str = "ai-computes"):
    if tab not in ADMIN_LOG_TYPES:
        tab = "ai-computes"
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "config": _load_admin_config_safe(),
            "jobs": _load_scheduler_jobs(),
            "active_tab": tab,
            "log_types": ADMIN_LOG_TYPES,
            # The log_table partial reads log_type / rows / limit / page_size
            # from the parent context — keep names aligned with the fragment
            # route so the same partial works for inline render and htmx swap.
            "log_type": tab,
            "rows": _load_admin_log_rows(tab, ADMIN_LOG_PAGE_SIZE),
            "limit": ADMIN_LOG_PAGE_SIZE,
            "page_size": ADMIN_LOG_PAGE_SIZE,
        },
    )


@router.get("/admin/scheduler/jobs/fragment",
            summary="Scheduler jobs table fragment (auto-refresh target)")
def admin_scheduler_jobs_fragment(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="partials/scheduler_jobs.html",
        context={"jobs": _load_scheduler_jobs()},
    )


@router.get("/admin/logs/{log_type}/fragment",
            summary="Log table fragment (parametrized by type, paginated)")
def admin_log_fragment(request: Request, log_type: str,
                       limit: int = ADMIN_LOG_PAGE_SIZE):
    from fastapi import HTTPException
    if log_type not in ADMIN_LOG_TYPES:
        raise HTTPException(404, f"Unknown log type {log_type!r}")
    # Cap to a sane upper bound so a malicious / typo'd `limit` query param
    # can't pull millions of rows.
    limit = max(1, min(limit, 5000))
    return templates.TemplateResponse(
        request=request,
        name="partials/log_table.html",
        context={
            "log_type": log_type,
            "rows": _load_admin_log_rows(log_type, limit),
            "limit": limit,
            "page_size": ADMIN_LOG_PAGE_SIZE,
        },
    )


@router.get("/projects/{code}/jira-activity",
            summary="Recent JIRA activity (htmx fragment)")
def project_jira_activity(request: Request, code: str):
    """Lazy-loaded fragment — kept off the main page render path so a slow
    or down JIRA doesn't block the project detail page from appearing.
    """
    from fastapi import HTTPException
    from app.registry.projects import get_project_by_code

    project = get_project_by_code(code)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {code!r} not found in registry",
        )

    activity_rows: list[dict] = []
    error: Optional[str] = None
    jira_base: str = ""
    try:
        from app.clients import get_jira_client
        client = get_jira_client()
        snap = client.get_project_snapshot(
            project["jira_project_key"], project.get("issue_types") or None,
        )
        jira_base = (client.base or "").rstrip("/")
        for r in (snap.recent_activity or []):
            activity_rows.append({
                "id": r["id"],
                "title": r["title"],
                "status": r["status"],
                "last_activity": r.get("last_activity"),
                "url": f"{jira_base}/browse/{r['id']}" if jira_base else "",
            })
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    return templates.TemplateResponse(
        request=request,
        name="partials/jira_activity.html",
        context={
            "project": project,
            "rows": activity_rows,
            "error": error,
        },
    )


@router.post("/projects/{code}/refresh-status",
             summary="Trigger Status Engine for a single project")
def project_refresh_status(code: str):
    """Same compute as POST /api/projects/{code}/status/refresh, but returns
    HX-Refresh so htmx reloads the project detail page.
    """
    from fastapi import HTTPException
    from app.engines.status import run_status_compute
    from app.registry.projects import get_project_by_code

    if get_project_by_code(code) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {code!r} not found in registry",
        )

    try:
        run_status_compute(code)
    except Exception:
        # Same swallow-and-continue pattern as refresh-all. Errors are still
        # captured in AIComputeLog + system.jsonl.
        pass

    return Response(status_code=204, headers={"HX-Refresh": "true"})
