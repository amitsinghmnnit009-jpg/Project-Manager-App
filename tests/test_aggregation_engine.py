"""Tests for app.engines.aggregation — the Aggregation Engine (Step 6).

Mocks at the same boundaries as test_status_engine.py:
  - get_jira_client()  → stub with collect_engineer_activity()
  - get_llm_client()   → stub with mode + complete()
  - cfg.engineers.mapping_file → temp JSON file

Uses fresh in-memory SQLite + StaticPool fixture (same as
test_registry / test_status_engine) so DB persistence is real but
isolated per test.
"""
from __future__ import annotations
import json
import pytest
from datetime import date, datetime
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace


# ------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------

@pytest.fixture
def fresh_db(monkeypatch):
    """Per-test in-memory SQLite. Same pattern as test_registry / test_status_engine."""
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
def fake_engineers(tmp_path):
    """Point cfg.engineers.mapping_file at a temp file with two engineers
    assigned to project TESTAGG. Restores on teardown + clears the lru_cache."""
    import app.config as config_mod
    from app.registry import engineers as engineers_mod

    real = config_mod.get_config()
    original_path = real.engineers.mapping_file

    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({
        "engineers": [
            {"name": "Alice E", "knox_id": "alice.e"},
            {"name": "Bob C", "knox_id": "bob.c"},
        ],
        "assignments": [
            {"knox_id": "alice.e", "projects": ["TESTAGG"]},
            {"knox_id": "bob.c", "projects": ["TESTAGG"]},
        ],
    }), encoding="utf-8")

    real.engineers.mapping_file = str(path)
    engineers_mod.load_engineer_mapping.cache_clear()

    yield path

    real.engineers.mapping_file = original_path
    engineers_mod.load_engineer_mapping.cache_clear()


@pytest.fixture
def project_in_db(fresh_db):
    """Insert one project so registry lookups succeed. Returns the code."""
    from app.db import session_scope
    from app.models import Project

    with session_scope() as s:
        s.add(Project(
            code="TESTAGG",
            name="Test Aggregation Project",
            type="firmware",
            description="Test project for aggregation engine",
            jira_project_key="TESTKEY",
            confluence_milestones_url="https://confluence.example.com/m",
            confluence_fr_url="https://confluence.example.com/fr",
            confluence_extra_pages_json=[],
            issue_types_json=["Task", "Bug"],
            chronic_threshold=3,
            holiday_calendar_id="default",
        ))
    return "TESTAGG"


def _make_activity_record(**overrides):
    """Build an ActivityRecord-shaped SimpleNamespace for mocked JIRA returns."""
    from app.clients.jira_client import ActivityRecord
    defaults = dict(
        task_id="TESTKEY-1",
        task_title="Implement feature",
        task_status="In Progress",
        task_assignee=None,
        activity_kind="comment",
        author_name="Alice E",
        author_id="alice.e",
        timestamp="2026-05-08T10:00:00.000+0530",
        detail="Reviewed code, all good",
        url="",
    )
    defaults.update(overrides)
    return ActivityRecord(**defaults)


def _make_jira_activity(by_engineer=None, unmapped=None):
    """Build a JiraEngineerActivity-shaped object for mocked JIRA returns."""
    from app.clients.jira_client import JiraEngineerActivity
    return JiraEngineerActivity(
        project_key="TESTKEY",
        week_of=date(2026, 5, 4),
        by_engineer=by_engineer or {},
        unmapped_authors=unmapped or [],
        lookup_keys_knox=[],
        lookup_keys_name=[],
    )


def _stub_clients(monkeypatch, *,
                  llm_response_text="## Accomplishments\n- Test report content\n",
                  llm_raises=None,
                  jira_raises=None,
                  jira_activity=None,
                  llm_mode="ollama"):
    """Patch get_jira_client and get_llm_client in app.engines.aggregation."""
    import app.engines.aggregation as agg_mod

    class StubLLM:
        mode = llm_mode
        def complete(self, sys_p, user_p, *, json_output=False, **kw):
            if llm_raises:
                raise llm_raises
            return SimpleNamespace(
                text=llm_response_text, model="stub-model",
                duration_seconds=0.5, prompt_tokens=200, completion_tokens=100,
            )
    monkeypatch.setattr(agg_mod, "get_llm_client", lambda: StubLLM())

    class StubJira:
        def collect_engineer_activity(self, project_key, week_of, engineers,
                                      issue_types=None, exclude_labels=None):
            if jira_raises:
                raise jira_raises
            return jira_activity if jira_activity is not None else _make_jira_activity()
    monkeypatch.setattr(agg_mod, "get_jira_client", lambda: StubJira())


