"""APScheduler — daily status + weekly pipeline + reminders (Step 9 + Step 10).

Phase 1 design: ONE BackgroundScheduler running inside the FastAPI process.
For each active project (read from the DB at startup), FOUR jobs are
registered:

1. **Status recompute** — daily by default at `cfg.scheduler.daily_status_hour`
   (IST), or hourly, or not scheduled at all if cadence == "manual". Reads the
   per-project `recompute_cadence` field first, falling back to the global
   `cfg.scheduler.status_recompute_cadence`. Calls
   `engines.status.run_status_compute(project_code)`.

2. **Weekly pipeline** — once per week, at the project's `weekly_cutoff` time
   plus `cfg.scheduler.weekly_aggregation_offset_minutes`. Runs the composite
   pipeline `_run_weekly_pipeline_safe()`:
     - First: `engines.aggregation.run_weekly_aggregation(code, week_of=now)`
     - On success: `engines.highlights.run_highlights(code, week_of=now)`
     - On any failure or crash: log + continue. The next week's run will
       try again from scratch.

3. **Pre-cutoff reminder** (Step 10) — once per week, at the project's
   `weekly_cutoff` MINUS `cfg.reminders.hours_before_cutoff`. Sends a
   proactive heads-up email (mocked in Phase 1) to ALL engineers assigned
   to the project. No JIRA query. Day-of-week wraps correctly when the
   subtraction crosses midnight or week boundary.

4. **Post-cutoff reminder** (Step 10) — once per week, at the project's
   `weekly_cutoff` PLUS `cfg.reminders.hours_after_cutoff`. Queries JIRA
   for the week's activity, identifies engineers who haven't recorded any
   comments/work-logs/status-changes, and emails ONLY them.

Memory job store (default): jobs are recreated from the DB at every restart
via `_build_scheduler()`. Misfires within `misfire_grace_seconds` (default
24h) still run when the process comes back up. Persistent SQLAlchemyJobStore
can be wired later if Phase 2 needs cross-restart job continuity.

Tests: `_build_scheduler()` is the seam. Build a scheduler with all jobs
registered but don't call `.start()` — inspect `sched.get_jobs()` to verify
trigger configuration without firing anything.
"""
from __future__ import annotations
from typing import Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_config
from app.registry.projects import list_projects
from app.utils.dates import parse_cutoff, week_of as compute_week_of
from app.utils.logging import system_log


# Process-global handle so start_scheduler is idempotent and stop_scheduler
# can shut down what start_scheduler started.
_scheduler: Optional[BackgroundScheduler] = None

# APScheduler accepts day_of_week as either int 0..6 (Mon..Sun) or these strings.
# Strings are easier to read in logs / job IDs.
_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# ---------- Public lifecycle (called from FastAPI lifespan) -------------

def start_scheduler() -> Optional[BackgroundScheduler]:
    """Build + start the scheduler. Idempotent — calling twice returns the
    same instance instead of double-registering jobs.

    Returns the running scheduler, or None when there are no active projects
    to schedule for (calling .start() on an empty scheduler is harmless but
    we skip it to keep the no-op visible in logs)."""
    global _scheduler
    if _scheduler is not None:
        system_log().info(
            "scheduler already running — start_scheduler is a no-op",
            extra={"event": "scheduler_already_running"},
        )
        return _scheduler

    cfg = get_config()
    sched = _build_scheduler(cfg)

    job_count = len(sched.get_jobs())
    if job_count == 0:
        system_log().warning(
            "scheduler: no projects to schedule — not starting",
            extra={"event": "scheduler_no_jobs"},
        )
        return None

    sched.start()
    _scheduler = sched
    system_log().info(
        "scheduler started",
        extra={"event": "scheduler_started", "job_count": job_count,
               "timezone": cfg.scheduler.timezone},
    )
    return sched


