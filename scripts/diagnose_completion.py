"""Diagnose why completion_pct is wrong for a project.

Usage:
    python scripts/diagnose_completion.py MAICTJ

What it checks:
  1. Current DB state — stored completion_pct, milestones breakdown
  2. Expected completion_pct — computed from the stored milestone data
     using the same rule as _apply_completion_pct_rule
  3. Discrepancy — whether the stored value matches the expected value,
     and what would fix it (trigger a fresh compute)
  4. Recent AIComputeLog entries — success/failure + error text for the
     last 5 runs, so you can see WHY a compute failed
  5. What _normalise_milestone_ai_verification would change on the
     CURRENT stored milestones (diagnosis only, no DB write)
"""
from __future__ import annotations
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os
os.chdir(ROOT)  # ensure relative DB path in config resolves correctly

from app.db import init_database, session_scope
from app.models import Project, ProjectStatus, AIComputeLog
from sqlalchemy import select


def _sep(label: str = "") -> None:
    width = 72
    if label:
        pad = width - len(label) - 4
        print(f"\n{'='*2} {label} {'='*pad}")
    else:
        print("=" * width)


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "MAICTJ"

    init_database()

    with session_scope() as s:
        proj = s.execute(
            select(Project).where(Project.code == code)
        ).scalar_one_or_none()

        if proj is None:
            print(f"[FAIL] Project {code!r} not found in DB.")
            print("       Run: python manage.py sync-projects")
            sys.exit(1)

        project_id = proj.id

        status_row = s.execute(
            select(ProjectStatus).where(ProjectStatus.project_id == project_id)
        ).scalar_one_or_none()

        logs = s.execute(
            select(AIComputeLog)
            .where(AIComputeLog.project_id == project_id)
            .order_by(AIComputeLog.started_at.desc())
            .limit(5)
        ).scalars().all()

        # Snapshot all data before session closes
        stored_pct   = status_row.completion_pct if status_row else None
        stored_health = status_row.overall_health if status_row else None
        stored_schedule = status_row.schedule_status if status_row else None
        computed_at  = status_row.computed_at if status_row else None
        milestones   = list(status_row.milestones_json or []) if status_row else []
        prompt_ver   = status_row.prompt_version if status_row else None

        log_rows = [
            {
                "started_at": r.started_at,
                "success": r.success_flag,
                "error_text": r.error_text or "",
                "response_excerpt": (r.response_excerpt or "")[:200],
            }
            for r in logs
        ]

    # ------------------------------------------------------------------ #
    # 1. Current DB state
    # ------------------------------------------------------------------ #
    _sep(f"DB state for {code}")
    if status_row is None:
        print("  No ProjectStatus row found — status has never been computed.")
        print("  Fix: click 'Refresh Status' on the project detail page,")
        print("       or run: python manage.py run-status " + code)
    else:
        print(f"  overall_health  : {stored_health}")
        print(f"  schedule_status : {stored_schedule}")
        print(f"  completion_pct  : {stored_pct}")
        print(f"  computed_at     : {computed_at}")
        print(f"  prompt_version  : {prompt_ver}")
        print(f"  milestone count : {len(milestones)}")

    # ------------------------------------------------------------------ #
    # 2. Milestone breakdown (tl_declared_status x ai_verification)
    # ------------------------------------------------------------------ #
    _sep("Milestone breakdown")
    if not milestones:
        print("  (no milestones stored)")
    else:
        counts: dict[tuple, int] = {}
        for m in milestones:
            key = (m.get("tl_declared_status", "?"), m.get("ai_verification", "?"))
            counts[key] = counts.get(key, 0) + 1

        # Print sorted
        for (tld, aiv), n in sorted(counts.items(), key=lambda x: (x[0][0] or "", x[0][1] or "")):
            flag = ""
            if tld == "Done":
                if aiv in ("Verified", "Inconclusive"):
                    flag = "  ← COUNTS toward completion"
                elif aiv == "Disputed":
                    flag = "  ← EXCLUDED (Disputed)"
                elif aiv == "NotApplicable":
                    flag = "  ← BUG: Done milestone should not be NotApplicable"
            else:
                if aiv != "NotApplicable":
                    flag = "  ← BUG: non-Done should be NotApplicable (LLM error)"
            print(f"  tl={tld:<12}  ai={aiv:<14}  count={n:>3}{flag}")

    # ------------------------------------------------------------------ #
    # 3. Expected completion_pct
    # ------------------------------------------------------------------ #
    _sep("Expected completion_pct (Rule 5 arithmetic)")
    if milestones:
        total = len(milestones)

        # Apply normalisation (same logic as _normalise_milestone_ai_verification)
        normalised = []
        for m in milestones:
            m2 = dict(m)
            if m2.get("tl_declared_status") != "Done":
                m2["ai_verification"] = "NotApplicable"
            elif m2.get("ai_verification") == "NotApplicable":
                m2["ai_verification"] = "Inconclusive"
            normalised.append(m2)

        effective_done_before = sum(
            1 for m in milestones
            if m.get("tl_declared_status") == "Done"
            and m.get("ai_verification") in ("Verified", "Inconclusive")
        )
        effective_done_after = sum(
            1 for m in normalised
            if m.get("tl_declared_status") == "Done"
            and m.get("ai_verification") in ("Verified", "Inconclusive")
        )

        expected_before = round(effective_done_before / total * 100) if total else 0
        expected_after  = round(effective_done_after  / total * 100) if total else 0

        print(f"  Total milestones : {total}")
        print(f"  Without normalisation : effective_done={effective_done_before}  →  expected_pct={expected_before}%")
        print(f"  With normalisation    : effective_done={effective_done_after }  →  expected_pct={expected_after }%")
        print(f"  Currently stored      : completion_pct={stored_pct}")

        if stored_pct == expected_after:
            print("\n  OK — stored value matches expected. No issue here.")
            print("     (If the UI shows something different, it may be a")
            print("      browser cache issue — hard-refresh the page.)")
        else:
            print(f"\n  MISMATCH — stored={stored_pct}  expected={expected_after}%")
            if stored_pct == expected_before:
                print("  Diagnosis: _normalise_milestone_ai_verification is NOT")
                print("  being called (old code running, or compute not triggered).")
            else:
                print("  Diagnosis: the stored milestone data itself is stale —")
                print("  a fresh run-status compute is needed.")
            print()
            print("  To fix: click 'Refresh Status' on the project detail page")
            print("         or run: python manage.py run-status " + code)

    # ------------------------------------------------------------------ #
    # 4. Recent AIComputeLog entries
    # ------------------------------------------------------------------ #
    _sep("Last 5 AIComputeLog runs")
    if not log_rows:
        print("  No log entries found — status has never been computed.")
    else:
        for i, r in enumerate(log_rows, 1):
            ok = "OK " if r["success"] else "FAIL"
            print(f"  [{i}] {ok}  started={r['started_at']}")
            if not r["success"] and r["error_text"]:
                print(f"       ERROR: {r['error_text'][:120]}")
            if r["success"] and r["response_excerpt"]:
                # Show just the completion_pct from the raw LLM response
                excerpt = r["response_excerpt"]
                pct_idx = excerpt.find('"completion_pct"')
                if pct_idx >= 0:
                    print(f"       LLM said: ...{excerpt[pct_idx:pct_idx+40]}...")

    # ------------------------------------------------------------------ #
    # 5. What normalisation would change
    # ------------------------------------------------------------------ #
    _sep("_normalise_milestone_ai_verification diff")
    if milestones:
        changed = []
        for m in milestones:
            tld = m.get("tl_declared_status")
            aiv = m.get("ai_verification")
            if tld != "Done" and aiv != "NotApplicable":
                changed.append((m.get("name", "?")[:55], tld, aiv, "NotApplicable"))
            elif tld == "Done" and aiv == "NotApplicable":
                changed.append((m.get("name", "?")[:55], tld, aiv, "Inconclusive"))

        if not changed:
            print("  Nothing to normalise — all milestones already correct.")
            print("  The LLM's output was fine; completion_pct should match")
            print("  the expected value above after a fresh compute.")
        else:
            print(f"  {len(changed)} milestone(s) would be corrected:")
            for name, tld, old_aiv, new_aiv in changed:
                print(f"    '{name}'")
                print(f"      tl={tld}  ai: {old_aiv!r} → {new_aiv!r}")
    else:
        print("  (no milestone data to inspect)")

    _sep()
    print()


if __name__ == "__main__":
    main()
