"""Tests for app.scheduler — Step 9.

Inspects APScheduler jobs WITHOUT actually starting the background thread.
`_build_scheduler()` is the seam: it constructs a fully-configured scheduler
with all jobs registered but never calls `.start()`.

The scheduled job bodies (`_run_status_safe`, `_run_weekly_pipeline_safe`)
are tested by mocking the engine functions and calling the wrappers
directly — no APScheduler involvement needed there.
"""
from __future__ import annotations
import pytest
from datetime import time
from types import SimpleNamespace


# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------

def _make_project(
    code="P1",
    weekly_cutoff="Mon 13:00",
    recompute_cadence=None,
):
    """Build a minimal project dict matching what list_projects() returns."""
    return {
        "id": 1, "code": code, "name": f"{code} name", "type": "general",
        "description": "", "owning_tl": "", "owning_pgm": "",
        "start_date": None, "planned_end_date": None,
        "confluence_milestones_url": "https://x/m",
        "confluence_fr_url": "https://x/fr",
        "confluence_extra_pages": [],
        "jira_project_key": code,
        "weekly_cutoff": weekly_cutoff,
        "week_boundary": "monday",
        "recompute_cadence": recompute_cadence,
        "issue_types": [],
        "chronic_threshold": 3,
        "holiday_calendar_id": "default",
    }


def _stub_list_projects(monkeypatch, projects):
    """Patch app.scheduler.list_projects to return the given list."""
    import app.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "list_projects", lambda: list(projects))


def _real_cfg():
    """Return the real (unmodified) AppConfig — tests only read from it."""
    from app.config import get_config
    return get_config()


# ------------------------------------------------------------------------
# _build_scheduler — job registration
# ------------------------------------------------------------------------

def test_build_scheduler_no_projects(monkeypatch):
    _stub_list_projects(monkeypatch, [])
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(_real_cfg())
    assert sched.get_jobs() == []


def test_build_scheduler_default_cadence_creates_status_and_weekly(monkeypatch):
    _stub_list_projects(monkeypatch, [_make_project(code="ALPHA")])
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(_real_cfg())
    job_ids = sorted(j.id for j in sched.get_jobs())
    assert job_ids == ["status:ALPHA", "weekly:ALPHA"]


def test_build_scheduler_multiple_projects(monkeypatch):
    _stub_list_projects(monkeypatch, [
        _make_project(code="A"),
        _make_project(code="B"),
        _make_project(code="C"),
    ])
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(_real_cfg())
    job_ids = sorted(j.id for j in sched.get_jobs())
    assert job_ids == [
        "status:A", "status:B", "status:C",
        "weekly:A", "weekly:B", "weekly:C",
    ]


def test_status_job_skipped_when_per_project_cadence_manual(monkeypatch):
    _stub_list_projects(monkeypatch, [
        _make_project(code="ALPHA", recompute_cadence="manual"),
    ])
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(_real_cfg())
    job_ids = [j.id for j in sched.get_jobs()]
    assert "status:ALPHA" not in job_ids
    assert "weekly:ALPHA" in job_ids   # weekly still scheduled


def test_status_job_hourly_cadence_uses_top_of_hour(monkeypatch):
    _stub_list_projects(monkeypatch, [
        _make_project(code="ALPHA", recompute_cadence="hourly"),
    ])
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(_real_cfg())
    status_job = sched.get_job("status:ALPHA")
    assert status_job is not None
    # CronTrigger(minute=0) → triggers at minute 0 of every hour. Inspect via str().
    assert "minute='0'" in str(status_job.trigger)


def test_status_job_daily_cadence_uses_configured_hour(monkeypatch):
    _stub_list_projects(monkeypatch, [_make_project(code="ALPHA")])
    cfg = _real_cfg()
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(cfg)
    status_job = sched.get_job("status:ALPHA")
    trig_str = str(status_job.trigger)
    assert f"hour='{cfg.scheduler.daily_status_hour}'" in trig_str
    assert "minute='0'" in trig_str


def test_per_project_cadence_overrides_global(monkeypatch):
    """Project cadence=hourly overrides global cadence=daily."""
    _stub_list_projects(monkeypatch, [
        _make_project(code="ALPHA", recompute_cadence="hourly"),
        _make_project(code="BETA"),  # uses global default (daily)
    ])
    cfg = _real_cfg()
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(cfg)

    alpha_str = str(sched.get_job("status:ALPHA").trigger)
    beta_str = str(sched.get_job("status:BETA").trigger)

    # ALPHA hourly: hour wildcard, minute=0
    assert "minute='0'" in alpha_str
    # BETA daily: hour fixed
    assert f"hour='{cfg.scheduler.daily_status_hour}'" in beta_str


