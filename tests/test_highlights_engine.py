"""Tests for app.engines.highlights — Highlights Engine (Step 7).

Mocks at the same boundary as test_aggregation_engine.py:
  - get_llm_client() → stub with mode + complete()

Doesn't mock JIRA or Confluence — Highlights doesn't call them. It only
reads/writes the WeeklyReport DB rows.

Uses the same fresh in-memory SQLite + StaticPool fixture as the other
engine test suites.
"""
from __future__ import annotations
import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace


# ------------------------------------------------------------------------
# Fixtures (mirror test_aggregation_engine.py)
# ------------------------------------------------------------------------

@pytest.fixture
def fresh_db(monkeypatch):
    """Per-test in-memory SQLite with StaticPool — same canonical pattern."""
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
    """Insert one project so registry lookups succeed. Returns the code."""
    from app.db import session_scope
    from app.models import Project

    with session_scope() as s:
        s.add(Project(
            code="TESTHL",
            name="Test Highlights Project",
            type="firmware",
            description="Test project for highlights engine",
            jira_project_key="TESTKEY",
            confluence_milestones_url="https://confluence.example.com/m",
            confluence_fr_url="https://confluence.example.com/fr",
            confluence_extra_pages_json=[],
            issue_types_json=["Task", "Bug"],
            chronic_threshold=3,
            holiday_calendar_id="default",
        ))
    return "TESTHL"


def _insert_weekly_report(project_code: str, week_of_date: date, content: str):
    """Helper to seed a WeeklyReport row for a given (project, week)."""
    from app.db import session_scope
    from app.models import Project, WeeklyReport

    with session_scope() as s:
        proj = s.execute(
            select(Project).where(Project.code == project_code)
        ).scalar_one()
        s.add(WeeklyReport(
            project_id=proj.id,
            week_of=week_of_date,
            content_markdown=content,
            regenerated_count=0,
            prompt_version_aggregation="WeeklyAggregation/v1",
            prompt_version_highlights="",
            llm_mode_used="ollama",
        ))


def _stub_llm(monkeypatch, *, response_text="### Missed commitments\n- (none)\n",
              raises=None, mode="ollama"):
    """Patch get_llm_client in app.engines.highlights."""
    import app.engines.highlights as hl_mod

    class StubLLM:
        @property
        def mode(self):
            return mode
        def complete(self, sys_p, user_p, *, json_output=False, **kw):
            # Capture the prompts on the instance so tests can assert against them.
            self.last_sys = sys_p
            self.last_user = user_p
            self.last_json_output = json_output
            if raises:
                raise raises
            return SimpleNamespace(
                text=response_text, model="stub-model",
                duration_seconds=0.42, prompt_tokens=150, completion_tokens=80,
            )

    instance = StubLLM()
    monkeypatch.setattr(hl_mod, "get_llm_client", lambda: instance)
    return instance


THIS_WEEK = date(2026, 5, 4)
LAST_WEEK = THIS_WEEK - timedelta(days=7)


REPORT_THIS_WEEK_EMPTY_HL = """\
# Weekly Project Report: Demo
**Week of:** 2026-05-04

## Accomplishments
- closed PROJ-1

## Risks and Blockers
- vendor delay still open

## Next-week plan
- complete PROJ-2

## Highlights / Things to Watch

"""

REPORT_LAST_WEEK = """\
# Weekly Project Report: Demo
**Week of:** 2026-04-27

## Accomplishments
- started work on PROJ-1

## Risks and Blockers
- vendor delay

## Next-week plan
- close PROJ-1
- start PROJ-2

## Highlights / Things to Watch

"""

LLM_HIGHLIGHTS_OUTPUT = """\
### Missed commitments
- last week's plan to "start PROJ-2" not addressed this week

### Carried-over risks/blockers
- vendor delay (also present last week)

### Newly raised risks/blockers
- (none)

### Notably absent items
- (none)
"""


# ------------------------------------------------------------------------
# Failure paths
# ------------------------------------------------------------------------

