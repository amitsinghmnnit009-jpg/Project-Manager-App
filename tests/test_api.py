"""Integration tests for the public REST API (Step 11).

Exercises every router via FastAPI's TestClient against a fresh in-memory
SQLite (StaticPool) seeded per-test. Mocks at the engine boundaries so no
live LLM / JIRA / Confluence calls happen.

Same database/scheduler-stub fixture pattern as the engine test suites.
"""
from __future__ import annotations
import pytest
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace


# ------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------

@pytest.fixture
def fresh_db(monkeypatch):
    """In-memory SQLite + StaticPool (canonical pattern)."""
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
def project_in_db(fresh_db):
    from app.db import session_scope
    from app.models import Project
    with session_scope() as s:
        s.add(Project(
            code="APITEST",
            name="API Test Project",
            type="firmware",
            description="Integration test project",
            owning_tl="TL One", owning_pgm="PGM One",
            jira_project_key="APITESTKEY",
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
    return "APITEST"


@pytest.fixture
def client(fresh_db, monkeypatch):
    """Build a TestClient with lifespan stubbed where it would touch real
    external systems / spawn real threads.

    - Scheduler start/stop become no-ops (tests don't want a real APScheduler
      thread running).
    - Project sync becomes a no-op (we seed the DB directly per test).
    - Engineer mapping pre-warm becomes a no-op (each test that needs
      engineers patches the registry directly).
    """
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
    monkeypatch.setattr(eng_reg, "load_engineer_mapping",
                        lambda: SimpleNamespace(
                            engineers=[], by_knox={}, by_project={},
                            file_path="(test stub)", parse_warnings=[],
                        ))

    from fastapi.testclient import TestClient
    from app.api.main import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c


# ------------------------------------------------------------------------
# /health (sanity)
# ------------------------------------------------------------------------

def test_health_endpoint_responds(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


# ------------------------------------------------------------------------
# /api/projects
# ------------------------------------------------------------------------

def test_list_projects_empty_when_db_empty(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == []


def test_list_projects_returns_seeded_project(client, project_in_db):
    r = client.get("/api/projects")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["code"] == "APITEST"
    assert body[0]["name"] == "API Test Project"
    assert body[0]["type"] == "firmware"


def test_get_project_404_when_missing(client):
    r = client.get("/api/projects/DOES_NOT_EXIST")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_get_project_detail(client, project_in_db):
    r = client.get(f"/api/projects/{project_in_db}")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "APITEST"
    assert body["jira_project_key"] == "APITESTKEY"
    assert body["confluence_milestones_url"].endswith("/m")
    assert body["weekly_cutoff"] == "Mon 13:00"
    assert body["chronic_threshold"] == 3
    assert body["issue_types"] == ["Task", "Bug"]


def test_get_project_tasks_uses_jira_snapshot(client, project_in_db, monkeypatch):
    """JIRA client mocked — endpoint formats snapshot.recent_activity into TaskItem list."""
    import app.api.projects as proj_api

    class StubJira:
        base = "https://jira.test.local"
        def get_project_snapshot(self, key, types=None):
            return SimpleNamespace(
                recent_activity=[
                    {"id": "APITESTKEY-1", "title": "Do thing 1",
                     "status": "In Progress", "last_activity": "2026-05-09T10:00:00+05:30"},
                    {"id": "APITESTKEY-2", "title": "Do thing 2",
                     "status": "Done", "last_activity": "2026-05-08T10:00:00+05:30"},
                ],
            )
    import app.clients
    monkeypatch.setattr(app.clients, "get_jira_client", lambda: StubJira())

    r = client.get(f"/api/projects/{project_in_db}/tasks")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert body[0]["id"] == "APITESTKEY-1"
    assert body[0]["url"] == "https://jira.test.local/browse/APITESTKEY-1"


def test_get_project_tasks_502_on_jira_failure(client, project_in_db, monkeypatch):
    import app.clients
    def boom():
        raise ConnectionError("jira 503")
    monkeypatch.setattr(app.clients, "get_jira_client", boom)

    r = client.get(f"/api/projects/{project_in_db}/tasks")
    assert r.status_code == 502
    assert "JIRA fetch failed" in r.json()["detail"]


# ------------------------------------------------------------------------
# /api/projects/{code}/status
# ------------------------------------------------------------------------

def _seed_status(project_id: int, **overrides):
    """Insert a ProjectStatus row directly via the test DB session."""
    from app.db import session_scope
    from app.models import ProjectStatus
    defaults = dict(
        project_id=project_id, computed_at=datetime.utcnow(),
        overall_health="Amber", schedule_status="AtRisk",
        completion_pct=42, milestones_json=[
            {"name": "M1", "tl_declared_status": "In-progress",
             "ai_verification": "NotApplicable", "evidence": "ok"},
        ],
        rationale="Sample rationale.", confidence="Medium",
        prompt_version="ProjectStatusReasoning/v2", llm_mode_used="ollama",
    )
    defaults.update(overrides)
    with session_scope() as s:
        s.add(ProjectStatus(**defaults))


def test_get_status_404_when_no_row_yet(client, project_in_db):
    r = client.get(f"/api/projects/{project_in_db}/status")
    assert r.status_code == 404
    assert "no status computed yet" in r.json()["detail"].lower()


def test_get_status_returns_seeded_row(client, project_in_db):
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    _seed_status(pid)
    r = client.get(f"/api/projects/{project_in_db}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["project_code"] == "APITEST"
    assert body["overall_health"] == "Amber"
    assert body["schedule_status"] == "AtRisk"
    assert body["completion_pct"] == 42
    assert body["confidence"] == "Medium"
    assert len(body["milestones"]) == 1
    assert body["prompt_version"] == "ProjectStatusReasoning/v2"


def test_status_history_empty_returns_empty_list(client, project_in_db):
    r = client.get(f"/api/projects/{project_in_db}/status/history")
    assert r.status_code == 200
    assert r.json() == []


def test_status_history_returns_rows_newest_first(client, project_in_db):
    from app.db import session_scope
    from app.models import ProjectStatusHistory
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    base = datetime(2026, 5, 1, 6, 0, 0)
    with session_scope() as s:
        for i in range(3):
            s.add(ProjectStatusHistory(
                project_id=pid,
                computed_at=base + timedelta(days=i),
                prior_health="Green", new_health="Amber",
                prior_schedule="OnTrack", new_schedule="AtRisk",
                prior_completion_pct=10, new_completion_pct=20,
                rationale=f"change {i}",
                prompt_version="ProjectStatusReasoning/v2",
            ))
    r = client.get(f"/api/projects/{project_in_db}/status/history")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    # Newest first
    rationales = [h["rationale"] for h in body]
    assert rationales == ["change 2", "change 1", "change 0"]


def test_status_refresh_calls_engine(client, project_in_db, monkeypatch):
    captured = {}
    def fake_engine(code):
        captured["called_with"] = code
        return SimpleNamespace(
            success=True, error="",
            parsed={"overall_health": "Green", "schedule_status": "OnTrack",
                    "completion_pct": 75, "confidence": "High",
                    "rationale": "Looking good."},
            duration_seconds=1.5, changed=True, issues=[],
        )
    import app.engines.status as status_mod
    monkeypatch.setattr(status_mod, "run_status_compute", fake_engine)

    r = client.post(f"/api/projects/{project_in_db}/status/refresh")
    assert r.status_code == 200
    body = r.json()
    assert captured["called_with"] == "APITEST"
    assert body["success"] is True
    assert body["overall_health"] == "Green"
    assert body["completion_pct"] == 75
    assert body["changed"] is True


def test_status_refresh_failure_serialised(client, project_in_db, monkeypatch):
    import app.engines.status as status_mod
    monkeypatch.setattr(
        status_mod, "run_status_compute",
        lambda code: SimpleNamespace(
            success=False, error="Confluence fetch failed",
            parsed=None, duration_seconds=0.2, changed=None,
            issues=["one issue"],
        ),
    )
    r = client.post(f"/api/projects/{project_in_db}/status/refresh")
    assert r.status_code == 200   # 200 — engine reports failure as data, not HTTP error
    body = r.json()
    assert body["success"] is False
    assert "Confluence fetch failed" in body["error"]
    assert body["validation_issues"] == ["one issue"]


# ------------------------------------------------------------------------
# /api/projects/{code}/reports
# ------------------------------------------------------------------------

def _seed_weekly_report(project_id: int, week_of_date: date,
                       content="## Accomplishments\n- did stuff\n",
                       has_highlights: bool = False):
    from app.db import session_scope
    from app.models import WeeklyReport
    with session_scope() as s:
        s.add(WeeklyReport(
            project_id=project_id, week_of=week_of_date,
            content_markdown=content,
            generated_at=datetime.utcnow(),
            regenerated_count=0,
            prompt_version_aggregation="WeeklyAggregation/v1",
            prompt_version_highlights="HighlightsComparison/v1" if has_highlights else "",
            llm_mode_used="ollama",
        ))


def test_list_reports_empty(client, project_in_db):
    r = client.get(f"/api/projects/{project_in_db}/reports")
    assert r.status_code == 200
    assert r.json() == []


def test_list_reports_newest_first(client, project_in_db):
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    _seed_weekly_report(pid, date(2026, 4, 27))
    _seed_weekly_report(pid, date(2026, 5, 4), has_highlights=True)
    _seed_weekly_report(pid, date(2026, 5, 11))

    r = client.get(f"/api/projects/{project_in_db}/reports")
    body = r.json()
    weeks = [w["week_of"] for w in body]
    assert weeks == ["2026-05-11", "2026-05-04", "2026-04-27"]
    # has_highlights derived correctly
    by_week = {w["week_of"]: w for w in body}
    assert by_week["2026-05-04"]["has_highlights"] is True
    assert by_week["2026-04-27"]["has_highlights"] is False


def test_list_reports_filters_by_from_to(client, project_in_db):
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    for d in (date(2026, 4, 13), date(2026, 4, 20),
              date(2026, 4, 27), date(2026, 5, 4)):
        _seed_weekly_report(pid, d)
    r = client.get(
        f"/api/projects/{project_in_db}/reports",
        params={"from": "2026-04-20", "to": "2026-04-27"},
    )
    body = r.json()
    weeks = [w["week_of"] for w in body]
    assert weeks == ["2026-04-27", "2026-04-20"]


def test_get_latest_report_returns_full_content(client, project_in_db):
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    _seed_weekly_report(pid, date(2026, 4, 27),
                        content="## Older content\n- x\n")
    _seed_weekly_report(pid, date(2026, 5, 4),
                        content="## NEWEST content\n- y\n")
    r = client.get(f"/api/projects/{project_in_db}/reports/latest")
    body = r.json()
    assert body["week_of"] == "2026-05-04"
    assert "NEWEST content" in body["content_markdown"]


def test_get_latest_report_404_when_none(client, project_in_db):
    r = client.get(f"/api/projects/{project_in_db}/reports/latest")
    assert r.status_code == 404


def test_get_report_for_specific_week(client, project_in_db):
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    _seed_weekly_report(pid, date(2026, 5, 4),
                        content="## SPECIFIC week\n- z\n")
    r = client.get(f"/api/projects/{project_in_db}/reports/2026-05-04")
    body = r.json()
    assert body["week_of"] == "2026-05-04"
    assert "SPECIFIC week" in body["content_markdown"]


def test_get_report_404_for_unknown_week(client, project_in_db):
    r = client.get(f"/api/projects/{project_in_db}/reports/2026-05-04")
    assert r.status_code == 404


def test_regenerate_report_calls_aggregation(client, project_in_db, monkeypatch):
    captured = {}
    def fake_agg(code, week_of=None, regenerate=False):
        captured["called_with"] = (code, week_of, regenerate)
        return SimpleNamespace(
            success=True, error="", week_of=week_of,
            is_regeneration=False, engineer_count=2,
            activity_records=10, unmapped_authors_count=0,
            duration_seconds=1.0,
            content_markdown="## Accomplishments\n- generated\n",
        )
    import app.engines.aggregation as agg_mod
    monkeypatch.setattr(agg_mod, "run_weekly_aggregation", fake_agg)

    r = client.post(
        f"/api/projects/{project_in_db}/reports/2026-05-04/regenerate"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["week_of"] == "2026-05-04"
    assert body["engineer_count"] == 2
    assert "generated" in body["content_excerpt"]
    # Regenerate flag must be passed through as True
    assert captured["called_with"][2] is True


def test_highlights_refresh_calls_engine(client, project_in_db, monkeypatch):
    import app.engines.highlights as hl_mod
    monkeypatch.setattr(
        hl_mod, "run_highlights",
        lambda code, week_of=None: SimpleNamespace(
            success=True, error="", week_of=week_of,
            is_first_week=True, last_week_of=None,
            duration_seconds=0.8,
            highlights_section="First report for this project — no prior week to compare against.",
        ),
    )
    r = client.post(
        f"/api/projects/{project_in_db}/reports/2026-05-04/highlights/refresh"
    )
    body = r.json()
    assert r.status_code == 200
    assert body["success"] is True
    assert body["is_first_week"] is True
    assert "First report" in body["highlights_excerpt"]


# ------------------------------------------------------------------------
# /admin
# ------------------------------------------------------------------------

def test_admin_config_excludes_secrets(client):
    r = client.get("/admin/config")
    assert r.status_code == 200
    body = r.json()
    # Things present
    assert "llm_provider" in body
    assert "scheduler_daily_status_hour" in body
    assert "reminders_hours_before_cutoff" in body
    # Things absent (sanity — these are NOT in AdminConfigSafe by design)
    assert "token" not in body
    assert "api_key" not in body
    assert "smtp_host" not in body


def test_admin_logs_ai_computes_empty(client):
    r = client.get("/admin/logs/ai-computes")
    assert r.status_code == 200
    assert r.json() == []


def test_admin_logs_ai_computes_returns_seeded(client, project_in_db):
    from app.db import session_scope
    from app.models import AIComputeLog
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    with session_scope() as s:
        s.add(AIComputeLog(
            project_id=pid, prompt_name="weekly_aggregation_v1",
            prompt_version="WeeklyAggregation/v1",
            started_at=datetime.utcnow(), finished_at=datetime.utcnow(),
            success_flag=True, llm_mode="ollama",
            response_excerpt="## Accomplishments\n- ok\n",
            error_text="",
        ))
    r = client.get("/admin/logs/ai-computes")
    body = r.json()
    assert len(body) == 1
    assert body[0]["project_code"] == "APITEST"
    assert body[0]["prompt_name"] == "weekly_aggregation_v1"
    assert body[0]["success_flag"] is True


def test_admin_logs_reminders_filter_by_project(client, project_in_db):
    from app.db import session_scope
    from app.models import ReminderLog
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    with session_scope() as s:
        s.add(ReminderLog(
            engineer_knox_id="alice.e", engineer_name="Alice E",
            project_id=pid, week_of=date(2026, 5, 4),
            type="pre_cutoff", sent_at=datetime.utcnow(),
            channel="mock_smtp", status="mocked",
        ))
    r = client.get("/admin/logs/reminders",
                   params={"project_code": project_in_db})
    body = r.json()
    assert len(body) == 1
    assert body[0]["engineer_knox_id"] == "alice.e"
    assert body[0]["type"] == "pre_cutoff"
    assert body[0]["status"] == "mocked"


def test_admin_logs_sync_filter_by_source(client, project_in_db):
    from app.db import session_scope
    from app.models import SyncLog
    from app.registry.projects import get_project_by_code
    pid = get_project_by_code(project_in_db)["id"]
    with session_scope() as s:
        s.add(SyncLog(
            source="jira", project_id=pid,
            started_at=datetime.utcnow(), finished_at=datetime.utcnow(),
            success_flag=True,
        ))
        s.add(SyncLog(
            source="confluence", project_id=pid,
            started_at=datetime.utcnow(), finished_at=datetime.utcnow(),
            success_flag=False, error_text="429",
        ))
    r = client.get("/admin/logs/sync", params={"source": "confluence"})
    body = r.json()
    assert len(body) == 1
    assert body[0]["source"] == "confluence"
    assert body[0]["success_flag"] is False


def test_admin_scheduler_jobs_empty_when_not_running(client):
    """We stub start_scheduler in the fixture, so get_scheduler() returns None."""
    r = client.get("/admin/scheduler/jobs")
    assert r.status_code == 200
    assert r.json() == []
