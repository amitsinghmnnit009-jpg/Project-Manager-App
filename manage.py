"""CLI for Project-Manager-App management tasks.

Usage:
    python manage.py reset-db                            # Drop and recreate the SQLite DB
    python manage.py serve                               # Start the FastAPI server
    python manage.py sync-projects                       # Sync config.json projects[] into DB
    python manage.py list-projects                       # List projects currently in DB
    python manage.py list-engineers [--project-code X]   # List engineers from mapping JSON
    python manage.py run-status <project_code>           # Status Engine: compute + persist
    python manage.py run-aggregation <project_code>      # Aggregation Engine: weekly report (Step 6)
    python manage.py run-highlights <project_code>       # Highlights Engine: week-over-week (Step 7)
    python manage.py scheduler-status                    # List APScheduler jobs and next run times
    python manage.py whoami-jira                         # Verify JIRA token
    python manage.py whoami-confluence                   # Verify Confluence token
    python manage.py jira-search <project_key>           # Search recent issues in JIRA
    python manage.py jira-snapshot <project_key>         # Project snapshot (counts/overdue/recent)
    python manage.py jira-task <issue_key>               # Comments + worklogs of one task
    python manage.py jira-activity <project_key>         # Engineer-grouped weekly activity
    python manage.py show-mapping [project_key]          # Diagnostic: see parsed engineer mapping
    python manage.py fetch-confluence-page <url>                # Parse a Milestones page (default)
    python manage.py fetch-confluence-page <url> --kind fr      # Parse a Functional Requirements page
    python manage.py fetch-confluence-page <url> --kind extra   # Parse an extra context page
    python manage.py fetch-confluence-page <url> --full         # Print full text (no terminal truncation)
    python manage.py confluence-probe                    # Single-shot raw 429-diagnostic probe
    python manage.py llm-ping                            # Sanity check: send one short completion
    python manage.py llm-embed <text>                    # Sanity check: embed one piece of text
    python manage.py show-last-llm-call [--lines N]      # Print full prompt+response of recent LLM call(s)
    python manage.py show-last-external-calls [--source jira|confluence] [--lines N] [--errors-only]   # Print recent JIRA/Confluence calls
"""
from __future__ import annotations
import sys
import click


@click.group()
def cli():
    """Project-Manager-App management commands."""
    pass


@cli.command("reset-db")
@click.confirmation_option(
    prompt="This will DROP the entire database. Continue?"
)
def reset_db():
    """Drop all tables and recreate from current models. Phase-1 dev convenience."""
    from app.db import reset_database
    reset_database()
    click.echo("Database reset complete.")


@cli.command("serve")
@click.option("--host", default=None, help="Override host from config.json")
@click.option("--port", default=None, type=int, help="Override port from config.json")
@click.option("--reload", is_flag=True, help="Auto-reload on code change (dev only)")
def serve(host, port, reload):
    """Start the FastAPI server using uvicorn."""
    import uvicorn
    from app.config import get_config
    cfg = get_config()
    uvicorn.run(
        "app.api.main:app",
        host=host or cfg.api.host,
        port=port or cfg.api.port,
        reload=reload,
    )


