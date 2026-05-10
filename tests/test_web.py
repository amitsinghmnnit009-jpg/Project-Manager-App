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
    assert "TL One" in body


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
