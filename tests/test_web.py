"""Phase 2 W1 — server-rendered UI smoke tests.

Same fresh_db + client fixture pattern as test_api.py — TestClient against
in-memory SQLite with lifespan stubs so no real scheduler / sync runs.

Coverage in this step is intentionally thin:
- Root redirects to /portfolio
- /portfolio renders 200 (empty + with-data cases)
- /static/ assets are mounted
- PM_UI_ENABLED gate function reads the env var correctly
"""
from __future__ import annotations
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace


# ------------------------------------------------------------------------
# Fixtures (mirror tests/test_api.py — kept local rather than promoted to
# conftest.py because the rest of the suite doesn't need them and the
# stubbing is intentionally narrow.)
# ------------------------------------------------------------------------

@pytest.fixture
def fresh_db(monkeypatch):
    import app.db as db_mod
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    test_session = sessionmaker(
        bind=test_engine, autoflush=True, autocommit=False, future=True,
    )
    monkeypatch.setattr(db_mod, "_engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", test_session)

    from app import models  # noqa: F401
    db_mod.Base.metadata.create_all(bind=test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def client(fresh_db, monkeypatch):
    import app.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "start_scheduler", lambda: None)
    monkeypatch.setattr(sched_mod, "stop_scheduler", lambda: None)

    import app.registry.projects as proj_reg
    monkeypatch.setattr(
        proj_reg, "sync_projects_from_config",
        lambda: {"created_count": 0, "updated_count": 0,
                 "deleted_count": 0, "warnings": []},
    )

    import app.registry.engineers as eng_reg
    monkeypatch.setattr(
        eng_reg, "load_engineer_mapping",
        lambda: SimpleNamespace(
            engineers=[], by_knox={}, by_project={},
            file_path="(test stub)", parse_warnings=[],
        ),
    )

    from fastapi.testclient import TestClient
    from app.api.main import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture
def project_in_db(fresh_db):
    from app.db import session_scope
    from app.models import Project
    with session_scope() as s:
        s.add(Project(
            code="WEBTEST", name="Web Test Project", type="firmware",
            description="UI smoke project",
            owning_tl="TL One", owning_pgm="PGM One",
            jira_project_key="WEBTESTKEY",
            confluence_milestones_url="https://confluence.example.com/m",
            confluence_fr_url="https://confluence.example.com/fr",
            confluence_extra_pages_json=[],
            issue_types_json=["Task", "Bug"],
            chronic_threshold=3,
            holiday_calendar_id="default",
            weekly_cutoff="Mon 13:00",
            week_boundary="monday",
            state="active",
        ))
    return "WEBTEST"


# ------------------------------------------------------------------------
# Root redirect
# ------------------------------------------------------------------------