@cli.command("sync-projects")
def sync_projects_cmd():
    """Sync config.json's projects[] into the projects DB table.

    Idempotent — safe to run any time. The FastAPI server also runs this
    automatically on startup, but this CLI command is useful when:
      - You edited config.json and want changes reflected without restart
      - You want to verify the sync result before starting the server
      - You're running CLI engines (run-status, run-aggregation, etc.)
        before the server has been started.

    Insert: project in config but not in DB by code -> insert.
    Update: project in both -> overwrite syncable columns.
    Delete: NOT performed. A project removed from config.json stays in
            DB; the sync logs a warning. This protects history (status
            timeline, weekly reports) from accidental config edits.
    """
    from app.db import init_database
    from app.registry.projects import sync_projects_from_config

    init_database()  # ensure tables exist
    try:
        report = sync_projects_from_config()
    except Exception as e:
        click.echo(f"[FAIL] {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo("[OK] Project registry sync complete.")
    click.echo(f"  Created:           {report['created_count']}")
    click.echo(f"  Updated:           {report['updated_count']}")
    click.echo(f"  Total in config:   {report['total_in_config']}")
    click.echo(f"  Total in DB now:   {report['total_in_db_after']}")
    if report["stale_codes"]:
        click.echo(
            f"  [WARN] Stale (in DB, not in config.json): {report['stale_codes']}"
        )
        click.echo(
            "         These rows are LEFT UNCHANGED to protect history. "
            "Hard-delete via direct DB edit if truly desired."
        )


@cli.command("list-projects")
def list_projects_cmd():
    """List every project currently in the DB."""
    from app.registry.projects import list_projects
    rows = list_projects()
    if not rows:
        click.echo("[INFO] No projects in DB. Run 'python manage.py sync-projects' first.")
        return
    click.echo(f"{len(rows)} project(s) in DB:")
    for p in rows:
        click.echo(
            f"  - {p['code']:20s} | jira_key={p['jira_project_key']:12s} | "
            f"state={p['state']:10s} | {p['name']}"
        )


@cli.command("list-engineers")
@click.option("--project-code", default=None,
              help="Filter to engineers assigned to this project")
def list_engineers_cmd(project_code):
    """List engineers from the engineer mapping JSON.

    Without --project-code: lists every engineer + their assigned project codes.
    With --project-code:    lists only engineers assigned to that project.
    """
    from app.registry.engineers import (
        load_engineer_mapping, engineers_on_project, projects_for_engineer,
    )

    mapping = load_engineer_mapping()
    if mapping.parse_warnings:
        click.echo("[WARN] Mapping parse warnings:")
        for w in mapping.parse_warnings:
            click.echo(f"  ! {w}")
        click.echo("")

    if project_code:
        engs = engineers_on_project(project_code)
        if not engs:
            click.echo(f"No engineers assigned to project {project_code!r}.")
            return
        click.echo(f"{len(engs)} engineer(s) assigned to {project_code!r}:")
        for e in engs:
            click.echo(f"  - {e.name:30s}  knox_id={e.knox_id}")
    else:
        click.echo(f"{len(mapping.engineers)} engineer(s) total:")
        for e in mapping.engineers:
            projects = projects_for_engineer(e.knox_id)
            click.echo(
                f"  - {e.name:30s}  knox_id={e.knox_id:20s}  "
                f"projects={projects}"
            )


@cli.command("run-aggregation")
@click.argument("project_code")
@click.option("--week-of", default=None,
              help="Monday of the report week (YYYY-MM-DD). Defaults to current week (IST).")
@click.option("--regenerate", is_flag=True,
              help="Marker for caller intent. The engine is ALWAYS idempotent — "
                   "if a row already exists for (project, week) it's updated and "
                   "regenerated_count is bumped (per FR §B.3.4). The flag is "
                   "currently informational only.")
def run_aggregation(project_code, week_of, regenerate):
    """Manually trigger weekly aggregation for one project for one week.

    Pipeline:
      1. Look up project in DB
      2. Resolve engineers on project (from engineer mapping JSON)
      3. Fetch each engineer's JIRA activity for the week
      4. Render Prompt 1 with raw inputs grouped by anonymised engineer
      5. Call LLM (Markdown output, NOT JSON)
      6. Persist as a WeeklyReport row (insert OR update + bump regenerated_count)

    The Highlights / Things to Watch section is intentionally left empty
    by Prompt 1's system message — Step 7 (Highlights Engine) fills it
    in via Prompt 2 in a separate run.
    """
    from datetime import datetime as _dt
    from app.engines.aggregation import run_weekly_aggregation

    week_date = None
    if week_of:
        try:
            week_date = _dt.strptime(week_of, "%Y-%m-%d").date()
        except ValueError as e:
            click.echo(f"[FAIL] --week-of must be YYYY-MM-DD: {e}", err=True)
            sys.exit(1)

    result = run_weekly_aggregation(
        project_code, week_of=week_date, regenerate=regenerate,
    )

    if not result.success:
        click.echo(f"[FAIL] Aggregation failed: {result.error}", err=True)
        sys.exit(1)

    click.echo("[OK] Aggregation succeeded.")
    click.echo(f"  Week of:                 {result.week_of}")
    click.echo(f"  LLM mode:                {result.llm_mode}")
    click.echo(f"  Duration:                {result.duration_seconds}s")
    click.echo(
        f"  Tokens:                  {result.prompt_tokens} prompt + "
        f"{result.completion_tokens} completion"
    )
    click.echo(f"  Engineers in scope:      {result.engineer_count}")
    click.echo(f"  Activity records:        {result.activity_records}")
    if result.unmapped_authors_count:
        click.echo(
            f"  [WARN] Unmapped authors: {result.unmapped_authors_count} "
            f"(see system.jsonl for names — update engineer_project_mapping.json)"
        )
    if result.is_regeneration:
        click.echo("  Regeneration?:           YES — existing row updated, regenerated_count bumped")
    else:
        click.echo("  Regeneration?:           no — fresh insert")
    click.echo("")
    click.echo("=== Generated report ===")
    click.echo(result.content_markdown)


@cli.command("run-highlights")
@click.argument("project_code")
@click.option("--week-of", default=None,
              help="Monday of the report week (YYYY-MM-DD). Defaults to current week (IST).")
def run_highlights(project_code, week_of):
    """Manually trigger Highlights generation for one project for one week.

    Pipeline (Step 7):
      1. Look up project in DB
      2. Load this week's WeeklyReport row (REQUIRED — Step 6 must have run)
      3. Load last week's WeeklyReport row (OPTIONAL — first-week is OK)
      4. Strip any existing Highlights from this week (re-run safety)
      5. Render Prompt 2 (Markdown output, NOT JSON)
      6. Splice the LLM's output into this week's report
      7. Persist updated content_markdown + prompt_version_highlights

    Idempotent: re-running for the same (project, week) replaces the existing
    Highlights section. To produce a meaningful (non first-week) result,
    Step 6 must have produced WeeklyReport rows for BOTH this week AND last
    week — typically by running:
        python manage.py run-aggregation <code> --week-of <last-monday>
        python manage.py run-aggregation <code>
    before this command.
    """
    from datetime import datetime as _dt
    from app.engines.highlights import run_highlights as run_highlights_engine

    week_date = None
    if week_of:
        try:
            week_date = _dt.strptime(week_of, "%Y-%m-%d").date()
        except ValueError as e:
            click.echo(f"[FAIL] --week-of must be YYYY-MM-DD: {e}", err=True)
            sys.exit(1)

    result = run_highlights_engine(project_code, week_of=week_date)

    if not result.success:
        click.echo(f"[FAIL] Highlights failed: {result.error}", err=True)
        sys.exit(1)

    click.echo("[OK] Highlights succeeded.")
    click.echo(f"  Week of:                 {result.week_of}")
    click.echo(f"  Last week of:            "
               f"{result.last_week_of if result.last_week_of else '(no prior week)'}")
    click.echo(f"  First week (no compare): {'YES' if result.is_first_week else 'no'}")
    click.echo(f"  LLM mode:                {result.llm_mode}")
    click.echo(f"  Duration:                {result.duration_seconds}s")
    click.echo(
        f"  Tokens:                  {result.prompt_tokens} prompt + "
        f"{result.completion_tokens} completion"
    )
    click.echo("")
    click.echo("=== Highlights section produced by LLM ===")
    click.echo(result.highlights_section)
    click.echo("")
    click.echo("=== Updated full report (with Highlights spliced in) ===")
    click.echo(result.content_markdown)


@cli.command("scheduler-status")
def scheduler_status():
    """Build a scheduler from current config + DB and print all jobs that
    WOULD be registered at server startup, with their next run times.

    Does NOT actually start the scheduler — safe to run on the office
    machine without firing any LLM/JIRA work.
    """
    from app.config import get_config
    from app.scheduler import _build_scheduler
    from app.registry.projects import list_projects

    cfg = get_config()
    projects = list_projects()
    if not projects:
        click.echo("[INFO] No projects in DB. Run 'sync-projects' first.")
        return

    sched = _build_scheduler(cfg)
    jobs = sched.get_jobs()
    if not jobs:
        click.echo(f"[INFO] {len(projects)} project(s) but no jobs registered "
                   "(check status_recompute_cadence + weekly_cutoff).")
        return

    click.echo(f"[OK] {len(jobs)} job(s) registered for "
               f"{len(projects)} project(s):")
    click.echo(f"  Timezone:                {cfg.scheduler.timezone}")
    click.echo(f"  Daily status hour:       {cfg.scheduler.daily_status_hour:02d}:00")
    click.echo(f"  Weekly aggregation offset: +{cfg.scheduler.weekly_aggregation_offset_minutes} min after cutoff")
    click.echo(f"  Misfire grace:           {cfg.scheduler.misfire_grace_seconds}s")
    click.echo("")

    # APScheduler 3.x sets `next_run_time` on the Job; some versions or
    # not-yet-started schedulers don't expose that attribute. Compute next
    # fire time from the trigger directly so this CLI works regardless.
    import pytz
    from datetime import datetime as _dt
    tz = pytz.timezone(cfg.scheduler.timezone)
    now = _dt.now(tz)

    for job in sorted(jobs, key=lambda j: j.id):
        next_run = (
            getattr(job, "next_run_time", None)
            or getattr(job, "next_fire_time", None)  # APScheduler 4.x rename
        )
        if next_run is None:
            try:
                next_run = job.trigger.get_next_fire_time(None, now)
            except Exception:
                next_run = None
        next_str = next_run.strftime("%Y-%m-%d %H:%M %Z") if next_run else "(not scheduled)"
        click.echo(f"  {job.id:30s}  next: {next_str}")
        click.echo(f"    name:   {job.name}")
        click.echo(f"    trigger: {job.trigger}")


@cli.command("run-status")
@click.argument("project_code")
def run_status(project_code):
    """Manually trigger project status computation for one project.

    Runs the full Status Engine pipeline:
      Confluence (Milestones + FR + extras) + JIRA snapshot + past weekly
      reports → Prompt 3 v2 → LLM → schema-validated JSON → DB.

    On success: persists ProjectStatus (upsert) + appends ProjectStatusHistory
    if health/schedule/completion changed since the last compute. Always
    appends an AIComputeLog row regardless of success.

    The same engine runs on the daily scheduler (Step 9) once that's wired.
    """
    from app.engines.status import run_status_compute

    result = run_status_compute(project_code)

    if not result.success:
        click.echo(f"[FAIL] Status compute failed: {result.error}", err=True)
        if result.issues:
            click.echo("Validation issues:")
            for i in result.issues:
                click.echo(f"  - {i}")
        if result.raw_response:
            click.echo("Raw response (first 500 chars):")
            click.echo(result.raw_response[:500])
        sys.exit(1)

    click.echo("[OK] Status compute succeeded.")
    click.echo(f"  LLM mode:        {result.llm_mode}")
    click.echo(f"  Duration:        {result.duration_seconds}s")
    click.echo(
        f"  Tokens:          {result.prompt_tokens} prompt + "
        f"{result.completion_tokens} completion"
    )
    p = result.parsed or {}
    click.echo(f"  Overall health:  {p.get('overall_health')}")
    click.echo(f"  Schedule:        {p.get('schedule_status')}")
    click.echo(f"  Completion %:    {p.get('completion_pct')}")
    click.echo(f"  Confidence:      {p.get('confidence')}")
    click.echo(f"  Milestones:      {len(p.get('milestones') or [])}")
    if result.changed is True:
        click.echo("  Changed since last compute? YES — history row appended.")
    elif result.changed is False:
        click.echo("  Changed since last compute? no — current row refreshed only.")
    else:
        click.echo("  Changed since last compute? (n/a — first compute for this project).")


@cli.command("whoami-jira")
def whoami_jira():
    """Verify JIRA token is valid by calling /rest/api/2/myself."""
    from app.clients import get_jira_client
    try:
        me = get_jira_client().whoami()
    except Exception as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)
    click.echo(f"[OK] Authenticated as: {me.get('displayName') or me.get('name')} "
               f"({me.get('accountId') or me.get('key') or me.get('name')})")