def test_highlights_fails_when_project_not_found(fresh_db, monkeypatch):
    _stub_llm(monkeypatch)
    from app.engines.highlights import run_highlights
    from app.models import AIComputeLog
    from app.db import session_scope

    res = run_highlights("DOES_NOT_EXIST")
    assert res.success is False
    assert "not found" in res.error.lower()

    with session_scope() as s:
        log = s.execute(select(AIComputeLog)).scalar_one()
        assert log.project_id is None
        assert log.success_flag is False


def test_highlights_fails_when_this_week_report_missing(project_in_db, monkeypatch):
    _stub_llm(monkeypatch)
    from app.engines.highlights import run_highlights
    from app.models import AIComputeLog, WeeklyReport
    from app.db import session_scope

    res = run_highlights(project_in_db, week_of=THIS_WEEK)
    assert res.success is False
    assert "No WeeklyReport row exists" in res.error
    assert "run-aggregation" in res.error

    with session_scope() as s:
        # No new WeeklyReport written
        assert s.execute(select(WeeklyReport)).scalars().all() == []
        # AIComputeLog written with project_id set + failure
        log = s.execute(select(AIComputeLog)).scalar_one()
        assert log.project_id is not None
        assert log.success_flag is False


def test_highlights_fails_on_llm_error(project_in_db, monkeypatch):
    _insert_weekly_report(project_in_db, THIS_WEEK, REPORT_THIS_WEEK_EMPTY_HL)
    _stub_llm(monkeypatch, raises=TimeoutError("model timed out"))
    from app.engines.highlights import run_highlights

    res = run_highlights(project_in_db, week_of=THIS_WEEK)
    assert res.success is False
    assert "LLM call failed" in res.error
    assert res.llm_mode == "ollama"


def test_highlights_fails_on_empty_llm_response(project_in_db, monkeypatch):
    _insert_weekly_report(project_in_db, THIS_WEEK, REPORT_THIS_WEEK_EMPTY_HL)
    _stub_llm(monkeypatch, response_text="   \n  \n")
    from app.engines.highlights import run_highlights

    res = run_highlights(project_in_db, week_of=THIS_WEEK)
    assert res.success is False
    assert "empty content" in res.error.lower()


# ------------------------------------------------------------------------
# Happy path — both weeks exist
# ------------------------------------------------------------------------

def test_highlights_happy_path_with_prior_week(project_in_db, monkeypatch):
    _insert_weekly_report(project_in_db, LAST_WEEK, REPORT_LAST_WEEK)
    _insert_weekly_report(project_in_db, THIS_WEEK, REPORT_THIS_WEEK_EMPTY_HL)
    llm = _stub_llm(monkeypatch, response_text=LLM_HIGHLIGHTS_OUTPUT)

    from app.engines.highlights import run_highlights
    from app.models import WeeklyReport, AIComputeLog
    from app.db import session_scope

    res = run_highlights(project_in_db, week_of=THIS_WEEK)

    assert res.success is True
    assert res.is_first_week is False
    assert res.last_week_of == LAST_WEEK
    assert "Missed commitments" in res.highlights_section
    # Spliced into the full report
    assert "## Highlights / Things to Watch" in res.content_markdown
    assert "vendor delay (also present last week)" in res.content_markdown
    # Original sections still present
    assert "## Accomplishments" in res.content_markdown
    assert "closed PROJ-1" in res.content_markdown

    # LLM saw both reports
    assert "PROJ-1" in llm.last_user
    assert "started work on PROJ-1" in llm.last_user  # from last week
    assert "closed PROJ-1" in llm.last_user           # from this week
    assert "(no prior week)" not in llm.last_user
    assert llm.last_json_output is False              # Markdown, not JSON

    # DB row updated
    with session_scope() as s:
        row = s.execute(
            select(WeeklyReport).where(WeeklyReport.week_of == THIS_WEEK)
        ).scalar_one()
        assert "vendor delay (also present last week)" in row.content_markdown
        assert row.prompt_version_highlights == "HighlightsComparison/v1"
        # Step 6's field untouched
        assert row.regenerated_count == 0
        assert row.prompt_version_aggregation == "WeeklyAggregation/v1"

        # AIComputeLog success row written
        log = s.execute(
            select(AIComputeLog).where(AIComputeLog.success_flag == True)
        ).scalar_one()
        assert log.prompt_name == "highlights_comparison_v1"
        assert log.prompt_version == "HighlightsComparison/v1"
        assert log.llm_mode == "ollama"