def stop_scheduler() -> None:
    """Graceful shutdown. Safe to call when nothing was started."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
        system_log().info("scheduler stopped",
                          extra={"event": "scheduler_stopped"})
    finally:
        _scheduler = None


def get_scheduler() -> Optional[BackgroundScheduler]:
    """Return the running scheduler instance (or None). For diagnostics —
    e.g. CLI 'scheduler-status' uses this to dump next run times."""
    return _scheduler


# ---------- Builder (test seam) -----------------------------------------

def _build_scheduler(cfg) -> BackgroundScheduler:
    """Construct a BackgroundScheduler with all per-project jobs registered
    but DO NOT start it.

    Tests inspect `sched.get_jobs()` to verify trigger configuration without
    firing real LLM/JIRA/DB work.
    """
    tz = pytz.timezone(cfg.scheduler.timezone)
    sched = BackgroundScheduler(timezone=tz)

    projects = list_projects()
    for project in projects:
        _schedule_status_for_project(sched, project, cfg, tz)
        _schedule_weekly_for_project(sched, project, cfg, tz)
        _schedule_reminders_for_project(sched, project, cfg, tz)

    return sched


# ---------- Per-project job builders ------------------------------------

def _schedule_status_for_project(sched, project, cfg, tz) -> None:
    """Add (or skip) the daily/hourly status recompute job for one project.

    Per-project `recompute_cadence` overrides the global default. cadence ==
    "manual" means no scheduled job (CLI / API only).
    """
    cadence = (project.get("recompute_cadence")
               or cfg.scheduler.status_recompute_cadence)
    code = project["code"]

    if cadence == "manual":
        system_log().info(
            "scheduler: status job skipped (cadence=manual)",
            extra={"event": "scheduler_status_skipped",
                   "project_code": code, "cadence": cadence},
        )
        return

    if cadence == "hourly":
        trigger = CronTrigger(minute=0, timezone=tz)
    elif cadence == "daily":
        trigger = CronTrigger(
            hour=cfg.scheduler.daily_status_hour, minute=0, timezone=tz,
        )
    else:
        # Defensive — Pydantic Literal validation should prevent this
        system_log().error(
            "scheduler: unknown cadence — status job skipped",
            extra={"event": "scheduler_unknown_cadence",
                   "project_code": code, "cadence": cadence},
        )
        return

    sched.add_job(
        _run_status_safe,
        trigger=trigger,
        args=[code],
        id=f"status:{code}",
        name=f"Status compute for {code}",
        replace_existing=True,
        misfire_grace_time=cfg.scheduler.misfire_grace_seconds,
        max_instances=1,   # never double-fire status for the same project
    )


def _schedule_weekly_for_project(sched, project, cfg, tz) -> None:
    """Add the weekly pipeline (aggregation → highlights) job for one project.

    Trigger fires once per week at the project's weekly_cutoff + offset.
    Defaults: Mon 13:00 + 5min = Mon 13:05.
    """
    code = project["code"]
    cutoff_str = project.get("weekly_cutoff") or "Mon 13:00"
    try:
        weekday, t = parse_cutoff(cutoff_str)
    except ValueError as e:
        system_log().error(
            "scheduler: invalid weekly_cutoff — weekly job skipped",
            extra={"event": "scheduler_bad_cutoff",
                   "project_code": code, "cutoff": cutoff_str,
                   "error": str(e)},
        )
        return

    offset_min = cfg.scheduler.weekly_aggregation_offset_minutes
    total_min = t.hour * 60 + t.minute + offset_min
    # Wrap into the same day for offsets that don't cross midnight (typical
    # 5–60 min). Crossing midnight would shift the day-of-week — for Phase 1
    # we keep it simple and clamp.
    fire_hour = (total_min // 60) % 24
    fire_minute = total_min % 60
    fire_day = _DAY_NAMES[weekday]

    trigger = CronTrigger(
        day_of_week=fire_day,
        hour=fire_hour,
        minute=fire_minute,
        timezone=tz,
    )

    sched.add_job(
        _run_weekly_pipeline_safe,
        trigger=trigger,
        args=[code],
        id=f"weekly:{code}",
        name=f"Weekly pipeline (aggregation→highlights) for {code}",
        replace_existing=True,
        misfire_grace_time=cfg.scheduler.misfire_grace_seconds,
        max_instances=1,   # serialise re-fires; weekly pipeline is heavy
    )


# ---------- Reminder jobs (Step 10) ------------------------------------

def _split_week_minute(week_min: int) -> tuple[int, int, int]:
    """Convert a 'minute-of-week' (0..7*24*60-1) into (weekday, hour, minute).

    weekday is Mon=0..Sun=6 (Python's weekday() convention, also what
    parse_cutoff returns and what _DAY_NAMES is keyed on).
    """
    day = week_min // (24 * 60)
    hour = (week_min % (24 * 60)) // 60
    minute = week_min % 60
    return day, hour, minute


def _schedule_reminders_for_project(sched, project, cfg, tz) -> None:
    """Add the pre-cutoff and post-cutoff reminder jobs for one project.

    Pre-cutoff fires at `weekly_cutoff − cfg.reminders.hours_before_cutoff`,
    post-cutoff at `weekly_cutoff + cfg.reminders.hours_after_cutoff`.
    Both wrap correctly across midnight and week boundaries via modulo
    arithmetic on minute-of-week.
    """
    code = project["code"]
    cutoff_str = project.get("weekly_cutoff") or "Mon 13:00"
    try:
        weekday, t = parse_cutoff(cutoff_str)
    except ValueError:
        # Already logged by _schedule_weekly_for_project — don't double-log.
        return

    minutes_per_week = 7 * 24 * 60
    week_min = weekday * 24 * 60 + t.hour * 60 + t.minute

    pre_offset = cfg.reminders.hours_before_cutoff * 60
    post_offset = cfg.reminders.hours_after_cutoff * 60

    pre_min = (week_min - pre_offset) % minutes_per_week
    post_min = (week_min + post_offset) % minutes_per_week

    pre_day, pre_hour, pre_minute = _split_week_minute(pre_min)
    post_day, post_hour, post_minute = _split_week_minute(post_min)

    sched.add_job(
        _run_pre_cutoff_reminder_safe,
        trigger=CronTrigger(
            day_of_week=_DAY_NAMES[pre_day],
            hour=pre_hour, minute=pre_minute, timezone=tz,
        ),
        args=[code],
        id=f"reminder-pre:{code}",
        name=f"Pre-cutoff reminders for {code}",
        replace_existing=True,
        misfire_grace_time=cfg.scheduler.misfire_grace_seconds,
        max_instances=1,
    )
    sched.add_job(
        _run_post_cutoff_reminder_safe,
        trigger=CronTrigger(
            day_of_week=_DAY_NAMES[post_day],
            hour=post_hour, minute=post_minute, timezone=tz,
        ),
        args=[code],
        id=f"reminder-post:{code}",
        name=f"Post-cutoff reminders for {code}",
        replace_existing=True,
        misfire_grace_time=cfg.scheduler.misfire_grace_seconds,
        max_instances=1,
    )


# ---------- Job bodies (always wrapped in safe error handling) ----------

def _run_status_safe(project_code: str) -> None:
    """Scheduled wrapper around engines.status.run_status_compute().

    The engine itself never raises on normal failure modes; we still wrap in
    try/except for unexpected programmer errors so a broken engine call
    doesn't crash the APScheduler thread and silently kill all future runs.
    """
    log = system_log()
    log.info("scheduler: status compute starting",
             extra={"event": "scheduler_status_start",
                    "project_code": project_code})
    try:
        from app.engines.status import run_status_compute
        res = run_status_compute(project_code)
        if res.success:
            log.info(
                "scheduler: status compute complete",
                extra={"event": "scheduler_status_success",
                       "project_code": project_code,
                       "overall_health": (res.parsed or {}).get("overall_health"),
                       "schedule_status": (res.parsed or {}).get("schedule_status"),
                       "completion_pct": (res.parsed or {}).get("completion_pct"),
                       "changed": res.changed,
                       "duration_seconds": res.duration_seconds},
            )
        else:
            log.error(
                "scheduler: status compute returned failure",
                extra={"event": "scheduler_status_failed",
                       "project_code": project_code,
                       "error": res.error},
            )
    except Exception as e:
        log.error(
            "scheduler: status compute crashed (uncaught exception)",
            extra={"event": "scheduler_status_crashed",
                   "project_code": project_code,
                   "error": str(e), "type": type(e).__name__},
        )


def _run_weekly_pipeline_safe(project_code: str) -> None:
    """Scheduled wrapper running aggregation followed by highlights.

    Pipeline:
      1. run_weekly_aggregation(project_code, week_of=current_week)
      2. If aggregation succeeds, run_highlights(project_code, week_of=...)
      3. Aggregation FAILURE: log + skip highlights (nothing to splice into).
         Highlights FAILURE: log + accept partial completion (the report
         exists and is readable; just no comparison annotations).

    Idempotent in both halves — safe to re-fire if needed (manually or via
    misfire grace).
    """
    log = system_log()
    week_of_date = compute_week_of()
    log.info(
        "scheduler: weekly pipeline starting",
        extra={"event": "scheduler_weekly_start",
               "project_code": project_code,
               "week_of": str(week_of_date)},
    )

    try:
        from app.engines.aggregation import run_weekly_aggregation
        agg_res = run_weekly_aggregation(project_code, week_of=week_of_date)
    except Exception as e:
        log.error(
            "scheduler: aggregation crashed",
            extra={"event": "scheduler_weekly_agg_crashed",
                   "project_code": project_code,
                   "error": str(e), "type": type(e).__name__},
        )
        return

    if not agg_res.success:
        log.error(
            "scheduler: aggregation failed — skipping highlights",
            extra={"event": "scheduler_weekly_agg_failed",
                   "project_code": project_code,
                   "error": agg_res.error,
                   "week_of": str(week_of_date)},
        )
        return

    log.info(
        "scheduler: aggregation complete — proceeding to highlights",
        extra={"event": "scheduler_weekly_agg_success",
               "project_code": project_code,
               "is_regeneration": agg_res.is_regeneration,
               "engineer_count": agg_res.engineer_count,
               "activity_records": agg_res.activity_records,
               "duration_seconds": agg_res.duration_seconds},
    )

    try:
        from app.engines.highlights import run_highlights
        hl_res = run_highlights(project_code, week_of=week_of_date)
    except Exception as e:
        log.error(
            "scheduler: highlights crashed (aggregation succeeded)",
            extra={"event": "scheduler_weekly_hl_crashed",
                   "project_code": project_code,
                   "error": str(e), "type": type(e).__name__},
        )
        return

    if not hl_res.success:
        log.error(
            "scheduler: highlights failed (aggregation succeeded — partial)",
            extra={"event": "scheduler_weekly_hl_failed",
                   "project_code": project_code,
                   "error": hl_res.error},
        )
        return

    log.info(
        "scheduler: weekly pipeline complete",
        extra={"event": "scheduler_weekly_success",
               "project_code": project_code,
               "is_first_week": hl_res.is_first_week,
               "duration_seconds": hl_res.duration_seconds},
    )


def _run_pre_cutoff_reminder_safe(project_code: str) -> None:
    """Scheduled wrapper around notifications.run_pre_cutoff_reminders.

    Same try/except discipline as the other safe wrappers — engine-returned
    failure is logged; uncaught exceptions are caught so they don't kill
    the APScheduler thread.
    """
    log = system_log()
    log.info(
        "scheduler: pre-cutoff reminders starting",
        extra={"event": "scheduler_reminders_pre_start",
               "project_code": project_code},
    )
    try:
        from app.notifications import run_pre_cutoff_reminders
        res = run_pre_cutoff_reminders(project_code)
        if res.success:
            log.info(
                "scheduler: pre-cutoff reminders complete",
                extra={"event": "scheduler_reminders_pre_success",
                       "project_code": project_code,
                       "engineers_targeted": res.engineers_targeted,
                       "sent_count": len(res.sent),
                       "failed_count": len(res.failed_knox_ids),
                       "week_of": str(res.week_of)},
            )
        else:
            log.error(
                "scheduler: pre-cutoff reminders returned failure",
                extra={"event": "scheduler_reminders_pre_failed",
                       "project_code": project_code,
                       "error": res.error},
            )
    except Exception as e:
        log.error(
            "scheduler: pre-cutoff reminders crashed",
            extra={"event": "scheduler_reminders_pre_crashed",
                   "project_code": project_code,
                   "error": str(e), "type": type(e).__name__},
        )


def _run_post_cutoff_reminder_safe(project_code: str) -> None:
    """Scheduled wrapper around notifications.run_post_cutoff_reminders."""
    log = system_log()
    log.info(
        "scheduler: post-cutoff reminders starting",
        extra={"event": "scheduler_reminders_post_start",
               "project_code": project_code},
    )
    try:
        from app.notifications import run_post_cutoff_reminders
        res = run_post_cutoff_reminders(project_code)
        if res.success:
            log.info(
                "scheduler: post-cutoff reminders complete",
                extra={"event": "scheduler_reminders_post_success",
                       "project_code": project_code,
                       "engineers_targeted": res.engineers_targeted,
                       "sent_count": len(res.sent),
                       "failed_count": len(res.failed_knox_ids),
                       "week_of": str(res.week_of)},
            )
        else:
            log.error(
                "scheduler: post-cutoff reminders returned failure",
                extra={"event": "scheduler_reminders_post_failed",
                       "project_code": project_code,
                       "error": res.error},
            )
    except Exception as e:
        log.error(
            "scheduler: post-cutoff reminders crashed",
            extra={"event": "scheduler_reminders_post_crashed",
                   "project_code": project_code,
                   "error": str(e), "type": type(e).__name__},
        )