@cli.command("whoami-confluence")
def whoami_confluence():
    """Verify Confluence token works by fetching one content item.

    Uses /rest/api/content (not /user/current or /space) because corporate
    Confluence DC instances commonly apply aggressive per-endpoint rate-limit
    overrides to /user/* and /space (anti-enumeration), while leaving /content
    permissive. This mirrors the WR-Project POC's verified pattern.
    """
    from app.clients import get_confluence_client
    try:
        result = get_confluence_client().whoami()
    except Exception as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)
    visible = result.get("content_visible", 0)
    first = result.get("first_page") or {}
    if visible:
        click.echo(f"[OK] Token works. {visible}+ content item(s) visible. "
                   f"First: {first.get('id', '?')} — {first.get('title', '?')!r} "
                   f"({first.get('type', '?')})")
    else:
        click.echo("[OK] Token works (no content visible to this account).")


@cli.command("jira-search")
@click.argument("project_key")
@click.option("--days", default=7, type=int, help="Issues updated in last N days (default: 7)")
@click.option("--issue-types", default=None,
              help="Comma-separated types to filter, e.g. 'Task,Story,Bug'")
@click.option("--show", default=50, type=int, help="How many to print (default: 50; use --all)")
@click.option("--all", "show_all", is_flag=True, help="Print all fetched issues regardless of --show")
def jira_search(project_key, days, issue_types, show, show_all):
    """Run the recent-issues JQL search for a project. Verifies the JIRA path
    we'll use for the Aggregation Engine."""
    from datetime import datetime, timedelta
    from app.clients import get_jira_client

    types = [t.strip() for t in issue_types.split(",")] if issue_types else None
    since = datetime.utcnow() - timedelta(days=days)

    client = get_jira_client()
    try:
        issues = client.search_issues_in_project(project_key, since, types)
    except Exception as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)

    click.echo(f"JQL: {getattr(client, '_last_jql', '<unknown>')}")
    click.echo(f"[OK] {len(issues)} issue(s) returned in last {days} day(s) in {project_key}"
               + (f" (filtered to {types})" if types else ""))
    limit = len(issues) if show_all else show
    for i in issues[:limit]:
        f = i.get("fields", {})
        itype = (f.get("issuetype") or {}).get("name", "?")
        status = (f.get("status") or {}).get("name", "?")
        assignee_obj = f.get("assignee") or {}
        assignee = assignee_obj.get("displayName", "-")
        summary = f.get("summary", "")
        click.echo(f"  {i['key']:14s} {itype:10s} [{status:14s}] "
                   f"assignee={assignee[:20]:20s} — {summary[:60]}")
    if len(issues) > limit:
        click.echo(f"  ... ({len(issues) - limit} more not shown — pass --all to see them)")


