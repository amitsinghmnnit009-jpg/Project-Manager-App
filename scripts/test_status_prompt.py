"""Standalone Prompt 3 (Project Status Reasoning) test — Step 4.

Runs Prompt 3 against ONE real project's data, end-to-end, with no
infrastructure around it. Validates the riskiest unknown in Phase 1:
can the configured LLM (gpt-oss:latest) produce the structured JSON
shape Prompt 3 specifies?

Usage:
    python scripts/test_status_prompt.py --project-code SSDFW
    python scripts/test_status_prompt.py --project-code SSDFW --dry-run
    python scripts/test_status_prompt.py --project-code SSDFW --n-reports 0

This script does NOT touch the DB or the scheduler. It only hits JIRA,
Confluence, and the LLM. The full prompt + raw response are saved to
logs/prompt3_<code>_<ts>.jsonl for offline analysis.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `app` importable when run as a plain script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config
from app.clients import get_jira_client, get_confluence_client, ConfluenceClient
from app.llm.base import get_llm_client
from app.prompts import load_prompt
from app.utils.dates import today_ist


# Expected Prompt 3 JSON schema (per AI_PROMPTS_PHASE1.md project_status_reasoning_v1)
HEALTH_VALID = {"Green", "Amber", "Red", "InsufficientEvidence"}
SCHEDULE_VALID = {"OnTrack", "AtRisk", "Slipping", "Delayed", "InsufficientEvidence"}
CONFIDENCE_VALID = {"High", "Medium", "Low"}
TL_STATUS_VALID = {"Pending", "In-progress", "Done", "Delayed", "Blocked", "Cancelled"}
AI_VERIFICATION_VALID = {"Verified", "Disputed", "Inconclusive", "NotApplicable"}

PROMPT_VERSION = "ProjectStatusReasoning/v2"
PROMPT_FILE = "project_status_reasoning_v2"


# ---------- Project lookup ------------------------------------------------

def find_project(cfg, code: str):
    """Find a project by `code` in config.yaml's projects: list."""
    norm = code.strip().lower()
    for p in cfg.projects:
        if p.code.strip().lower() == norm:
            return p
    print(f"[FAIL] Project code {code!r} not found in config.yaml's projects: list.")
    available = [p.code for p in cfg.projects]
    if available:
        print(f"       Available codes: {available}")
    else:
        print(f"       Your projects: list is empty. Add at least one project entry — see config.yaml example.")
    sys.exit(2)


# ---------- Block renderers (substituted into prompt placeholders) -------

def render_milestones_block(milestones) -> str:
    if not milestones:
        return "(none — Confluence page has no milestones table or it could not be parsed)"
    lines = []
    for m in milestones:
        lines.append(
            f"- {m.name} | quarter={m.quarter} | planned={m.planned_date} | "
            f"priority={m.priority} | tl_declared_status={m.status} | "
            f"dependency={m.dependency} | description={m.description}"
            + (f" | remark={m.remark}" if m.remark else "")
        )
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
    """For Phase 1 Step 4 there is no DB persistence yet, so this is a stub.
    Once the Aggregation Engine (Step 6) writes to WeeklyReport rows we'll
    feed the last N here. For now the AI sees an empty signal."""
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


# ---------- Prompt 3 JSON validator --------------------------------------

