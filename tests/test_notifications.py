"""Tests for app.notifications — Step 10.

Mocks at the same boundaries as the engine test suites:
  - get_jira_client() → stub with collect_engineer_activity()
  - cfg.engineers.mapping_file → temp JSON file
  - cfg.email.mock_log_path → temp JSONL file (so each test is isolated)

Same fresh in-memory SQLite + StaticPool fixture as the rest of the engine
suites so DB persistence is real but test-isolated.
"""
from __future__ import annotations
import json
import pytest
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace


# ------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------

@pytest.fixture
def fresh_db(monkeypatch):
    """Per-test in-memory SQLite — same canonical pattern as other engine tests."""
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
            code="TESTNOT",
            name="Test Notifications Project",
            type="firmware",
            description="",
            jira_project_key="TESTKEY",
            confluence_milestones_url="https://confluence.example.com/m",
            confluence_fr_url="https://confluence.example.com/fr",
            confluence_extra_pages_json=[],
            issue_types_json=["Task"],
            chronic_threshold=3,
            holiday_calendar_id="default",
        ))
    return "TESTNOT"


@pytest.fixture
def fake_engineers(tmp_path, monkeypatch):
    """Point cfg.engineers.mapping_file at a temp file with three engineers
    assigned to project TESTNOT. Restores on teardown + clears lru_cache."""
    import app.config as config_mod
    from app.registry import engineers as engineers_mod

    real = config_mod.get_config()
    original_path = real.engineers.mapping_file

    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({
        "engineers": [
            {"name": "Alice E", "knox_id": "alice.e"},
            {"name": "Bob C", "knox_id": "bob.c"},
            {"name": "Carol D", "knox_id": "carol.d"},
        ],
        "assignments": [
            {"knox_id": "alice.e", "projects": ["TESTNOT"]},
            {"knox_id": "bob.c", "projects": ["TESTNOT"]},
            {"knox_id": "carol.d", "projects": ["TESTNOT"]},
        ],
    }), encoding="utf-8")

    real.engineers.mapping_file = str(path)
    engineers_mod.load_engineer_mapping.cache_clear()

    yield path

    real.engineers.mapping_file = original_path
    engineers_mod.load_engineer_mapping.cache_clear()


@pytest.fixture
def temp_email_log(tmp_path, monkeypatch):
    """Redirect cfg.email.mock_log_path at a temp file so each test is
    isolated and we can inspect what would have been sent."""
    import app.config as config_mod
    real = config_mod.get_config()
    original = real.email.mock_log_path
    path = tmp_path / "sent_emails.jsonl"
    real.email.mock_log_path = str(path)
    yield path
    real.email.mock_log_path = original


def _stub_jira(monkeypatch, *, by_engineer=None, raises=None):
    """Patch get_jira_client in app.notifications."""
    import app.notifications as notif_mod
    from app.clients.jira_client import JiraEngineerActivity

    class StubJira:
        def collect_engineer_activity(self, key, week, engineers, types,
                                      exclude_labels=None):
            if raises:
                raise raises
            return JiraEngineerActivity(
                project_key=key, week_of=week,
                by_engineer=by_engineer or {},
                unmapped_authors=[],
                lookup_keys_knox=[], lookup_keys_name=[],
            )

    monkeypatch.setattr(notif_mod, "get_jira_client", lambda: StubJira())


WEEK_OF = date(2026, 5, 4)


# ------------------------------------------------------------------------
# send_engineer_reminder — single-recipient sender
# ------------------------------------------------------------------------

def test_send_writes_jsonl_and_db_in_mock_mode(project_in_db, temp_email_log):
    from app.notifications import send_engineer_reminder
    from app.models import ReminderLog
    from app.db import session_scope

    sent = send_engineer_reminder(
        knox_id="alice.e", name="Alice E",
        project_code=project_in_db, week_of="2026-05-04",
        type="pre_cutoff",
    )

    assert sent.status == "mocked"
    assert sent.channel == "mock_smtp"
    assert sent.error == ""
    assert "Alice E" in sent.body
    assert "TESTNOT" in sent.subject

    # JSONL line written
    assert temp_email_log.exists()
    lines = temp_email_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["to_knox_id"] == "alice.e"
    assert rec["to_name"] == "Alice E"
    assert rec["type"] == "pre_cutoff"
    assert rec["channel"] == "mock_smtp"

    # DB row written
    with session_scope() as s:
        rows = s.execute(select(ReminderLog)).scalars().all()
        assert len(rows) == 1
        assert rows[0].engineer_knox_id == "alice.e"
        assert rows[0].engineer_name == "Alice E"
        assert rows[0].type == "pre_cutoff"
        assert rows[0].status == "mocked"
        assert rows[0].channel == "mock_smtp"
        assert rows[0].project_id is not None


def test_send_subject_differs_for_pre_vs_post(project_in_db, temp_email_log):
    from app.notifications import send_engineer_reminder
    pre = send_engineer_reminder(
        "alice.e", "Alice E", project_in_db, "2026-05-04", "pre_cutoff",
    )
    post = send_engineer_reminder(
        "alice.e", "Alice E", project_in_db, "2026-05-04", "post_cutoff",
    )
    assert "Reminder" in pre.subject
    assert "Action Required" in post.subject
    assert pre.subject != post.subject


def test_send_skips_db_when_project_not_resolvable(temp_email_log, fresh_db):
    """No project in DB matching the code → JSONL still written, DB row skipped."""
    from app.notifications import send_engineer_reminder
    from app.models import ReminderLog
    from app.db import session_scope

    sent = send_engineer_reminder(
        "alice.e", "Alice E", "GHOST_PROJECT",
        "2026-05-04", "pre_cutoff",
    )
    assert sent.status == "mocked"   # JSONL succeeded
    assert temp_email_log.exists()

    with session_scope() as s:
        rows = s.execute(select(ReminderLog)).scalars().all()
        assert rows == []   # no DB row for unresolvable project