@cli.command("jira-snapshot")
@click.argument("project_key")
@click.option("--issue-types", default=None,
              help="Comma-separated types to filter, e.g. 'Task,Story,Bug'")
def jira_snapshot(project_key, issue_types):
    """Compute the project snapshot used by the Status Engine (Prompt 3)."""
    from app.clients import get_jira_client

    types = [t.strip() for t in issue_types.split(",")] if issue_types else None

    client = get_jira_client()
    try:
        snap = client.get_project_snapshot(project_key, types)
    except Exception as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)

    click.echo(f"[OK] Snapshot of {project_key} as of {snap.snapshot_at.isoformat()}")
    click.echo(f"Total tasks: {snap.total_tasks}")
    if snap.by_status:
        click.echo("By status:")
        for status, count in sorted(snap.by_status.items(), key=lambda x: -x[1]):
            click.echo(f"  {status:24s} {count}")
    click.echo(f"Overdue (past due-date, not Done): {snap.overdue_count}")
    click.echo(f"Stale (no update in 14d, not Done): {snap.stale_count}")
    click.echo(f"Recently active (last 14d): {len(snap.recent_activity)} task(s)")
    for r in snap.recent_activity[:5]:
        click.echo(f"  {r['id']:14s} [{r['status']:14s}] last={r['last_activity'][:10]} — {r['title'][:60]}")


@cli.command("jira-task")
@click.argument("issue_key")
@click.option("--show", default=5, type=int, help="How many comments/worklogs to print")
def jira_task(issue_key, show):
    """Fetch comments + worklogs for a single JIRA issue. Useful for verifying
    the data shape that the Aggregation prompt will see."""
    from app.clients import get_jira_client
    from app.clients.jira_client import _adf_to_text

    client = get_jira_client()
    try:
        comments = client.get_comments(issue_key)
        worklogs = client.get_worklogs(issue_key)
    except Exception as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)

    click.echo(f"[OK] {issue_key}: {len(comments)} comment(s), {len(worklogs)} worklog(s)")
    click.echo("Comments:")
    for c in comments[:show]:
        author = (c.get("author") or {}).get("displayName", "?")
        ts = (c.get("created", "") or "")[:19]
        body = c.get("body")
        text = _adf_to_text(body) if isinstance(body, dict) else str(body or "")
        text = text.replace("\n", " ").strip()
        click.echo(f"  [{ts}] {author}: {text[:200]}{'…' if len(text) > 200 else ''}")
    click.echo("Worklogs:")
    for w in worklogs[:show]:
        author = (w.get("author") or {}).get("displayName", "?")
        ts = ((w.get("started") or w.get("created") or "")[:19])
        time_spent = w.get("timeSpent", "")
        comment = w.get("comment")
        text = _adf_to_text(comment) if isinstance(comment, dict) else str(comment or "")
        text = text.replace("\n", " ").strip()
        click.echo(f"  [{ts}] {author} {time_spent:>8s}: {text[:200]}{'…' if len(text) > 200 else ''}")


@cli.command("jira-activity")
@click.argument("project_key")
@click.option("--week-of", default=None,
              help="Monday of the week (YYYY-MM-DD). Defaults to current week.")
@click.option("--engineers-file", default=None,
              help="Path to engineer mapping JSON. Defaults to config.engineers.mapping_file.")
@click.option("--issue-types", default=None,
              help="Comma-separated types to filter (overrides config). Empty = no filter.")
