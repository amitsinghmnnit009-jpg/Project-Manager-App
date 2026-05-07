"""CLI for Project-Manager-App management tasks.

Usage:
    python manage.py reset-db                            # Drop and recreate the SQLite DB
    python manage.py serve                               # Start the FastAPI server
    python manage.py whoami-jira                         # Verify JIRA token
    python manage.py whoami-confluence                   # Verify Confluence token
    python manage.py jira-search <project_key>           # Search recent issues in JIRA
    python manage.py jira-snapshot <project_key>         # Project snapshot (counts/overdue/recent)
    python manage.py jira-task <issue_key>               # Comments + worklogs of one task
    python manage.py jira-activity <project_key>         # Engineer-grouped weekly activity
    python manage.py fetch-confluence-page <url>         # Fetch+parse a Confluence project page
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
@click.option("--host", default=None, help="Override host from config.yaml")
@click.option("--port", default=None, type=int, help="Override port from config.yaml")
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


@cli.command("run-aggregation")
@click.argument("project_code")
def run_aggregation(project_code):
    """Manually trigger weekly aggregation for one project."""
    click.echo(f"[stub] Would run aggregation for {project_code}")
    # TODO: wire up to engines.aggregation


@cli.command("run-status")
@click.argument("project_code")
def run_status(project_code):
    """Manually trigger project status computation for one project."""
    click.echo(f"[stub] Would run status compute for {project_code}")
    # TODO: wire up to engines.status


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
    """Verify Confluence token works by listing one space.

    Goes through /rest/api/space (not /user/current) to avoid the
    aggressive per-endpoint rate limits many corporate Confluence DC
    instances apply to user-info endpoints.
    """
    from app.clients import get_confluence_client
    try:
        result = get_confluence_client().whoami()
    except Exception as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)
    spaces_visible = result.get("spaces_visible", 0)
    first = result.get("first_space") or {}
    if spaces_visible:
        click.echo(f"[OK] Token works. {spaces_visible}+ space(s) visible. "
                   f"First: {first.get('key', '?')} ({first.get('name', '?')})")
    else:
        click.echo("[OK] Token works (no spaces visible to this account).")


@cli.command("jira-search")
@click.argument("project_key")
@click.option("--days", default=7, type=int, help="Issues updated in last N days (default: 7)")
@click.option("--issue-types", default=None,
              help="Comma-separated types to filter, e.g. 'Task,Story,Bug'")
@click.option("--show", default=10, type=int, help="How many to print (default: 10)")
def jira_search(project_key, days, issue_types, show):
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

    click.echo(f"[OK] {len(issues)} issue(s) updated in last {days} day(s) in {project_key}"
               + (f" (filtered to {types})" if types else ""))
    for i in issues[:show]:
        f = i.get("fields", {})
        itype = (f.get("issuetype") or {}).get("name", "?")
        status = (f.get("status") or {}).get("name", "?")
        assignee = (f.get("assignee") or {}).get("displayName", "-")
        summary = f.get("summary", "")
        click.echo(f"  {i['key']:14s} {itype:10s} [{status:14s}] "
                   f"assignee={assignee[:20]:20s} — {summary[:60]}")


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

    assigned_ids = set()
    for asn in mapping.get("assignments", []):
        if project_key in asn.get("projects", []):
            assigned_ids.add(asn["knox_id"])

    engineers = [
        {"name": e["name"], "knox_id": e["knox_id"]}
        for e in mapping.get("engineers", [])
        if e["knox_id"] in assigned_ids
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
        for u in activity.unmapped_authors[:10]:
            click.echo(f"    {u.display_name} (id={u.user_id})")
        if len(activity.unmapped_authors) > 10:
            click.echo(f"    … and {len(activity.unmapped_authors) - 10} more")


@cli.command("fetch-confluence-page")
@click.argument("url")
def fetch_confluence_page(url):
    """Fetch and parse a Confluence project page (smoke test for the parser)."""
    from app.clients import get_confluence_client, ConfluenceClient
    client = get_confluence_client()
    try:
        page = client.get_page_by_url(url)
        parsed = ConfluenceClient.parse_project_page(page)
    except Exception as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)

    click.echo(f"Title: {parsed.title}")
    click.echo(f"Overview: {parsed.overview[:200]!r}{'…' if len(parsed.overview) > 200 else ''}")
    click.echo(f"Milestones found: {len(parsed.milestones)}")
    for m in parsed.milestones:
        click.echo(f"  - {m.name} | {m.quarter} | {m.planned_date} | "
                   f"{m.priority} | status={m.status} | dep={m.dependency}")
    click.echo(f"Functional Requirements ({len(parsed.functional_requirements)} chars): "
               f"{parsed.functional_requirements[:200]!r}…")
    if parsed.parse_warnings:
        click.echo("Warnings:")
        for w in parsed.parse_warnings:
            click.echo(f"  ! {w}")


if __name__ == "__main__":
    cli()
