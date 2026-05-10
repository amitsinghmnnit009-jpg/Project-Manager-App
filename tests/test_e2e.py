"""End-to-end integration test (Step 12).

Wires real engines + real DB + real scheduler-build + the public REST API
together; mocks ONLY the external boundaries (LLM, JIRA, Confluence). Proves
the pieces fit when joined up — distinct from the per-component tests in
test_aggregation_engine, test_highlights_engine, test_status_engine, etc.

Scenario: simulate one project's full weekly cycle:
  1. Project synced into DB (via fixture)
  2. Aggregation runs for week N-1 → WeeklyReport row created
  3. Aggregation runs for week N → WeeklyReport row created
  4. Highlights runs for week N → uses week N-1 from DB → spliced into report
  5. Status compute runs → reads recent reports as one of its inputs
  6. Pre + post-cutoff reminders dispatched → ReminderLog rows + JSONL
  7. Public API surfaces everything correctly
  8. Admin API shows the AIComputeLog + ReminderLog activity

Same fixture pattern as test_api.py + the engine suites: in-memory SQLite
with StaticPool, scheduler.start_scheduler stubbed (we don't want a
background thread firing during the test).
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

WEEK_PRIOR = date(2026, 4, 27)
WEEK_CURRENT = date(2026, 5, 4)


@pytest.fixture
def fresh_db(monkeypatch):
    import app.db as db_mod
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
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
            code="E2ETEST", name="E2E Test Project", type="firmware",
            description="Full-pipeline integration test",
            owning_tl="TL One", owning_pgm="PGM One",
            jira_project_key="E2EKEY",
            confluence_milestones_url="https://confluence.example.com/m",
            confluence_fr_url="https://confluence.example.com/fr",
            confluence_extra_pages_json=[],
            issue_types_json=["Task", "Bug"],
            chronic_threshold=3, holiday_calendar_id="default",
            weekly_cutoff="Mon 13:00", week_boundary="monday", state="active",
        ))
    return "E2ETEST"


@pytest.fixture
def fake_engineers(tmp_path, monkeypatch):
    """Two engineers assigned to E2ETEST. Same pattern as the engine tests."""
    import app.config as config_mod
    from app.registry import engineers as engineers_mod
    real = config_mod.get_config()
    original = real.engineers.mapping_file
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({
        "engineers": [
            {"name": "Alice E", "knox_id": "alice.e"},
            {"name": "Bob C",   "knox_id": "bob.c"},
        ],
        "assignments": [
            {"knox_id": "alice.e", "projects": ["E2ETEST"]},
            {"knox_id": "bob.c",   "projects": ["E2ETEST"]},
        ],
    }), encoding="utf-8")
    real.engineers.mapping_file = str(path)
    engineers_mod.load_engineer_mapping.cache_clear()
    yield path
    real.engineers.mapping_file = original
    engineers_mod.load_engineer_mapping.cache_clear()


@pytest.fixture
def temp_email_log(tmp_path):
    """Per-test mock email log so reminder asserts see only this test's sends."""
    import app.config as config_mod
    real = config_mod.get_config()
    original = real.email.mock_log_path
    path = tmp_path / "sent_emails.jsonl"
    real.email.mock_log_path = str(path)
    yield path
    real.email.mock_log_path = original