def jira_activity(project_key, week_of, engineers_file, issue_types):
    """Run collect_engineer_activity — the input to the Aggregation prompt.

    Reads engineer mapping JSON, filters to those assigned to <project_key>,
    pulls comments + worklogs + status-changes for the chosen week, prints
    the grouping and any unmapped JIRA authors observed.
    """
    import json
    from datetime import datetime
    from app.clients import get_jira_client
    from app.config import get_config
    from app.utils.dates import week_of as compute_week_of

    cfg = get_config()
    mapping_path = engineers_file or cfg.engineers.mapping_file
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
    except FileNotFoundError:
        click.echo(f"[FAIL] mapping file not found: {mapping_path}", err=True)
        sys.exit(1)

    # Match case-insensitively + whitespace-trimmed — JIRA project keys are
    # conventionally uppercase but people type them with various casing on CLI.
    pk_norm = project_key.strip().lower()
    assigned_ids = set()
    for asn in mapping.get("assignments", []):
        for p in asn.get("projects", []):
            if str(p).strip().lower() == pk_norm:
                assigned_ids.add(asn["knox_id"])
                break

    # Also case-insensitive match on knox_id when filtering engineers list
    assigned_ids_norm = {x.strip().lower() for x in assigned_ids}
    engineers = [
        {"name": e["name"], "knox_id": e["knox_id"]}
        for e in mapping.get("engineers", [])
        if e["knox_id"].strip().lower() in assigned_ids_norm
    ]

    if not engineers:
        click.echo(f"[WARN] No engineers in {mapping_path} are assigned to {project_key}.")
        click.echo("       Either update the mapping JSON, or pass --engineers-file <path>.")
        click.echo("       Continuing with empty engineers list — every JIRA author will appear")
        click.echo("       as 'unmapped'. That's fine for a connectivity test.")

    # Resolve issue types: CLI > project config > none
    if issue_types is not None:
        types = [t.strip() for t in issue_types.split(",") if t.strip()] or None
    else:
        types = None
        for p in cfg.projects:
            if p.code == project_key or p.jira_project_key == project_key:
                types = p.issue_types
                break

    # Compute the week
    if week_of:
        week_date = datetime.strptime(week_of, "%Y-%m-%d").date()
    else:
        week_date = compute_week_of()

    click.echo(f"Project: {project_key}")
    click.echo(f"Week of: {week_date}")
    click.echo(f"Engineers ({len(engineers)}): {[e['name'] for e in engineers]}")
    click.echo(f"Issue types: {types or '(no filter)'}")
    click.echo("")

    client = get_jira_client()
    try:
        activity = client.collect_engineer_activity(project_key, week_date, engineers, types)
    except Exception as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)

    total_records = sum(len(r) for r in activity.by_engineer.values())
    click.echo(f"[OK] {total_records} activity record(s) collected for "
               f"{len(activity.by_engineer)} engineer(s) on {project_key} for week {week_date}")

    for knox_id, recs in activity.by_engineer.items():
        click.echo(f"  {knox_id} — {len(recs)} record(s)")
        for r in recs[:3]:
            ts = (r.timestamp or "")[:10]
            detail = (r.detail or "").replace("\n", " ")
            click.echo(f"    [{r.activity_kind:14s}] {r.task_id:14s} {ts}  "
                       f"{detail[:80]}{'…' if len(detail) > 80 else ''}")
        if len(recs) > 3:
            click.echo(f"    … and {len(recs) - 3} more")

    if activity.unmapped_authors:
        click.echo("")
        click.echo(f"[WARN] {len(activity.unmapped_authors)} unmapped JIRA author(s) "
                   "observed (not in engineer mapping):")
        click.echo(f"  Lookup tables we matched against (after normalisation):")
        click.echo(f"    knox_id keys: {activity.lookup_keys_knox}")
        click.echo(f"    name keys:    {activity.lookup_keys_name}")
        click.echo("")
        for u in activity.unmapped_authors[:10]:
            click.echo(f"  - {u.display_name!r} (id={u.user_id!r})")
            for line in u.lookup_attempts:
                click.echo(f"      tried: {line}")
        if len(activity.unmapped_authors) > 10:
            click.echo(f"    … and {len(activity.unmapped_authors) - 10} more")


@cli.command("show-mapping")
@click.argument("project_key", required=False)
@click.option("--engineers-file", default=None,
              help="Path to engineer mapping JSON. Defaults to config.engineers.mapping_file.")
def show_mapping(project_key, engineers_file):
    """Show the parsed engineer mapping. Diagnostic for 'no engineers assigned' issues.

    Without arguments: lists all engineers and their project assignments.
    With <project_key>: shows which engineers are assigned to that project
    (using the same case-insensitive match logic the real pipeline uses).
    """
    import json
    from app.config import get_config

    cfg = get_config()
    mapping_path = engineers_file or cfg.engineers.mapping_file
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
    except FileNotFoundError:
        click.echo(f"[FAIL] mapping file not found: {mapping_path}", err=True)
        sys.exit(1)
    except json.JSONDecodeError as e:
        click.echo(f"[FAIL] mapping file is invalid JSON: {e}", err=True)
        sys.exit(1)

    click.echo(f"Mapping file: {mapping_path}")
    click.echo(f"Top-level keys: {list(mapping.keys())}")
    click.echo("")

    engineers_list = mapping.get("engineers", [])
    assignments_list = mapping.get("assignments", [])

    click.echo(f"Engineers loaded ({len(engineers_list)}):")
    for e in engineers_list:
        keys = list(e.keys())
        name = e.get("name", "?")
        knox = e.get("knox_id", "?")
        extra = "" if set(keys) == {"name", "knox_id"} else f"  [extra keys: {keys}]"
        click.echo(f"  name={name!r:30s} knox_id={knox!r}{extra}")

    click.echo("")
    click.echo(f"Assignments loaded ({len(assignments_list)}):")
    for asn in assignments_list:
        keys = list(asn.keys())
        knox = asn.get("knox_id", "?")
        projects = asn.get("projects", [])
        extra = "" if set(keys) == {"knox_id", "projects"} else f"  [extra keys: {keys}]"
        click.echo(f"  knox_id={knox!r:25s} projects={projects}{extra}")

    if project_key:
        click.echo("")
        click.echo(f"=== Engineers matching project_key={project_key!r} (case-insensitive) ===")
        pk_norm = project_key.strip().lower()
        matched_knox = set()
        for asn in assignments_list:
            for p in asn.get("projects", []):
                if str(p).strip().lower() == pk_norm:
                    matched_knox.add(asn["knox_id"])
                    break
        if not matched_knox:
            click.echo(f"  (none)")
            click.echo(f"")
            click.echo(f"  Tip: check that '{project_key}' (or any case-insensitive variant)")
            click.echo(f"       appears literally inside one of the projects[] arrays above.")
        else:
            matched_norm = {k.strip().lower() for k in matched_knox}
            for e in engineers_list:
                if e.get("knox_id", "").strip().lower() in matched_norm:
                    click.echo(f"  - {e.get('name')} ({e.get('knox_id')})")