# ------------------------------------------------------------------------
# Failure paths
# ------------------------------------------------------------------------

def test_aggregation_fails_when_project_not_found(fresh_db, fake_engineers, monkeypatch):
    _stub_clients(monkeypatch)
    from app.engines.aggregation import run_weekly_aggregation
    from app.models import AIComputeLog, WeeklyReport
    from app.db import session_scope

    res = run_weekly_aggregation("DOES_NOT_EXIST")
    assert res.success is False
    assert "not found" in res.error.lower()

    with session_scope() as s:
        # No WeeklyReport written
        assert s.execute(select(WeeklyReport)).scalars().all() == []
        # AIComputeLog row written with project_id=None
        log = s.execute(select(AIComputeLog)).scalar_one()
        assert log.project_id is None
        assert log.success_flag is False


def test_aggregation_fails_on_jira_error(project_in_db, fake_engineers, monkeypatch):
    _stub_clients(monkeypatch, jira_raises=ConnectionError("jira 503"))
    from app.engines.aggregation import run_weekly_aggregation
    from app.models import WeeklyReport
    from app.db import session_scope

    res = run_weekly_aggregation(project_in_db)
    assert res.success is False
    assert "JIRA activity fetch failed" in res.error
    with session_scope() as s:
        assert s.execute(select(WeeklyReport)).scalars().all() == []


def test_aggregation_fails_on_llm_error(project_in_db, fake_engineers, monkeypatch):
    _stub_clients(monkeypatch, llm_raises=TimeoutError("model timed out"))
    from app.engines.aggregation import run_weekly_aggregation
    from app.models import WeeklyReport, AIComputeLog
    from app.db import session_scope

    res = run_weekly_aggregation(project_in_db)
    assert res.success is False
    assert "LLM call failed" in res.error
    with session_scope() as s:
        assert s.execute(select(WeeklyReport)).scalars().all() == []
        log = s.execute(select(AIComputeLog)).scalar_one()
        assert log.llm_mode == "ollama"   # captured from stub before the failure


def test_aggregation_fails_on_empty_llm_response(project_in_db, fake_engineers, monkeypatch):
    _stub_clients(monkeypatch, llm_response_text="   \n  \n  ")
    from app.engines.aggregation import run_weekly_aggregation
    from app.models import WeeklyReport
    from app.db import session_scope

    res = run_weekly_aggregation(project_in_db)
    assert res.success is False
    assert "empty" in res.error.lower()
    with session_scope() as s:
        assert s.execute(select(WeeklyReport)).scalars().all() == []


# ------------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------------

def test_aggregation_first_run_inserts_weekly_report(project_in_db, fake_engineers, monkeypatch):
    """Fresh insert. Two engineers contribute; LLM returns markdown; one
    WeeklyReport row appears with regenerated_count=0 + the prompt version."""
    activity = _make_jira_activity(by_engineer={
        "alice.e": [
            _make_activity_record(activity_kind="comment", detail="Reviewed PR"),
            _make_activity_record(activity_kind="worklog", detail="3h",
                                  task_id="TESTKEY-2", task_title="Task two"),
        ],
        "bob.c": [
            _make_activity_record(activity_kind="comment", author_name="Bob C",
                                  author_id="bob.c", detail="Investigated bug",
                                  task_id="TESTKEY-3", task_title="Bug three"),
        ],
    })
    _stub_clients(monkeypatch, jira_activity=activity,
                  llm_response_text="## Accomplishments\n- Reviewed PR\n## In-progress\n- Bug investigation\n")

    from app.engines.aggregation import run_weekly_aggregation
    from app.models import WeeklyReport, AIComputeLog
    from app.db import session_scope

    res = run_weekly_aggregation(project_in_db, week_of=date(2026, 5, 4))

    assert res.success is True
    assert res.is_regeneration is False
    assert res.engineer_count == 2
    assert res.activity_records == 3
    assert res.unmapped_authors_count == 0
    assert "Reviewed PR" in res.content_markdown

    with session_scope() as s:
        wr = s.execute(select(WeeklyReport)).scalar_one()
        assert wr.week_of == date(2026, 5, 4)
        assert wr.regenerated_count == 0
        assert wr.last_regenerated_at is None
        assert wr.prompt_version_aggregation == "WeeklyAggregation/v1"
        assert wr.prompt_version_highlights == ""   # Step 7 will populate
        assert wr.llm_mode_used == "ollama"
        assert "Reviewed PR" in wr.content_markdown

        log = s.execute(select(AIComputeLog)).scalar_one()
        assert log.success_flag is True
        assert log.prompt_name == "weekly_aggregation_v1"


