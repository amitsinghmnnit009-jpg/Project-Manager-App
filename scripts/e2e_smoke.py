"""End-to-end smoke test for the office machine (Step 12).

Runs the full Phase 1 pipeline against ONE real project — connectivity
checks → registry sync → aggregation → highlights → status compute →
reminders — and prints [OK] / [FAIL] with timing at each step. Exit
code 0 if every step passes, 1 otherwise.

Usage:
    python scripts/e2e_smoke.py --project-code MAICTJ
    python scripts/e2e_smoke.py --project-code MAICTJ --week-of 2026-05-04
    python scripts/e2e_smoke.py --project-code MAICTJ --skip-reminders

This script does NOT start the FastAPI server or scheduler — it exercises
the engine code paths directly. To verify the REST API end-to-end as
well, after this script passes:

    python manage.py serve            # in another terminal
    curl http://127.0.0.1:8000/api/projects
    curl http://127.0.0.1:8000/api/projects/<CODE>/status
    curl http://127.0.0.1:8000/api/projects/<CODE>/reports/latest
"""
from __future__ import annotations
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path


# Force UTF-8 stdout on Windows so engine-output em-dashes / non-ASCII
# milestone names render correctly in cmd.exe (cp1252 default).
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------- Step harness --------------------------------------------------

class StepReporter:
    """Tiny [OK]/[FAIL]-with-timing reporter."""

    def __init__(self):
        self.results: list[tuple[str, bool, float, str]] = []

    def run(self, label: str, fn):
        """Run fn(); catch exceptions; report. Returns fn's value or None."""
        print(f"==> {label}")
        t0 = time.perf_counter()
        try:
            value = fn()
            dt = time.perf_counter() - t0
            print(f"    [OK] in {dt:.2f}s")
            self.results.append((label, True, dt, ""))
            return value
        except Exception as e:
            dt = time.perf_counter() - t0
            err = f"{type(e).__name__}: {e}"
            print(f"    [FAIL] in {dt:.2f}s — {err}")
            self.results.append((label, False, dt, err))
            return None

    def summary(self) -> bool:
        print()
        print("=" * 64)
        print("Summary")
        print("=" * 64)
        for label, ok, dt, err in self.results:
            tag = "OK  " if ok else "FAIL"
            print(f"  [{tag}] {dt:6.2f}s  {label}")
            if err:
                print(f"            ! {err}")
        all_ok = all(ok for _, ok, _, _ in self.results)
        print()
        print("=" * 64)
        print(f"Overall: {'PASS' if all_ok else 'FAIL'} "
              f"({sum(1 for _, ok, _, _ in self.results if ok)}/"
              f"{len(self.results)} steps OK)")
        return all_ok


