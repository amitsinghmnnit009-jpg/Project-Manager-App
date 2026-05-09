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

# Force UTF-8 stdout on Windows so prompt content (em-dashes, non-ASCII
# project titles, Korean Confluence titles, etc.) prints correctly. The
# console codepage on Windows is cp1252 by default, which mangles e.g.
# em-dash and curly quotes that the LLM faithfully copies through.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

# Make `app` importable when run as a plain script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_config
from app.clients import get_jira_client, get_confluence_client, ConfluenceClient
from app.llm.base import get_llm_client
from app.utils.dates import today_ist

# Step 8 refactor: prompt rendering + JSON validation moved into a shared
# module so the standalone script and the production Status Engine produce
# IDENTICAL prompts and validation verdicts for the same inputs. The script
# remains useful for prompt-tuning workflows (saves the full prompt + raw
# response to logs/prompt3_<code>_<ts>.jsonl); the engine writes to DB.
from app.engines._status_prompt import (
    PROMPT_FILE, PROMPT_VERSION,
    render_full_prompt, validate_prompt3_json,
    render_milestones_block, render_jira_status_block, render_recent_activity,
    render_weekly_reports_block, render_extra_pages_block,
)


# ---------- Project lookup ------------------------------------------------

def find_project(cfg, code: str):
    """Find a project by `code` in config.json's projects: list."""
    norm = code.strip().lower()
    for p in cfg.projects:
        if p.code.strip().lower() == norm:
            return p
    print(f"[FAIL] Project code {code!r} not found in config.json's projects: list.")
    available = [p.code for p in cfg.projects]
    if available:
        print(f"       Available codes: {available}")
    else:
        print(f"       Your projects: list is empty. Add at least one project entry — see config.json example.")
    sys.exit(2)


# Block renderers and validate_prompt3_json are imported from
# app.engines._status_prompt (Step 8 refactor) so the script and the
# Status Engine produce identical prompts + verdicts. The script keeps
# its own save_run() because the engine persists to DB instead of JSONL.


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
                        help="Project code (matches config.json projects[].code)")
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
    # Single shared renderer used by both the script (here) and the
    # production Status Engine — so the LLM sees IDENTICAL prompts for the
    # same inputs whether invoked via this script or via the engine.
    sys_prompt, user_prompt = render_full_prompt(
        project_name=project.name,
        project_type=project.type,
        start_date=project.start_date,
        planned_end_date=project.planned_end_date,
        today_date=today_ist().strftime("%Y-%m-%d"),
        milestones_page=ms,
        fr_page=fr,
        extras=extras,
        jira_snapshot=snap,
        weekly_reports=reports,
        n_reports=args.n_reports,
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
