"""Email notifications — mocked SMTP in Phase 1 (Step 10).

Two layers:

1. **send_engineer_reminder()** — sends ONE reminder email to one engineer.
   In mock mode (default per FR §B.5 + NFR Phase 1: "Notifications to chat
   platforms (only email notifications in Phase 1)" + "Mock SMTP for now"),
   writes a JSONL line to `cfg.email.mock_log_path` and appends one row to
   the ReminderLog DB table. Real SMTP path is stubbed for a future flip.

2. **run_pre_cutoff_reminders() / run_post_cutoff_reminders()** —
   orchestration: figure out who to remind for one project for one week,
   then call send_engineer_reminder() for each.

   - Pre-cutoff (FR §B.5.2a): proactive reminder to ALL engineers assigned
     to the project. No JIRA query — fired some hours before the cutoff to
     give engineers a heads-up.
   - Post-cutoff (FR §B.5.2b): late notification to engineers who STILL
     haven't updated their JIRA tasks for the report week. Queries JIRA
     for activity, computes the missing set (assigned − active), sends to
     each missing engineer.

The scheduler (Step 9) wires these into per-project recurring jobs at
cutoff − reminders.hours_before_cutoff and cutoff + reminders.hours_after_cutoff.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from app.clients import get_jira_client
from app.config import get_config
from app.db import session_scope
from app.models import ReminderLog
from app.registry.engineers import engineers_on_project
from app.registry.projects import get_project_by_code
from app.utils.dates import week_of as compute_week_of
from app.utils.logging import system_log, reminder_log


# ---------- Data classes ------------------------------------------------

@dataclass
class SentEmail:
    """Outcome of one mock-or-real send attempt."""
    knox_id: str
    name: str
    project_code: str
    week_of: str          # ISO date string
    type: str             # 'pre_cutoff' | 'post_cutoff'
    sent_at: datetime
    channel: str          # 'mock_smtp' | 'smtp'
    status: str           # 'mocked' | 'sent' | 'failed'
    subject: str = ""
    body: str = ""
    error: str = ""


@dataclass
class ReminderRunResult:
    """Outcome of one reminder-orchestration run for one project for one week."""
    success: bool = False
    project_code: str = ""
    week_of: Optional[date] = None
    type: str = ""                     # 'pre_cutoff' | 'post_cutoff'
    engineers_targeted: int = 0        # how many we tried to remind
    sent: list[SentEmail] = field(default_factory=list)
    failed_knox_ids: list[str] = field(default_factory=list)
    error: str = ""                    # populated when success=False


# ---------- Body builders -----------------------------------------------

def _build_subject(type_: str, project_code: str, week_of_str: str) -> str:
    if type_ == "pre_cutoff":
        return f"[Reminder] Weekly JIRA update due — project {project_code} (week of {week_of_str})"
    if type_ == "post_cutoff":
        return f"[Action Required] Missing weekly JIRA update — project {project_code} (week of {week_of_str})"
    return f"[Notification] {project_code} (week of {week_of_str})"


def _build_body(type_: str, name: str, project_code: str, week_of_str: str) -> str:
    salutation = f"Hi {name}," if name else "Hi,"
    if type_ == "pre_cutoff":
        return (
            f"{salutation}\n\n"
            f"This is a reminder that your weekly JIRA update for project "
            f"{project_code} (week of {week_of_str}) is due soon.\n\n"
            f"Please add comments and/or work-logs to your JIRA tasks for the "
            f"week so the consolidated weekly report can include your "
            f"contributions. If you've already updated, please disregard this.\n\n"
            f"— Project-Manager-App (automated, do not reply)"
        )
    if type_ == "post_cutoff":
        return (
            f"{salutation}\n\n"
            f"Our system noticed your weekly JIRA update for project "
            f"{project_code} (week of {week_of_str}) is missing — the cutoff has "
            f"passed and no comments or work-logs from you were found in JIRA "
            f"for any of your assigned tasks for the week.\n\n"
            f"Please update your JIRA tasks as soon as possible so your work "
            f"is reflected in this week's consolidated report.\n\n"
            f"— Project-Manager-App (automated, do not reply)"
        )
    return f"{salutation}\n\nNotification for {project_code} (week of {week_of_str})."


# ---------- Single-recipient sender -------------------------------------

def send_engineer_reminder(
    knox_id: str,
    name: str,
    project_code: str,
    week_of: str,
    type: str,
    *,
    project_id: Optional[int] = None,
) -> SentEmail:
    """Mock-send (or, when email.mock=False in a future phase, really send)
    one reminder to one engineer.

    Always writes:
      - One JSONL line to `cfg.email.mock_log_path` (mock mode) — also
        echoed to the rotated `reminder.jsonl` log
      - One ReminderLog row in the DB (so the PGM-facing API in Step 11
        can summarise reminder activity)

    `project_id` is the FK target for ReminderLog. When the caller already
    has it (e.g. after looking up the project via the registry), pass it
    through to avoid an extra DB hit. When None, we resolve it ourselves;
    if the project lookup fails the email is still recorded to the JSONL
    log but the DB row is skipped (with a warning).
    """
    cfg = get_config()
    sys = system_log()
    rem = reminder_log()

    sent_at = datetime.now(timezone.utc)
    subject = _build_subject(type, project_code, week_of)
    body = _build_body(type, name, project_code, week_of)

    email = SentEmail(
        knox_id=knox_id, name=name, project_code=project_code,
        week_of=week_of, type=type, sent_at=sent_at,
        channel="mock_smtp" if cfg.email.mock else "smtp",
        status="mocked" if cfg.email.mock else "sent",
        subject=subject, body=body,
    )

    # ---- 1. Write to the mock JSONL log (always — also useful in real mode for audit) ----
    try:
        log_path = Path(cfg.email.mock_log_path)
        if not log_path.is_absolute():
            log_path = Path(__file__).resolve().parent.parent / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": sent_at.isoformat().replace("+00:00", "Z"),
                "channel": email.channel,
                "type": email.type,
                "to_knox_id": email.knox_id,
                "to_name": email.name,
                "project_code": email.project_code,
                "week_of": email.week_of,
                "subject": email.subject,
                "body": email.body,
                "status": email.status,
            }, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.error(
            "notifications: mock JSONL write failed",
            extra={"event": "notification_mock_log_failed",
                   "knox_id": knox_id, "project_code": project_code,
                   "error": str(e), "type": type(e).__name__},
        )
        email.status = "failed"
        email.error = f"mock JSONL write failed: {e}"
        return email

    # ---- 2. Real SMTP path (stubbed — Phase 1 always mock) ----
    if not cfg.email.mock:
        # Future: smtplib.SMTP(cfg.email.smtp_host, cfg.email.smtp_port).send_message(...)
        # For Phase 1 we never enter this branch (mock=True default).
        sys.warning(
            "notifications: real SMTP not implemented in Phase 1 — falling back to mock-only behaviour",
            extra={"event": "notification_real_smtp_not_implemented",
                   "knox_id": knox_id},
        )
        email.status = "mocked"

    # ---- 3. Persist ReminderLog row ----
    if project_id is None:
        proj = get_project_by_code(project_code)
        project_id = proj["id"] if proj else None

    if project_id is not None:
        try:
            with session_scope() as s:
                s.add(ReminderLog(
                    engineer_knox_id=knox_id,
                    engineer_name=name,
                    project_id=project_id,
                    week_of=date.fromisoformat(week_of) if week_of else None,
                    type=type,
                    sent_at=sent_at,
                    channel=email.channel,
                    status=email.status,
                ))
        except Exception as e:
            sys.error(
                "notifications: ReminderLog insert failed",
                extra={"event": "notification_db_insert_failed",
                       "knox_id": knox_id, "project_code": project_code,
                       "error": str(e), "type": type(e).__name__},
            )
    else:
        sys.warning(
            "notifications: project not found for ReminderLog — DB row skipped",
            extra={"event": "notification_project_missing_skip_db",
                   "project_code": project_code, "knox_id": knox_id},
        )

    rem.info(
        "reminder sent (mock)" if cfg.email.mock else "reminder sent",
        extra={"event": "reminder_sent",
               "knox_id": knox_id, "name": name,
               "project_code": project_code, "week_of": week_of,
               "type": type, "channel": email.channel,
               "status": email.status},
    )
    return email


# ---------- Orchestrators -----------------------------------------------

def run_pre_cutoff_reminders(
    project_code: str,
    week_of: Optional[date] = None,
) -> ReminderRunResult:
    """Send pre-cutoff reminders to ALL engineers assigned to the project.

    Proactive — no JIRA query needed. Fires some hours before the cutoff
    to give engineers a heads-up to update their JIRA tasks. Returns a
    structured ReminderRunResult; never raises on normal failure modes.
    """
    log = system_log()
    if week_of is None:
        week_of = compute_week_of()
    week_of_str = week_of.isoformat()

    result = ReminderRunResult(
        project_code=project_code, week_of=week_of, type="pre_cutoff",
    )

    project = get_project_by_code(project_code)
    if project is None:
        result.error = (
            f"Project {project_code!r} not found in DB. "
            f"Run 'python manage.py sync-projects' first."
        )
        log.error("reminders: project not found",
                  extra={"event": "reminders_failed",
                         "project_code": project_code, "type": "pre_cutoff"})
        return result

    engineers = engineers_on_project(project_code)
    result.engineers_targeted = len(engineers)
    if not engineers:
        log.warning(
            "reminders: pre-cutoff — no engineers assigned to project (no-op)",
            extra={"event": "reminders_no_engineers",
                   "project_code": project_code, "week_of": week_of_str,
                   "type": "pre_cutoff"},
        )
        result.success = True
        return result

    log.info(
        "reminders: pre-cutoff starting",
        extra={"event": "reminders_pre_start",
               "project_code": project_code, "week_of": week_of_str,
               "engineer_count": len(engineers)},
    )

    for eng in engineers:
        try:
            sent = send_engineer_reminder(
                knox_id=eng.knox_id, name=eng.name,
                project_code=project_code, week_of=week_of_str,
                type="pre_cutoff", project_id=project["id"],
            )
            if sent.status == "failed":
                result.failed_knox_ids.append(eng.knox_id)
            result.sent.append(sent)
        except Exception as e:
            log.error(
                "reminders: send raised unexpectedly — continuing with next engineer",
                extra={"event": "reminders_send_crashed",
                       "project_code": project_code,
                       "knox_id": eng.knox_id,
                       "error": str(e), "type": type(e).__name__},
            )
            result.failed_knox_ids.append(eng.knox_id)

    result.success = True
    log.info(
        "reminders: pre-cutoff complete",
        extra={"event": "reminders_pre_complete",
               "project_code": project_code, "week_of": week_of_str,
               "engineers_targeted": result.engineers_targeted,
               "sent_count": len(result.sent),
               "failed_count": len(result.failed_knox_ids)},
    )
    return result


def run_post_cutoff_reminders(
    project_code: str,
    week_of: Optional[date] = None,
) -> ReminderRunResult:
    """Send post-cutoff reminders ONLY to engineers who haven't updated
    their JIRA tasks for the report week.

    Pulls JIRA activity for the week (same path the Aggregation Engine uses).
    Engineer is "missing" if their knox_id does NOT appear in
    activity.by_engineer (i.e. zero comments/work-logs/status changes for
    the week). Per FR §B.5.1, those engineers receive an email AND are
    recorded in the ReminderLog table.

    Per Decision Log #16: missing engineers are NOT surfaced on the PGM
    dashboard or in the consolidated report — only logged + emailed.

    Returns a structured ReminderRunResult; never raises on normal failure
    modes (project missing, JIRA fetch failure).
    """
    log = system_log()
    if week_of is None:
        week_of = compute_week_of()
    week_of_str = week_of.isoformat()

    result = ReminderRunResult(
        project_code=project_code, week_of=week_of, type="post_cutoff",
    )

    project = get_project_by_code(project_code)
    if project is None:
        result.error = (
            f"Project {project_code!r} not found in DB. "
            f"Run 'python manage.py sync-projects' first."
        )
        log.error("reminders: project not found",
                  extra={"event": "reminders_failed",
                         "project_code": project_code, "type": "post_cutoff"})
        return result

    engineers = engineers_on_project(project_code)
    if not engineers:
        log.warning(
            "reminders: post-cutoff — no engineers assigned (no-op)",
            extra={"event": "reminders_no_engineers",
                   "project_code": project_code, "week_of": week_of_str,
                   "type": "post_cutoff"},
        )
        result.success = True
        return result

    # Fetch JIRA activity for the week — only need to know WHO touched
    # something, not the full content. We share the engine path here so
    # noise filtering + NFR-compliant retry behaviour are inherited.
    try:
        jira = get_jira_client()
        engineers_dicts = [{"name": e.name, "knox_id": e.knox_id} for e in engineers]
        activity = jira.collect_engineer_activity(
            project["jira_project_key"],
            week_of,
            engineers_dicts,
            project.get("issue_types") or None,
        )
    except Exception as e:
        result.error = f"JIRA fetch failed: {type(e).__name__}: {e}"
        log.error(
            "reminders: jira fetch failed",
            extra={"event": "reminders_failed",
                   "project_code": project_code, "type": "post_cutoff",
                   "error": str(e), "type_": type(e).__name__},
        )
        return result

    # The "missing" set is engineers assigned to the project who do NOT
    # appear in activity.by_engineer (i.e. zero recorded activity for week).
    active_knox = set(activity.by_engineer.keys())
    missing = [e for e in engineers if e.knox_id not in active_knox]
    result.engineers_targeted = len(missing)

    log.info(
        "reminders: post-cutoff starting",
        extra={"event": "reminders_post_start",
               "project_code": project_code, "week_of": week_of_str,
               "assigned_count": len(engineers),
               "active_count": len(active_knox),
               "missing_count": len(missing)},
    )

    if not missing:
        result.success = True
        log.info(
            "reminders: post-cutoff complete — no missing engineers",
            extra={"event": "reminders_post_complete",
                   "project_code": project_code, "week_of": week_of_str,
                   "engineers_targeted": 0, "sent_count": 0},
        )
        return result

    for eng in missing:
        try:
            sent = send_engineer_reminder(
                knox_id=eng.knox_id, name=eng.name,
                project_code=project_code, week_of=week_of_str,
                type="post_cutoff", project_id=project["id"],
            )
            if sent.status == "failed":
                result.failed_knox_ids.append(eng.knox_id)
            result.sent.append(sent)
        except Exception as e:
            log.error(
                "reminders: send raised unexpectedly — continuing",
                extra={"event": "reminders_send_crashed",
                       "project_code": project_code,
                       "knox_id": eng.knox_id,
                       "error": str(e), "type": type(e).__name__},
            )
            result.failed_knox_ids.append(eng.knox_id)

    result.success = True
    log.info(
        "reminders: post-cutoff complete",
        extra={"event": "reminders_post_complete",
               "project_code": project_code, "week_of": week_of_str,
               "engineers_targeted": result.engineers_targeted,
               "sent_count": len(result.sent),
               "failed_count": len(result.failed_knox_ids)},
    )
    return result
