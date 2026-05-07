"""CLI for Project-Manager-App management tasks.

Usage:
    python manage.py reset-db           # Drop and recreate the SQLite DB
    python manage.py serve              # Start the FastAPI server
    python manage.py run-aggregation <project_code>   # Manually trigger weekly aggregation
    python manage.py run-status <project_code>        # Manually trigger status compute
    python manage.py whoami-jira        # Verify JIRA token is valid
    python manage.py whoami-confluence  # Verify Confluence token is valid
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