# ------------------------------------------------------------------------
# Regeneration
# ------------------------------------------------------------------------

def test_aggregation_second_run_same_week_bumps_regenerated_count(
    project_in_db, fake_engineers, monkeypatch,
):
    """Re-running on the same (project, week) updates the row in place,
    increments regenerated_count, sets last_regenerated_at, refreshes content."""
    _stub_clients(monkeypatch,
                  llm_response_text="## Accomplishments\n- First version\n")
    from app.engines.aggregation import run_weekly_aggregation
    from app.models import WeeklyReport
    from app.db import session_scope

    week = date(2026, 5, 4)

    # First run
    first = run_weekly_aggregation(project_in_db, week_of=week)
    assert first.success is True
    assert first.is_regeneration is False

    # Second run with different LLM output (e.g. engineers added more comments)
    _stub_clients(monkeypatch,
                  llm_response_text="## Accomplishments\n- Second version with more detail\n")
    second = run_weekly_aggregation(project_in_db, week_of=week, regenerate=True)

    assert second.success is True
    assert second.is_regeneration is True

    with session_scope() as s:
        # Still exactly one WeeklyReport row (unique (project_id, week_of))
        rows = s.execute(select(WeeklyReport)).scalars().all()
        assert len(rows) == 1
        wr = rows[0]
        assert wr.regenerated_count == 1
        assert wr.last_regenerated_at is not None
        assert "Second version" in wr.content_markdown
        assert "First version" not in wr.content_markdown


def test_aggregation_third_run_bumps_count_to_two(
    project_in_db, fake_engineers, monkeypatch,
):
    """Third run on same week → regenerated_count goes from 1 to 2."""
    _stub_clients(monkeypatch)
    from app.engines.aggregation import run_weekly_aggregation
    from app.models import WeeklyReport
    from app.db import session_scope

    week = date(2026, 5, 4)
    run_weekly_aggregation(project_in_db, week_of=week)
    run_weekly_aggregation(project_in_db, week_of=week)
    run_weekly_aggregation(project_in_db, week_of=week)

    with session_scope() as s:
        wr = s.execute(select(WeeklyReport)).scalar_one()
        assert wr.regenerated_count == 2  # third run is the second regeneration


# ------------------------------------------------------------------------
# Multi-week — distinct rows per week
# ------------------------------------------------------------------------

def test_aggregation_different_weeks_are_distinct_rows(
    project_in_db, fake_engineers, monkeypatch,
):
    """Two runs for the same project but different weeks → two rows."""
    _stub_clients(monkeypatch)
    from app.engines.aggregation import run_weekly_aggregation
    from app.models import WeeklyReport
    from app.db import session_scope

    run_weekly_aggregation(project_in_db, week_of=date(2026, 5, 4))
    run_weekly_aggregation(project_in_db, week_of=date(2026, 5, 11))

    with session_scope() as s:
        rows = s.execute(
            select(WeeklyReport).order_by(WeeklyReport.week_of)
        ).scalars().all()
        assert [r.week_of for r in rows] == [date(2026, 5, 4), date(2026, 5, 11)]
        assert all(r.regenerated_count == 0 for r in rows)


# ------------------------------------------------------------------------
# Edge case — no engineers but still proceed
# ------------------------------------------------------------------------

