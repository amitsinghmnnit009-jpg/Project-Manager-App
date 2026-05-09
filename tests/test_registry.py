"""Tests for app.registry.projects + app.registry.engineers.

Uses a fresh in-memory SQLite per test (via monkeypatch on app.db._engine
and SessionLocal) so the dev DB at ./app.db isn't touched, and tests are
isolated from each other.

For the engineer registry, uses tmp_path to write a synthetic mapping
file and points config.engineers.mapping_file at it for the test only.
"""
from __future__ import annotations
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace


# ------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------

@pytest.fixture
def fresh_db(monkeypatch):
    """Per-test in-memory SQLite. Swaps app.db._engine and SessionLocal."""
    import app.db as db_mod

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    test_session = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, future=True
    )

    monkeypatch.setattr(db_mod, "_engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", test_session)

    # Ensure models are imported so Base.metadata knows every table
    from app import models  # noqa: F401
    db_mod.Base.metadata.create_all(bind=test_engine)

    yield test_engine

    test_engine.dispose()


@pytest.fixture
def fake_config():
    """Use the REAL config object, but allow tests to mutate .projects.
    Restores .projects on teardown.

    Why mutate instead of replace get_config(): app.utils.logging accesses
    cfg.logging.directory and cfg.logging.level when building loggers; the
    registry calls system_log() during sync; if we swap get_config() with a
    SimpleNamespace that lacks `logging`, we crash before any registry code
    even runs. Keeping the real cfg object intact and mutating only the
    fields the test cares about avoids re-deriving the entire AppConfig
    shape in the fixture.
    """
    import app.config as config_mod
    real = config_mod.get_config()
    original_projects = list(real.projects)

    def _project_cfg(**overrides):
        # SimpleNamespace shaped like ProjectConfig — every field
        # _project_config_to_columns() reads must be present.
        # Pydantic v2 ProjectConfig would also work but instantiating it
        # would re-validate on construction; SimpleNamespace just sticks
        # the values on as attributes (duck typing wins downstream).
        defaults = dict(
            code="TEST",
            name="Test Project",
            type="general",
            description="",
            owning_tl="",
            owning_pgm="",
            start_date=None,
            planned_end_date=None,
            jira_project_key="TEST",
            confluence_milestones_url="https://confluence.example.com/m",
            confluence_fr_url="https://confluence.example.com/fr",
            confluence_extra_pages=[],
            weekly_cutoff="Mon 13:00",
            week_boundary="monday",
            recompute_cadence=None,
            issue_types=["Task"],
            chronic_threshold=3,
            holiday_calendar_id="default",
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    yield SimpleNamespace(cfg=real, project=_project_cfg)

    # Restore the original projects list on teardown so other tests / the
    # dev DB don't see whatever this test pushed.
    real.projects = original_projects


@pytest.fixture
def fake_mapping_file(tmp_path):
    """Write a temp engineer mapping file and point cfg.engineers.mapping_file
    at it. Restores the original path on teardown.

    Same pattern as fake_config: mutate one field on the real config rather
    than replacing get_config() wholesale, so logging / database / other
    sections stay valid throughout the test.
    """
    import app.config as config_mod
    from app.registry import engineers as engineers_mod

    real = config_mod.get_config()
    original_path = real.engineers.mapping_file

    path = tmp_path / "mapping.json"

    def _write(data: dict):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # Default content — tests can override via _write()
    _write({
        "engineers": [
            {"name": "Alice Engineer", "knox_id": "alice.eng"},
            {"name": "Bob Coder",      "knox_id": "bob.coder"},
            {"name": "Charlie QA",     "knox_id": "charlie.qa"},
        ],
        "assignments": [
            {"knox_id": "alice.eng",   "projects": ["PROJ_A", "PROJ_B"]},
            {"knox_id": "bob.coder",   "projects": ["PROJ_A"]},
            {"knox_id": "charlie.qa",  "projects": ["PROJ_C"]},
        ],
    })

    real.engineers.mapping_file = str(path)
    engineers_mod.load_engineer_mapping.cache_clear()

    yield SimpleNamespace(path=path, write=_write)

    real.engineers.mapping_file = original_path
    engineers_mod.load_engineer_mapping.cache_clear()


# ------------------------------------------------------------------------
# Project registry — sync + lookups
# ------------------------------------------------------------------------

def test_sync_inserts_new_projects(fresh_db, fake_config):
    fake_config.cfg.projects = [
        fake_config.project(code="PROJ_A", name="Project A"),
        fake_config.project(code="PROJ_B", name="Project B", jira_project_key="JIRA_B"),
    ]
    from app.registry.projects import sync_projects_from_config

    report = sync_projects_from_config()
    assert report["created_count"] == 2
    assert report["updated_count"] == 0
    assert report["total_in_db_after"] == 2
    assert report["stale_codes"] == []


def test_sync_is_idempotent(fresh_db, fake_config):
    fake_config.cfg.projects = [fake_config.project(code="X", name="Project X")]
    from app.registry.projects import sync_projects_from_config

    sync_projects_from_config()
    second = sync_projects_from_config()
    assert second["created_count"] == 0
    assert second["updated_count"] == 1
    assert second["total_in_db_after"] == 1


def test_sync_updates_existing_project(fresh_db, fake_config):
    """Re-running sync after editing config should update the row, not duplicate."""
    fake_config.cfg.projects = [fake_config.project(code="X", name="Old name")]
    from app.registry.projects import sync_projects_from_config, get_project_by_code

    sync_projects_from_config()
    fake_config.cfg.projects = [fake_config.project(code="X", name="New name", description="now with desc")]
    sync_projects_from_config()

    row = get_project_by_code("X")
    assert row is not None
    assert row["name"] == "New name"
    assert row["description"] == "now with desc"


def test_sync_warns_about_stale_codes(fresh_db, fake_config):
    """Project removed from config stays in DB and is reported as stale."""
    fake_config.cfg.projects = [fake_config.project(code="KEEP"), fake_config.project(code="REMOVE")]
    from app.registry.projects import sync_projects_from_config

    sync_projects_from_config()

    # Remove one from config, re-sync
    fake_config.cfg.projects = [fake_config.project(code="KEEP")]
    report = sync_projects_from_config()

    assert report["stale_codes"] == ["REMOVE"]
    assert report["total_in_db_after"] == 2  # both rows remain


def test_sync_parses_dates_correctly(fresh_db, fake_config):
    fake_config.cfg.projects = [
        fake_config.project(
            code="DATED",
            start_date="2026-01-15",
            planned_end_date="2026-12-31",
        )
    ]
    from app.registry.projects import sync_projects_from_config, get_project_by_code

    sync_projects_from_config()
    row = get_project_by_code("DATED")
    assert row["start_date"] == "2026-01-15"
    assert row["planned_end_date"] == "2026-12-31"


def test_sync_persists_extra_pages_and_issue_types(fresh_db, fake_config):
    fake_config.cfg.projects = [
        fake_config.project(
            code="LISTS",
            confluence_extra_pages=["https://x/1", "https://x/2"],
            issue_types=["Task", "Bug", "Story"],
        )
    ]
    from app.registry.projects import sync_projects_from_config, get_project_by_code

    sync_projects_from_config()
    row = get_project_by_code("LISTS")
    assert row["confluence_extra_pages"] == ["https://x/1", "https://x/2"]
    assert row["issue_types"] == ["Task", "Bug", "Story"]


def test_get_project_by_code_returns_none_for_missing(fresh_db, fake_config):
    fake_config.cfg.projects = []
    from app.registry.projects import sync_projects_from_config, get_project_by_code

    sync_projects_from_config()
    assert get_project_by_code("NONEXISTENT") is None


def test_get_project_by_jira_key_finds_via_jira_key(fresh_db, fake_config):
    """code and jira_project_key can differ; lookup by jira_project_key."""
    fake_config.cfg.projects = [
        fake_config.project(code="INTERNAL", jira_project_key="EXTERNAL")
    ]
    from app.registry.projects import sync_projects_from_config, get_project_by_jira_key

    sync_projects_from_config()
    row = get_project_by_jira_key("EXTERNAL")
    assert row is not None
    assert row["code"] == "INTERNAL"


def test_list_projects_returns_all_ordered_by_code(fresh_db, fake_config):
    fake_config.cfg.projects = [
        fake_config.project(code="ZEBRA"),
        fake_config.project(code="ALPHA"),
        fake_config.project(code="MIDDLE"),
    ]
    from app.registry.projects import sync_projects_from_config, list_projects

    sync_projects_from_config()
    rows = list_projects()
    assert [r["code"] for r in rows] == ["ALPHA", "MIDDLE", "ZEBRA"]


# ------------------------------------------------------------------------
# Engineer registry — load + lookups + JIRA matching
# ------------------------------------------------------------------------

def test_load_engineer_mapping_parses_engineers_and_assignments(fake_mapping_file):
    from app.registry.engineers import load_engineer_mapping
    m = load_engineer_mapping()
    assert len(m.engineers) == 3
    assert {e.knox_id for e in m.engineers} == {"alice.eng", "bob.coder", "charlie.qa"}
    assert m.parse_warnings == []


def test_engineers_on_project_returns_assigned(fake_mapping_file):
    from app.registry.engineers import engineers_on_project
    engs = engineers_on_project("PROJ_A")
    assert {e.knox_id for e in engs} == {"alice.eng", "bob.coder"}


def test_engineers_on_project_is_case_insensitive(fake_mapping_file):
    from app.registry.engineers import engineers_on_project
    # Mapping has "PROJ_A"; lookup with lowercase should still hit
    engs = engineers_on_project("proj_a")
    assert len(engs) == 2


def test_engineers_on_project_returns_empty_for_unknown(fake_mapping_file):
    from app.registry.engineers import engineers_on_project
    assert engineers_on_project("DOES_NOT_EXIST") == []


def test_projects_for_engineer_returns_codes(fake_mapping_file):
    from app.registry.engineers import projects_for_engineer
    assert sorted(projects_for_engineer("alice.eng")) == ["PROJ_A", "PROJ_B"]


def test_projects_for_engineer_is_case_insensitive(fake_mapping_file):
    from app.registry.engineers import projects_for_engineer
    # mapping has "alice.eng"; query with mixed case
    assert sorted(projects_for_engineer("Alice.Eng")) == ["PROJ_A", "PROJ_B"]


def test_is_known_engineer_matches_jira_dc_user_shape(fake_mapping_file):
    """JIRA DC user object: {key: 'JIRAUSER...', name: '<knox_id>',
    displayName: '<full name>'}. The matcher must find by `name`."""
    from app.registry.engineers import is_known_engineer
    user = {
        "key": "JIRAUSER123456",
        "name": "alice.eng",
        "displayName": "Alice Engineer",
    }
    eng = is_known_engineer(user)
    assert eng is not None
    assert eng.knox_id == "alice.eng"


def test_is_known_engineer_falls_back_to_displayName(fake_mapping_file):
    """When none of the ID fields match, fall back to displayName matching
    against engineer.name (case+whitespace-insensitive)."""
    from app.registry.engineers import is_known_engineer
    user = {
        "key": "JIRAUSER999999",
        "name": "unknown.user",
        "displayName": "Bob Coder",
    }
    eng = is_known_engineer(user)
    assert eng is not None
    assert eng.knox_id == "bob.coder"


def test_is_known_engineer_returns_none_for_unknown(fake_mapping_file):
    from app.registry.engineers import is_known_engineer
    user = {"key": "JIRAUSER000", "name": "ghost", "displayName": "Ghost User"}
    assert is_known_engineer(user) is None


def test_is_known_engineer_handles_empty_input(fake_mapping_file):
    from app.registry.engineers import is_known_engineer
    assert is_known_engineer({}) is None
    assert is_known_engineer(None) is None  # type: ignore[arg-type]


def _swap_mapping_file_path(real_cfg, new_path: str):
    """Helper: swap real_cfg.engineers.mapping_file. Returns the original
    so the test can restore it on teardown (used by tests that need a
    custom path the default fake_mapping_file fixture wouldn't set)."""
    original = real_cfg.engineers.mapping_file
    real_cfg.engineers.mapping_file = new_path
    return original


def test_load_engineer_mapping_handles_missing_file(tmp_path):
    """A missing mapping file should produce an empty mapping with a warning,
    not crash. Same fixture pattern as fake_mapping_file: mutate the real
    cfg.engineers.mapping_file rather than swapping get_config()."""
    import app.config as config_mod
    from app.registry import engineers as engineers_mod

    real = config_mod.get_config()
    original_path = _swap_mapping_file_path(
        real, str(tmp_path / "does_not_exist.json")
    )
    engineers_mod.load_engineer_mapping.cache_clear()
    try:
        m = engineers_mod.load_engineer_mapping()
        assert m.engineers == []
        assert m.by_knox == {}
        assert any("not found" in w.lower() for w in m.parse_warnings)
    finally:
        real.engineers.mapping_file = original_path
        engineers_mod.load_engineer_mapping.cache_clear()


def test_load_engineer_mapping_handles_malformed_json(tmp_path):
    """A malformed JSON file should produce an empty mapping with a parse
    error in warnings, not crash the whole app."""
    import app.config as config_mod
    from app.registry import engineers as engineers_mod

    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{ not valid json", encoding="utf-8")

    real = config_mod.get_config()
    original_path = _swap_mapping_file_path(real, str(bad_path))
    engineers_mod.load_engineer_mapping.cache_clear()
    try:
        m = engineers_mod.load_engineer_mapping()
        assert m.engineers == []
        assert any("parse error" in w.lower() for w in m.parse_warnings)
    finally:
        real.engineers.mapping_file = original_path
        engineers_mod.load_engineer_mapping.cache_clear()


def test_load_engineer_mapping_skips_underscore_documentation_keys(tmp_path):
    """Mapping files now ship with _README and _help_* keys for self-documentation.
    The loader must ignore them and parse normal entries fine."""
    import app.config as config_mod
    from app.registry import engineers as engineers_mod

    path = tmp_path / "with_docs.json"
    path.write_text(json.dumps({
        "_README": "Test mapping with documentation keys",
        "_help_engineers": "Should be ignored",
        "engineers": [{"name": "Eng One", "knox_id": "eng.1"}],
        "assignments": [{"knox_id": "eng.1", "projects": ["P1"]}],
    }), encoding="utf-8")

    real = config_mod.get_config()
    original_path = _swap_mapping_file_path(real, str(path))
    engineers_mod.load_engineer_mapping.cache_clear()
    try:
        m = engineers_mod.load_engineer_mapping()
        assert len(m.engineers) == 1
        assert m.engineers[0].knox_id == "eng.1"
        assert m.parse_warnings == []
    finally:
        real.engineers.mapping_file = original_path
        engineers_mod.load_engineer_mapping.cache_clear()