@cli.command("show-last-external-calls")
@click.option("--source", default=None, type=click.Choice(["jira", "confluence"]),
              help="Filter to one source")
@click.option("--lines", default=5, type=int,
              help="Print the last N entries (default 5)")
@click.option("--errors-only", is_flag=True,
              help="Only print entries that recorded an error")
def show_last_external_calls(source, lines, errors_only):
    """Print the most recent N entries from logs/external_calls.jsonl.

    Each entry is one JIRA or Confluence HTTP call: method, path, query
    params (including JQL for searches), status, duration, and a result
    summary keyed off the response shape. Use this to debug 'why didn't
    issue X show up?' / 'what JQL did we run?' / 'how slow are calls?'.

    The companion file `logs/system.jsonl` carries higher-level engine
    events (collect_engineer_activity start/done, retry warnings on 429)
    — use this command when you need EVERY individual HTTP call.
    """
    import json as _json
    from pathlib import Path
    from app.config import get_config

    cfg = get_config()
    log_dir = Path(cfg.logging.directory)
    if not log_dir.is_absolute():
        log_dir = Path(__file__).resolve().parent / log_dir
    path = log_dir / "external_calls.jsonl"

    if not cfg.logging.log_full_external_calls:
        click.echo("[INFO] config.logging.log_full_external_calls = false — "
                   "no new JIRA/Confluence calls are being logged here.")
        click.echo("       The high-level summaries + retry/error logs in "
                   "logs/system.jsonl continue.")
        click.echo("       To re-enable, set log_full_external_calls: true in config.json.")
        click.echo("")

    if not path.exists():
        click.echo(f"[INFO] {path} does not exist yet.")
        if cfg.logging.log_full_external_calls:
            click.echo("       No external calls have been logged. Run a JIRA / Confluence command:")
            click.echo("         python manage.py whoami-jira")
            click.echo("         python manage.py whoami-confluence")
            click.echo("         python manage.py jira-search <PROJ>")
            click.echo("         python manage.py run-aggregation <code>")
        return

    entries: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if source and e.get("source") != source:
                continue
            if errors_only and not e.get("error"):
                continue
            entries.append(e)

    if not entries:
        click.echo(f"[INFO] No matching entries in {path}.")
        return

    last_n = entries[-lines:]
    for i, e in enumerate(last_n, 1):
        click.echo(f"========== Entry {i} of {len(last_n)} ==========")
        click.echo(f"ts:         {e.get('ts')}")
        click.echo(f"source:     {e.get('source')}")
        click.echo(f"method:     {e.get('method')}")
        click.echo(f"path:       {e.get('path')}")
        params = e.get("query_params") or {}
        if params:
            click.echo("query_params:")
            for k, v in params.items():
                # JQL is the most useful field — surface it on its own line
                click.echo(f"  {k}: {v}")
        click.echo(f"status:     {e.get('status')}")
        click.echo(f"duration:   {e.get('duration_seconds')}s")
        if e.get("error"):
            click.echo(f"error:      {e.get('error')}")
        if e.get("result_summary"):
            click.echo("result_summary:")
            for k, v in (e.get("result_summary") or {}).items():
                click.echo(f"  {k}: {v}")
        click.echo("")


@cli.command("show-last-llm-call")
@click.option("--mode", default=None, type=click.Choice(["ollama", "openai"]),
              help="Filter to one provider")
@click.option("--lines", default=1, type=int,
              help="Print the last N entries (default 1)")
@click.option("--prompt-only", is_flag=True,
              help="Print only the system + user prompt; skip the response")
@click.option("--response-only", is_flag=True,
              help="Print only the response; skip the prompts")
def show_last_llm_call(mode, lines, prompt_only, response_only):
    """Print the most recent N entries from logs/llm_prompts.jsonl.

    Each entry is one full LLM call: system prompt + user prompt + raw
    response, plus mode/model/duration/tokens metadata. Use this to debug
    what was actually sent to the LLM by the most recent engine run.

    The companion file `logs/ai_compute.jsonl` carries a 200-char excerpt
    only — use this command when you need the FULL text.
    """
    import json as _json
    from pathlib import Path
    from app.config import get_config

    cfg = get_config()
    log_dir = Path(cfg.logging.directory)
    if not log_dir.is_absolute():
        log_dir = Path(__file__).resolve().parent / log_dir
    path = log_dir / "llm_prompts.jsonl"

    # Surface the gate state up-front so the user knows whether new calls
    # will continue to populate this log.
    if not cfg.logging.log_full_llm_prompts:
        click.echo("[INFO] config.logging.log_full_llm_prompts = false — "
                   "no new LLM calls are being logged here.")
        click.echo("       The 200-char excerpt in logs/ai_compute.jsonl + "
                   "the AIComputeLog DB table are still kept.")
        click.echo("       To re-enable, set log_full_llm_prompts: true in config.json.")
        click.echo("")

    if not path.exists():
        click.echo(f"[INFO] {path} does not exist yet.")
        if cfg.logging.log_full_llm_prompts:
            click.echo("       No LLM calls have been logged. Run an engine first:")
            click.echo("         python manage.py run-status <code>")
            click.echo("         python manage.py run-aggregation <code>")
            click.echo("         python manage.py llm-ping")
        return

    entries: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if mode and e.get("mode") != mode:
                continue
            entries.append(e)

    if not entries:
        click.echo(f"[INFO] No matching entries in {path}.")
        if mode:
            click.echo(f"       Filter applied: mode={mode!r}. Try without --mode.")
        return

    # Show the rotation backup files too if user asked for more lines than
    # the active file holds — Phase 1 simplification: just print what's in
    # the active file. Backups can be inspected manually if needed.
    last_n = entries[-lines:]
    for i, e in enumerate(last_n, 1):
        click.echo(f"========== Entry {i} of {len(last_n)} ==========")
        click.echo(f"ts:               {e.get('ts')}")
        click.echo(f"mode:             {e.get('mode')}")
        click.echo(f"model:            {e.get('model')}")
        click.echo(f"duration:         {e.get('duration_seconds')}s")
        click.echo(
            f"tokens:           {e.get('prompt_tokens')} prompt + "
            f"{e.get('completion_tokens')} completion"
        )
        click.echo(f"json_output:      {e.get('json_output')}")
        click.echo("")
        if not response_only:
            click.echo("--- System prompt ---")
            click.echo(e.get("system_prompt", "(empty)"))
            click.echo("")
            click.echo("--- User prompt ---")
            click.echo(e.get("user_prompt", "(empty)"))
            click.echo("")
        if not prompt_only:
            click.echo("--- Response ---")
            click.echo(e.get("response", "(empty)"))
            click.echo("")