def test_aggregation_proceeds_with_no_engineers(project_in_db, monkeypatch, tmp_path):
    """Per Prompt 1's edge-case spec, the engine should still run when no
    engineers are mapped to the project. The LLM produces a 'No updates this
    week' template and the WeeklyReport is persisted.

    Doesn't use fake_engineers fixture because we want EMPTY assignments
    for project TESTAGG specifically.
    """
    import app.config as config_mod
    from app.registry import engineers as engineers_mod

    real = config_mod.get_config()
    original_path = real.engineers.mapping_file
    path = tmp_path / "empty_mapping.json"
    path.write_text(json.dumps({
        "engineers": [{"name": "Other E", "knox_id": "other.e"}],
        "assignments": [{"knox_id": "other.e", "projects": ["DIFFERENT_PROJECT"]}],
    }), encoding="utf-8")
    real.engineers.mapping_file = str(path)
    engineers_mod.load_engineer_mapping.cache_clear()

    try:
        _stub_clients(monkeypatch,
                      llm_response_text="## Accomplishments\nNo updates this week.\n")
        from app.engines.aggregation import run_weekly_aggregation
        from app.models import WeeklyReport
        from app.db import session_scope

        res = run_weekly_aggregation(project_in_db)
        assert res.success is True
        assert res.engineer_count == 0
        assert res.activity_records == 0

        with session_scope() as s:
            wr = s.execute(select(WeeklyReport)).scalar_one()
            assert "No updates this week" in wr.content_markdown
    finally:
        real.engineers.mapping_file = original_path
        engineers_mod.load_engineer_mapping.cache_clear()


# ------------------------------------------------------------------------
# Unmapped authors logged but not fatal
# ------------------------------------------------------------------------

def test_aggregation_records_unmapped_authors_count(
    project_in_db, fake_engineers, monkeypatch,
):
    """Unmapped JIRA authors observed in activity should populate the
    result.unmapped_authors_count without failing the run."""
    from app.clients.jira_client import UnmappedAuthor

    activity = _make_jira_activity(
        by_engineer={"alice.e": [_make_activity_record()]},
        unmapped=[
            UnmappedAuthor(display_name="Ghost User",
                           user_id="JIRAUSER999", email="",
                           lookup_attempts=[]),
        ],
    )
    _stub_clients(monkeypatch, jira_activity=activity)

    from app.engines.aggregation import run_weekly_aggregation
    res = run_weekly_aggregation(project_in_db)

    assert res.success is True
    assert res.unmapped_authors_count == 1


# ------------------------------------------------------------------------
# Default week_of derives from today (IST)
# ------------------------------------------------------------------------

def test_aggregation_default_week_of_is_current_week(project_in_db, fake_engineers, monkeypatch):
    """Calling without week_of should default to the current week's Monday (IST)."""
    _stub_clients(monkeypatch)
    from app.engines.aggregation import run_weekly_aggregation
    from app.utils.dates import week_of as compute_week_of

    res = run_weekly_aggregation(project_in_db)
    assert res.success is True
    assert res.week_of == compute_week_of()


# ========================================================================
# Phase 2 backfill: backfill_mode + exclude_labels propagation
# ========================================================================

def _stub_clients_with_call_tracking(monkeypatch, *, jira_activity=None,
                                     backfill_activity=None,
                                     llm_response_text="## body\nstuff\n",
                                     llm_mode="ollama"):
    """Variant of _stub_clients that captures which JIRA method was called
    and with what args. Returns the captured-calls dict for assertions."""
    import app.engines.aggregation as agg_mod
    calls: dict = {"normal": [], "backfill": []}

    class StubLLM:
        mode = llm_mode
        def complete(self, sys_p, user_p, *, json_output=False, **kw):
            return SimpleNamespace(
                text=llm_response_text, model="stub-model",
                duration_seconds=0.5, prompt_tokens=200, completion_tokens=100,
            )
    monkeypatch.setattr(agg_mod, "get_llm_client", lambda: StubLLM())

    class StubJira:
        def collect_engineer_activity(self, project_key, week_of, engineers,
                                      issue_types=None, exclude_labels=None):
            calls["normal"].append({
                "project_key": project_key, "week_of": week_of,
                "issue_types": issue_types, "exclude_labels": exclude_labels,
            })
            return jira_activity if jira_activity is not None else _make_jira_activity()

        def collect_engineer_activity_for_backfill(
            self, project_key, field_name, week_of, engineers, issue_types=None,
        ):
            calls["backfill"].append({
                "project_key": project_key, "field_name": field_name,
                "week_of": week_of, "issue_types": issue_types,
            })
            return backfill_activity if backfill_activity is not None else _make_jira_activity()

    monkeypatch.setattr(agg_mod, "get_jira_client", lambda: StubJira())
    return calls


