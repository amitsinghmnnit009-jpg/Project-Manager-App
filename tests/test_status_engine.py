"""Tests for app.engines.status — the Status Engine (Step 8).

Mocks at three boundaries:
  - get_jira_client()   → returns a stub with get_project_snapshot()
  - get_confluence_client() → returns a stub with get_page_by_url()
  - get_llm_client()    → returns a stub with mode + complete()

Uses the same fresh in-memory SQLite + StaticPool fixture as
test_registry.py so DB persistence is real but isolated per test.
"""
from __future__ import annotations
import json
import pytest
from datetime import date, datetime
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace
from unittest.mock import patch


# ------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------

@pytest.fixture
def fresh_db(monkeypatch):
    """Per-test in-memory SQLite. Same pattern as tests/test_registry.py."""
    import app.db as db_mod

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    test_session = sessionmaker(
        bind=test_engine, autoflush=True, autocommit=False, future=True
    )

    monkeypatch.setattr(db_mod, "_engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", test_session)

    from app import models  # noqa: F401
    db_mod.Base.metadata.create_all(bind=test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def project_in_db(fresh_db):
    """Insert one project row so registry lookups succeed.

    Returns the project's `code` for tests to pass to run_status_compute.
    """
    from app.db import session_scope
    from app.models import Project

    with session_scope() as s:
        s.add(Project(
            code="TESTPROJ",
            name="Test Project",
            type="general",
            description="",
            jira_project_key="TESTKEY",
            confluence_milestones_url="https://confluence.example.com/m",
            confluence_fr_url="https://confluence.example.com/fr",
            confluence_extra_pages_json=[],
            issue_types_json=["Task"],
            chronic_threshold=3,
            holiday_calendar_id="default",
        ))
    return "TESTPROJ"


def _valid_prompt3_json() -> dict:
    """Build a minimal Prompt 3 response that passes schema validation."""
    return {
        "overall_health": "Amber",
        "schedule_status": "AtRisk",
        "completion_pct": 30,
        "milestones": [
            {
                "name": "M1",
                "planned_date": "2026-06-01",
                "tl_declared_status": "In-progress",
                "ai_verification": "NotApplicable",
                "evidence": "TL declaration accepted as-is",
            }
        ],
        "rationale": "Schedule slipping due to vendor delay; one milestone overdue.",
        "confidence": "Medium",
        "evidence_cited": ["M1"],
    }


def _stub_clients(monkeypatch, *,
                  llm_response_text=None,
                  llm_raises=None,
                  confluence_raises=None,
                  jira_raises=None,
                  llm_mode="ollama"):
    """Patch get_jira_client / get_confluence_client / get_llm_client at the
    locations app.engines.status imports them from."""
    import app.engines.status as status_mod

    # ---- LLM stub ----
    class StubLLM:
        mode = llm_mode
        def complete(self, sys_p, user_p, *, json_output=False, **kw):
            if llm_raises:
                raise llm_raises
            text = llm_response_text if llm_response_text is not None else json.dumps(_valid_prompt3_json())
            return SimpleNamespace(
                text=text, model="stub-model",
                duration_seconds=0.1, prompt_tokens=100, completion_tokens=50,
            )

    monkeypatch.setattr(status_mod, "get_llm_client", lambda: StubLLM())

    # ---- Confluence stub ----
    class StubConfl:
        def get_page_by_url(self, url):
            if confluence_raises:
                raise confluence_raises
            return {"title": "Page", "body": {"storage": {"value": "<p>x</p>"}}}

    monkeypatch.setattr(status_mod, "get_confluence_client", lambda: StubConfl())

    # ---- JIRA stub ----
    class StubJira:
        def get_project_snapshot(self, project_key, issue_types=None,
                                  recent_window_days=14, stale_threshold_days=14):
            if jira_raises:
                raise jira_raises
            return SimpleNamespace(
                project_key=project_key,
                snapshot_at=datetime.utcnow(),
                total_tasks=10,
                by_status={"In Progress": 5, "Done": 3, "Open": 2},
                overdue_count=1,
                stale_count=2,
                recent_activity=[
                    {"id": "TESTKEY-1", "title": "Task one", "status": "In Progress",
                     "last_activity": "2026-05-08T12:00:00.000+0530"},
                ],
            )

    monkeypatch.setattr(status_mod, "get_jira_client", lambda: StubJira())


# ------------------------------------------------------------------------
# Failure paths — each writes a failed AIComputeLog row
# ------------------------------------------------------------------------

def test_run_status_fails_when_project_not_found(fresh_db, monkeypatch):
    _stub_clients(monkeypatch)
    from app.engines.status import run_status_compute
    from app.models import AIComputeLog
    from app.db import session_scope

    res = run_status_compute("DOES_NOT_EXIST")
    assert res.success is False
    assert "not found" in res.error.lower()

    # AIComputeLog written with project_id=None
    with session_scope() as s:
        rows = s.execute(select(AIComputeLog)).scalars().all()
        assert len(rows) == 1
        assert rows[0].project_id is None
        assert rows[0].success_flag is False


def test_run_status_fails_on_confluence_error(project_in_db, monkeypatch):
    _stub_clients(monkeypatch, confluence_raises=ConnectionError("network down"))
    from app.engines.status import run_status_compute
    from app.models import AIComputeLog, ProjectStatus
    from app.db import session_scope

    res = run_status_compute(project_in_db)
    assert res.success is False
    assert "Confluence fetch failed" in res.error

    # No ProjectStatus written; AIComputeLog row written
    with session_scope() as s:
        assert s.execute(select(ProjectStatus)).scalar_one_or_none() is None
        log_rows = s.execute(select(AIComputeLog)).scalars().all()
        assert len(log_rows) == 1
        assert log_rows[0].success_flag is False
        assert "Confluence" in log_rows[0].error_text


def test_run_status_fails_on_jira_error(project_in_db, monkeypatch):
    _stub_clients(monkeypatch, jira_raises=RuntimeError("jira 503"))
    from app.engines.status import run_status_compute
    from app.models import ProjectStatus
    from app.db import session_scope

    res = run_status_compute(project_in_db)
    assert res.success is False
    assert "JIRA fetch failed" in res.error

    with session_scope() as s:
        assert s.execute(select(ProjectStatus)).scalar_one_or_none() is None


def test_run_status_fails_on_llm_error(project_in_db, monkeypatch):
    _stub_clients(monkeypatch, llm_raises=TimeoutError("model timed out"))
    from app.engines.status import run_status_compute
    from app.models import ProjectStatus, AIComputeLog
    from app.db import session_scope

    res = run_status_compute(project_in_db)
    assert res.success is False
    assert "LLM call failed" in res.error

    with session_scope() as s:
        assert s.execute(select(ProjectStatus)).scalar_one_or_none() is None
        log_rows = s.execute(select(AIComputeLog)).scalars().all()
        assert len(log_rows) == 1
        assert log_rows[0].llm_mode == "ollama"   # the stub's mode is recorded


def test_run_status_fails_on_invalid_json(project_in_db, monkeypatch):
    _stub_clients(monkeypatch, llm_response_text="this is not JSON {{{")
    from app.engines.status import run_status_compute
    from app.models import ProjectStatus, AIComputeLog
    from app.db import session_scope

    res = run_status_compute(project_in_db)
    assert res.success is False
    assert "valid JSON" in res.error

    with session_scope() as s:
        assert s.execute(select(ProjectStatus)).scalar_one_or_none() is None
        log = s.execute(select(AIComputeLog)).scalar_one()
        assert log.success_flag is False
        # response_excerpt should capture the raw bad text for debugging
        assert "this is not JSON" in log.response_excerpt


def test_run_status_fails_on_schema_violation(project_in_db, monkeypatch):
    """Asymmetric trust violation: TL says In-progress, AI tries to verify it.
    Schema validator must catch this; engine must NOT persist."""
    bad_json = _valid_prompt3_json()
    bad_json["milestones"][0]["ai_verification"] = "Verified"  # invalid for non-Done
    _stub_clients(monkeypatch, llm_response_text=json.dumps(bad_json))

    from app.engines.status import run_status_compute
    from app.models import ProjectStatus, AIComputeLog
    from app.db import session_scope

    res = run_status_compute(project_in_db)
    assert res.success is False
    assert "Schema validation failed" in res.error
    assert any("ai_verification" in i for i in res.issues)

    with session_scope() as s:
        assert s.execute(select(ProjectStatus)).scalar_one_or_none() is None
        log = s.execute(select(AIComputeLog)).scalar_one()
        assert log.success_flag is False
        # error_text should mention the schema issue
        assert "ai_verification" in log.error_text


# ------------------------------------------------------------------------
# Happy path — first compute
# ------------------------------------------------------------------------

def test_run_status_first_compute_inserts_project_status(project_in_db, monkeypatch):
    _stub_clients(monkeypatch)
    from app.engines.status import run_status_compute
    from app.models import ProjectStatus, ProjectStatusHistory, AIComputeLog
    from app.db import session_scope

    res = run_status_compute(project_in_db)
    assert res.success is True
    assert res.parsed is not None
    assert res.parsed["overall_health"] == "Amber"
    # First compute → no prior state → changed is False (no history append)
    assert res.changed is False

    with session_scope() as s:
        ps = s.execute(select(ProjectStatus)).scalar_one()
        assert ps.overall_health == "Amber"
        assert ps.schedule_status == "AtRisk"
        assert ps.completion_pct == 30
        assert ps.confidence == "Medium"
        assert ps.prompt_version == "ProjectStatusReasoning/v3"
        assert ps.llm_mode_used == "ollama"
        # No history row on first compute
        assert s.execute(select(ProjectStatusHistory)).scalars().all() == []
        # AIComputeLog written with success
        log = s.execute(select(AIComputeLog)).scalar_one()
        assert log.success_flag is True


# ------------------------------------------------------------------------
# Second compute — no change vs change detection
# ------------------------------------------------------------------------

def test_run_status_second_compute_unchanged_no_history_row(project_in_db, monkeypatch):
    """Re-running with the same outputs refreshes ProjectStatus.computed_at
    but does NOT append to history."""
    _stub_clients(monkeypatch)
    from app.engines.status import run_status_compute
    from app.models import ProjectStatus, ProjectStatusHistory
    from app.db import session_scope

    run_status_compute(project_in_db)  # first
    res = run_status_compute(project_in_db)  # second, same outputs

    assert res.success is True
    assert res.changed is False

    with session_scope() as s:
        # Still exactly one ProjectStatus row (upsert, not insert)
        assert len(s.execute(select(ProjectStatus)).scalars().all()) == 1
        # No history rows — nothing changed
        assert s.execute(select(ProjectStatusHistory)).scalars().all() == []


def test_run_status_second_compute_changed_appends_history(project_in_db, monkeypatch):
    """When health/schedule/completion change between computes, a history
    row is appended capturing the prior + new values."""
    from app.engines.status import run_status_compute
    from app.models import ProjectStatus, ProjectStatusHistory
    from app.db import session_scope

    # First compute — Amber/AtRisk/30
    _stub_clients(monkeypatch)
    first = run_status_compute(project_in_db)
    assert first.success is True

    # Second compute — change to Red/Delayed/20
    changed_json = _valid_prompt3_json()
    changed_json["overall_health"] = "Red"
    changed_json["schedule_status"] = "Delayed"
    changed_json["completion_pct"] = 20
    _stub_clients(monkeypatch, llm_response_text=json.dumps(changed_json))
    second = run_status_compute(project_in_db)

    assert second.success is True
    assert second.changed is True

    with session_scope() as s:
        # Still one ProjectStatus row, now reflecting the new values
        ps = s.execute(select(ProjectStatus)).scalar_one()
        assert ps.overall_health == "Red"
        assert ps.schedule_status == "Delayed"
        assert ps.completion_pct == 20

        # One history row capturing the transition
        hist = s.execute(select(ProjectStatusHistory)).scalars().all()
        assert len(hist) == 1
        assert hist[0].prior_health == "Amber"
        assert hist[0].new_health == "Red"
        assert hist[0].prior_schedule == "AtRisk"
        assert hist[0].new_schedule == "Delayed"
        assert hist[0].prior_completion_pct == 30
        assert hist[0].new_completion_pct == 20


def test_run_status_completion_change_alone_triggers_history(project_in_db, monkeypatch):
    """Even when health and schedule are unchanged, a completion% change
    counts as 'changed' and appends to history."""
    from app.engines.status import run_status_compute
    from app.models import ProjectStatusHistory
    from app.db import session_scope

    _stub_clients(monkeypatch)
    run_status_compute(project_in_db)  # first: Amber/AtRisk/30

    changed_json = _valid_prompt3_json()
    changed_json["completion_pct"] = 45  # health/schedule unchanged
    _stub_clients(monkeypatch, llm_response_text=json.dumps(changed_json))
    res = run_status_compute(project_in_db)

    assert res.changed is True
    with session_scope() as s:
        hist = s.execute(select(ProjectStatusHistory)).scalars().all()
        assert len(hist) == 1
        assert hist[0].prior_completion_pct == 30
        assert hist[0].new_completion_pct == 45


# ------------------------------------------------------------------------
# Tolerance — extra-page failures don't abort the compute
# ------------------------------------------------------------------------

def test_extra_page_failure_does_not_abort_status_compute(fresh_db, monkeypatch):
    """An optional extra context page that fails to fetch should log a
    warning but the compute proceeds and persists ProjectStatus."""
    from app.db import session_scope
    from app.models import Project, ProjectStatus
    from app.engines.status import run_status_compute

    # Insert a project that has one extra page configured
    with session_scope() as s:
        s.add(Project(
            code="WITHEXTRA",
            name="Project With Extra Page",
            jira_project_key="WX",
            confluence_milestones_url="https://confluence.example.com/m",
            confluence_fr_url="https://confluence.example.com/fr",
            confluence_extra_pages_json=["https://confluence.example.com/extra1"],
            issue_types_json=["Task"],
        ))

    # Stub that succeeds on the first 2 fetches (milestones + FR) but
    # raises on the 3rd (the extra page).
    import app.engines.status as status_mod

    call_count = {"n": 0}
    class FlakyConfl:
        def get_page_by_url(self, url):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("extra page 503")
            return {"title": "Page", "body": {"storage": {"value": "<p>x</p>"}}}

    monkeypatch.setattr(status_mod, "get_confluence_client", lambda: FlakyConfl())
    _stub_clients(monkeypatch)  # also stubs LLM + JIRA
    monkeypatch.setattr(status_mod, "get_confluence_client", lambda: FlakyConfl())  # restore

    res = run_status_compute("WITHEXTRA")
    assert res.success is True

    with session_scope() as s:
        assert s.execute(select(ProjectStatus)).scalar_one_or_none() is not None
