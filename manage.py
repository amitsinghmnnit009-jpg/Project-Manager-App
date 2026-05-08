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
    python manage.py show-mapping [project_key]          # Diagnostic: see parsed engineer mapping
    python manage.py fetch-confluence-page <url>                # Parse a Milestones page (default)
    python manage.py fetch-confluence-page <url> --kind fr      # Parse a Functional Requirements page
    python manage.py fetch-confluence-page <url> --kind extra   # Parse an extra context page
    python manage.py fetch-confluence-page <url> --full         # Print full text (no terminal truncation)
    python manage.py confluence-probe                    # Single-shot raw 429-diagnostic probe
    python manage.py llm-ping                            # Sanity check: send one short completion
    python manage.py llm-embed <text>                    # Sanity check: embed one piece of text
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