def _patch_project_config(monkeypatch, *,
                          activity_date_field: str = "",
                          exclude_labels: list = None):
    """Make _project_backfill_config return controlled values without
    touching real config.json."""
    import app.engines.aggregation as agg_mod
    monkeypatch.setattr(
        agg_mod, "_project_backfill_config",
        lambda code: (activity_date_field, list(exclude_labels or [])),
    )


def test_aggregation_normal_mode_calls_normal_jira_method(
    project_in_db, fake_engineers, monkeypatch,
):
    calls = _stub_clients_with_call_tracking(monkeypatch)
    _patch_project_config(monkeypatch)
    from app.engines.aggregation import run_weekly_aggregation

    res = run_weekly_aggregation(project_in_db, week_of=date(2026, 5, 4))
    assert res.success is True
    assert len(calls["normal"]) == 1
    assert calls["backfill"] == []


def test_aggregation_normal_mode_passes_exclude_labels(
    project_in_db, fake_engineers, monkeypatch,
):
    calls = _stub_clients_with_call_tracking(monkeypatch)
    _patch_project_config(monkeypatch, exclude_labels=["backfill", "ignore"])
    from app.engines.aggregation import run_weekly_aggregation

    res = run_weekly_aggregation(project_in_db, week_of=date(2026, 5, 4))
    assert res.success is True
    assert calls["normal"][0]["exclude_labels"] == ["backfill", "ignore"]


def test_aggregation_normal_mode_passes_none_when_no_exclude_labels(
    project_in_db, fake_engineers, monkeypatch,
):
    calls = _stub_clients_with_call_tracking(monkeypatch)
    _patch_project_config(monkeypatch, exclude_labels=[])
    from app.engines.aggregation import run_weekly_aggregation

    run_weekly_aggregation(project_in_db, week_of=date(2026, 5, 4))
    # Empty list collapses to None so we pass nothing to JIRA — no
    # 'AND labels NOT IN ()' clause appears
    assert calls["normal"][0]["exclude_labels"] is None


def test_aggregation_backfill_mode_calls_backfill_jira_method(
    project_in_db, fake_engineers, monkeypatch,
):
    calls = _stub_clients_with_call_tracking(monkeypatch)
    _patch_project_config(monkeypatch, activity_date_field="Baseline end date")
    from app.engines.aggregation import run_weekly_aggregation

    res = run_weekly_aggregation(
        project_in_db, week_of=date(2026, 2, 2), backfill_mode=True,
    )
    assert res.success is True
    assert calls["normal"] == []
    assert len(calls["backfill"]) == 1
    assert calls["backfill"][0]["field_name"] == "Baseline end date"
    assert calls["backfill"][0]["week_of"] == date(2026, 2, 2)


def test_aggregation_backfill_mode_fails_when_no_activity_date_field(
    project_in_db, fake_engineers, monkeypatch,
):
    """backfill_mode=True requires activity_date_field — fails fast otherwise."""
    calls = _stub_clients_with_call_tracking(monkeypatch)
    _patch_project_config(monkeypatch, activity_date_field="")
    from app.engines.aggregation import run_weekly_aggregation
    from app.models import WeeklyReport
    from app.db import session_scope

    res = run_weekly_aggregation(
        project_in_db, week_of=date(2026, 2, 2), backfill_mode=True,
    )
    assert res.success is False
    assert "activity_date_field" in res.error
    assert calls["backfill"] == []   # never reached the JIRA call
    with session_scope() as s:
        assert s.execute(select(WeeklyReport)).scalars().all() == []