# ------------------------------------------------------------------------
# run_pre_cutoff_reminders
# ------------------------------------------------------------------------

def test_pre_reminders_sends_to_all_engineers(
    project_in_db, fake_engineers, temp_email_log,
):
    from app.notifications import run_pre_cutoff_reminders
    from app.models import ReminderLog
    from app.db import session_scope

    res = run_pre_cutoff_reminders(project_in_db, week_of=WEEK_OF)

    assert res.success is True
    assert res.engineers_targeted == 3
    assert len(res.sent) == 3
    assert res.failed_knox_ids == []
    assert res.type == "pre_cutoff"
    knox_set = {s.knox_id for s in res.sent}
    assert knox_set == {"alice.e", "bob.c", "carol.d"}

    # 3 lines in JSONL
    lines = temp_email_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3

    # 3 ReminderLog rows
    with session_scope() as s:
        rows = s.execute(select(ReminderLog)).scalars().all()
        assert len(rows) == 3
        assert all(r.type == "pre_cutoff" for r in rows)


def test_pre_reminders_project_not_found(fresh_db, fake_engineers, temp_email_log):
    from app.notifications import run_pre_cutoff_reminders
    res = run_pre_cutoff_reminders("GHOST", week_of=WEEK_OF)
    assert res.success is False
    assert "not found" in res.error.lower()
    assert res.sent == []
    assert not temp_email_log.exists() or temp_email_log.read_text() == ""


def test_pre_reminders_no_engineers_assigned(
    project_in_db, tmp_path, monkeypatch, temp_email_log,
):
    """Empty mapping for the project → success with engineers_targeted=0."""
    import app.config as config_mod
    from app.registry import engineers as engineers_mod
    real = config_mod.get_config()
    orig_path = real.engineers.mapping_file
    empty_path = tmp_path / "empty.json"
    empty_path.write_text(json.dumps({
        "engineers": [], "assignments": [],
    }), encoding="utf-8")
    real.engineers.mapping_file = str(empty_path)
    engineers_mod.load_engineer_mapping.cache_clear()

    try:
        from app.notifications import run_pre_cutoff_reminders
        res = run_pre_cutoff_reminders(project_in_db, week_of=WEEK_OF)
        assert res.success is True
        assert res.engineers_targeted == 0
        assert res.sent == []
    finally:
        real.engineers.mapping_file = orig_path
        engineers_mod.load_engineer_mapping.cache_clear()


# ------------------------------------------------------------------------
# run_post_cutoff_reminders
# ------------------------------------------------------------------------

def test_post_reminders_only_missing_get_emails(
    project_in_db, fake_engineers, temp_email_log, monkeypatch,
):
    """Three engineers assigned. JIRA shows alice.e + bob.c had activity.
    Only carol.d (no recorded activity) gets a reminder."""
    # alice.e + bob.c have activity (any value passes — we just check key presence)
    _stub_jira(monkeypatch, by_engineer={
        "alice.e": ["dummy_record"],
        "bob.c": ["dummy_record"],
    })

    from app.notifications import run_post_cutoff_reminders
    res = run_post_cutoff_reminders(project_in_db, week_of=WEEK_OF)

    assert res.success is True
    assert res.engineers_targeted == 1
    assert len(res.sent) == 1
    assert res.sent[0].knox_id == "carol.d"
    assert res.sent[0].type == "post_cutoff"

    # JSONL has exactly one entry
    lines = temp_email_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["to_knox_id"] == "carol.d"
    assert rec["type"] == "post_cutoff"


def test_post_reminders_all_engineers_active_no_emails(
    project_in_db, fake_engineers, temp_email_log, monkeypatch,
):
    _stub_jira(monkeypatch, by_engineer={
        "alice.e": ["x"], "bob.c": ["x"], "carol.d": ["x"],
    })
    from app.notifications import run_post_cutoff_reminders
    res = run_post_cutoff_reminders(project_in_db, week_of=WEEK_OF)
    assert res.success is True
    assert res.engineers_targeted == 0
    assert res.sent == []
    assert not temp_email_log.exists() or temp_email_log.read_text() == ""


def test_post_reminders_all_missing_emails_all(
    project_in_db, fake_engineers, temp_email_log, monkeypatch,
):
    _stub_jira(monkeypatch, by_engineer={})  # nobody had activity
    from app.notifications import run_post_cutoff_reminders
    res = run_post_cutoff_reminders(project_in_db, week_of=WEEK_OF)
    assert res.success is True
    assert res.engineers_targeted == 3
    assert len(res.sent) == 3
    assert {s.knox_id for s in res.sent} == {"alice.e", "bob.c", "carol.d"}


def test_post_reminders_jira_error_fails_run(
    project_in_db, fake_engineers, temp_email_log, monkeypatch,
):
    _stub_jira(monkeypatch, raises=ConnectionError("jira 503"))
    from app.notifications import run_post_cutoff_reminders
    res = run_post_cutoff_reminders(project_in_db, week_of=WEEK_OF)
    assert res.success is False
    assert "JIRA fetch failed" in res.error
    assert res.sent == []


def test_post_reminders_default_week_is_current(
    project_in_db, fake_engineers, temp_email_log, monkeypatch,
):
    """Omitting week_of falls back to the current IST week_of()."""
    from app.utils.dates import week_of as compute_week_of
    _stub_jira(monkeypatch, by_engineer={"alice.e": ["x"], "bob.c": ["x"]})
    from app.notifications import run_post_cutoff_reminders
    res = run_post_cutoff_reminders(project_in_db)
    assert res.success is True
    assert res.week_of == compute_week_of()