@pytest.fixture
def mock_externals(monkeypatch):
    """Stub LLM + JIRA + Confluence at every module that imports them.

    LLM:
      - aggregation: returns a Markdown report stub including the section
        names so the highlights splicer can find the Highlights heading
      - highlights: returns four-category bullets
      - status: returns a valid Prompt-3 JSON object

    JIRA:
      - collect_engineer_activity: returns one record per engineer per task
      - get_project_snapshot: returns canned counts + recent_activity

    Confluence:
      - get_page_by_url + parse_milestones_page / parse_fr_page: return
        a tiny milestones page + an FR text blob
    """
    from app.clients.jira_client import (
        JiraEngineerActivity, JiraProjectSnapshot, ActivityRecord,
    )
    from app.clients.confluence_client import (
        ProjectPageContent, MilestoneRow,
    )

    # --- LLM dispatcher -------------------------------------------------
    AGGREGATION_OUTPUT = (
        "## Accomplishments\n"
        "- Implemented retry-on-timeout for the storage write path\n\n"
        "## In-progress / Ongoing work\n"
        "- Wear-levelling v3 — performance tuning ongoing\n\n"
        "## Risks and Blockers\n"
        "- Vendor SDK headers still pending\n\n"
        "## Next-week plan\n"
        "- Complete vendor SDK integration once headers arrive\n\n"
        "## Highlights / Things to Watch\n\n"
    )
    HIGHLIGHTS_OUTPUT = (
        "### Missed commitments\n"
        "- (none)\n\n"
        "### Carried-over risks/blockers\n"
        "- Vendor SDK headers still pending (also present last week)\n\n"
        "### Newly raised risks/blockers\n"
        "- (none)\n\n"
        "### Notably absent items\n"
        "- (none)\n"
    )
    STATUS_JSON = json.dumps({
        "overall_health": "Amber",
        "schedule_status": "Slipping",
        "completion_pct": 33,
        "milestones": [
            {"name": "M1 — Vendor SDK", "planned_date": "2026-05-15",
             "tl_declared_status": "In-progress",
             "ai_verification": "NotApplicable",
             "evidence": "TL declaration accepted as-is — no verification required for non-Done status"},
        ],
        "rationale": (
            "Milestone M1 is overdue 5 days; vendor SDK blocker present "
            "in two recent weekly reports — slipping but not delayed."
        ),
        "confidence": "Medium",
        "evidence_cited": ["Milestone M1 — Vendor SDK", "Weekly report 2026-W18", "Weekly report 2026-W19"],
    })

    class StubLLM:
        mode = "ollama"
        def complete(self, sys_p, user_p, *, json_output=False, **kw):
            # Route by what's in the system prompt — distinct enough between Prompts 1/2/3
            if "consolidated weekly project status reports" in sys_p.lower():
                text = AGGREGATION_OUTPUT
            elif "missed commitments" in sys_p.lower() and "highlights" in sys_p.lower():
                text = HIGHLIGHTS_OUTPUT
            elif json_output:
                text = STATUS_JSON
            else:
                text = "[stub LLM output]"
            return SimpleNamespace(
                text=text, model="stub-model",
                duration_seconds=0.5, prompt_tokens=200, completion_tokens=120,
            )

    import app.engines.aggregation as agg_mod
    import app.engines.highlights as hl_mod
    import app.engines.status as status_mod
    monkeypatch.setattr(agg_mod, "get_llm_client", lambda: StubLLM())
    monkeypatch.setattr(hl_mod, "get_llm_client", lambda: StubLLM())
    monkeypatch.setattr(status_mod, "get_llm_client", lambda: StubLLM())

    # --- JIRA stub ------------------------------------------------------
    def _make_activity(week_of):
        rec_alice = ActivityRecord(
            task_id="E2EKEY-1", task_title="Vendor SDK integration",
            task_status="In Progress", task_assignee="Alice E",
            activity_kind="comment", author_name="Alice E",
            author_id="alice.e",
            timestamp=f"{week_of.isoformat()}T10:00:00.000+0530",
            detail="reduced retry count to 1", url="https://jira.example/browse/E2EKEY-1",
        )
        rec_bob = ActivityRecord(
            task_id="E2EKEY-2", task_title="Wear-levelling v3 tuning",
            task_status="In Progress", task_assignee="Bob C",
            activity_kind="worklog", author_name="Bob C",
            author_id="bob.c",
            timestamp=f"{week_of.isoformat()}T11:00:00.000+0530",
            detail="2h", url="https://jira.example/browse/E2EKEY-2",
        )
        return JiraEngineerActivity(
            project_key="E2EKEY", week_of=week_of,
            by_engineer={"alice.e": [rec_alice], "bob.c": [rec_bob]},
            unmapped_authors=[], lookup_keys_knox=[], lookup_keys_name=[],
        )

    class StubJira:
        base = "https://jira.example.local"
        def collect_engineer_activity(self, key, week, engineers, types=None):
            return _make_activity(week)
        def get_project_snapshot(self, key, types=None):
            return JiraProjectSnapshot(
                project_key=key, snapshot_at=datetime.utcnow(),
                total_tasks=12,
                by_status={"In Progress": 5, "Done": 4, "Open": 3},
                overdue_count=1, stale_count=2,
                recent_activity=[
                    {"id": "E2EKEY-1", "title": "Vendor SDK integration",
                     "status": "In Progress", "last_activity": "2026-05-09T10:00:00+05:30"},
                ],
            )

    import app.clients
    import app.notifications as notif_mod
    monkeypatch.setattr(agg_mod, "get_jira_client", lambda: StubJira())
    monkeypatch.setattr(status_mod, "get_jira_client", lambda: StubJira())
    monkeypatch.setattr(notif_mod, "get_jira_client", lambda: StubJira())
    monkeypatch.setattr(app.clients, "get_jira_client", lambda: StubJira())

    # --- Confluence stub ------------------------------------------------
    ms_page = ProjectPageContent(
        title="E2E Project — Milestones",
        overview="A test firmware project for the E2E suite.",
        milestones=[
            MilestoneRow(name="M1 — Vendor SDK", planned_date="2026-05-15",
                         priority="P1", status="In-progress",
                         dependency="", description="Integrate vendor SDK", remark=""),
            MilestoneRow(name="M2 — Beta release", planned_date="2026-08-01",
                         priority="P1", status="Pending",
                         dependency="M1", description="Beta build", remark=""),
        ],
        functional_requirements="",
    )
    fr_page = ProjectPageContent(
        title="E2E Project — Functional Requirements",
        overview="", milestones=[],
        functional_requirements="FR-1: Boot reliability\nFR-2: Wear-levelling v3",
    )

    class StubConfluence:
        def get_page_by_url(self, url):
            return {"_url": url}
        # Module-level helpers parse_milestones_page / parse_fr_page are
        # called as ConfluenceClient.parse_*(page) — patch the class methods.

    import app.clients.confluence_client as conf_mod
    monkeypatch.setattr(status_mod, "get_confluence_client", lambda: StubConfluence())
    monkeypatch.setattr(
        conf_mod.ConfluenceClient, "parse_milestones_page",
        staticmethod(lambda page: ms_page),
    )
    monkeypatch.setattr(
        conf_mod.ConfluenceClient, "parse_fr_page",
        staticmethod(lambda page: fr_page),
    )


