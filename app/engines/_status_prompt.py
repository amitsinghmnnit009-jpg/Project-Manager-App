"""Shared building blocks for Project Status (Prompt 3).

Used by BOTH the standalone test script (scripts/test_status_prompt.py)
AND the production Status Engine (app/engines/status.py). Single source of
truth for prompt rendering + JSON schema validation, so the script and the
engine produce IDENTICAL prompts for identical inputs.

Module-private prefix (`_`) is convention only — the standalone script
imports from here without restriction.
"""
from __future__ import annotations

from app.prompts import load_prompt


# Locked prompt + version (recorded with each persisted compute per FR §A.6.1)
PROMPT_FILE = "project_status_reasoning_v3"
PROMPT_VERSION = "ProjectStatusReasoning/v3"


# Prompt 3 schema enums (per AI_PROMPTS_PHASE1.md §Prompt 3)
HEALTH_VALID = {"Green", "Amber", "Red", "InsufficientEvidence"}
SCHEDULE_VALID = {"OnTrack", "AtRisk", "Slipping", "Delayed", "InsufficientEvidence"}
CONFIDENCE_VALID = {"High", "Medium", "Low"}
TL_STATUS_VALID = {"Pending", "In-progress", "Done", "Delayed", "Blocked", "Cancelled"}
AI_VERIFICATION_VALID = {"Verified", "Disputed", "Inconclusive", "NotApplicable"}


# ---------- Block renderers (substituted into prompt placeholders) -------