def validate_prompt3_json(parsed) -> tuple[bool, list[str]]:
    """Verify structure matches the schema in AI_PROMPTS_PHASE1.md §Prompt 3.

    Returns (is_valid, list_of_issues). Issues are strings, ordered by
    severity-ish (top-level fields first, then milestones).
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
        # before the numeric check, otherwise `True`/`False` would pass.
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            issues.append(f"completion_pct={pct!r} should be int 0..100 or null")
        elif not (0 <= pct <= 100):
            issues.append(f"completion_pct={pct!r} out of range 0..100")
        elif isinstance(pct, float) and not pct.is_integer():
            # LLM occasionally emits 40.0; accept that as 40, but flag a
            # genuine fractional value (e.g. 42.7) since the schema says int.
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
            # Asymmetric trust model check (the core invariant of Prompt 3)
            if tld is not None and tld != "Done" and aiv != "NotApplicable":
                issues.append(
                    f"milestones[{i}] tl_declared_status={tld!r} but "
                    f"ai_verification={aiv!r} — AI must NOT verify non-Done "
                    f"milestones (set ai_verification='NotApplicable')"
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


# ---------- Save the run --------------------------------------------------

def save_run(custom_path, code: str, sys_p: str, user_p: str,
             raw_response: str, parsed, issues: list, llm_mode: str,
             duration_s: float, prompt_tokens: int, completion_tokens: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if custom_path:
        path = Path(custom_path)
    else:
        path = ROOT / "logs" / f"prompt3_{code}_{ts}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": ts,
        "project_code": code,
        "prompt_version": PROMPT_VERSION,
        "llm_mode": llm_mode,
        "duration_seconds": duration_s,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "system_prompt": sys_p,
        "user_prompt": user_p,
        "raw_response": raw_response,
        "parsed_response": parsed,
        "validation_issues": issues,
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return str(path)


# ---------- Main ----------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Standalone Prompt 3 test — end-to-end on one real project."
    )
    parser.add_argument("--project-code", required=True,
                        help="Project code (matches config.yaml projects[].code)")
    parser.add_argument("--save-to", default=None,
                        help="Output JSONL path (default: logs/prompt3_<code>_<ts>.jsonl)")
    parser.add_argument("--n-reports", type=int, default=4,
                        help="Past consolidated reports to include (default: 4)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render the prompt and print it; do NOT call the LLM")
    args = parser.parse_args()

    cfg = get_config()
    project = find_project(cfg, args.project_code)
    extra_max_chars = cfg.confluence.extra_page_max_chars

    print("=== Step 4: Standalone Prompt 3 Test ===")
    print(f"Project:                {project.code} - {project.name} (type: {project.type})")
    print(f"JIRA key:               {project.jira_project_key}")
    print(f"Milestones page URL:    {project.confluence_milestones_url}")
    print(f"FR page URL:            {project.confluence_fr_url}")
    n_extras = len(project.confluence_extra_pages or [])
    print(f"Extra context pages:    {n_extras}"
          + (f" (capped at {extra_max_chars} chars each)" if n_extras else ""))
    print(f"Today (IST):            {today_ist().strftime('%Y-%m-%d')}")
    print()

    # ---- 1. Confluence ---------------------------------------------------
    print("[1/6] Fetching Confluence pages (Milestones + FR + extras)...")
    confl = get_confluence_client()

    # 1a. Milestones page (REQUIRED)
    try:
        mpage = confl.get_page_by_url(project.confluence_milestones_url)
        ms = ConfluenceClient.parse_milestones_page(mpage)
    except Exception as e:
        print(f"      [FAIL] Milestones page: {type(e).__name__}: {e}")
        sys.exit(3)
    print(f"      Milestones page: {ms.title!r}")
    if ms.overview:
        print(f"        Overview:   {ms.overview[:80]!r}"
              + ("..." if len(ms.overview) > 80 else ""))
    print(f"        Milestones: {len(ms.milestones)} parsed")
    if ms.parse_warnings:
        for w in ms.parse_warnings:
            print(f"        ! {w}")

    # 1b. Functional Requirements page (REQUIRED)
    try:
        fpage = confl.get_page_by_url(project.confluence_fr_url)
        fr = ConfluenceClient.parse_fr_page(fpage)
    except Exception as e:
        print(f"      [FAIL] FR page: {type(e).__name__}: {e}")
        sys.exit(3)
    print(f"      FR page:         {fr.title!r}")
    print(f"        FR text:    {len(fr.functional_requirements or '')} chars")
    if fr.parse_warnings:
        for w in fr.parse_warnings:
            print(f"        ! {w}")

    # 1c. Extra context pages (OPTIONAL — failures don't abort)
    extras: list = []
    for url in project.confluence_extra_pages or []:
        try:
            epage = confl.get_page_by_url(url)
            ep = ConfluenceClient.parse_extra_page(epage, max_chars=extra_max_chars)
            extras.append(ep)
            mark = " [TRUNCATED]" if ep.truncated else ""
            print(f"      Extra page: {ep.title!r} ({len(ep.body_text)} chars{mark})")
        except Exception as e:
            print(f"      [WARN] extra page {url} failed: {type(e).__name__}: {e}")
            print(f"             (continuing — extras are optional)")

    # ---- 2. JIRA snapshot ------------------------------------------------
    print()
    print("[2/6] Fetching JIRA snapshot...")
    jira = get_jira_client()
    try:
        snap = jira.get_project_snapshot(
            project.jira_project_key, project.issue_types
        )
    except Exception as e:
        print(f"      [FAIL] {type(e).__name__}: {e}")
        sys.exit(3)
    print(f"      Total tasks:  {snap.total_tasks}")
    print(f"      By status:    {dict(snap.by_status)}")
    print(f"      Overdue:      {snap.overdue_count}")
    print(f"      Stale (14d):  {snap.stale_count}")
    print(f"      Recent (14d): {len(snap.recent_activity)}")

    # ---- 3. Past consolidated weekly reports ----------------------------
    print()
    print(f"[3/6] Loading last {args.n_reports} weekly reports...")
    # Phase 1 Step 4: DB-backed reports don't exist yet (Step 6 will create them).
    # Pass an empty signal — AI should lower confidence on first-run projects.
    reports: list = []
    print(f"      (Step 6 not implemented yet — passing empty list.")
    print(f"       AI should respond with reduced confidence for first-run projects.)")

    # ---- 4. Render Prompt 3 ---------------------------------------------
    print()
    print(f"[4/6] Rendering Prompt 3 from {PROMPT_FILE}.txt...")
    sys_prompt, user_template = load_prompt(PROMPT_FILE)
    user_prompt = user_template.format(
        project_name=project.name,
        project_type=project.type,
        start_date=project.start_date or "(not set)",
        planned_end_date=project.planned_end_date or "(not set)",
        today_date=today_ist().strftime("%Y-%m-%d"),
        # Milestones page
        milestones_page_overview=ms.overview or "(none)",
        confluence_milestones_block=render_milestones_block(ms.milestones),
        # FR page
        fr_page_overview=fr.overview or "(none)",
        confluence_functional_requirements=fr.functional_requirements or "(none)",
        # Extra context pages
        extra_context_pages_block=render_extra_pages_block(extras),
        # JIRA snapshot
        jira_total=snap.total_tasks,
        jira_status_block=render_jira_status_block(snap.by_status),
        jira_overdue_count=snap.overdue_count,
        jira_stale_count=snap.stale_count,
        jira_recent_activity_block=render_recent_activity(snap.recent_activity),
        # Past weekly reports
        n_reports=args.n_reports,
        weekly_reports_block=render_weekly_reports_block(reports),
    )
    print(f"      System prompt: {len(sys_prompt)} chars")
    print(f"      User prompt:   {len(user_prompt)} chars")

    if args.dry_run:
        print()
        print("=== System Prompt ===")
        print(sys_prompt)
        print()
        print("=== User Prompt ===")
        print(user_prompt)
        print()
        print("[dry-run] Skipping LLM call. Re-run without --dry-run for a real status.")
        sys.exit(0)

    # ---- 5. LLM call -----------------------------------------------------
    print()
    print("[5/6] Calling LLM (json_output=True)...")
    llm = get_llm_client()
    print(f"      Provider: {llm.mode}")
    try:
        result = llm.complete(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            json_output=True,
        )
    except Exception as e:
        print(f"      [FAIL] {type(e).__name__}: {e}")
        sys.exit(4)
    print(f"      Duration: {result.duration_seconds}s")
    print(f"      Tokens:   {result.prompt_tokens} prompt + "
          f"{result.completion_tokens} completion")

    # ---- 6. Parse + validate --------------------------------------------
    print()
    print("[6/6] Parsing + validating JSON output...")
    raw = result.text
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"      [FAIL] LLM did not return valid JSON: {e}")
        print()
        print("=== Raw response (first 800 chars) ===")
        print(raw[:800])
        save_path = save_run(
            args.save_to, project.code, sys_prompt, user_prompt, raw,
            None, [f"JSON decode failed: {e}"], llm.mode,
            result.duration_seconds, result.prompt_tokens, result.completion_tokens,
        )
        print()
        print(f"Saved raw run to {save_path}")
        sys.exit(5)

    valid, issues = validate_prompt3_json(parsed)

    print()
    print("=== Result ===")
    print(f"overall_health:  {parsed.get('overall_health')}")
    print(f"schedule_status: {parsed.get('schedule_status')}")
    print(f"completion_pct:  {parsed.get('completion_pct')}")
    print(f"confidence:      {parsed.get('confidence')}")
    print(f"milestones:      {len(parsed.get('milestones') or [])}")
    print(f"evidence_cited:  {len(parsed.get('evidence_cited') or [])}")
    print()
    print(f"rationale: {parsed.get('rationale', '')}")
    print()

    # Persist the full run for offline analysis / regression
    save_path = save_run(
        args.save_to, project.code, sys_prompt, user_prompt, raw,
        parsed, issues, llm.mode,
        result.duration_seconds, result.prompt_tokens, result.completion_tokens,
    )
    print(f"Saved run to {save_path}")
    print()

    if valid:
        print("[PASS] Output matches Prompt 3's expected schema.")
        sys.exit(0)
    else:
        print(f"[FAIL] Output has {len(issues)} schema issue(s):")
        for i in issues:
            print(f"  - {i}")
        sys.exit(6)


if __name__ == "__main__":
    main()