def test_weekly_job_applies_offset_to_cutoff(monkeypatch):
    """Cutoff Mon 13:00 + 5min offset → trigger fires Mon 13:05."""
    _stub_list_projects(monkeypatch, [
        _make_project(code="ALPHA", weekly_cutoff="Mon 13:00"),
    ])
    cfg = _real_cfg()
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(cfg)
    weekly_job = sched.get_job("weekly:ALPHA")
    trig_str = str(weekly_job.trigger)
    assert "day_of_week='mon'" in trig_str
    assert "hour='13'" in trig_str
    assert f"minute='{cfg.scheduler.weekly_aggregation_offset_minutes}'" in trig_str


def test_weekly_job_for_friday_cutoff(monkeypatch):
    _stub_list_projects(monkeypatch, [
        _make_project(code="ALPHA", weekly_cutoff="Fri 17:30"),
    ])
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(_real_cfg())
    trig_str = str(sched.get_job("weekly:ALPHA").trigger)
    assert "day_of_week='fri'" in trig_str
    assert "hour='17'" in trig_str
    # 17:30 + 5min = 17:35
    assert "minute='35'" in trig_str


def test_invalid_weekly_cutoff_skips_weekly_but_keeps_status(monkeypatch):
    _stub_list_projects(monkeypatch, [
        _make_project(code="ALPHA", weekly_cutoff="garbage"),
    ])
    from app.scheduler import _build_scheduler
    sched = _build_scheduler(_real_cfg())
    job_ids = [j.id for j in sched.get_jobs()]
    assert "status:ALPHA" in job_ids   # status survives bad weekly config
    assert "weekly:ALPHA" not in job_ids


# ------------------------------------------------------------------------
# start_scheduler / stop_scheduler lifecycle
# ------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_scheduler_global():
    """Ensure the module-global _scheduler is reset between tests."""
    import app.scheduler as sched_mod
    sched_mod._scheduler = None
    yield
    if sched_mod._scheduler is not None:
        try:
            sched_mod._scheduler.shutdown(wait=False)
        except Exception:
            pass
        sched_mod._scheduler = None


def test_start_scheduler_returns_none_when_no_projects(monkeypatch):
    _stub_list_projects(monkeypatch, [])
    from app.scheduler import start_scheduler, get_scheduler
    assert start_scheduler() is None
    assert get_scheduler() is None


def test_start_scheduler_idempotent(monkeypatch):
    _stub_list_projects(monkeypatch, [_make_project(code="ALPHA")])
    from app.scheduler import start_scheduler, get_scheduler

    s1 = start_scheduler()
    s2 = start_scheduler()  # second call must NOT register duplicate jobs
    assert s1 is s2 is get_scheduler()
    job_ids = [j.id for j in s1.get_jobs()]
    assert sorted(job_ids) == ["status:ALPHA", "weekly:ALPHA"]


def test_stop_scheduler_after_start(monkeypatch):
    _stub_list_projects(monkeypatch, [_make_project(code="ALPHA")])
    from app.scheduler import start_scheduler, stop_scheduler, get_scheduler
    start_scheduler()
    assert get_scheduler() is not None
    stop_scheduler()
    assert get_scheduler() is None


def test_stop_scheduler_when_never_started_is_noop():
    from app.scheduler import stop_scheduler, get_scheduler
    assert get_scheduler() is None
    stop_scheduler()  # must not raise
    assert get_scheduler() is None


# ------------------------------------------------------------------------
# _run_status_safe — wraps the engine, never raises
# ------------------------------------------------------------------------

def test_run_status_safe_calls_engine_and_logs_success(monkeypatch):
    captured = {}
    def fake_engine(project_code):
        captured["called_with"] = project_code
        return SimpleNamespace(
            success=True,
            parsed={"overall_health": "Green", "schedule_status": "OnTrack",
                    "completion_pct": 50},
            changed=False,
            duration_seconds=1.2,
            error="",
        )
    import app.engines.status as status_mod
    monkeypatch.setattr(status_mod, "run_status_compute", fake_engine)

    from app.scheduler import _run_status_safe
    _run_status_safe("ALPHA")  # must not raise
    assert captured["called_with"] == "ALPHA"


def test_run_status_safe_handles_engine_failure(monkeypatch):
    import app.engines.status as status_mod
    monkeypatch.setattr(
        status_mod, "run_status_compute",
        lambda code: SimpleNamespace(success=False, error="boom",
                                     parsed=None, changed=None,
                                     duration_seconds=0.0),
    )
    from app.scheduler import _run_status_safe
    _run_status_safe("ALPHA")  # must not raise (engine returned failure, not exception)


