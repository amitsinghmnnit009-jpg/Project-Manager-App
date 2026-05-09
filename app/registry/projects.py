"""Project registry — sync from config.json to DB, lookup helpers.

Reads the `projects` array from config.json and upserts into the
`projects` table. Engines / scheduler / API call the lookup helpers
(`get_project_by_code`, `list_projects`) instead of re-reading
config.json each time.

Phase 1 sync model:
- Insert: project in config.json but not in DB by `code` -> insert
- Update: project in both -> overwrite all syncable columns
- Delete: project in DB but not in config.json -> NOT auto-deleted
  (admin removes from config.json and the row stays; we log a
  warning. Hard-delete via direct DB edit if truly desired.)

This avoids accidental data loss when an admin temporarily comments out
a project entry. ProjectStatusHistory rows are preserved either way.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select

from app.config import get_config, ProjectConfig
from app.db import session_scope
from app.models import Project
from app.utils.logging import system_log


# ---------- Helpers ------------------------------------------------------

def _parse_date(s: Optional[str]) -> Optional[date]:
    """Parse a YYYY-MM-DD string. Returns None for empty / None."""
    if not s:
        return None
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _project_config_to_columns(cfg: ProjectConfig) -> dict:
    """Map a ProjectConfig (Pydantic) to a dict of Project ORM columns.

    JSON list fields are stored as `*_json` columns to keep the DB schema
    simple in Phase 1. SQLAlchemy serialises them automatically.
    """
    return {
        "code": cfg.code,
        "name": cfg.name,
        "type": cfg.type,
        "description": cfg.description,
        "owning_tl": cfg.owning_tl,
        "owning_pgm": cfg.owning_pgm,
        "start_date": _parse_date(cfg.start_date),
        "planned_end_date": _parse_date(cfg.planned_end_date),
        "confluence_milestones_url": cfg.confluence_milestones_url,
        "confluence_fr_url": cfg.confluence_fr_url,
        "confluence_extra_pages_json": list(cfg.confluence_extra_pages),
        "jira_project_key": cfg.jira_project_key,
        "weekly_cutoff": cfg.weekly_cutoff,
        "week_boundary": cfg.week_boundary,
        "recompute_cadence": cfg.recompute_cadence,
        "issue_types_json": list(cfg.issue_types),
        "chronic_threshold": cfg.chronic_threshold,
        "holiday_calendar_id": cfg.holiday_calendar_id,
    }


def _row_to_dict(row: Project) -> dict:
    """Convert a Project ORM row to a plain dict (so callers don't have to
    pin the SQLAlchemy session for the duration of their use)."""
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "type": row.type,
        "description": row.description,
        "owning_tl": row.owning_tl,
        "owning_pgm": row.owning_pgm,
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "planned_end_date": row.planned_end_date.isoformat() if row.planned_end_date else None,
        "state": row.state,
        "confluence_milestones_url": row.confluence_milestones_url,
        "confluence_fr_url": row.confluence_fr_url,
        "confluence_extra_pages": list(row.confluence_extra_pages_json or []),
        "jira_project_key": row.jira_project_key,
        "weekly_cutoff": row.weekly_cutoff,
        "week_boundary": row.week_boundary,
        "recompute_cadence": row.recompute_cadence,
        "issue_types": list(row.issue_types_json or []),
        "chronic_threshold": row.chronic_threshold,
        "holiday_calendar_id": row.holiday_calendar_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ---------- Sync ---------------------------------------------------------

def sync_projects_from_config() -> dict:
    """Read config.json projects[] and upsert into the projects table.

    Returns a small summary dict so CLI / startup hook can print it:
        {
          "created_count":       <int>,   # number of newly-inserted rows
          "updated_count":       <int>,   # number of existing rows refreshed
          "total_in_config":     <int>,
          "total_in_db_after":   <int>,
          "stale_codes":         [<codes in DB not in config>],
        }

    Note on key names: 'created' / 'updated' are tempting but 'created' is
    a reserved attribute on Python's LogRecord (it's the log line's own
    timestamp). Passing it via `extra={...}` raises KeyError. Using
    `created_count` / `updated_count` avoids the collision and is also
    less ambiguous to the reader.

    Idempotent — safe to call repeatedly. Code matches are exact (no
    case-folding) because `code` is the project's canonical identifier
    and case mismatches deserve to be visible.
    """
    log = system_log()
    cfg = get_config()
    config_codes = {p.code for p in cfg.projects}

    created_count = 0
    updated_count = 0

    with session_scope() as s:
        for pcfg in cfg.projects:
            row = s.execute(
                select(Project).where(Project.code == pcfg.code)
            ).scalar_one_or_none()

            data = _project_config_to_columns(pcfg)

            if row is None:
                row = Project(**data)
                s.add(row)
                created_count += 1
            else:
                for k, v in data.items():
                    setattr(row, k, v)
                updated_count += 1

        # Detect stale projects (in DB but no longer in config)
        all_db_codes = list(s.execute(select(Project.code)).scalars().all())
        stale = sorted(c for c in all_db_codes if c not in config_codes)
        total_in_db_after = len(all_db_codes)

    if stale:
        log.warning(
            "registry sync: project codes present in DB but not in config.json — left untouched",
            extra={"event": "registry_stale_projects", "stale_codes": stale},
        )

    report = {
        "created_count": created_count,
        "updated_count": updated_count,
        "total_in_config": len(cfg.projects),
        "total_in_db_after": total_in_db_after,
        "stale_codes": stale,
    }
    log.info("registry sync complete", extra={"event": "registry_sync", **report})
    return report


# ---------- Lookups ------------------------------------------------------

def get_project_by_code(code: str) -> Optional[dict]:
    """Look up a project by its canonical `code`. Returns dict or None."""
    with session_scope() as s:
        row = s.execute(
            select(Project).where(Project.code == code)
        ).scalar_one_or_none()
        if row is None:
            return None
        return _row_to_dict(row)


def get_project_by_jira_key(jira_project_key: str) -> Optional[dict]:
    """Look up a project by its JIRA project key.

    Useful when an engine is processing a JIRA payload and only knows the
    JIRA key — the code/jira_project_key fields can differ.
    """
    with session_scope() as s:
        row = s.execute(
            select(Project).where(Project.jira_project_key == jira_project_key)
        ).scalar_one_or_none()
        if row is None:
            return None
        return _row_to_dict(row)


def list_projects() -> list[dict]:
    """List all projects in the DB, ordered by code."""
    with session_scope() as s:
        rows = s.execute(
            select(Project).order_by(Project.code)
        ).scalars().all()
        return [_row_to_dict(r) for r in rows]