@cli.command("llm-ping")
@click.option("--prompt", default="Reply with exactly: OK",
              help="User prompt to send (default: 'Reply with exactly: OK')")
@click.option("--system", "system_prompt", default="You are a concise assistant. Answer briefly.",
              help="System prompt to send")
@click.option("--json-output", is_flag=True,
              help="Request JSON-mode output (constrained decode where supported)")
@click.option("--temperature", default=None, type=float,
              help="Override config temperature for this call")
def llm_ping(prompt, system_prompt, json_output, temperature):
    """Send one short completion through the configured LLM provider.

    Verifies that:
      - Ollama is running (or the OpenAI-compatible gateway is reachable)
      - The configured model is loaded / accessible
      - Network + auth headers + timeouts are sane
    Prints duration and token counts so you can spot warm vs cold start.
    """
    from app.llm.base import get_llm_client

    try:
        client = get_llm_client()
    except Exception as e:
        click.echo(f"[FAIL] could not build LLM client: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo(f"Provider: {client.mode}")
    try:
        result = client.complete(
            system_prompt=system_prompt,
            user_prompt=prompt,
            temperature=temperature,
            json_output=json_output,
        )
    except Exception as e:
        click.echo(f"[FAIL] {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo(f"Model:    {result.model}")
    click.echo(f"Duration: {result.duration_seconds}s")
    click.echo(f"Tokens:   {result.prompt_tokens} prompt + {result.completion_tokens} completion")
    click.echo(f"Response ({len(result.text)} chars):")
    click.echo(result.text)


@cli.command("llm-embed")
@click.argument("text")
def llm_embed(text):
    """Embed one piece of text. Verifies the embeddings endpoint works."""
    from app.llm.base import get_llm_client

    try:
        client = get_llm_client()
    except Exception as e:
        click.echo(f"[FAIL] could not build LLM client: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo(f"Provider: {client.mode}")
    try:
        result = client.embed(text)
    except Exception as e:
        click.echo(f"[FAIL] {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo(f"Model:        {result.model}")
    click.echo(f"Vector dim:   {len(result.vector)}")
    if result.vector:
        click.echo(f"First 5 dims: {[round(x, 4) for x in result.vector[:5]]}")


@cli.command("confluence-probe")
@click.option("--path", default="/rest/api/space", help="Endpoint to probe (default: /rest/api/space)")
@click.option("--params", default="limit=1",
              help="Query string (default: 'limit=1'). Pass '' for none.")
def confluence_probe(path, params):
    """Single-shot Confluence diagnostic that bypasses retry/log infrastructure.

    Use when whoami-confluence keeps failing — this makes ONE bare GET, prints
    the full status code + ALL response headers + body excerpt, then interprets
    the result. Eliminates retry as a variable so we can see exactly what the
    server is saying right now.

    No retries. No fail-fast logic. No 10-second pre-delay.
    """
    import requests
    from app.config import get_config

    cfg = get_config().confluence
    if not cfg.base_url or not cfg.token:
        click.echo("[FAIL] confluence.base_url and confluence.token must be set in config.json", err=True)
        sys.exit(1)

    base = cfg.base_url.rstrip("/")
    qs = ("?" + params) if params else ""
    url = f"{base}{path}{qs}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {cfg.token}",
    }
    verify = cfg.ca_bundle if cfg.ca_bundle else cfg.verify_ssl
    if not verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    click.echo(f"GET {url}")
    click.echo(f"verify_ssl={verify!r}  ca_bundle={cfg.ca_bundle!r}")
    click.echo("")

    try:
        r = requests.get(url, headers=headers, verify=verify, timeout=30)
    except Exception as e:
        click.echo(f"[NETWORK] {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo(f"Status: {r.status_code}")
    click.echo("Response headers:")
    for k, v in r.headers.items():
        click.echo(f"  {k}: {v}")
    click.echo("")
    body = r.text or ""
    click.echo(f"Body ({len(body)} chars):")
    click.echo(body[:1000] + ("…" if len(body) > 1000 else ""))
    click.echo("")

    # ---- Interpretation ----
    sc = r.status_code
    if sc == 200:
        click.echo("[OK] 200 — token + endpoint work. If whoami-confluence still fails, "
                   "the issue is in our retry path; please share the failure log line.")
    elif sc == 401:
        click.echo("[AUTH] 401 — token is invalid (revoked, expired, wrong format). "
                   "Re-issue a Personal Access Token from your Confluence profile.")
    elif sc == 403:
        click.echo("[AUTH] 403 — token authenticated but lacks permission for this endpoint. "
                   "Try --path /rest/api/content with --params 'limit=1'.")
    elif sc == 404:
        click.echo("[URL] 404 — base_url is reachable but the path is wrong, or there is "
                   "a reverse-proxy stripping API paths. Check confluence.base_url.")
    elif sc == 429:
        fr = r.headers.get("x-ratelimit-fillrate", "?")
        iv = r.headers.get("x-ratelimit-interval-seconds", "?")
        ra = r.headers.get("Retry-After", "?")
        click.echo(f"[RATE-LIMIT] 429 (fillrate={fr}, interval={iv}s, Retry-After={ra})")
        if fr == "0":
            click.echo("")
            click.echo("  ⚠ fillrate=0 means the bucket is empty AND not refilling automatically.")
            click.echo("    This is server-side: typically a per-token quota or an admin-applied")
            click.echo("    rate-limit override. Code-side retries cannot fix this.")
            click.echo("")
            click.echo("  Action: contact your Confluence admin and ask them to:")
            click.echo("    1. Check whether a rate-limit override is applied to your user/token")
            click.echo("    2. Reset the bucket / remove the override if so")
            click.echo("    Reference: <Confluence base URL>/admin/rate-limiting")
        else:
            click.echo(f"  Bucket IS refilling. Wait ~{iv}s and retry; do not run repeated commands.")
    elif sc in (502, 503, 504):
        click.echo(f"[GATEWAY] {sc} — upstream/proxy issue. Try again in a few minutes.")
    else:
        click.echo(f"[UNEXPECTED] {sc} — read the headers and body above to interpret.")


@cli.command("fetch-confluence-page")
@click.argument("url")
@click.option("--kind", default="milestones",
              type=click.Choice(["milestones", "fr", "extra"]),
              help="Which parser to use (default: milestones). "
                   "Pick the one matching the page's role.")
@click.option("--max-chars", default=None, type=int,
              help="Truncation cap for --kind extra (default: confluence.extra_page_max_chars from config)")
@click.option("--full", "show_full", is_flag=True,
              help="Print full text — no terminal-display truncation. "
                   "Use this to verify your page content was parsed correctly. "
                   "(Note: with --kind extra, content is still truncated to "
                   "extra_page_max_chars at parse time — that is by design for "
                   "the AI's token budget; pass --max-chars to override.)")
def fetch_confluence_page(url, kind, max_chars, show_full):
    """Fetch and parse one Confluence page using the parser matching its role.

    Smoke test for the parsers. Use --kind=milestones for the project's
    Milestones page, --kind=fr for its Functional Requirements page, and
    --kind=extra for any supplementary context page.

    By default the output is truncated for terminal readability — actual
    parsed objects in memory always hold full text, and the AI prompts
    never use the terminal-display truncation. Pass --full to see the
    untruncated content for verification.
    """
    from app.clients import get_confluence_client, ConfluenceClient
    from app.config import get_config

    client = get_confluence_client()
    try:
        page = client.get_page_by_url(url)
    except Exception as e:
        click.echo(f"[FAIL] fetch failed: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    # Display caps. With --full each becomes "no cap"; the values below are
    # only for the terminal — the parsed object holds full text either way.
    overview_cap = None if show_full else 200
    desc_cap = None if show_full else 60
    remark_cap = None if show_full else 40
    fr_cap = None if show_full else 500
    extra_excerpt_cap = None if show_full else 300

    def _trunc(s: str, cap):
        """Truncate `s` to `cap` chars (or return as-is if cap is None)."""
        if cap is None or s is None or len(s) <= cap:
            return s
        return s[:cap] + "..."

    if kind == "milestones":
        parsed = ConfluenceClient.parse_milestones_page(page)
        click.echo(f"Title:    {parsed.title}")
        if parsed.overview:
            click.echo(f"Overview ({len(parsed.overview)} chars):")
            click.echo(f"  {_trunc(parsed.overview, overview_cap)}")
        click.echo(f"Milestones found: {len(parsed.milestones)}")
        for m in parsed.milestones:
            extra_bits = []
            if m.quarter:
                extra_bits.append(f"Q={m.quarter}")
            if m.priority:
                extra_bits.append(f"prio={m.priority}")
            if m.dependency:
                extra_bits.append(f"dep={m.dependency}")
            if m.remark:
                extra_bits.append(f"remark={_trunc(m.remark, remark_cap)}")
            extras = (" [" + ", ".join(extra_bits) + "]") if extra_bits else ""
            click.echo(f"  - {m.name} | {m.planned_date} | status={m.status}"
                       f" | {_trunc(m.description, desc_cap)}{extras}")

    elif kind == "fr":
        parsed = ConfluenceClient.parse_fr_page(page)
        click.echo(f"Title: {parsed.title}")
        if parsed.overview:
            click.echo(f"Overview ({len(parsed.overview)} chars):")
            click.echo(f"  {_trunc(parsed.overview, overview_cap)}")
        click.echo(f"Functional Requirements ({len(parsed.functional_requirements)} chars):")
        click.echo(f"  {_trunc(parsed.functional_requirements, fr_cap)}")

    else:  # extra
        cap = max_chars if max_chars is not None else get_config().confluence.extra_page_max_chars
        parsed = ConfluenceClient.parse_extra_page(page, max_chars=cap)
        click.echo(f"Title:     {parsed.title}")
        click.echo(f"Body:      {len(parsed.body_text)} chars"
                   + (f" (parser-truncated at {cap})" if parsed.truncated else ""))
        click.echo(f"Excerpt ({len(parsed.body_text)} chars):")
        click.echo(f"  {_trunc(parsed.body_text, extra_excerpt_cap)}")

    if parsed.parse_warnings:
        click.echo("Warnings:")
        for w in parsed.parse_warnings:
            click.echo(f"  ! {w}")


if __name__ == "__main__":
    cli()