# ---------- Steps --------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="End-to-end smoke test against ONE real project."
    )
    parser.add_argument("--project-code", required=True,
                        help="Project code (matches config.json projects[].code)")
    parser.add_argument("--week-of", default=None,
                        help="Monday of the report week (YYYY-MM-DD). "
                             "Defaults to current IST week.")
    parser.add_argument("--skip-reminders", action="store_true",
                        help="Skip the pre/post-cutoff reminder dispatch steps")
    parser.add_argument("--skip-prior-week", action="store_true",
                        help="Skip aggregating the prior week (highlights will then "
                             "produce the 'first report' message)")
    args = parser.parse_args()

    code = args.project_code
    rep = StepReporter()

    print()
    print("=" * 64)
    print(f"E2E Smoke Test — project {code!r}")
    print("=" * 64)

    # ---- 0. Resolve week_of --------------------------------------------
    from datetime import date as _date, timedelta
    if args.week_of:
        try:
            week_of = _date.fromisoformat(args.week_of)
        except ValueError:
            print(f"[FAIL] --week-of must be YYYY-MM-DD: {args.week_of!r}")
            sys.exit(2)
    else:
        from app.utils.dates import week_of as compute_week_of
        week_of = compute_week_of()
    week_prior = week_of - timedelta(days=7)
    print(f"  This week:  {week_of}")
    print(f"  Prior week: {week_prior}")
    print()

    # ---- 1. JIRA connectivity ------------------------------------------
    def step_jira_whoami():
        from app.clients import get_jira_client
        me = get_jira_client().whoami()
        print(f"    auth as: {me.get('displayName') or me.get('name')}")
    rep.run("JIRA connectivity (whoami)", step_jira_whoami)

    # ---- 2. Confluence connectivity ------------------------------------
    def step_confl_whoami():
        from app.clients import get_confluence_client
        out = get_confluence_client().whoami()
        n = out.get("spaces_visible") or out.get("content_visible") or 0
        print(f"    items visible: {n}")
    rep.run("Confluence connectivity (whoami via /content)", step_confl_whoami)

    # ---- 3. LLM connectivity -------------------------------------------
    def step_llm_ping():
        from app.llm.base import get_llm_client
        client = get_llm_client()
        out = client.complete(
            "You are a smoke test. Respond with exactly the word OK.",
            "ping",
        )
        text = (out.text or "").strip()[:40]
        print(f"    mode={client.mode} response={text!r}")
    rep.run("LLM connectivity (ping)", step_llm_ping)

    # ---- 4. Project registry -------------------------------------------
    def step_registry_sync():
        from app.db import init_database
        from app.registry.projects import sync_projects_from_config, get_project_by_code
        init_database()
        report = sync_projects_from_config()
        print(f"    created={report.get('created_count', 0)} "
              f"updated={report.get('updated_count', 0)}")
        if get_project_by_code(code) is None:
            raise RuntimeError(
                f"Project {code!r} not in DB after sync. "
                f"Check that it's in config.json projects[].code."
            )
    rep.run(f"Registry sync + project {code!r} present in DB", step_registry_sync)

    # ---- 5. Engineer mapping -------------------------------------------
    def step_engineers():
        from app.registry.engineers import engineers_on_project
        engs = engineers_on_project(code)
        if not engs:
            raise RuntimeError(
                f"No engineers assigned to {code!r} in engineer mapping JSON."
            )
        print(f"    {len(engs)} engineer(s) assigned: "
              f"{[e.knox_id for e in engs[:5]]}"
              + ("..." if len(engs) > 5 else ""))
    rep.run("Engineer mapping resolves project's engineers", step_engineers)

    # ---- 6. Aggregate prior week (optional seed for highlights) --------
    if not args.skip_prior_week:
        def step_agg_prior():
            from app.engines.aggregation import run_weekly_aggregation
            r = run_weekly_aggregation(code, week_of=week_prior)
            if not r.success:
                raise RuntimeError(r.error or "aggregation returned success=False")
            print(f"    week_of={r.week_of} engineers={r.engineer_count} "
                  f"records={r.activity_records} "
                  f"is_regen={r.is_regeneration} "
                  f"duration={r.duration_seconds:.1f}s")
        rep.run(f"Aggregate prior week ({week_prior})", step_agg_prior)

    # ---- 7. Aggregate current week -------------------------------------
    def step_agg_current():
        from app.engines.aggregation import run_weekly_aggregation
        r = run_weekly_aggregation(code, week_of=week_of)
        if not r.success:
            raise RuntimeError(r.error or "aggregation returned success=False")
        print(f"    week_of={r.week_of} engineers={r.engineer_count} "
              f"records={r.activity_records} is_regen={r.is_regeneration} "
              f"duration={r.duration_seconds:.1f}s")
        if r.unmapped_authors_count:
            print(f"    [WARN] {r.unmapped_authors_count} unmapped JIRA author(s) "
                  "— update engineer_project_mapping.json")
    rep.run(f"Aggregate current week ({week_of})", step_agg_current)

    # ---- 8. Highlights for current week --------------------------------
    def step_highlights():
        from app.engines.highlights import run_highlights
        r = run_highlights(code, week_of=week_of)
        if not r.success:
            raise RuntimeError(r.error or "highlights returned success=False")
        kind = "first-week (no prior comparison)" if r.is_first_week else (
            f"compared against {r.last_week_of}"
        )
        print(f"    {kind}, duration={r.duration_seconds:.1f}s")
    rep.run("Highlights for current week", step_highlights)

    # ---- 9. Status compute ---------------------------------------------
    def step_status():
        from app.engines.status import run_status_compute
        r = run_status_compute(code)
        if not r.success:
            raise RuntimeError(r.error or "status compute returned success=False")
        p = r.parsed or {}
        print(f"    health={p.get('overall_health')} "
              f"schedule={p.get('schedule_status')} "
              f"completion={p.get('completion_pct')}% "
              f"confidence={p.get('confidence')} "
              f"changed={r.changed} duration={r.duration_seconds:.1f}s")
    rep.run("Status compute", step_status)

    # ---- 10. Reminders -------------------------------------------------
    if not args.skip_reminders:
        def step_pre_reminders():
            from app.notifications import run_pre_cutoff_reminders
            r = run_pre_cutoff_reminders(code, week_of=week_of)
            if not r.success:
                raise RuntimeError(r.error or "pre reminders returned success=False")
            print(f"    targeted={r.engineers_targeted} sent={len(r.sent)} "
                  f"failed={len(r.failed_knox_ids)}")
        rep.run("Pre-cutoff reminders (mock-sent)", step_pre_reminders)

        def step_post_reminders():
            from app.notifications import run_post_cutoff_reminders
            r = run_post_cutoff_reminders(code, week_of=week_of)
            if not r.success:
                raise RuntimeError(r.error or "post reminders returned success=False")
            print(f"    targeted={r.engineers_targeted} sent={len(r.sent)} "
                  f"failed={len(r.failed_knox_ids)}")
        rep.run("Post-cutoff reminders (mock-sent)", step_post_reminders)

    # ---- 11. Summary + exit code ---------------------------------------
    ok = rep.summary()
    print()
    print("Now verify via the REST API:")
    print(f"  python manage.py serve   # in another terminal")
    print(f"  curl http://127.0.0.1:8000/api/projects/{code}/status")
    print(f"  curl http://127.0.0.1:8000/api/projects/{code}/reports/latest")
    print(f"  curl http://127.0.0.1:8000/admin/scheduler/jobs")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
