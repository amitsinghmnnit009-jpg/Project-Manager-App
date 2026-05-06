"""SQLAlchemy ORM models — Phase 1 schema.

Tables (per BACKEND_ARCHITECTURE_PHASE1.md §5):
- Project                  — projects synced from config.yaml at startup
- WeeklyReport             — AI-generated consolidated reports
- ProjectStatus            — latest AI status per project (one row per project)
- ProjectStatusHistory     — append-only timeline of status changes
- JiraTaskCache            — last fetched JIRA task snapshot
- ConfluencePageCache      — last fetched Confluence page content per project
- ReminderLog              — emails sent to engineers (mocked in Phase 1)
- AIComputeLog             — every LLM call (for observability)
- SyncLog                  — every JIRA/Confluence fetch (for observability)
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Date, Boolean, ForeignKey,
    JSON, Index,
)
from sqlalchemy.orm import relationship

from app.db import Base


# --- Project ---------------------------------------------------------------

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(64), default="general")
    description = Column(Text, default="")
    owning_tl = Column(String(255), default="")
    owning_pgm = Column(String(255), default="")
    start_date = Column(Date, nullable=True)
    planned_end_date = Column(Date, nullable=True)
    state = Column(String(32), default="active")
    confluence_page_url = Column(String(1024), nullable=False)
    jira_project_key = Column(String(64), nullable=False)
    weekly_cutoff = Column(String(32), default="Mon 13:00")
    week_boundary = Column(String(16), default="monday")
    recompute_cadence = Column(String(16), nullable=True)
    issue_types_json = Column(JSON, default=list)  # configurable per FR
    chronic_threshold = Column(Integer, default=3)
    holiday_calendar_id = Column(String(64), default="default")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --- WeeklyReport ----------------------------------------------------------

class WeeklyReport(Base):
    __tablename__ = "weekly_reports"
    __table_args__ = (
        Index("idx_weekly_reports_project_week", "project_id", "week_of", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    week_of = Column(Date, nullable=False)
    content_markdown = Column(Text, default="")
    generated_at = Column(DateTime, default=datetime.utcnow)
    regenerated_count = Column(Integer, default=0)
    last_regenerated_at = Column(DateTime, nullable=True)
    prompt_version_aggregation = Column(String(64), default="")
    prompt_version_highlights = Column(String(64), default="")
    llm_mode_used = Column(String(16), default="")  # ollama|openai


# --- ProjectStatus (current) ----------------------------------------------

class ProjectStatus(Base):
    __tablename__ = "project_statuses"
    __table_args__ = (
        Index("idx_project_statuses_project", "project_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    computed_at = Column(DateTime, default=datetime.utcnow)
    overall_health = Column(String(32), default="InsufficientEvidence")
    schedule_status = Column(String(32), default="InsufficientEvidence")
    completion_pct = Column(Integer, nullable=True)
    milestones_json = Column(JSON, default=list)
    rationale = Column(Text, default="")
    confidence = Column(String(16), default="Low")
    prompt_version = Column(String(64), default="")
    llm_mode_used = Column(String(16), default="")


# --- ProjectStatusHistory (append-only) -----------------------------------

class ProjectStatusHistory(Base):
    __tablename__ = "project_status_history"
    __table_args__ = (
        Index("idx_status_history_project_time", "project_id", "computed_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    computed_at = Column(DateTime, default=datetime.utcnow)
    prior_health = Column(String(32), default="")
    new_health = Column(String(32), default="")
    prior_schedule = Column(String(32), default="")
    new_schedule = Column(String(32), default="")
    prior_completion_pct = Column(Integer, nullable=True)
    new_completion_pct = Column(Integer, nullable=True)
    rationale = Column(Text, default="")
    prompt_version = Column(String(64), default="")


# --- JiraTaskCache --------------------------------------------------------

class JiraTaskCache(Base):
    __tablename__ = "jira_task_cache"
    __table_args__ = (
        Index("idx_jira_task_cache_project_jira_id", "project_id", "jira_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    jira_id = Column(String(64), nullable=False)
    snapshot_json = Column(JSON, default=dict)
    last_synced_at = Column(DateTime, default=datetime.utcnow)


# --- ConfluencePageCache --------------------------------------------------

class ConfluencePageCache(Base):
    __tablename__ = "confluence_page_cache"
    __table_args__ = (
        Index("idx_conf_cache_project", "project_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    content_raw = Column(Text, default="")
    content_parsed_json = Column(JSON, default=dict)
    last_synced_at = Column(DateTime, default=datetime.utcnow)
    last_sync_error_text = Column(Text, default="")


# --- ReminderLog ----------------------------------------------------------

class ReminderLog(Base):
    __tablename__ = "reminder_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    engineer_knox_id = Column(String(255), nullable=False)
    engineer_name = Column(String(255), default="")
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    week_of = Column(Date, nullable=False)
    type = Column(String(32), default="")  # pre_cutoff | post_cutoff
    sent_at = Column(DateTime, default=datetime.utcnow)
    channel = Column(String(16), default="email")
    status = Column(String(32), default="mocked")  # mocked | sent | failed


# --- AIComputeLog ---------------------------------------------------------

class AIComputeLog(Base):
    __tablename__ = "ai_compute_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    prompt_name = Column(String(64), default="")
    prompt_version = Column(String(64), default="")
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    success_flag = Column(Boolean, default=False)
    llm_mode = Column(String(16), default="")
    response_excerpt = Column(Text, default="")
    error_text = Column(Text, default="")


# --- SyncLog --------------------------------------------------------------

class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(16), default="")  # jira | confluence
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    success_flag = Column(Boolean, default=False)
    error_text = Column(Text, default="")