@pytest.fixture
def client(fresh_db, monkeypatch):
    """Same TestClient pattern as test_api.py — stub scheduler/sync/engineers
    in lifespan so the test process doesn't start a real background thread."""
    import app.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "start_scheduler", lambda: None)
    monkeypatch.setattr(sched_mod, "stop_scheduler", lambda: None)

    import app.registry.projects as proj_reg
    monkeypatch.setattr(
        proj_reg, "sync_projects_from_config",
        lambda: {"created_count": 0, "updated_count": 0,
                 "deleted_count": 0, "warnings": []},
    )

    from fastapi.testclient import TestClient
    from app.api.main import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c


# ------------------------------------------------------------------------
# E2E happy path
# ------------------------------------------------------------------------

def test_e2e_full_weekly_cycle(
    client, project_in_db, fake_engineers, temp_email_log, mock_externals,
):
    """Aggregation (prior + current) → Highlights → Status → API surfacing.

    Exercises every engine + the API in one test. Each assertion is a
    waypoint along the pipeline — if one fails, the failure points exactly
    at the broken link.
    """
    # ---- Step 1: aggregate prior week ---------------------------------
    from app.engines.aggregation import run_weekly_aggregation
    res_prior = run_weekly_aggregation(project_in_db, week_of=WEEK_PRIOR)
    assert res_prior.success, f"prior-week aggregation failed: {res_prior.error}"
    assert res_prior.is_regeneration is False
    assert res_prior.engineer_count == 2
    assert "## Accomplishments" in res_prior.content_markdown

    # ---- Step 2: aggregate current week --------------------------------
    res_current = run_weekly_aggregation(project_in_db, week_of=WEEK_CURRENT)
    assert res_current.success, f"current-week aggregation failed: {res_current.error}"
    assert res_current.is_regeneration is False
    assert "## Highlights / Things to Watch" in res_current.content_markdown
    # Highlights section must be EMPTY at this point — Step 3 fills it in
    assert "Carried-over" not in res_current.content_markdown

    # ---- Step 3: highlights for current week (uses prior week from DB)
    from app.engines.highlights import run_highlights
    hl = run_highlights(project_in_db, week_of=WEEK_CURRENT)
    assert hl.success, f"highlights failed: {hl.error}"
    assert hl.is_first_week is False, "should not be first week — prior was seeded"
    assert hl.last_week_of == WEEK_PRIOR
    assert "Carried-over risks/blockers" in hl.highlights_section

    # ---- Step 4: status compute uses recent weekly reports -------------
    from app.engines.status import run_status_compute
    st = run_status_compute(project_in_db)
    assert st.success, f"status compute failed: {st.error}"
    parsed = st.parsed
    assert parsed["overall_health"] == "Amber"
    assert parsed["schedule_status"] == "Slipping"
    assert parsed["completion_pct"] == 33
    assert parsed["confidence"] == "Medium"
    assert len(parsed["milestones"]) == 1
    assert parsed["milestones"][0]["ai_verification"] == "NotApplicable"
    assert st.changed is False  # first compute → no history (per FR §A.6.1)

    # ---- Step 5: reminders dispatched ----------------------------------
    from app.notifications import run_pre_cutoff_reminders, run_post_cutoff_reminders
    pre = run_pre_cutoff_reminders(project_in_db, week_of=WEEK_CURRENT)
    assert pre.success
    assert pre.engineers_targeted == 2
    assert len(pre.sent) == 2

    # Both engineers had activity → post-cutoff finds nobody missing
    post = run_post_cutoff_reminders(project_in_db, week_of=WEEK_CURRENT)
    assert post.success
    assert post.engineers_targeted == 0
    assert post.sent == []

    # ---- Step 6: PUBLIC API surfaces everything ------------------------
    # Projects
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert any(p["code"] == project_in_db for p in r.json())

    # Status
    r = client.get(f"/api/projects/{project_in_db}/status")
    body = r.json()
    assert body["overall_health"] == "Amber"
    assert body["schedule_status"] == "Slipping"
    assert body["completion_pct"] == 33

    # Reports — list shows both weeks newest-first; current week has highlights
    r = client.get(f"/api/projects/{project_in_db}/reports")
    body = r.json()
    weeks = [w["week_of"] for w in body]
    assert weeks == [WEEK_CURRENT.isoformat(), WEEK_PRIOR.isoformat()]
    by_week = {w["week_of"]: w for w in body}
    assert by_week[WEEK_CURRENT.isoformat()]["has_highlights"] is True
    assert by_week[WEEK_PRIOR.isoformat()]["has_highlights"] is False

    # Latest report has the spliced highlights
    r = client.get(f"/api/projects/{project_in_db}/reports/latest")
    body = r.json()
    assert body["week_of"] == WEEK_CURRENT.isoformat()
    assert "Carried-over risks/blockers" in body["content_markdown"]

    # Specific week
    r = client.get(f"/api/projects/{project_in_db}/reports/{WEEK_PRIOR}")
    assert r.status_code == 200
    assert r.json()["week_of"] == WEEK_PRIOR.isoformat()

    # ---- Step 7: ADMIN API surfaces engine + reminder activity ---------
    r = client.get("/admin/logs/ai-computes")
    body = r.json()
    # Three engine runs on the current week + one prior aggregation:
    # aggregation ×2, highlights ×1, status ×1 = 4 successful AIComputeLog rows
    assert len(body) >= 4
    success_rows = [row for row in body if row["success_flag"]]
    assert len(success_rows) == len(body), "All engines should have succeeded"
    # Project code joined onto every row
    assert all(row["project_code"] == project_in_db for row in body)

    r = client.get("/admin/logs/reminders", params={"project_code": project_in_db})
    body = r.json()
    # 2 pre-cutoff sends; 0 post-cutoff (everyone was active) = 2 rows
    assert len(body) == 2
    types = sorted(row["type"] for row in body)
    assert types == ["pre_cutoff", "pre_cutoff"]
    assert all(row["status"] == "mocked" for row in body)


