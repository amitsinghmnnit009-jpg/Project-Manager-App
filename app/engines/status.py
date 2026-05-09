"""Status Engine — Prompt 3 wrapped for production use (Step 8).

For one project:
1. Look up the project in the DB (registry must be sync'd first).
2. Fetch Confluence pages (Milestones + FR + extras) via ConfluenceClient.
3. Fetch JIRA snapshot via JiraClient.
4. Load last N consolidated weekly reports from DB. Step 6 will populate
   WeeklyReport rows; until then this returns [] and the AI's confidence
   appropriately drops.
5. Render Prompt 3 v2 (shared helpers in `_status_prompt.py`).
6. Call the configured LLM with json_output=True.
7. Parse + validate the JSON against the schema (asymmetric trust enforced).
8. Persist on success:
     - upsert ProjectStatus (one row per project — refreshes computed_at +
       all output fields)
     - append ProjectStatusHistory ONLY when health/schedule/completion
       changed since the last row (per FR §A.6.1)
9. Always: append AIComputeLog row for observability (NFR §6).

The standalone test script at scripts/test_status_prompt.py shares the
prompt rendering + validation helpers with this engine — same prompt for
the same inputs, no drift.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.clients import (
    get_jira_client, get_confluence_client, ConfluenceClient,
)
from app.config import get_config
from app.db import session_scope
from app.engines._status_prompt import (
    PROMPT_FILE, PROMPT_VERSION,
    render_full_prompt, validate_prompt3_json,
)
from app.llm.base import get_llm_client
from app.models import (
    ProjectStatus, ProjectStatusHistory, AIComputeLog, WeeklyReport,
)
from app.registry.projects import get_project_by_code
from app.utils.dates import today_ist
from app.utils.logging import system_log


@dataclass
class StatusComputeResult:
    """Outcome of a single status-engine run.

    success:     True iff every step succeeded (lookup, fetch, LLM call,
                 JSON parse, schema validation, DB persist). False on any
                 individual failure.
    parsed:      Validated JSON dict (only when success=True).
    raw_response: LLM's raw text (set whenever the LLM was actually called).
    issues:      Schema validation issues (empty when success=True).
    error:       Short human-readable error string when success=False.
    duration_seconds, prompt_tokens, completion_tokens: from the LLM call.
    llm_mode:    "ollama" / "openai" — recorded for the AIComputeLog row.
    changed:     True iff this compute differs from the previous ProjectStatus
                 row on health/schedule/completion (drove a history append).
                 None when there was no previous row OR no successful persist.
    """
    success: bool = False
    parsed: Optional[dict] = None
    raw_response: str = ""
    issues: list[str] = field(default_factory=list)
    error: str = ""
    duration_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_mode: str = ""
    changed: Optional[bool] = None


# ---------- Public entrypoint -------------------------------------------

def run_status_compute(project_code: str) -> StatusComputeResult:
    """Compute current project status and persist to DB.

    See module docstring for the full pipeline. Returns a
    StatusComputeResult — see dataclass docs for fields.

    No exceptions raised for normal failure modes (project not found,
    Confluence unreachable, LLM error, schema violation). All of these
    return success=False with a populated `error` string AND write an
    AIComputeLog row marking the failure. Real exceptions (programming
    errors) propagate.
    """
    log = system_log()
    started_at = datetime.utcnow()
    result = StatusComputeResult()

    # ---- 1. Look up project in DB ---------------------------------------
    project = get_project_by_code(project_code)
    if project is None:
        result.error = (
            f"Project {project_code!r} not found in DB. "
            f"Run 'python manage.py sync-projects' first."
        )
        log.error("status compute: project not found",
                  extra={"event": "status_compute_failed",
                         "project_code": project_code, "stage": "lookup"})
        _save_ai_compute_log(None, started_at, success=False,
                             llm_mode="", error_text=result.error,
                             response_excerpt="")
        return result

    project_id = project["id"]

    # ---- 2. Fetch Confluence pages (required: milestones + FR) ---------
    confl_cfg = get_config().confluence
    try:
        confl = get_confluence_client()
        m_page = confl.get_page_by_url(project["confluence_milestones_url"])
        ms = ConfluenceClient.parse_milestones_page(m_page)
        f_page = confl.get_page_by_url(project["confluence_fr_url"])
        fr = ConfluenceClient.parse_fr_page(f_page)
    except Exception as e:
        result.error = f"Confluence fetch failed: {type(e).__name__}: {e}"
        log.error("status compute: confluence fetch failed",
                  extra={"event": "status_compute_failed",
                         "project_code": project_code, "stage": "confluence",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode="", error_text=result.error,
                             response_excerpt="")
        return result

    # ---- 2b. Fetch optional extra context pages (failures don't abort) -
    extras: list = []
    extra_max = confl_cfg.extra_page_max_chars
    for url in project.get("confluence_extra_pages") or []:
        try:
            ep = confl.get_page_by_url(url)
            extras.append(
                ConfluenceClient.parse_extra_page(ep, max_chars=extra_max)
            )
        except Exception as e:
            log.warning(
                "status compute: extra page fetch failed; continuing",
                extra={"event": "status_compute_extra_page_failed",
                       "project_code": project_code, "url": url,
                       "error": str(e), "type": type(e).__name__},
            )

    # ---- 3. Fetch JIRA snapshot -----------------------------------------
    try:
        jira = get_jira_client()
        snap = jira.get_project_snapshot(
            project["jira_project_key"], project.get("issue_types") or None
        )
    except Exception as e:
        result.error = f"JIRA fetch failed: {type(e).__name__}: {e}"
        log.error("status compute: jira fetch failed",
                  extra={"event": "status_compute_failed",
                         "project_code": project_code, "stage": "jira",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode="", error_text=result.error,
                             response_excerpt="")
        return result

    # ---- 4. Load past N weekly reports (Step 6 pending; usually empty) -
    weekly_reports = _load_past_weekly_reports(project_id, n=4)

    # ---- 5. Render Prompt 3 ---------------------------------------------
    sys_prompt, user_prompt = render_full_prompt(
        project_name=project["name"],
        project_type=project.get("type") or "general",
        start_date=project.get("start_date"),
        planned_end_date=project.get("planned_end_date"),
        today_date=today_ist().strftime("%Y-%m-%d"),
        milestones_page=ms,
        fr_page=fr,
        extras=extras,
        jira_snapshot=snap,
        weekly_reports=weekly_reports,
        n_reports=4,
    )

    # ---- 6. Call LLM ----------------------------------------------------
    try:
        llm = get_llm_client()
    except Exception as e:
        result.error = f"LLM client construction failed: {type(e).__name__}: {e}"
        log.error("status compute: llm client construction failed",
                  extra={"event": "status_compute_failed",
                         "project_code": project_code, "stage": "llm_init",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode="", error_text=result.error,
                             response_excerpt="")
        return result

    result.llm_mode = llm.mode
    try:
        llm_result = llm.complete(sys_prompt, user_prompt, json_output=True)
    except Exception as e:
        result.error = f"LLM call failed: {type(e).__name__}: {e}"
        log.error("status compute: llm call failed",
                  extra={"event": "status_compute_failed",
                         "project_code": project_code, "stage": "llm_call",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode=llm.mode, error_text=result.error,
                             response_excerpt="")
        return result

    result.raw_response = llm_result.text
    result.duration_seconds = llm_result.duration_seconds
    result.prompt_tokens = llm_result.prompt_tokens
    result.completion_tokens = llm_result.completion_tokens

    # ---- 7. Parse + validate JSON ---------------------------------------
    try:
        parsed = json.loads(llm_result.text)
    except json.JSONDecodeError as e:
        result.error = f"LLM did not return valid JSON: {e}"
        log.error("status compute: invalid JSON from LLM",
                  extra={"event": "status_compute_failed",
                         "project_code": project_code, "stage": "json_parse",
                         "error": str(e),
                         "response_excerpt": (llm_result.text or "")[:500]})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode=llm.mode, error_text=result.error,
                             response_excerpt=(llm_result.text or "")[:500])
        return result

    is_valid, issues = validate_prompt3_json(parsed)
    if not is_valid:
        result.issues = issues
        result.error = f"Schema validation failed: {len(issues)} issue(s)"
        log.warning(
            "status compute: schema validation failed — not persisting",
            extra={"event": "status_compute_validation_failed",
                   "project_code": project_code,
                   "issue_count": len(issues),
                   "issues": issues},
        )
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode=llm.mode,
                             error_text="; ".join(issues)[:1000],
                             response_excerpt=(llm_result.text or "")[:500])
        return result

    result.parsed = parsed

    # ---- 8. Persist ProjectStatus + (conditionally) ProjectStatusHistory
    try:
        result.changed = _persist_status(project_id, parsed, llm.mode)
    except Exception as e:
        result.error = f"DB persist failed: {type(e).__name__}: {e}"
        log.error("status compute: persist failed",
                  extra={"event": "status_compute_failed",
                         "project_code": project_code, "stage": "persist",
                         "error": str(e), "type": type(e).__name__})
        _save_ai_compute_log(project_id, started_at, success=False,
                             llm_mode=llm.mode, error_text=result.error,
                             response_excerpt=(llm_result.text or "")[:500])
        return result

    # ---- 9. Mark success + log ------------------------------------------
    result.success = True
    _save_ai_compute_log(project_id, started_at, success=True,
                         llm_mode=llm.mode, error_text="",
                         response_excerpt=(llm_result.text or "")[:500])
    log.info(
        "status compute success",
        extra={"event": "status_compute_success",
               "project_code": project_code,
               "overall_health": parsed["overall_health"],
               "schedule_status": parsed["schedule_status"],
               "completion_pct": parsed.get("completion_pct"),
               "confidence": parsed["confidence"],
               "milestone_count": len(parsed.get("milestones") or []),
               "changed": result.changed,
               "duration_seconds": result.duration_seconds,
               "prompt_tokens": result.prompt_tokens,
               "completion_tokens": result.completion_tokens},
    )
    return result


# ---------- DB helpers ---------------------------------------------------

def _load_past_weekly_reports(project_id: int, n: int = 4) -> list[dict]:
    """Load the most recent N consolidated weekly reports for the project.

    Returns list of {"week_of": "YYYY-MM-DD", "content_markdown": "..."}.
    Empty list when no reports exist (Step 6 not implemented yet, OR this
    is a brand-new project). The AI is instructed to lower confidence
    when this signal is empty.
    """
    with session_scope() as s:
        rows = s.execute(
            select(WeeklyReport)
            .where(WeeklyReport.project_id == project_id)
            .order_by(WeeklyReport.week_of.desc())
            .limit(n)
        ).scalars().all()
        return [
            {
                "week_of": r.week_of.isoformat() if r.week_of else "",
                "content_markdown": r.content_markdown or "",
            }
            for r in rows
        ]


def _persist_status(project_id: int, parsed: dict, llm_mode: str) -> bool:
    """Upsert ProjectStatus + append ProjectStatusHistory if changed.

    Returns True iff one of overall_health / schedule_status / completion_pct
    changed since the previous compute (which is the trigger for a history
    append per FR §A.6.1).

    On the FIRST compute for a project (no existing row), only ProjectStatus
    is inserted — no history row, since there's no prior state to diff
    against. Returns False in that case.
    """
    now = datetime.utcnow()

    new_data = {
        "project_id": project_id,
        "computed_at": now,
        "overall_health": parsed["overall_health"],
        "schedule_status": parsed["schedule_status"],
        "completion_pct": parsed.get("completion_pct"),
        "milestones_json": list(parsed.get("milestones") or []),
        "rationale": parsed.get("rationale", ""),
        "confidence": parsed.get("confidence", "Low"),
        "prompt_version": PROMPT_VERSION,
        "llm_mode_used": llm_mode,
    }

    with session_scope() as s:
        existing = s.execute(
            select(ProjectStatus).where(ProjectStatus.project_id == project_id)
        ).scalar_one_or_none()

        if existing is None:
            # First compute for this project — insert only
            s.add(ProjectStatus(**new_data))
            return False

        # Detect change on the three load-bearing fields
        changed = (
            existing.overall_health != new_data["overall_health"]
            or existing.schedule_status != new_data["schedule_status"]
            or existing.completion_pct != new_data["completion_pct"]
        )

        if changed:
            s.add(ProjectStatusHistory(
                project_id=project_id,
                computed_at=now,
                prior_health=existing.overall_health,
                new_health=new_data["overall_health"],
                prior_schedule=existing.schedule_status,
                new_schedule=new_data["schedule_status"],
                prior_completion_pct=existing.completion_pct,
                new_completion_pct=new_data["completion_pct"],
                rationale=new_data["rationale"],
                prompt_version=PROMPT_VERSION,
            ))

        # Refresh the current row regardless — even when nothing changed,
        # we still update computed_at + the JSON blob + rationale so callers
        # can see the freshness of the latest assessment.
        for k, v in new_data.items():
            setattr(existing, k, v)

        return changed


def _save_ai_compute_log(
    project_id: Optional[int],
    started_at: datetime,
    *,
    success: bool,
    llm_mode: str,
    error_text: str,
    response_excerpt: str,
) -> None:
    """Append one row to ai_compute_log table (NFR §6 observability).

    Called for every status-compute attempt, success or failure. Uses its
    own session so a failure here doesn't roll back upstream work. project_id
    may be None when the failure is 'project not found in DB' (the
    AIComputeLog.project_id column is nullable).
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
        # Don't let an observability failure mask the original error.
        # JSONL system log still captures the event.
        system_log().error(
            "ai_compute_log write failed",
            extra={"event": "ai_compute_log_write_failed",
                   "project_id": project_id, "error": str(e),
                   "type": type(e).__name__},
        )