def test_run_status_safe_swallows_unexpected_exceptions(monkeypatch):
    """If the engine itself raises (programmer error), the wrapper must
    still not propagate — APScheduler thread must keep running."""
    import app.engines.status as status_mod
    def boom(_):
        raise RuntimeError("genuine programmer error")
    monkeypatch.setattr(status_mod, "run_status_compute", boom)
    from app.scheduler import _run_status_safe
    _run_status_safe("ALPHA")  # must not raise


# ------------------------------------------------------------------------
# _run_weekly_pipeline_safe — sequential aggregation + highlights
# ------------------------------------------------------------------------

def test_weekly_pipeline_runs_highlights_after_successful_aggregation(monkeypatch):
    calls = []

    def fake_agg(code, week_of=None):
        calls.append(("agg", code, week_of))
        return SimpleNamespace(
            success=True, error="", is_regeneration=False,
            engineer_count=2, activity_records=10, duration_seconds=2.0,
        )

    def fake_hl(code, week_of=None):
        calls.append(("hl", code, week_of))
        return SimpleNamespace(success=True, error="",
                               is_first_week=False, duration_seconds=1.0)

    import app.engines.aggregation as agg_mod
    import app.engines.highlights as hl_mod
    monkeypatch.setattr(agg_mod, "run_weekly_aggregation", fake_agg)
    monkeypatch.setattr(hl_mod, "run_highlights", fake_hl)

    from app.scheduler import _run_weekly_pipeline_safe
    _run_weekly_pipeline_safe("ALPHA")

    assert [c[0] for c in calls] == ["agg", "hl"]
    assert calls[0][1] == "ALPHA"
    assert calls[1][1] == "ALPHA"
    assert calls[0][2] == calls[1][2]  # same week_of passed to both


def test_weekly_pipeline_skips_highlights_when_aggregation_fails(monkeypatch):
    calls = []
    def fake_agg(code, week_of=None):
        calls.append("agg")
        return SimpleNamespace(success=False, error="jira down",
                               is_regeneration=False, engineer_count=0,
                               activity_records=0, duration_seconds=0.5)
    def fake_hl(code, week_of=None):
        calls.append("hl")
        return SimpleNamespace(success=True, error="",
                               is_first_week=False, duration_seconds=1.0)
    import app.engines.aggregation as agg_mod
    import app.engines.highlights as hl_mod
    monkeypatch.setattr(agg_mod, "run_weekly_aggregation", fake_agg)
    monkeypatch.setattr(hl_mod, "run_highlights", fake_hl)

    from app.scheduler import _run_weekly_pipeline_safe
    _run_weekly_pipeline_safe("ALPHA")
    assert calls == ["agg"]   # highlights never invoked


def test_weekly_pipeline_handles_aggregation_crash(monkeypatch):
    """An uncaught exception in aggregation must not propagate or abort
    cleanly without crashing the scheduler thread."""
    import app.engines.aggregation as agg_mod
    def boom(code, week_of=None):
        raise ConnectionError("network exploded")
    monkeypatch.setattr(agg_mod, "run_weekly_aggregation", boom)

    from app.scheduler import _run_weekly_pipeline_safe
    _run_weekly_pipeline_safe("ALPHA")  # must not raise


def test_weekly_pipeline_handles_highlights_crash(monkeypatch):
    """Highlights raising must not propagate after aggregation succeeded."""
    import app.engines.aggregation as agg_mod
    import app.engines.highlights as hl_mod
    monkeypatch.setattr(
        agg_mod, "run_weekly_aggregation",
        lambda code, week_of=None: SimpleNamespace(
            success=True, error="", is_regeneration=False,
            engineer_count=0, activity_records=0, duration_seconds=1.0,
        ),
    )
    def hl_boom(code, week_of=None):
        raise RuntimeError("splice broke")
    monkeypatch.setattr(hl_mod, "run_highlights", hl_boom)

    from app.scheduler import _run_weekly_pipeline_safe
    _run_weekly_pipeline_safe("ALPHA")  # must not raise


def test_weekly_pipeline_continues_when_highlights_returns_failure(monkeypatch):
    """Highlights returning success=False is logged but does not raise.
    Aggregation succeeded so the report exists — partial completion."""
    import app.engines.aggregation as agg_mod
    import app.engines.highlights as hl_mod
    monkeypatch.setattr(
        agg_mod, "run_weekly_aggregation",
        lambda code, week_of=None: SimpleNamespace(
            success=True, error="", is_regeneration=False,
            engineer_count=2, activity_records=5, duration_seconds=1.0,
        ),
    )
    monkeypatch.setattr(
        hl_mod, "run_highlights",
        lambda code, week_of=None: SimpleNamespace(
            success=False, error="LLM timeout",
            is_first_week=False, duration_seconds=0.0,
        ),
    )
    from app.scheduler import _run_weekly_pipeline_safe
    _run_weekly_pipeline_safe("ALPHA")  # must not raise