def test_e2e_status_history_appended_on_change(
    client, project_in_db, fake_engineers, temp_email_log, mock_externals, monkeypatch,
):
    """Two consecutive status computes with DIFFERENT outputs must produce
    one ProjectStatus row (upserted) AND one ProjectStatusHistory row
    (appended on change). Verified end-to-end via the public API."""
    from app.engines.status import run_status_compute
    from app.engines import status as status_mod

    # First compute → seed (uses Amber/Slipping/33% from the default mock)
    st1 = run_status_compute(project_in_db)
    assert st1.success
    assert st1.changed is False  # first ever — no prior to diff against

    # Swap the LLM stub to return Green/OnTrack for the SECOND compute.
    GREEN_JSON = json.dumps({
        "overall_health": "Green", "schedule_status": "OnTrack",
        "completion_pct": 65, "confidence": "High",
        "milestones": [{
            "name": "M1 — Vendor SDK", "planned_date": "2026-05-15",
            "tl_declared_status": "Done", "ai_verification": "Verified",
            "evidence": "Vendor SDK epic closed; tasks all Done",
        }],
        "rationale": "Vendor SDK milestone Done; nothing overdue.",
        "evidence_cited": ["Milestone M1"],
    })
    class GreenLLM:
        mode = "ollama"
        def complete(self, *a, **kw):
            return SimpleNamespace(text=GREEN_JSON, model="stub",
                                   duration_seconds=0.4,
                                   prompt_tokens=200, completion_tokens=80)
    monkeypatch.setattr(status_mod, "get_llm_client", lambda: GreenLLM())

    st2 = run_status_compute(project_in_db)
    assert st2.success
    assert st2.changed is True  # health/schedule/% all changed

    # Public API: current status reflects the Green compute
    r = client.get(f"/api/projects/{project_in_db}/status")
    assert r.json()["overall_health"] == "Green"
    assert r.json()["completion_pct"] == 65

    # History API: exactly one entry (the Amber→Green transition)
    r = client.get(f"/api/projects/{project_in_db}/status/history")
    body = r.json()
    assert len(body) == 1
    assert body[0]["prior_health"] == "Amber"
    assert body[0]["new_health"] == "Green"
    assert body[0]["new_completion_pct"] == 65


