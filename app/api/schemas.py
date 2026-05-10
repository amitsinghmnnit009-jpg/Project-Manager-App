"""Pydantic response models for the public REST API (Step 11).

One module so callers (UI in a future phase, integration tests, ad-hoc
scripts) can `from app.api.schemas import ProjectSummary` instead of
chasing types across routers.

Convention:
- Read-side models end in `Out` / `Summary` / `Detail`
- Trigger-result models end in `RefreshResult`
- Log-entry models end in `Entry`
- All `Optional`s are explicit; defaults are `None`
- Dates are `date`; datetimes are `datetime` (FastAPI serialises both as ISO)
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


# ---------- Projects ----------------------------------------------------

class ProjectSummary(BaseModel):
    """Used by GET /api/projects (list view)."""
    code: str
    name: str
    type: str
    state: str
    owning_tl: str
    owning_pgm: str
    start_date: Optional[date] = None
    planned_end_date: Optional[date] = None


class ProjectDetail(ProjectSummary):
    """Used by GET /api/projects/{code} — full record, no secrets."""
    description: str = ""
    confluence_milestones_url: str
    confluence_fr_url: str
    confluence_extra_pages: list[str] = []
    jira_project_key: str
    weekly_cutoff: str
    week_boundary: str
    recompute_cadence: Optional[str] = None
    issue_types: list[str] = []
    chronic_threshold: int
    holiday_calendar_id: str


class TaskItem(BaseModel):
    """Used by GET /api/projects/{code}/tasks — minimal per FR §C.2."""
    id: str            # JIRA issue key (e.g. PROJ-123)
    title: str         # JIRA summary
    status: str        # JIRA status name
    last_activity: Optional[str] = None  # ISO timestamp
    url: str           # browse URL


# ---------- Status ------------------------------------------------------

class ProjectStatusOut(BaseModel):
    """Used by GET /api/projects/{code}/status."""
    project_code: str
    computed_at: datetime
    overall_health: str           # Green | Amber | Red | InsufficientEvidence
    schedule_status: str          # OnTrack | AtRisk | Slipping | Delayed | InsufficientEvidence
    completion_pct: Optional[int] = None
    confidence: str               # High | Medium | Low
    rationale: str
    milestones: list[dict] = []   # per-milestone JSON blob from the LLM
    prompt_version: str
    llm_mode_used: str


class StatusHistoryEntry(BaseModel):
    """One row from /api/projects/{code}/status/history."""
    computed_at: datetime
    prior_health: str
    new_health: str
    prior_schedule: str
    new_schedule: str
    prior_completion_pct: Optional[int] = None
    new_completion_pct: Optional[int] = None
    rationale: str
    prompt_version: str


class StatusRefreshResult(BaseModel):
    """Returned by POST /api/projects/{code}/status/refresh.
    Mirrors StatusComputeResult but trimmed to JSON-serialisable fields."""
    success: bool
    error: str = ""
    overall_health: Optional[str] = None
    schedule_status: Optional[str] = None
    completion_pct: Optional[int] = None
    confidence: Optional[str] = None
    rationale: str = ""
    duration_seconds: float = 0.0
    changed: Optional[bool] = None
    validation_issues: list[str] = []


# ---------- Weekly reports ----------------------------------------------

class WeeklyReportSummary(BaseModel):
    """Used by GET /api/projects/{code}/reports (list)."""
    project_code: str
    week_of: date
    generated_at: datetime
    regenerated_count: int = 0
    last_regenerated_at: Optional[datetime] = None
    has_highlights: bool = False  # True iff prompt_version_highlights is non-empty


class WeeklyReportDetail(WeeklyReportSummary):
    """Used by GET /api/projects/{code}/reports/{week_of}
    and /reports/latest — full markdown content."""
    content_markdown: str
    prompt_version_aggregation: str
    prompt_version_highlights: str
    llm_mode_used: str


class AggregationRefreshResult(BaseModel):
    """Returned by POST /api/projects/{code}/reports/{week_of}/regenerate."""
    success: bool
    error: str = ""
    week_of: Optional[date] = None
    is_regeneration: bool = False
    engineer_count: int = 0
    activity_records: int = 0
    unmapped_authors_count: int = 0
    duration_seconds: float = 0.0
    content_excerpt: str = ""   # first 500 chars of generated markdown


class HighlightsRefreshResult(BaseModel):
    """Returned by POST /api/projects/{code}/reports/{week_of}/highlights/refresh."""
    success: bool
    error: str = ""
    week_of: Optional[date] = None
    is_first_week: bool = False
    last_week_of: Optional[date] = None
    duration_seconds: float = 0.0
    highlights_excerpt: str = ""   # first 500 chars of LLM-produced highlights


# ---------- Admin / observability --------------------------------------

class AdminConfigSafe(BaseModel):
    """Selected, non-sensitive subset of config exposed via GET /admin/config.

    Excludes: jira.token, confluence.token, llm.openai.api_key,
    llm.openai.custom_headers, smtp credentials, etc.
    """
    llm_provider: str
    llm_temperature: float
    jira_base_url: str
    jira_api_version: str
    confluence_base_url: str
    database_url: str
    logging_directory: str
    logging_level: str
    scheduler_status_recompute_cadence: str
    scheduler_daily_status_hour: int
    scheduler_weekly_aggregation_offset_minutes: int
    scheduler_timezone: str
    scheduler_misfire_grace_seconds: int
    reminders_hours_before_cutoff: int
    reminders_hours_after_cutoff: int
    reports_retention_weeks: int
    api_host: str
    api_port: int
    project_count: int


class AIComputeLogEntry(BaseModel):
    id: int
    project_id: Optional[int] = None
    project_code: Optional[str] = None     # joined from projects.code
    prompt_name: str
    prompt_version: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    success_flag: bool
    llm_mode: str
    response_excerpt: str
    error_text: str


class ReminderLogEntry(BaseModel):
    id: int
    engineer_knox_id: str
    engineer_name: str
    project_id: int
    project_code: Optional[str] = None     # joined
    week_of: date
    type: str            # 'pre_cutoff' | 'post_cutoff'
    sent_at: datetime
    channel: str
    status: str


class SyncLogEntry(BaseModel):
    id: int
    source: str
    project_id: Optional[int] = None
    project_code: Optional[str] = None     # joined
    started_at: datetime
    finished_at: Optional[datetime] = None
    success_flag: bool
    error_text: str


class SchedulerJobInfo(BaseModel):
    """One entry in GET /admin/scheduler/jobs."""
    id: str
    name: str
    next_run: Optional[str] = None       # ISO datetime, in scheduler timezone
    trigger: str                          # str(CronTrigger) for transparency
