"""Aggregation Engine — Prompt 1 wrapped for production use (Step 6).

For one project for one week:
1. Look up the project in the DB (registry must be sync'd first).
2. Resolve engineers on this project via engineer_registry (Step 5).
3. Fetch each engineer's JIRA activity in the report week
   (JiraClient.collect_engineer_activity, already battle-tested).
4. Render Prompt 1 with raw inputs grouped per engineer (anonymised E1, E2,
   ...) per FR §B "no engineer names".
5. Call the configured LLM (Markdown output — NOT JSON, unlike Status Engine).
6. Persist as a WeeklyReport row — insert OR update + bump regenerated_count
   (per FR §B.3.4 the aggregation must be idempotent and re-runnable).
7. Always: append AIComputeLog row for observability (NFR §6).

The output is markdown matching the project's report template sections.
The "Highlights / Things to Watch" section is intentionally left empty by
Prompt 1's system message — Step 7 (Highlights Engine, Prompt 2) fills it
in via a separate run.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select

from app.clients import get_jira_client
from app.config import get_config
from app.db import session_scope
from app.engines._aggregation_prompt import (
    PROMPT_FILE, PROMPT_VERSION,
    load_report_template, extract_section_names, render_full_prompt,
)
from app.llm.base import get_llm_client
from app.models import WeeklyReport, AIComputeLog
from app.registry.engineers import engineers_on_project
from app.registry.projects import get_project_by_code
from app.utils.dates import week_of as compute_week_of
from app.utils.logging import system_log


def _project_backfill_config(project_code: str) -> tuple[str, list[str]]:
    """Read (activity_date_field, exclude_labels) for a project from
    config.json. Returns ("", []) if the project isn't in config (we hit
    this in tests that seed the DB directly without populating config)."""
    cfg = get_config()
    for p in cfg.projects:
        if p.code == project_code:
            return p.activity_date_field or "", list(p.exclude_labels or [])
    return "", []


@dataclass
class AggregationResult:
    """Outcome of a single weekly-aggregation run.

    success:                 True iff every step succeeded (lookup, JIRA fetch,
                             template load, LLM call, non-empty content, persist).
                             False on any individual failure.
    week_of:                 The Monday of the report week the run targeted.
    content_markdown:        The LLM-generated report (only when success=True).
    error:                   Short human-readable error string when success=False.
    duration_seconds, prompt_tokens, completion_tokens: from the LLM call.
    llm_mode:                "ollama" / "openai" — recorded in AIComputeLog.
    is_regeneration:         True iff a WeeklyReport row already existed for this
                             (project, week) and was updated (regenerated_count
                             bumped). False on a fresh insert.
    engineer_count:          Engineers in scope for the week.
    activity_records:        Total ActivityRecord items collected from JIRA.
    unmapped_authors_count:  JIRA users found in activity who weren't in the
                             mapping — surfaced as a warning, not a failure.
    """
    success: bool = False
    week_of: Optional[date] = None
    content_markdown: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_mode: str = ""
    is_regeneration: bool = False
    engineer_count: int = 0
    activity_records: int = 0
    unmapped_authors_count: int = 0


def run_weekly_aggregation(
    project_code: str,
    week_of: Optional[date] = None,
    *,
    regenerate: bool = False,
    backfill_mode: bool = False,
) -> AggregationResult:
    """Run the weekly aggregation pipeline for one project for one week.

    Args:
        project_code: must already be in the DB (sync-projects first).
        week_of:      Monday of the report week. Defaults to current week (IST).
        regenerate:   Caller's intent flag — currently unused for behaviour
                      because the engine is ALWAYS idempotent: if a row exists
                      for (project, week), it's updated and regenerated_count
                      is bumped (per FR §B.3.4). Kept in the signature for
                      future extensions (e.g. force a re-fetch from JIRA even
                      if cached) and so the CLI / scheduler can record intent.
        backfill_mode: When True (used by `backfill-weekly` CLI), pull JIRA
                       tickets by the configured `activity_date_field`
                       (e.g. "Baseline end date") instead of by `updated`.
                       Required for past weeks where JIRA's system timestamps
                       all say "today" but you've manually set the activity
                       date to the historical week. When False (default), the
                       existing `updated >= week_of - 1d` JQL is used and
                       any configured `exclude_labels` are appended to filter
                       out retro-created tickets.

    Returns:
        AggregationResult — see dataclass docs.

    No exceptions raised for normal failure modes (project missing, JIRA fetch
    failure, template missing, LLM error, empty LLM response, DB persist
    failure). All return success=False with a populated `error` string AND
    write a failed AIComputeLog row. Real exceptions (programming errors)
    propagate.
    """
    log = system_log()
    started_at = datetime.utcnow()
    result = AggregationResult()

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
        log.error("aggregation: project not found",
                  extra={"event": "aggregation_failed",
                         "project_code": project_code, "stage": "lookup"})
        _save_ai_compute_log(None, started_at, success=False, llm_mode="",
                             error_text=result.error, response_excerpt="")
        return result

    project_id = project["id"]

    # ---- 2. Resolve engineers ------------------------------------------
    engineers = engineers_on_project(project_code)
    result.engineer_count = len(engineers)
    if not engineers:
        # NOT a fatal error — Prompt 1's edge case spec: "All engineers
        # missing. Output the template with each section populated as
        # 'No updates this week...'". We still call JIRA + the LLM, and
        # the LLM produces an empty-state report. The PGM sees that the
        # week was processed and the project simply had no recorded
        # activity — useful signal in itself.
        log.warning(
            "aggregation: no engineers assigned to project — proceeding with empty inputs",
            extra={"event": "aggregation_no_engineers",
                   "project_code": project_code, "week_of": str(week_of)},
        )

    # ---- 3. Fetch JIRA activity ----------------------------------------
    activity_date_field, exclude_labels = _project_backfill_config(project_code)
    try:
        jira = get_jira_client()
        engineers_dicts = [{"name": e.name, "knox_id": e.knox_id} for e in engineers]
        if backfill_mode:
            if not activity_date_field:
                result.error = (
                    f"backfill_mode=True but project {project_code!r} has no "
                    f"activity_date_field configured in config.json. "
                    f"Add e.g. \"activity_date_field\": \"Baseline end date\" "
                    f"to projects[] entry."
                )
                log.error("aggregation: backfill misconfigured",
                          extra={"event": "aggregation_failed",
                                 "project_code": project_code,
                                 "stage": "backfill_config"})
                _save_ai_compute_log(project_id, started_at, success=False,
                                     llm_mode="", error_text=result.error,
                                     response_excerpt="")
                return result
            activity = jira.collect_engineer_activity_for_backfill(
                project["jira_project_key"],
                activity_date_field,
                week_of,
                engineers_dicts,
                project.get("issue_types") or None,
            )
        else:
            activity = jira.collect_engineer_activity(
                project["jira_project_key"],
                week_of,
                engineers_dicts,
                project.get("issue_types") or None,
                exclude_labels=exclude_labels or None,
            )
    except Exception as e:
        result.error = f"JIRA activity fetch failed: {type(e).__name__}: {e}"
        log.error("aggregation: jira fetch failed",
                  extra={"event": "aggregation_failed",
                         "project_code": project_code, "stage": "jira",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False, llm_mode="",
                             error_text=result.error, response_excerpt="")
        return result

    result.activity_records = sum(len(r) for r in activity.by_engineer.values())
    result.unmapped_authors_count = len(activity.unmapped_authors)
    if result.unmapped_authors_count:
        # Log but don't fail — admin should be notified to update the mapping.
        log.warning(
            "aggregation: unmapped JIRA authors observed",
            extra={"event": "aggregation_unmapped_authors",
                   "project_code": project_code, "week_of": str(week_of),
                   "count": result.unmapped_authors_count,
                   "names": [u.display_name for u in activity.unmapped_authors[:10]]},
        )

    # ---- 4. Load report template ---------------------------------------
    try:
        template_md = load_report_template()
        sections = extract_section_names(template_md)
    except Exception as e:
        result.error = f"Report template load failed: {type(e).__name__}: {e}"
        log.error("aggregation: template load failed",
                  extra={"event": "aggregation_failed",
                         "project_code": project_code, "stage": "template",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False, llm_mode="",
                             error_text=result.error, response_excerpt="")
        return result

    # ---- 5. Render Prompt 1 --------------------------------------------
    sys_prompt, user_prompt = render_full_prompt(
        project_name=project["name"],
        project_type=project.get("type") or "general",
        project_overview=project.get("description") or "",
        week_of_str=week_of.isoformat(),
        sections=sections,
        activity_by_engineer=activity.by_engineer,
    )

    # ---- 6. Call LLM ---------------------------------------------------
    try:
        llm = get_llm_client()
    except Exception as e:
        result.error = f"LLM client construction failed: {type(e).__name__}: {e}"
        log.error("aggregation: llm client failed",
                  extra={"event": "aggregation_failed",
                         "project_code": project_code, "stage": "llm_init",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False, llm_mode="",
                             error_text=result.error, response_excerpt="")
        return result

    result.llm_mode = llm.mode
    try:
        # Prompt 1 outputs Markdown, NOT JSON — don't request json_output mode.
        llm_result = llm.complete(sys_prompt, user_prompt, json_output=False)
    except Exception as e:
        result.error = f"LLM call failed: {type(e).__name__}: {e}"
        log.error("aggregation: llm call failed",
                  extra={"event": "aggregation_failed",
                         "project_code": project_code, "stage": "llm_call",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False, llm_mode=llm.mode,
                             error_text=result.error, response_excerpt="")
        return result

    result.duration_seconds = llm_result.duration_seconds
    result.prompt_tokens = llm_result.prompt_tokens
    result.completion_tokens = llm_result.completion_tokens

    if not llm_result.text or not llm_result.text.strip():
        result.error = "LLM returned empty content"
        log.error("aggregation: empty LLM response",
                  extra={"event": "aggregation_failed",
                         "project_code": project_code, "stage": "llm_empty"})
        _save_ai_compute_log(project_id, started_at, success=False, llm_mode=llm.mode,
                             error_text=result.error, response_excerpt="")
        return result

    result.content_markdown = llm_result.text

    # ---- 7. Persist WeeklyReport ---------------------------------------
    try:
        result.is_regeneration = _persist_weekly_report(
            project_id, week_of, result.content_markdown, llm.mode
        )
    except Exception as e:
        result.error = f"DB persist failed: {type(e).__name__}: {e}"
        log.error("aggregation: persist failed",
                  extra={"event": "aggregation_failed",
                         "project_code": project_code, "stage": "persist",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False, llm_mode=llm.mode,
                             error_text=result.error,
                             response_excerpt=(llm_result.text or "")[:500])
        return result

    # ---- 8. Mark success + log -----------------------------------------
    result.success = True
    _save_ai_compute_log(project_id, started_at, success=True, llm_mode=llm.mode,
                         error_text="",
                         response_excerpt=(llm_result.text or "")[:500])
    log.info(
        "aggregation success",
        extra={"event": "aggregation_success",
               "project_code": project_code,
               "week_of": str(week_of),
               "is_regeneration": result.is_regeneration,
               "engineer_count": result.engineer_count,
               "activity_records": result.activity_records,
               "unmapped_authors_count": result.unmapped_authors_count,
               "duration_seconds": result.duration_seconds,
               "prompt_tokens": result.prompt_tokens,
               "completion_tokens": result.completion_tokens,
               "regenerate_intent": regenerate,
               "backfill_mode": backfill_mode},
    )
    return result


# ---------- DB helpers ---------------------------------------------------

def _persist_weekly_report(
    project_id: int,
    week_of_date: date,
    content_markdown: str,
    llm_mode: str,
) -> bool:
    """Upsert the WeeklyReport row for (project, week).

    First insert: regenerated_count=0, last_regenerated_at=None.
    Subsequent run for same (project, week): bump regenerated_count, set
    last_regenerated_at, refresh content + prompt_version + llm_mode_used.

    The unique index on (project_id, week_of) ensures we never duplicate.
    Returns True iff this was a regeneration (existing row updated), False
    if it was a fresh insert.
    """
    now = datetime.utcnow()

    with session_scope() as s:
        existing = s.execute(
            select(WeeklyReport).where(
                WeeklyReport.project_id == project_id,
                WeeklyReport.week_of == week_of_date,
            )
        ).scalar_one_or_none()

        if existing is None:
            s.add(WeeklyReport(
                project_id=project_id,
                week_of=week_of_date,
                content_markdown=content_markdown,
                generated_at=now,
                regenerated_count=0,
                last_regenerated_at=None,
                prompt_version_aggregation=PROMPT_VERSION,
                prompt_version_highlights="",   # populated by Step 7
                llm_mode_used=llm_mode,
            ))
            return False

        existing.content_markdown = content_markdown
        existing.regenerated_count = (existing.regenerated_count or 0) + 1
        existing.last_regenerated_at = now
        existing.prompt_version_aggregation = PROMPT_VERSION
        existing.llm_mode_used = llm_mode
        # NOTE: prompt_version_highlights left as-is. If/when Step 7 ran on
        # the prior version of this report, the highlights spliced into
        # content_markdown were appropriate to that prior content. After
        # this regeneration the content has changed, so any cached
        # Highlights should be refreshed by re-running Step 7. The Step 7
        # engine clears the prompt_version_highlights field appropriately;
        # we don't touch it here to avoid losing audit info if the user
        # never reruns highlights.
        return True


def _save_ai_compute_log(
    project_id: Optional[int],
    started_at: datetime,
    *,
    success: bool,
    llm_mode: str,
    error_text: str,
    response_excerpt: str,
) -> None:
    """Append one row to ai_compute_log table for observability per NFR §6.

    Same pattern as Status Engine — own session, doesn't roll back upstream
    work, project_id can be None on early failures.
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