def test_e2e_post_cutoff_reminder_targets_only_missing(
    client, project_in_db, fake_engineers, temp_email_log, monkeypatch,
):
    """Bob_c had NO activity for the week. Pre-cutoff emails ALL (alice + bob).
    Post-cutoff emails ONLY bob. Verified via /admin/logs/reminders."""
    from app.clients.jira_client import JiraEngineerActivity, ActivityRecord
    from app.notifications import run_pre_cutoff_reminders, run_post_cutoff_reminders

    # Stub JIRA so only alice has activity
    rec = ActivityRecord(
        task_id="E2EKEY-1", task_title="x", task_status="In Progress",
        task_assignee="Alice E", activity_kind="comment",
        author_name="Alice E", author_id="alice.e",
        timestamp="2026-05-08T10:00:00.000+0530", detail="ok",
        url="https://jira.example/browse/E2EKEY-1",
    )
    class StubJira:
        def collect_engineer_activity(self, key, week, engineers, types=None):
            return JiraEngineerActivity(
                project_key=key, week_of=week,
                by_engineer={"alice.e": [rec]},
                unmapped_authors=[], lookup_keys_knox=[], lookup_keys_name=[],
            )
    import app.notifications as notif_mod
    monkeypatch.setattr(notif_mod, "get_jira_client", lambda: StubJira())

    pre = run_pre_cutoff_reminders(project_in_db, week_of=WEEK_CURRENT)
    assert pre.engineers_targeted == 2

    post = run_post_cutoff_reminders(project_in_db, week_of=WEEK_CURRENT)
    assert post.engineers_targeted == 1
    assert post.sent[0].knox_id == "bob.c"

    # Admin API confirms
    r = client.get("/admin/logs/reminders", params={"project_code": project_in_db})
    body = r.json()
    assert len(body) == 3   # 2 pre + 1 post
    # The single post-cutoff must be for bob
    post_rows = [row for row in body if row["type"] == "post_cutoff"]
    assert len(post_rows) == 1
    assert post_rows[0]["engineer_knox_id"] == "bob.c"


def test_e2e_scheduler_jobs_visible_via_admin_api(
    client, project_in_db, monkeypatch,
):
    """Build a real scheduler from the DB project, register it as the
    module-level _scheduler, and verify /admin/scheduler/jobs surfaces
    all four expected jobs."""
    import app.scheduler as sched_mod
    from app.config import get_config

    sched = sched_mod._build_scheduler(get_config())
    monkeypatch.setattr(sched_mod, "_scheduler", sched)
    try:
        r = client.get("/admin/scheduler/jobs")
        assert r.status_code == 200
        ids = sorted(j["id"] for j in r.json())
        assert ids == [
            f"reminder-post:{project_in_db}",
            f"reminder-pre:{project_in_db}",
            f"status:{project_in_db}",
            f"weekly:{project_in_db}",
        ]
        # Trigger strings are inspectable
        for job in r.json():
            assert "cron" in job["trigger"].lower()
    finally:
        # Clean up — autouse fixture in test_scheduler also resets but we
        # don't run that here, so be explicit.
        monkeypatch.setattr(sched_mod, "_scheduler", None)