def render_milestones_block(milestones) -> str:
    """Format milestone rows for the Status Engine prompt.

    Omits empty optional fields so the LLM doesn't see noisy `field=`
    placeholders for columns the TL didn't fill (e.g. when using the slim
    4-column fallback, Priority/Dependency/Remark won't appear in the line).
    Required fields (name, planned_date, status, description) always render.

    completion_date (Phase B) is rendered next to planned_date when populated;
    feeds the v3 prompt's TL-Done verification rule (cross-check the asserted
    completion date against JIRA closure events).
    """
    if not milestones:
        return "(none — Confluence page has no milestones table or it could not be parsed)"

    lines = []
    for m in milestones:
        # Required fields — always present
        parts = [f"- {m.name}", f"planned={m.planned_date}",
                 f"tl_declared_status={m.status}",
                 f"description={m.description}"]
        # Optional fields — include only when populated
        if m.completion_date:
            # Slot right after planned= so the AI sees both dates side-by-side.
            parts.insert(2, f"completion_date={m.completion_date}")
        if m.priority:
            parts.insert(2, f"priority={m.priority}")
        if m.dependency:
            parts.insert(-1, f"dependency={m.dependency}")
        if m.quarter:
            parts.insert(2, f"quarter={m.quarter}")
        if m.remark:
            parts.append(f"remark={m.remark}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def render_jira_status_block(by_status: dict) -> str:
    if not by_status:
        return "  (no tasks)"
    return "\n".join(
        f"  - {s}: {c}" for s, c in sorted(by_status.items(), key=lambda x: -x[1])
    )


def render_recent_activity(recent: list) -> str:
    if not recent:
        return "(no activity in last 14 days)"
    lines = []
    for r in recent[:20]:
        title = (r.get("title") or "")[:80]
        last = (r.get("last_activity") or "")[:10]
        lines.append(
            f"  - {r['id']} - {title} (status={r.get('status','?')}, last_activity={last})"
        )
    if len(recent) > 20:
        lines.append(f"  ... and {len(recent) - 20} more recent task(s) not shown")
    return "\n".join(lines)


def render_weekly_reports_block(reports: list) -> str:
    """Format past consolidated weekly reports for the prompt.

    The Status Engine pulls these from the DB once Step 6 (Aggregation
    Engine) starts populating WeeklyReport rows. Until then, callers pass
    an empty list and the AI sees the 'no reports yet' signal — Prompt 3
    is instructed to lower confidence in that case.
    """
    if not reports:
        return ("(no consolidated weekly reports available yet — first run, "
                "or none generated)")
    blocks = []
    for r in reports:
        blocks.append(f"--- Week of {r['week_of']} ---\n{r['content_markdown']}")
    return "\n\n".join(blocks)


def render_extra_pages_block(extra_pages: list) -> str:
    """Render fetched ExtraPageContent objects into the prompt block.

    Each page is delimited with its title for citation. Bodies are already
    truncated by parse_extra_page (per confluence.extra_page_max_chars).
    The system prompt's CRITICAL RULE 9 prevents the AI from treating
    these as load-bearing — they're background only.
    """
    if not extra_pages:
        return "(no extra context pages configured for this project)"
    blocks = []
    for ep in extra_pages:
        marker = " [TRUNCATED]" if ep.truncated else ""
        blocks.append(
            f"--- Extra context page: {ep.title!r}{marker} ---\n{ep.body_text}"
        )
    return "\n\n".join(blocks)


# ---------- Full prompt assembly ----------------------------------------

def render_full_prompt(
    *,
    project_name: str,
    project_type: str,
    start_date,
    planned_end_date,
    today_date: str,
    milestones_page,    # MilestonesPageContent
    fr_page,            # FRsPageContent
    extras: list,       # list[ExtraPageContent]
    jira_snapshot,      # JiraProjectSnapshot
    weekly_reports: list,
    n_reports: int,
) -> tuple[str, str]:
    """Load prompt v2 + render with all placeholders. Returns (system, user).

    All keyword-only so callers don't accidentally swap a Confluence and a
    JIRA argument silently.
    """
    sys_prompt, user_template = load_prompt(PROMPT_FILE)
    user_prompt = user_template.format(
        project_name=project_name,
        project_type=project_type,
        start_date=start_date or "(not set)",
        planned_end_date=planned_end_date or "(not set)",
        today_date=today_date,
        # Milestones page
        milestones_page_overview=milestones_page.overview or "(none)",
        confluence_milestones_block=render_milestones_block(milestones_page.milestones),
        # FR page
        fr_page_overview=fr_page.overview or "(none)",
        confluence_functional_requirements=fr_page.functional_requirements or "(none)",
        # Extra context pages (supplementary; AI must not derive milestones/FRs)
        extra_context_pages_block=render_extra_pages_block(extras),
        # JIRA snapshot
        jira_total=jira_snapshot.total_tasks,
        jira_status_block=render_jira_status_block(jira_snapshot.by_status),
        jira_overdue_count=jira_snapshot.overdue_count,
        jira_stale_count=jira_snapshot.stale_count,
        jira_recent_activity_block=render_recent_activity(jira_snapshot.recent_activity),
        # Past weekly reports
        n_reports=n_reports,
        weekly_reports_block=render_weekly_reports_block(weekly_reports),
    )
    return sys_prompt, user_prompt


# ---------- Schema validator --------------------------------------------

def validate_prompt3_json(parsed) -> tuple[bool, list[str]]:
    """Verify structure matches the Prompt 3 schema (AI_PROMPTS_PHASE1.md).

    Returns (is_valid, list_of_issues). Issues are descriptive strings
    suitable for surfacing in CLI output / logs / error messages.

    Critical invariants checked:
      - overall_health / schedule_status / confidence enum membership
      - completion_pct: int 0..100 OR null. Bool excluded explicitly
        (isinstance(True, int) is True in Python, so without this check
        the LLM emitting True/False would silently pass).
      - completion_pct must be null when overall_health=InsufficientEvidence
      - milestones[].tl_declared_status / ai_verification enum membership
      - ASYMMETRIC TRUST INVARIANT (the core Prompt 3 contract): when
        tl_declared_status != "Done", ai_verification MUST be
        "NotApplicable" (the AI must not second-guess non-Done declarations)
      - rationale present and non-trivial
      - evidence_cited is a list
    """
    issues: list[str] = []
    if not isinstance(parsed, dict):
        return False, ["Top level is not a JSON object"]

    h = parsed.get("overall_health")
    if h not in HEALTH_VALID:
        issues.append(f"overall_health={h!r} not in {sorted(HEALTH_VALID)}")

    s = parsed.get("schedule_status")
    if s not in SCHEDULE_VALID:
        issues.append(f"schedule_status={s!r} not in {sorted(SCHEDULE_VALID)}")

    pct = parsed.get("completion_pct")
    if pct is not None:
        # bool is a subclass of int in Python — exclude it explicitly
        # before the numeric check, otherwise True/False would pass.
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            issues.append(f"completion_pct={pct!r} should be int 0..100 or null")
        elif not (0 <= pct <= 100):
            issues.append(f"completion_pct={pct!r} out of range 0..100")
        elif isinstance(pct, float) and not pct.is_integer():
            issues.append(f"completion_pct={pct!r} should be an integer")
    if h == "InsufficientEvidence" and pct is not None:
        issues.append(
            f"completion_pct={pct} should be null when overall_health=InsufficientEvidence"
        )

    ms = parsed.get("milestones")
    if not isinstance(ms, list):
        issues.append("milestones is not a list")
    else:
        for i, m in enumerate(ms):
            if not isinstance(m, dict):
                issues.append(f"milestones[{i}] is not an object")
                continue
            for f in ("name", "planned_date", "tl_declared_status",
                      "ai_verification", "evidence"):
                if f not in m:
                    issues.append(f"milestones[{i}] missing field {f!r}")
            tld = m.get("tl_declared_status")
            if tld is not None and tld not in TL_STATUS_VALID:
                issues.append(
                    f"milestones[{i}].tl_declared_status={tld!r} "
                    f"not in {sorted(TL_STATUS_VALID)}"
                )
            aiv = m.get("ai_verification")
            if aiv is not None and aiv not in AI_VERIFICATION_VALID:
                issues.append(
                    f"milestones[{i}].ai_verification={aiv!r} "
                    f"not in {sorted(AI_VERIFICATION_VALID)}"
                )
            # Asymmetric trust invariant — the core Prompt 3 contract
            if tld is not None and tld != "Done" and aiv != "NotApplicable":
                issues.append(
                    f"milestones[{i}] tl_declared_status={tld!r} but "
                    f"ai_verification={aiv!r} — AI must NOT verify non-Done "
                    f"milestones (set ai_verification='NotApplicable')"
                )
            # Phase B: completion_date is OPTIONAL but, when present, must be
            # either a string (ISO-ish date — we don't enforce strict format)
            # or null. The v3 prompt instructs the LLM to echo the value
            # through; we accept both representations.
            if "completion_date" in m:
                cd = m.get("completion_date")
                if cd is not None and not isinstance(cd, str):
                    issues.append(
                        f"milestones[{i}].completion_date={cd!r} "
                        f"is not a string or null"
                    )

    rationale = parsed.get("rationale")
    if not isinstance(rationale, str) or len(rationale) < 10:
        issues.append("rationale missing or too short (<10 chars)")

    c = parsed.get("confidence")
    if c not in CONFIDENCE_VALID:
        issues.append(f"confidence={c!r} not in {sorted(CONFIDENCE_VALID)}")

    if not isinstance(parsed.get("evidence_cited"), list):
        issues.append("evidence_cited is not a list")

    return len(issues) == 0, issues