def test_root_redirects_to_portfolio(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/portfolio"


# ------------------------------------------------------------------------
# /portfolio renders
# ------------------------------------------------------------------------

def test_portfolio_renders_with_empty_db(client):
    r = client.get("/portfolio")
    assert r.status_code == 200
    body = r.text
    # base.html chrome
    assert "Project Manager" in body
    assert "<title>Portfolio" in body
    # Empty-state message from portfolio.html
    assert "No projects in the registry yet" in body


def test_portfolio_lists_seeded_project(client, project_in_db):
    r = client.get("/portfolio")
    assert r.status_code == 200
    body = r.text
    assert "WEBTEST" in body
    assert "Web Test Project" in body
    # Code links into the project detail page (W3 will populate that route)
    assert 'href="/projects/WEBTEST"' in body


def test_portfolio_includes_tailwind_and_htmx_cdn(client):
    r = client.get("/portfolio")
    assert r.status_code == 200
    body = r.text
    assert "cdn.tailwindcss.com" in body
    assert "unpkg.com/htmx.org" in body


# ------------------------------------------------------------------------
# /static/ mount
# ------------------------------------------------------------------------

def test_static_app_css_is_served(client):
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert "Tailwind" in r.text  # the comment in app.css


# ------------------------------------------------------------------------
# PM_UI_ENABLED env gate
# ------------------------------------------------------------------------

def test_ui_enabled_default_true(monkeypatch):
    monkeypatch.delenv("PM_UI_ENABLED", raising=False)
    from app.api.main import _ui_enabled
    assert _ui_enabled() is True


def test_ui_enabled_false_when_env_false(monkeypatch):
    from app.api.main import _ui_enabled
    for value in ("false", "False", "FALSE", "  false  "):
        monkeypatch.setenv("PM_UI_ENABLED", value)
        assert _ui_enabled() is False, f"expected False for {value!r}"


def test_ui_enabled_true_for_other_values(monkeypatch):
    from app.api.main import _ui_enabled
    for value in ("true", "1", "yes", ""):
        monkeypatch.setenv("PM_UI_ENABLED", value)
        assert _ui_enabled() is True, f"expected True for {value!r}"


# ------------------------------------------------------------------------
# Web router is excluded from OpenAPI (no API noise from HTML routes)
# ------------------------------------------------------------------------

def test_html_routes_not_in_openapi(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    # /health and /api/* are still there
    assert "/health" in paths
    # but the HTML routes should NOT pollute the API schema
    assert "/portfolio" not in paths
    assert "/" not in paths


# ========================================================================
# W2 — needs-attention card, status columns, refresh-all
# ========================================================================

def _seed_status(project_id: int, *, health: str, schedule: str = "OnTrack",
                 completion: int = 50, confidence: str = "High",
                 rationale: str = "", computed_at=None):
    from datetime import datetime
    from app.db import session_scope
    from app.models import ProjectStatus
    with session_scope() as s:
        s.add(ProjectStatus(
            project_id=project_id, computed_at=computed_at or datetime.utcnow(),
            overall_health=health, schedule_status=schedule,
            completion_pct=completion, confidence=confidence,
            rationale=rationale, milestones_json=[],
            prompt_version="test", llm_mode_used="test",
        ))


def _seed_project(code: str, name: str = "X"):
    from app.db import session_scope
    from app.models import Project
    with session_scope() as s:
        p = Project(
            code=code, name=name, type="firmware",
            owning_tl="TL", owning_pgm="PGM",
            jira_project_key=code + "KEY",
            confluence_milestones_url="https://x/m",
            confluence_fr_url="https://x/fr",
            confluence_extra_pages_json=[], issue_types_json=["Task"],
            chronic_threshold=3, holiday_calendar_id="default",
            weekly_cutoff="Mon 13:00", week_boundary="monday", state="active",
        )
        s.add(p)
        s.flush()
        return p.id


# ---- needs-attention filter & sort -------------------------------------

def test_portfolio_shows_needs_attention_card_for_red_and_amber(client):
    pid_red = _seed_project("REDONE", "Red One")
    pid_amber = _seed_project("AMBONE", "Amber One")
    pid_green = _seed_project("GRNONE", "Green One")
    _seed_status(pid_red, health="Red", schedule="Slipping", completion=40,
                 rationale="Critical blockers stacking up")
    _seed_status(pid_amber, health="Amber", schedule="AtRisk", completion=60)
    _seed_status(pid_green, health="Green", schedule="OnTrack", completion=85)

    r = client.get("/portfolio")
    assert r.status_code == 200
    body = r.text
    assert "Needs attention" in body
    # red + amber appear in the attention card
    assert "REDONE" in body
    assert "AMBONE" in body
    # green does NOT appear in the attention card section
    # (it does appear in the All-projects table below — so check rationale
    # text is unique to the attention list)
    assert "Critical blockers stacking up" in body


def test_needs_attention_card_hidden_when_no_red_or_amber(client):
    pid = _seed_project("ALLGRN", "All Green")
    _seed_status(pid, health="Green")
    r = client.get("/portfolio")
    assert r.status_code == 200
    assert "Needs attention" not in r.text


def test_attention_rows_orders_red_before_amber(client):
    """Helper-level test: _attention_rows() sorts Red before Amber."""
    pid_a = _seed_project("AAA")
    pid_r = _seed_project("RRR")
    _seed_status(pid_a, health="Amber")
    _seed_status(pid_r, health="Red")

    from app.web.router import _load_portfolio_rows, _attention_rows
    rows = _load_portfolio_rows()
    out = _attention_rows(rows)
    assert [r["project"]["code"] for r in out] == ["RRR", "AAA"]


# ---- stale-data badge --------------------------------------------------

def test_stale_badge_appears_for_old_compute(client):
    from datetime import datetime, timedelta
    pid = _seed_project("OLDONE", "Old One")
    _seed_status(pid, health="Green",
                 computed_at=datetime.utcnow() - timedelta(days=15))
    r = client.get("/portfolio")
    assert r.status_code == 200
    assert "stale" in r.text.lower()


def test_stale_badge_absent_for_fresh_compute(client):
    from datetime import datetime, timedelta
    pid = _seed_project("FRESH", "Fresh One")
    _seed_status(pid, health="Green",
                 computed_at=datetime.utcnow() - timedelta(days=2))
    r = client.get("/portfolio")
    assert r.status_code == 200
    # The "stale" badge text shouldn't appear; "stale" might appear elsewhere
    # but we check the specific badge title attribute used by the macro.
    assert 'title="Last computed more than' not in r.text


def test_is_stale_helper():
    from datetime import datetime, timedelta, timezone
    from app.web.router import _is_stale
    assert _is_stale(None) is False
    assert _is_stale(datetime.utcnow() - timedelta(days=2)) is False
    assert _is_stale(datetime.utcnow() - timedelta(days=11)) is True
    # Aware datetime path
    assert _is_stale(
        datetime.now(timezone.utc) - timedelta(days=11)
    ) is True


# ---- "never run" rendering ---------------------------------------------

def test_project_with_no_status_shows_never_run_in_table(client, project_in_db):
    r = client.get("/portfolio")
    assert r.status_code == 200
    body = r.text
    assert "WEBTEST" in body
    assert "never run" in body
    # And does NOT appear in needs-attention
    assert "Needs attention" not in body


# ---- refresh-all endpoint ----------------------------------------------

def test_refresh_all_calls_status_compute_for_each_project(client, monkeypatch):
    _seed_project("AAA", "A"); _seed_project("BBB", "B"); _seed_project("CCC", "C")

    called: list[str] = []
    def fake_compute(code: str):
        called.append(code)
        from types import SimpleNamespace
        return SimpleNamespace(success=True, parsed={}, changed=False,
                               duration_seconds=0.01, issues=[])
    import app.web.router as web_router_mod
    monkeypatch.setattr(
        "app.engines.status.run_status_compute", fake_compute,
    )

    r = client.post("/portfolio/refresh-all")
    assert r.status_code == 204
    assert r.headers.get("hx-refresh") == "true"
    assert sorted(called) == ["AAA", "BBB", "CCC"]


def test_refresh_all_swallows_per_project_failures(client, monkeypatch):
    _seed_project("AAA"); _seed_project("BBB")

    def fake_compute(code: str):
        if code == "AAA":
            raise RuntimeError("LLM down")
        from types import SimpleNamespace
        return SimpleNamespace(success=True, parsed={}, changed=False,
                               duration_seconds=0.01, issues=[])
    monkeypatch.setattr("app.engines.status.run_status_compute", fake_compute)

    r = client.post("/portfolio/refresh-all")
    # Should still succeed overall — one bad project doesn't kill the loop
    assert r.status_code == 204
    assert r.headers.get("hx-refresh") == "true"


# ---- Refresh button is rendered when projects exist --------------------

def test_refresh_button_present_when_projects_exist(client, project_in_db):
    r = client.get("/portfolio")
    assert r.status_code == 200
    assert 'hx-post="/portfolio/refresh-all"' in r.text
    assert "Refresh all" in r.text


def test_refresh_button_absent_when_no_projects(client):
    r = client.get("/portfolio")
    assert r.status_code == 200
    assert 'hx-post="/portfolio/refresh-all"' not in r.text


# ========================================================================
# W3 — Project detail page
# ========================================================================

def _seed_status_history(project_id: int, points: list[tuple[str, int]]):
    """points = list of (computed_at_iso, completion_pct) tuples, oldest first."""
    from datetime import datetime
    from app.db import session_scope
    from app.models import ProjectStatusHistory
    with session_scope() as s:
        for ts_iso, pct in points:
            s.add(ProjectStatusHistory(
                project_id=project_id,
                computed_at=datetime.fromisoformat(ts_iso),
                prior_health="Amber", new_health="Green",
                prior_schedule="AtRisk", new_schedule="OnTrack",
                prior_completion_pct=max(0, pct - 5), new_completion_pct=pct,
                rationale="test", prompt_version="test",
            ))


# ---- Route + 404 -------------------------------------------------------

def test_project_detail_renders_with_code_and_name(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}")
    assert r.status_code == 200
    body = r.text
    assert "WEBTEST" in body
    assert "Web Test Project" in body
    # Header chrome present
    assert "← Portfolio" in body or "Portfolio" in body


def test_project_detail_404_for_unknown_code(client):
    r = client.get("/projects/DOES_NOT_EXIST")
    assert r.status_code == 404


# ---- Status card -------------------------------------------------------

def test_project_detail_renders_status_card_when_status_exists(client, project_in_db):
    pid = _seed_project("WITHSTAT", "With Status")
    _seed_status(pid, health="Green", schedule="OnTrack", completion=72,
                 confidence="High", rationale="All on track for the milestone.")
    r = client.get("/projects/WITHSTAT")
    assert r.status_code == 200
    body = r.text
    assert "All on track for the milestone." in body
    assert "72%" in body
    assert "Green" in body


def test_project_detail_shows_not_yet_computed_when_no_status(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}")
    assert r.status_code == 200
    assert "hasn't been computed yet" in r.text


def test_project_detail_renders_milestones(client):
    pid = _seed_project("WITHMS", "With Milestones")
    from datetime import datetime
    from app.db import session_scope
    from app.models import ProjectStatus
    with session_scope() as s:
        s.add(ProjectStatus(
            project_id=pid, computed_at=datetime.utcnow(),
            overall_health="Amber", schedule_status="AtRisk",
            completion_pct=55, confidence="Medium",
            rationale="Two milestones in flight.",
            milestones_json=[
                {"name": "M1 Design freeze", "planned_date": "2026-04-15",
                 "tl_declared_status": "Done", "ai_verification": "Verified",
                 "evidence": "..."},
                {"name": "M2 Build complete", "planned_date": "2026-05-20",
                 "tl_declared_status": "In-progress", "ai_verification": "NotApplicable",
                 "evidence": "..."},
            ],
            prompt_version="test", llm_mode_used="test",
        ))
    r = client.get("/projects/WITHMS")
    assert r.status_code == 200
    body = r.text
    assert "M1 Design freeze" in body
    assert "M2 Build complete" in body
    assert "AI: Verified" in body


# ---- Sparkline ---------------------------------------------------------

def test_sparkline_renders_svg_when_history_exists(client):
    pid = _seed_project("WITHHIST", "With History")
    _seed_status_history(pid, [
        ("2026-04-01T10:00:00", 30),
        ("2026-04-08T10:00:00", 45),
        ("2026-04-15T10:00:00", 60),
        ("2026-04-22T10:00:00", 72),
    ])
    r = client.get("/projects/WITHHIST")
    assert r.status_code == 200
    body = r.text
    # Inline SVG present
    assert "<svg" in body
    assert "polyline" in body
    # "latest" caption shows the most recent value
    assert "latest 72%" in body


def test_sparkline_shows_placeholder_when_no_history(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}")
    assert r.status_code == 200
    assert "no completion-% history yet" in r.text


def test_load_status_history_returns_oldest_first(client):
    """Helper: history rows must be oldest-first for left→right sparkline."""
    pid = _seed_project("ORDERTEST")
    _seed_status_history(pid, [
        ("2026-04-01T10:00:00", 10),
        ("2026-04-08T10:00:00", 20),
        ("2026-04-15T10:00:00", 30),
    ])
    from app.web.router import _load_status_history
    rows = _load_status_history(pid, limit=8)
    pcts = [r["new_completion_pct"] for r in rows]
    assert pcts == [10, 20, 30]


# ---- JIRA activity fragment -------------------------------------------

def test_jira_activity_fragment_renders_rows(client, project_in_db, monkeypatch):
    from types import SimpleNamespace

    class StubJira:
        base = "https://jira.test.local"
        def get_project_snapshot(self, key, types=None):
            return SimpleNamespace(
                recent_activity=[
                    {"id": "WEBTESTKEY-101", "title": "Implement X",
                     "status": "In Progress",
                     "last_activity": "2026-05-09T10:00:00+05:30"},
                    {"id": "WEBTESTKEY-102", "title": "Fix Y",
                     "status": "Done",
                     "last_activity": "2026-05-08T10:00:00+05:30"},
                ],
            )
    import app.clients
    monkeypatch.setattr(app.clients, "get_jira_client", lambda: StubJira())

    r = client.get(f"/projects/{project_in_db}/jira-activity")
    assert r.status_code == 200
    body = r.text
    assert "WEBTESTKEY-101" in body
    assert "Implement X" in body
    assert "WEBTESTKEY-102" in body
    assert 'href="https://jira.test.local/browse/WEBTESTKEY-101"' in body


def test_jira_activity_fragment_handles_jira_failure_gracefully(
        client, project_in_db, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("JIRA unreachable")
    import app.clients
    class BoomJira:
        base = "https://jira.test.local"
        def get_project_snapshot(self, key, types=None):
            raise RuntimeError("JIRA unreachable")
    monkeypatch.setattr(app.clients, "get_jira_client", lambda: BoomJira())

    r = client.get(f"/projects/{project_in_db}/jira-activity")
    # Endpoint returns 200 — error rendered inline so the page survives
    assert r.status_code == 200
    body = r.text
    assert "Couldn't load JIRA activity" in body
    assert "JIRA unreachable" in body


def test_jira_activity_fragment_404_for_unknown_project(client):
    r = client.get("/projects/DOES_NOT_EXIST/jira-activity")
    assert r.status_code == 404


def test_jira_activity_fragment_empty_state(client, project_in_db, monkeypatch):
    from types import SimpleNamespace

    class StubJira:
        base = "https://jira.test.local"
        def get_project_snapshot(self, key, types=None):
            return SimpleNamespace(recent_activity=[])
    import app.clients
    monkeypatch.setattr(app.clients, "get_jira_client", lambda: StubJira())

    r = client.get(f"/projects/{project_in_db}/jira-activity")
    assert r.status_code == 200
    assert "No JIRA activity in the last 14 days" in r.text


# ---- Per-project refresh-status endpoint -------------------------------

def test_refresh_status_calls_run_status_compute(client, project_in_db, monkeypatch):
    called: list[str] = []
    def fake_compute(code: str):
        called.append(code)
        from types import SimpleNamespace
        return SimpleNamespace(success=True, parsed={}, changed=False,
                               duration_seconds=0.01, issues=[])
    monkeypatch.setattr("app.engines.status.run_status_compute", fake_compute)

    r = client.post(f"/projects/{project_in_db}/refresh-status")
    assert r.status_code == 204
    assert r.headers.get("hx-refresh") == "true"
    assert called == [project_in_db]


def test_refresh_status_404_for_unknown_project(client):
    r = client.post("/projects/DOES_NOT_EXIST/refresh-status")
    assert r.status_code == 404


def test_refresh_status_swallows_engine_failure(client, project_in_db, monkeypatch):
    def boom(code: str):
        raise RuntimeError("LLM down")
    monkeypatch.setattr("app.engines.status.run_status_compute", boom)
    r = client.post(f"/projects/{project_in_db}/refresh-status")
    # Still 204 — failure logged, page just reloads with stale data
    assert r.status_code == 204
    assert r.headers.get("hx-refresh") == "true"


# ---- Project detail page wires the lazy JIRA loader -------------------

def test_project_detail_page_includes_lazy_jira_loader(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}")
    assert r.status_code == 200
    body = r.text
    assert f'hx-get="/projects/{project_in_db}/jira-activity"' in body
    assert 'hx-trigger="load"' in body


# ========================================================================
# W4 — Weekly report card + past-reports list + triggers
# ========================================================================

def _seed_weekly_report(project_id: int, week_of, *, content="## hello\nbody",
                        regenerated_count: int = 0,
                        prompt_v_agg: str = "agg-v1",
                        prompt_v_hl: str = "hl-v1"):
    from datetime import datetime, date as _date
    from app.db import session_scope
    from app.models import WeeklyReport
    if isinstance(week_of, str):
        week_of = _date.fromisoformat(week_of)
    with session_scope() as s:
        s.add(WeeklyReport(
            project_id=project_id, week_of=week_of,
            generated_at=datetime.utcnow(),
            regenerated_count=regenerated_count,
            content_markdown=content,
            prompt_version_aggregation=prompt_v_agg,
            prompt_version_highlights=prompt_v_hl,
            llm_mode_used="ollama",
        ))


# ---- markdown filter ---------------------------------------------------

def test_markdown_filter_renders_html():
    from app.web.router import _md_to_html
    html = str(_md_to_html("# Heading\n\n- one\n- two"))
    assert "<h1>Heading</h1>" in html
    assert "<ul>" in html and "<li>one</li>" in html


def test_markdown_filter_handles_empty():
    from app.web.router import _md_to_html
    assert str(_md_to_html("")) == ""
    assert str(_md_to_html(None)) == ""


def test_markdown_filter_supports_tables_and_fenced_code():
    from app.web.router import _md_to_html
    md = "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```"
    html = str(_md_to_html(md))
    assert "<table>" in html
    assert "<pre>" in html
    assert "<code>" in html


def test_markdown_filter_strips_script_tags():
    """Per FR §5.3 — the script-stripper kills <script> blocks."""
    from app.web.router import _md_to_html
    html = str(_md_to_html("Hello <script>alert(1)</script> world"))
    assert "<script>" not in html.lower()
    assert "alert(1)" not in html
    # Surrounding text survives
    assert "Hello" in html and "world" in html


def test_markdown_filter_strips_inline_event_handlers():
    """Per FR §5.3 — inline on* handlers are removed."""
    from app.web.router import _md_to_html
    html = str(_md_to_html('<a href="x" onclick="alert(1)">link</a>'))
    assert "onclick" not in html.lower()
    assert "alert(1)" not in html


# ---- Project detail loads latest report inline ------------------------

def test_project_detail_renders_latest_report_inline(client):
    pid = _seed_project("RPTONE", "Report One")
    _seed_weekly_report(pid, "2026-05-04",
                        content="## What happened\nWe shipped X.")
    r = client.get("/projects/RPTONE")
    assert r.status_code == 200
    body = r.text
    # Markdown rendered to HTML
    assert "<h2>What happened</h2>" in body
    assert "We shipped X." in body
    # Header chrome
    assert "Weekly report — week of" in body
    assert "2026-05-04" in body


def test_project_detail_shows_no_reports_state_when_empty(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}")
    assert r.status_code == 200
    assert "No weekly reports yet" in r.text


# ---- Past reports list -------------------------------------------------

def test_past_reports_list_renders_when_reports_exist(client):
    pid = _seed_project("PASTRPT", "Past Reports")
    _seed_weekly_report(pid, "2026-04-20")
    _seed_weekly_report(pid, "2026-04-27", regenerated_count=2)
    _seed_weekly_report(pid, "2026-05-04")
    r = client.get("/projects/PASTRPT")
    assert r.status_code == 200
    body = r.text
    # All three weeks listed; latest is in the report card, others as links
    assert "2026-04-20" in body
    assert "2026-04-27" in body
    assert "2026-05-04" in body
    # Past-week buttons have htmx attrs swapping the report card
    assert 'hx-get="/projects/PASTRPT/reports/2026-04-20/fragment"' in body
    assert 'hx-target="#report-card"' in body
    # The regen badge for the regen×2 row
    assert "regen ×2" in body


def test_past_reports_list_absent_when_no_reports(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}")
    assert r.status_code == 200
    assert "Past reports" not in r.text


def test_load_past_reports_returns_newest_first(client):
    pid = _seed_project("ORDERRPT")
    _seed_weekly_report(pid, "2026-04-20")
    _seed_weekly_report(pid, "2026-05-04")
    _seed_weekly_report(pid, "2026-04-27")
    from app.web.router import _load_past_reports
    rows = _load_past_reports(pid, limit=20)
    weeks = [r["week_of"].isoformat() for r in rows]
    assert weeks == ["2026-05-04", "2026-04-27", "2026-04-20"]


# ---- Report fragment endpoint -----------------------------------------

def test_report_fragment_returns_rendered_card(client):
    pid = _seed_project("FRAG", "Frag")
    _seed_weekly_report(pid, "2026-04-27", content="## week of 04-27\nbody")
    r = client.get("/projects/FRAG/reports/2026-04-27/fragment")
    assert r.status_code == 200
    body = r.text
    assert 'id="report-card"' in body
    assert "<h2>week of 04-27</h2>" in body
    # Buttons re-target the new week
    assert 'hx-post="/projects/FRAG/reports/2026-04-27/regenerate"' in body
    assert 'hx-post="/projects/FRAG/reports/2026-04-27/highlights/refresh"' in body


def test_report_fragment_404_for_unknown_week(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}/reports/2099-01-05/fragment")
    assert r.status_code == 404


def test_report_fragment_404_for_unknown_project(client):
    r = client.get("/projects/DOES_NOT_EXIST/reports/2026-05-04/fragment")
    assert r.status_code == 404


# ---- Regenerate trigger -----------------------------------------------

def test_regenerate_calls_aggregation_engine_and_returns_fragment(client, monkeypatch):
    pid = _seed_project("REGEN", "Regen")
    _seed_weekly_report(pid, "2026-05-04", content="## old", regenerated_count=0)

    called: list[tuple] = []
    def fake_agg(code: str, *, week_of=None, regenerate=False):
        called.append((code, week_of, regenerate))
        # After "running" we update the report content so the returned
        # fragment shows the new body, like a real run would.
        from app.db import session_scope
        from app.models import WeeklyReport
        from sqlalchemy import select as _sel
        with session_scope() as s:
            row = s.execute(_sel(WeeklyReport).where(
                WeeklyReport.project_id == pid,
                WeeklyReport.week_of == week_of,
            )).scalar_one()
            row.content_markdown = "## regenerated\nfresh body"
            row.regenerated_count = (row.regenerated_count or 0) + 1
        from types import SimpleNamespace
        return SimpleNamespace(success=True, error=None, week_of=week_of,
                               is_regeneration=True, engineer_count=1,
                               activity_records=10, unmapped_authors_count=0,
                               duration_seconds=0.01, content_markdown="## regenerated\nfresh body")
    monkeypatch.setattr("app.engines.aggregation.run_weekly_aggregation", fake_agg)

    r = client.post("/projects/REGEN/reports/2026-05-04/regenerate")
    assert r.status_code == 200
    body = r.text
    assert called == [("REGEN", _date_from("2026-05-04"), True)]
    # Fragment is the swap target
    assert 'id="report-card"' in body
    # Updated body appears in the fragment
    assert "<h2>regenerated</h2>" in body
    assert "fresh body" in body


def test_regenerate_404_for_unknown_project(client):
    r = client.post("/projects/DOES_NOT_EXIST/reports/2026-05-04/regenerate")
    assert r.status_code == 404


def test_regenerate_swallows_engine_failure(client, monkeypatch):
    pid = _seed_project("REGFAIL")
    _seed_weekly_report(pid, "2026-05-04")
    def boom(code: str, *, week_of=None, regenerate=False):
        raise RuntimeError("LLM down")
    monkeypatch.setattr("app.engines.aggregation.run_weekly_aggregation", boom)
    r = client.post("/projects/REGFAIL/reports/2026-05-04/regenerate")
    # Still returns the fragment (with the un-updated report content)
    assert r.status_code == 200
    assert 'id="report-card"' in r.text


# ---- Highlights refresh trigger ---------------------------------------

def test_highlights_refresh_calls_engine_and_returns_fragment(client, monkeypatch):
    pid = _seed_project("HLREF", "HL")
    _seed_weekly_report(pid, "2026-05-04",
                        prompt_v_hl="")  # no highlights yet

    called: list[tuple] = []
    def fake_hl(code: str, *, week_of=None):
        called.append((code, week_of))
        # Mark highlights as generated
        from app.db import session_scope
        from app.models import WeeklyReport
        from sqlalchemy import select as _sel
        with session_scope() as s:
            row = s.execute(_sel(WeeklyReport).where(
                WeeklyReport.project_id == pid,
                WeeklyReport.week_of == week_of,
            )).scalar_one()
            row.prompt_version_highlights = "hl-v2"
        from types import SimpleNamespace
        return SimpleNamespace(success=True, error=None, week_of=week_of,
                               is_first_week=False, last_week_of=None,
                               duration_seconds=0.01, highlights_section="## Highlights\nstuff")
    monkeypatch.setattr("app.engines.highlights.run_highlights", fake_hl)

    r = client.post("/projects/HLREF/reports/2026-05-04/highlights/refresh")
    assert r.status_code == 200
    assert called == [("HLREF", _date_from("2026-05-04"))]
    assert 'id="report-card"' in r.text


def test_highlights_refresh_404_for_unknown_project(client):
    r = client.post("/projects/DOES_NOT_EXIST/reports/2026-05-04/highlights/refresh")
    assert r.status_code == 404


# ---- Helper ------------------------------------------------------------

def _date_from(s: str):
    from datetime import date as _date
    return _date.fromisoformat(s)


# ========================================================================
# W5 — Compare weeks
# ========================================================================

# ---- Highlights extractor ---------------------------------------------

def test_extract_highlights_section_finds_block():
    from app.web.router import _extract_highlights_section
    md = (
        "## What happened\n"
        "stuff\n\n"
        "## Highlights\n"
        "- Closed 5 bugs\n"
        "- Shipped X\n\n"
        "## Risks\n"
        "blocker A\n"
    )
    out = _extract_highlights_section(md)
    assert out is not None
    assert "Closed 5 bugs" in out
    assert "Shipped X" in out
    # Must NOT bleed into next section
    assert "blocker A" not in out
    assert "stuff" not in out


def test_extract_highlights_section_matches_variant_heading():
    from app.web.router import _extract_highlights_section
    md = "## Highlights vs prior week\n- foo\n\n## Next\nbar"
    out = _extract_highlights_section(md)
    assert out is not None
    assert "foo" in out
    assert "bar" not in out


def test_extract_highlights_section_returns_none_when_absent():
    from app.web.router import _extract_highlights_section
    md = "## What happened\nno highlights here"
    assert _extract_highlights_section(md) is None
    assert _extract_highlights_section("") is None
    assert _extract_highlights_section(None) is None


def test_extract_highlights_section_when_last_section():
    """No following ## heading — returns from match to end of string."""
    from app.web.router import _extract_highlights_section
    md = "## Highlights\n- only line"
    out = _extract_highlights_section(md)
    assert out is not None
    assert "only line" in out


# ---- Status-at-week lookup --------------------------------------------

def test_load_status_at_week_picks_most_recent_within_window(client):
    pid = _seed_project("STATWIN")
    _seed_status_history(pid, [
        ("2026-04-15T10:00:00", 30),
        ("2026-04-22T10:00:00", 50),
        ("2026-05-01T10:00:00", 65),
        ("2026-05-08T10:00:00", 75),
    ])
    from app.web.router import _load_status_at_week
    from datetime import date

    # Week of 2026-04-20 covers 04-20..04-26 — latest <= 04-26 is 04-22 (50%)
    s = _load_status_at_week(pid, date(2026, 4, 20))
    assert s is not None
    assert s["new_completion_pct"] == 50

    # Week of 2026-05-04 covers 05-04..05-10 — latest <= 05-10 is 05-08 (75%)
    s = _load_status_at_week(pid, date(2026, 5, 4))
    assert s["new_completion_pct"] == 75

    # Way before any history → None
    assert _load_status_at_week(pid, date(2025, 1, 1)) is None


# ---- weeks query-param parser -----------------------------------------

def test_parse_weeks_param_handles_valid_and_skips_garbage():
    from app.web.router import _parse_weeks_param
    out = _parse_weeks_param("2026-05-04,2026-04-27, ,not-a-date,2026-04-20")
    iso = [d.isoformat() for d in out]
    assert iso == ["2026-05-04", "2026-04-27", "2026-04-20"]


def test_parse_weeks_param_empty_returns_empty():
    from app.web.router import _parse_weeks_param
    assert _parse_weeks_param("") == []
    assert _parse_weeks_param(",,,") == []


# ---- Compare route ----------------------------------------------------

def test_compare_404_for_unknown_project(client):
    r = client.get("/projects/DOES_NOT_EXIST/compare")
    assert r.status_code == 404


def test_compare_empty_state_when_no_reports(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}/compare")
    assert r.status_code == 200
    body = r.text
    assert "No weekly reports yet" in body
    # Picker form should NOT render when there's nothing to pick
    assert "Pick 2" not in body


def test_compare_default_shows_last_2_weeks(client):
    pid = _seed_project("DEFCMP", "Default Compare")
    _seed_weekly_report(pid, "2026-04-20")
    _seed_weekly_report(pid, "2026-04-27")
    _seed_weekly_report(pid, "2026-05-04")
    r = client.get("/projects/DEFCMP/compare")
    assert r.status_code == 200
    body = r.text
    # All three weeks appear in the picker dropdowns
    assert "2026-04-20" in body and "2026-04-27" in body and "2026-05-04" in body
    # Default columns = last 2 weeks (newest), oldest-on-left → 04-27, 05-04.
    # We slice to the columns area (after the grid-template-columns inline
    # style) so the picker dropdowns don't pollute the position check.
    columns_html = body.split("grid-template-columns:")[1]
    pos_27 = columns_html.find("2026-04-27")
    pos_04 = columns_html.find("2026-05-04")
    pos_20 = columns_html.find("2026-04-20")
    assert pos_27 != -1 and pos_04 != -1
    assert pos_27 < pos_04, "older week should appear before newer week"
    # 2026-04-20 should NOT be a column (only in dropdowns above the columns)
    assert pos_20 == -1


def test_compare_renders_explicit_weeks_param(client):
    pid = _seed_project("EXPCMP", "Explicit Compare")
    _seed_weekly_report(pid, "2026-04-20", content="## Highlights\nweek 1 hl")
    _seed_weekly_report(pid, "2026-04-27", content="## Highlights\nweek 2 hl")
    _seed_weekly_report(pid, "2026-05-04", content="## Highlights\nweek 3 hl")
    r = client.get(
        "/projects/EXPCMP/compare?weeks=2026-04-20,2026-05-04"
    )
    assert r.status_code == 200
    body = r.text
    assert "week 1 hl" in body
    assert "week 3 hl" in body
    # Middle week (04-27) is NOT in the columns, only in the picker — make sure
    # its highlights body text doesn't appear
    assert "week 2 hl" not in body


def test_compare_caps_at_max_columns(client, monkeypatch):
    pid = _seed_project("MAXCMP")
    for w in ["2026-04-06", "2026-04-13", "2026-04-20",
              "2026-04-27", "2026-05-04", "2026-05-11"]:
        _seed_weekly_report(pid, w, content=f"## Highlights\nbody {w}")
    # Request 6 — should be capped at 4 (COMPARE_MAX_COLUMNS)
    r = client.get(
        "/projects/MAXCMP/compare?weeks=2026-04-06,2026-04-13,2026-04-20,"
        "2026-04-27,2026-05-04,2026-05-11"
    )
    assert r.status_code == 200
    body = r.text
    # First 4 should appear in column headers/highlights bodies
    assert "body 2026-04-06" in body
    assert "body 2026-04-27" in body
    # 5th and 6th should NOT appear in highlights bodies (only in dropdowns)
    assert "body 2026-05-04" not in body
    assert "body 2026-05-11" not in body


def test_compare_skips_invalid_dates_in_weeks_param(client):
    pid = _seed_project("BADCMP")
    _seed_weekly_report(pid, "2026-05-04", content="## Highlights\nvalid one")
    r = client.get(
        "/projects/BADCMP/compare?weeks=junk,2026-05-04,more-junk"
    )
    assert r.status_code == 200
    assert "valid one" in r.text


def test_compare_includes_status_snapshot_when_history_exists(client):
    pid = _seed_project("STATCMP")
    _seed_weekly_report(pid, "2026-05-04",
                        content="## Highlights\nshipped feature X")
    # History at 2026-05-06 is within the week-of-2026-05-04 window
    _seed_status_history(pid, [
        ("2026-05-06T12:00:00", 88),
    ])
    r = client.get("/projects/STATCMP/compare?weeks=2026-05-04")
    assert r.status_code == 200
    body = r.text
    # Completion % from history shows up in the column
    assert "88%" in body
    # Highlights body present
    assert "shipped feature X" in body


def test_compare_no_status_snapshot_when_no_history(client):
    pid = _seed_project("NOHIST")
    _seed_weekly_report(pid, "2026-05-04",
                        content="## Highlights\nfoo")
    r = client.get("/projects/NOHIST/compare?weeks=2026-05-04")
    assert r.status_code == 200
    assert "no status snapshot for this week" in r.text


def test_compare_picker_form_lists_all_available_weeks(client):
    pid = _seed_project("PICKER")
    weeks = ["2026-04-13", "2026-04-20", "2026-04-27", "2026-05-04"]
    for w in weeks:
        _seed_weekly_report(pid, w)
    r = client.get("/projects/PICKER/compare")
    assert r.status_code == 200
    body = r.text
    # Each week should appear at least 4 times — once per dropdown <option>
    for w in weeks:
        assert body.count(w) >= 4


def test_compare_link_present_on_project_detail_page(client, project_in_db):
    r = client.get(f"/projects/{project_in_db}")
    assert r.status_code == 200
    assert f'href="/projects/{project_in_db}/compare"' in r.text
    assert "Compare weeks" in r.text
