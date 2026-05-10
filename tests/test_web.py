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
