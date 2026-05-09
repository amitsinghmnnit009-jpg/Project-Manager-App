"""Highlights Engine — Prompt 2 wrapped for production use (Step 7).

For one project for one week:
1. Look up the project in the DB (registry must be sync'd first).
2. Load this week's WeeklyReport row (REQUIRED — Step 6 must have run first).
3. Load last week's WeeklyReport row (OPTIONAL — first-week is a valid case
   that produces a "no prior week" placeholder, not a failure).
4. Strip any existing Highlights content from this week's report so the LLM
   never sees its own prior output (re-run safety).
5. Render Prompt 2 via shared helpers in `_highlights_prompt.py`.
6. Call the LLM (Markdown output, NOT JSON — same as Prompt 1).
7. Splice the LLM's output into this week's report's "Highlights / Things
   to Watch" section.
8. Persist: update the WeeklyReport row's content_markdown +
   prompt_version_highlights. Does NOT bump regenerated_count (that field
   tracks aggregation regenerations, not highlights re-runs).
9. Always: append AIComputeLog row for observability (NFR §6).

Idempotent: re-running for the same (project, week) replaces the existing
Highlights section. Step 6's docstring notes that prompt_version_highlights
goes stale after Step 6 regenerates a report — running this engine again
on the same week refreshes both the spliced content and that audit field.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select

from app.db import session_scope
from app.engines._highlights_prompt import (
    PROMPT_FILE, PROMPT_VERSION,
    empty_highlights_section, render_full_prompt, splice_highlights,
)
from app.llm.base import get_llm_client
from app.models import WeeklyReport, AIComputeLog
from app.registry.projects import get_project_by_code
from app.utils.dates import week_of as compute_week_of
from app.utils.logging import system_log


@dataclass
class HighlightsResult:
    """Outcome of a single highlights-engine run.

    success:                 True iff every step succeeded (lookup, this-week
                             report exists, LLM call, splice, DB persist).
    week_of:                 Monday of the report week the run targeted.
    last_week_of:            Monday of the prior week (None if first-week).
    is_first_week:           True iff no prior-week WeeklyReport row exists;
                             the LLM was told there's no prior report and
                             produces the standard placeholder line.
    highlights_section:      Just the LLM's Prompt 2 output (the contents of
                             the Highlights / Things to Watch section).
    content_markdown:        The full this-week report AFTER splicing.
    error:                   Short human-readable error when success=False.
    duration_seconds, prompt_tokens, completion_tokens: from the LLM call.
    llm_mode:                "ollama" / "openai" — recorded in AIComputeLog.
    """
    success: bool = False
    week_of: Optional[date] = None
    last_week_of: Optional[date] = None
    is_first_week: bool = False
    highlights_section: str = ""
    content_markdown: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_mode: str = ""


# ---------- Public entrypoint -------------------------------------------

def run_highlights(
    project_code: str,
    week_of: Optional[date] = None,
) -> HighlightsResult:
    """Generate the Highlights section for the current week's report and
    splice it into the saved WeeklyReport row.

    Args:
        project_code: must already be in the DB (sync-projects first).
        week_of:      Monday of the report week. Defaults to current week (IST).
                      Step 6 must have already produced a WeeklyReport row
                      for this (project, week) — otherwise we have nothing to
                      annotate and we fail with a clear error.

    Returns:
        HighlightsResult — see dataclass docs.

    No exceptions raised for normal failure modes (project missing, this-week
    report missing, LLM error, splice failure, DB persist failure). All of
    these return success=False with a populated `error` string AND write a
    failed AIComputeLog row. Real exceptions (programming errors) propagate.
    """
    log = system_log()
    started_at = datetime.utcnow()
    result = HighlightsResult()

    if week_of is None:
        week_of = compute_week_of()
    result.week_of = week_of

    # ---- 1. Look up project --------------------------------------------
    project = get_project_by_code(project_code)
    if project is None:
        result.error = (
            f"Project {project_code!r} not found in DB. "
            f"Run 'python manage.py sync-projects' first."
        )
        log.error("highlights: project not found",
                  extra={"event": "highlights_failed",
                         "project_code": project_code, "stage": "lookup"})
        _save_ai_compute_log(None, started_at, success=False, llm_mode="",
                             error_text=result.error, response_excerpt="")
        return result
    project_id = project["id"]

    # ---- 2. Load this week's WeeklyReport (REQUIRED) -------------------
    this_week_report = _load_weekly_report(project_id, week_of)
    if this_week_report is None:
        result.error = (
            f"No WeeklyReport row exists for project {project_code!r} "
            f"week_of={week_of.isoformat()}. Run 'python manage.py "
            f"run-aggregation {project_code} --week-of {week_of.isoformat()}' first."
        )
        log.error("highlights: this-week report missing",
                  extra={"event": "highlights_failed",
                         "project_code": project_code, "stage": "load_this_week",
                         "week_of": str(week_of)})
        _save_ai_compute_log(project_id, started_at, success=False, llm_mode="",
                             error_text=result.error, response_excerpt="")
        return result

    # ---- 3. Load last week's WeeklyReport (OPTIONAL) -------------------
    last_week_of = week_of - timedelta(days=7)
    last_week_report = _load_weekly_report(project_id, last_week_of)
    result.is_first_week = last_week_report is None
    result.last_week_of = None if last_week_report is None else last_week_of

    # ---- 4. Strip prior Highlights from this-week report ---------------
    # Re-run safety: if we already filled Highlights once, the LLM mustn't
    # see its own prior output. Step 6's freshly-generated reports already
    # have Highlights empty; this is a no-op for those.
    this_week_clean = empty_highlights_section(this_week_report["content_markdown"])

    # ---- 5. Render Prompt 2 --------------------------------------------
    sys_prompt, user_prompt = render_full_prompt(
        project_name=project["name"],
        this_week_date=week_of.isoformat(),
        last_week_date=(
            last_week_of.isoformat() if last_week_report is not None else None
        ),
        last_week_full_report=(
            last_week_report["content_markdown"]
            if last_week_report is not None else None
        ),
        this_week_draft_report=this_week_clean,
    )

    # ---- 6. Call LLM ---------------------------------------------------
    try:
        llm = get_llm_client()
    except Exception as e:
        result.error = f"LLM client construction failed: {type(e).__name__}: {e}"
        log.error("highlights: llm client failed",
                  extra={"event": "highlights_failed",
                         "project_code": project_code, "stage": "llm_init",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False, llm_mode="",
                             error_text=result.error, response_excerpt="")
        return result

    result.llm_mode = llm.mode
    try:
        # Prompt 2 outputs Markdown bullets, NOT JSON.
        llm_result = llm.complete(sys_prompt, user_prompt, json_output=False)
    except Exception as e:
        result.error = f"LLM call failed: {type(e).__name__}: {e}"
        log.error("highlights: llm call failed",
                  extra={"event": "highlights_failed",
                         "project_code": project_code, "stage": "llm_call",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode=llm.mode, error_text=result.error,
                             response_excerpt="")
        return result

    result.duration_seconds = llm_result.duration_seconds
    result.prompt_tokens = llm_result.prompt_tokens
    result.completion_tokens = llm_result.completion_tokens

    if not llm_result.text or not llm_result.text.strip():
        result.error = "LLM returned empty content"
        log.error("highlights: empty LLM response",
                  extra={"event": "highlights_failed",
                         "project_code": project_code, "stage": "llm_empty"})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode=llm.mode, error_text=result.error,
                             response_excerpt="")
        return result

    result.highlights_section = llm_result.text

    # ---- 7. Splice into this week's report -----------------------------
    try:
        result.content_markdown = splice_highlights(
            this_week_clean, result.highlights_section
        )
    except Exception as e:
        # splice should never raise on valid markdown — defensive only.
        result.error = f"Splice failed: {type(e).__name__}: {e}"
        log.error("highlights: splice failed",
                  extra={"event": "highlights_failed",
                         "project_code": project_code, "stage": "splice",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode=llm.mode, error_text=result.error,
                             response_excerpt=(llm_result.text or "")[:500])
        return result

    # ---- 8. Persist updated WeeklyReport -------------------------------
    try:
        _persist_highlights(project_id, week_of, result.content_markdown)
    except Exception as e:
        result.error = f"DB persist failed: {type(e).__name__}: {e}"
        log.error("highlights: persist failed",
                  extra={"event": "highlights_failed",
                         "project_code": project_code, "stage": "persist",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode=llm.mode, error_text=result.error,
                             response_excerpt=(llm_result.text or "")[:500])
        return result

    # ---- 9. Mark success + log -----------------------------------------
    result.success = True
    _save_ai_compute_log(project_id, started_at, success=True,
                         llm_mode=llm.mode, error_text="",
                         response_excerpt=(llm_result.text or "")[:500])
    log.info(
        "highlights success",
        extra={"event": "highlights_success",
               "project_code": project_code,
               "week_of": str(week_of),
               "is_first_week": result.is_first_week,
               "last_week_of": str(last_week_of) if last_week_report else None,
               "duration_seconds": result.duration_seconds,
               "prompt_tokens": result.prompt_tokens,
               "completion_tokens": result.completion_tokens},
    )
    return result


# ---------- DB helpers ---------------------------------------------------

def _load_weekly_report(project_id: int, week_of_date: date) -> Optional[dict]:
    """Load a WeeklyReport row by (project, week) and return as a plain dict
    with the fields this engine needs. Returns None when no row exists.

    Plain dict (not the ORM object) keeps callers free of session lifetime
    concerns — this engine doesn't need to mutate any field other than
    via _persist_highlights() below.
    """
    with session_scope() as s:
        row = s.execute(
            select(WeeklyReport).where(
                WeeklyReport.project_id == project_id,
                WeeklyReport.week_of == week_of_date,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": row.id,
            "week_of": row.week_of,
            "content_markdown": row.content_markdown or "",
        }


def _persist_highlights(
    project_id: int,
    week_of_date: date,
    full_markdown_with_highlights: str,
) -> None:
    """Update the WeeklyReport row's content_markdown +
    prompt_version_highlights.

    Does NOT touch regenerated_count or last_regenerated_at — those track
    aggregation runs (Step 6), not highlights runs. The unique
    (project_id, week_of) index guarantees we hit at most one row.

    Caller has already verified the row exists (via _load_weekly_report
    above), so this is an unconditional UPDATE rather than an upsert.
    """
    with session_scope() as s:
        row = s.execute(
            select(WeeklyReport).where(
                WeeklyReport.project_id == project_id,
                WeeklyReport.week_of == week_of_date,
            )
        ).scalar_one()
        row.content_markdown = full_markdown_with_highlights
        row.prompt_version_highlights = PROMPT_VERSION


def _save_ai_compute_log(
    project_id: Optional[int],
    started_at: datetime,
    *,
    success: bool,
    llm_mode: str,
    error_text: str,
    response_excerpt: str,
) -> None:
    """Append one row to ai_compute_log (NFR §6 observability).

    Same pattern as Status + Aggregation engines — own session, doesn't roll
    back upstream work, project_id can be None on early failures.
    """
    try:
        with session_scope() as s:
            s.add(AIComputeLog(
                project_id=project_id,
                prompt_name=PROMPT_FILE,
                prompt_version=PROMPT_VERSION,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                success_flag=success,
                llm_mode=llm_mode,
                response_excerpt=(response_excerpt or "")[:1000],
                error_text=(error_text or "")[:1000],
            ))
    except Exception as e:
        system_log().error(
            "ai_compute_log write failed",
            extra={"event": "ai_compute_log_write_failed",
                   "project_id": project_id, "error": str(e),
                   "type": type(e).__name__},
        )