# ------------------------------------------------------------------------
# First-week path — no prior report
# ------------------------------------------------------------------------

def test_highlights_first_week_uses_placeholder(project_in_db, monkeypatch):
    """Only this week exists. The engine doesn't call the LLM differently —
    it passes the placeholder per Prompt 2's spec, and the LLM produces the
    standard 'first report' line. Engine wraps that as is_first_week=True."""
    _insert_weekly_report(project_in_db, THIS_WEEK, REPORT_THIS_WEEK_EMPTY_HL)
    first_week_msg = "First report for this project — no prior week to compare against."
    llm = _stub_llm(monkeypatch, response_text=first_week_msg)

    from app.engines.highlights import run_highlights
    res = run_highlights(project_in_db, week_of=THIS_WEEK)

    assert res.success is True
    assert res.is_first_week is True
    assert res.last_week_of is None
    assert first_week_msg in res.highlights_section

    # Prompt was rendered with placeholders for the missing prior week
    assert "(no prior week)" in llm.last_user
    assert "(no prior-week report exists for this project)" in llm.last_user


# ------------------------------------------------------------------------
# Re-run idempotency
# ------------------------------------------------------------------------

def test_highlights_rerun_replaces_existing_section(project_in_db, monkeypatch):
    """Re-running on a report whose Highlights is already filled must:
       (a) NOT show the old highlights to the LLM
       (b) REPLACE the old highlights in the saved report (not append)."""
    _insert_weekly_report(project_in_db, LAST_WEEK, REPORT_LAST_WEEK)

    # Seed THIS_WEEK with already-filled highlights (simulate prior Step 7 run).
    seeded = REPORT_THIS_WEEK_EMPTY_HL.rstrip() + "\n\n" + (
        "### Missed commitments\n- OLD STALE BULLET FROM PRIOR RUN\n"
    )
    _insert_weekly_report(project_in_db, THIS_WEEK, seeded)

    llm = _stub_llm(monkeypatch, response_text=LLM_HIGHLIGHTS_OUTPUT)

    from app.engines.highlights import run_highlights
    from app.models import WeeklyReport
    from app.db import session_scope

    res = run_highlights(project_in_db, week_of=THIS_WEEK)
    assert res.success is True

    # (a) LLM did NOT see the old stale bullet
    assert "OLD STALE BULLET FROM PRIOR RUN" not in llm.last_user

    # (b) Saved report contains the new highlights, not the old
    with session_scope() as s:
        row = s.execute(
            select(WeeklyReport).where(WeeklyReport.week_of == THIS_WEEK)
        ).scalar_one()
        assert "OLD STALE BULLET FROM PRIOR RUN" not in row.content_markdown
        assert "vendor delay (also present last week)" in row.content_markdown
        # Heading not duplicated
        assert row.content_markdown.count("## Highlights / Things to Watch") == 1


# ------------------------------------------------------------------------
# Default week_of
# ------------------------------------------------------------------------

def test_highlights_default_week_of_is_current_week(project_in_db, monkeypatch):
    """When week_of is omitted, the engine uses today's IST week_of."""
    from app.utils.dates import week_of as compute_week_of
    today_week = compute_week_of()
    _insert_weekly_report(project_in_db, today_week, REPORT_THIS_WEEK_EMPTY_HL)
    _stub_llm(monkeypatch, response_text="First report for this project — no prior week to compare against.")

    from app.engines.highlights import run_highlights
    res = run_highlights(project_in_db)  # no week_of

    assert res.success is True
    assert res.week_of == today_week
