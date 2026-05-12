"""Smoke + unit tests for clients/ — uses `responses` to mock HTTP."""
from __future__ import annotations
import pytest
import responses

from app.clients.http_session import (
    rate_limit_wait, _FALLBACK_WAIT, _MAX_RETRY_AFTER, build_session,
)
from app.clients.jira_client import JiraClient
from app.clients.confluence_client import (
    ConfluenceClient, _parse_page_url, _parse_milestones_table, MilestoneRow,
)
from bs4 import BeautifulSoup


# ---------- http_session ----------

def test_rate_limit_wait_with_retry_after():
    assert rate_limit_wait({"Retry-After": "30"}) == 30.0


def test_rate_limit_wait_with_xratelimit_headers():
    assert rate_limit_wait({
        "x-ratelimit-interval-seconds": "60",
        "x-ratelimit-fillrate": "10",
    }) == 6.0


def test_rate_limit_wait_with_bogus_retry_after_uses_fallback():
    # Hour value is bogus; falls through to fallback
    assert rate_limit_wait({"Retry-After": "9999"}) == _FALLBACK_WAIT


def test_rate_limit_wait_caps_at_max():
    assert rate_limit_wait({"Retry-After": "300"}) == _MAX_RETRY_AFTER


def test_rate_limit_wait_no_headers():
    assert rate_limit_wait({}) == _FALLBACK_WAIT


def test_build_session_attaches_headers():
    s = build_session(verify_ssl=True, ca_bundle="", headers={"X-Test": "yes"})
    assert s.headers["X-Test"] == "yes"


# ---------- JIRA URL parsing ----------

@responses.activate
def test_jira_whoami():
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/myself",
        json={"displayName": "Alice", "accountId": "alice.id"},
        status=200,
    )
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    me = client.whoami()
    assert me["displayName"] == "Alice"


@responses.activate
def test_jira_search_pagination_single_page():
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/search",
        json={
            "issues": [{"key": "PROJ-1", "fields": {
                "summary": "do thing",
                "status": {"name": "In Progress"},
                "issuetype": {"name": "Task"},
                "assignee": None, "reporter": None,
                "created": "2026-05-01T10:00:00.000+0530",
                "updated": "2026-05-05T12:00:00.000+0530",
            }}],
            "total": 1,
        },
        status=200,
    )
    from datetime import datetime
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    issues = client.search_issues_in_project("PROJ", datetime(2026, 5, 1))
    assert len(issues) == 1
    assert issues[0]["key"] == "PROJ-1"


@responses.activate
def test_jira_collect_activity_case_insensitive_name_match():
    """Engineer mapping has 'Rahul Kumar' (capital K); JIRA returns 'Rahul kumar'
    (lowercase k). The matcher must handle this."""
    from datetime import date
    from app.clients.jira_client import JiraClient

    # One issue with one comment from "Rahul kumar" (lowercase k)
    issue = {
        "key": "PROJ-1",
        "fields": {
            "summary": "Test",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Task"},
            "assignee": None,
            "reporter": None,
            "created": "2026-04-01T10:00:00.000+0530",
            "updated": "2026-05-05T10:00:00.000+0530",
        },
        "changelog": {"histories": []},
    }
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/search",
        json={"issues": [issue], "total": 1},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/issue/PROJ-1/comment",
        json={"comments": [{
            "created": "2026-05-06T10:00:00.000+0530",  # within current week
            "author": {"displayName": "Rahul kumar", "name": "rahul.k"},
            "body": "ran tests, all green",
        }]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/issue/PROJ-1/worklog",
        json={"worklogs": []},
        status=200,
    )

    client = JiraClient(base_url="https://jira.example.com", token="tok")
    # Engineer mapping has the canonical "Rahul Kumar" with capital K
    engineers = [{"name": "Rahul Kumar", "knox_id": "RAHUL.K"}]

    # Use a recent week_of so the comment falls within
    from datetime import datetime, timedelta
    week_of = (datetime.now().date() - timedelta(days=datetime.now().weekday()))

    activity = client.collect_engineer_activity("PROJ", week_of, engineers)

    # Rahul should be MATCHED (not unmapped) despite the case mismatch
    assert "RAHUL.K" in activity.by_engineer, \
        f"expected RAHUL.K in by_engineer, got {list(activity.by_engineer.keys())} " \
        f"and unmapped={[u.display_name for u in activity.unmapped_authors]}"
    assert activity.unmapped_authors == [], \
        f"expected no unmapped authors, got {activity.unmapped_authors}"


@responses.activate
def test_jira_collect_activity_dc_user_shape():
    """JIRA Server/DC populates user objects like:
        {'key': 'JIRAUSER157459', 'name': 'rahul22.k', 'displayName': 'Rahul Kumar'}
    Knox_id is in 'name', not 'key'. Old matcher used the first non-empty
    among (accountId, key, name) as a_id and never tried 'name' separately,
    so 'JIRAUSER157459' got the lookup and missed. New matcher tries ALL
    fields against knox_id lookup."""
    from datetime import date, datetime, timedelta
    from app.clients.jira_client import JiraClient

    issue = {
        "key": "PROJ-1",
        "fields": {
            "summary": "Test",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Task"},
            "assignee": None,
            "reporter": None,
            "created": "2026-04-01T10:00:00.000+0530",
            "updated": "2026-05-05T10:00:00.000+0530",
        },
        "changelog": {"histories": []},
    }
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/search",
        json={"issues": [issue], "total": 1},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/issue/PROJ-1/comment",
        json={"comments": [{
            "created": "2026-05-06T10:00:00.000+0530",
            "author": {
                "key": "JIRAUSER157459",     # JIRA DC auto-generated
                "name": "rahul22.k",          # actual username = our knox_id
                "displayName": "Rahul Kumar",
            },
            "body": "ran tests, all green",
        }]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/issue/PROJ-1/worklog",
        json={"worklogs": []},
        status=200,
    )

    client = JiraClient(base_url="https://jira.example.com", token="tok")
    engineers = [{"name": "Rahul Kumar", "knox_id": "rahul22.k"}]

    # Pick a Monday so the comment falls within the report week
    week_of = (datetime.now().date() - timedelta(days=datetime.now().weekday()))

    activity = client.collect_engineer_activity("PROJ", week_of, engineers)

    assert "rahul22.k" in activity.by_engineer, \
        f"expected rahul22.k in by_engineer, got {list(activity.by_engineer.keys())} " \
        f"and unmapped={[u.display_name for u in activity.unmapped_authors]}"
    assert activity.unmapped_authors == []


@responses.activate
def test_jira_collect_activity_handles_nbsp_in_name():
    """JIRA returns 'Rahul Kumar' (non-breaking space) but mapping has
    'Rahul Kumar' (regular space). Normalisation must collapse the NBSP."""
    from datetime import datetime, timedelta
    from app.clients.jira_client import JiraClient

    issue = {
        "key": "PROJ-1",
        "fields": {
            "summary": "Test",
            "status": {"name": "Open"},
            "issuetype": {"name": "Task"},
            "assignee": None, "reporter": None,
            "created": "2026-04-01T10:00:00.000+0530",
            "updated": "2026-05-05T10:00:00.000+0530",
        },
        "changelog": {"histories": []},
    }
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/search",
        json={"issues": [issue], "total": 1},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/issue/PROJ-1/comment",
        json={"comments": [{
            "created": "2026-05-06T10:00:00.000+0530",
            "author": {"displayName": "Rahul Kumar"},  # NBSP between
            "body": "fixed",
        }]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/issue/PROJ-1/worklog",
        json={"worklogs": []}, status=200,
    )

    client = JiraClient(base_url="https://jira.example.com", token="tok")
    engineers = [{"name": "Rahul Kumar", "knox_id": "RK"}]
    week_of = (datetime.now().date() - timedelta(days=datetime.now().weekday()))

    activity = client.collect_engineer_activity("PROJ", week_of, engineers)
    assert "RK" in activity.by_engineer
    assert activity.unmapped_authors == []


@responses.activate
def test_jira_collect_activity_filters_assignee_changelog_to_protect_anonymity():
    """Assignee/Reporter/Watchers changes are person-valued — if they reach
    the prompt, the LLM faithfully echoes the names (knox_id or display name)
    into the report, violating FR §B 'no engineer names'. The _NOISY_FIELDS
    filter strips them at the JIRA-collection stage so they never reach the
    prompt's RAW INPUTS block.

    This test verifies that an Assignee changelog history item is dropped
    (not surfaced as an 'updated' ActivityRecord), while a Status change
    on the same issue still comes through (status_change is essential signal).
    """
    from datetime import date, datetime, timedelta
    from app.clients.jira_client import JiraClient

    issue = {
        "key": "PROJ-99",
        "fields": {
            "summary": "Test task",
            "status": {"name": "Done"},
            "issuetype": {"name": "Task"},
            "assignee": None, "reporter": None,
            "created": "2026-04-01T10:00:00.000+0530",
            "updated": "2026-05-08T10:00:00.000+0530",
        },
        "changelog": {"histories": [
            {
                "created": "2026-05-06T10:00:00.000+0530",
                "author": {"key": "K1", "name": "alice.eng",
                           "displayName": "Alice E"},
                "items": [
                    # This one MUST be filtered (person-valued)
                    {"field": "Assignee",
                     "fromString": "bob.coder", "toString": "alice.eng"},
                    # This one MUST come through (status is the load-bearing signal)
                    {"field": "status",
                     "fromString": "In Progress", "toString": "Done"},
                ],
            }
        ]},
    }
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/search",
        json={"issues": [issue], "total": 1},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/issue/PROJ-99/comment",
        json={"comments": []}, status=200,
    )
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/issue/PROJ-99/worklog",
        json={"worklogs": []}, status=200,
    )

    client = JiraClient(base_url="https://jira.example.com", token="tok")
    engineers = [{"name": "Alice E", "knox_id": "alice.eng"}]
    week_of = (datetime.now().date() - timedelta(days=datetime.now().weekday()))

    activity = client.collect_engineer_activity("PROJ", week_of, engineers)

    # Alice should have ONE record (the status_change), not TWO (status + assignee)
    records = activity.by_engineer.get("alice.eng", [])
    kinds = [r.activity_kind for r in records]
    assert "status_change" in kinds, f"Status change must come through; got kinds={kinds}"
    # No 'updated' record — the only non-status item was Assignee, which was filtered
    for r in records:
        assert "Assignee" not in r.detail, (
            f"Assignee changelog leaked into a record: {r.detail!r}"
        )
        assert "alice.eng" not in r.detail, (
            f"Engineer knox_id leaked into a record's detail: {r.detail!r}"
        )


@responses.activate
def test_jira_search_filters_by_issue_type():
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/search",
        json={
            "issues": [
                {"key": "PROJ-1", "fields": {"issuetype": {"name": "Task"}}},
                {"key": "PROJ-2", "fields": {"issuetype": {"name": "Epic"}}},
                {"key": "PROJ-3", "fields": {"issuetype": {"name": "Bug"}}},
            ],
            "total": 3,
        },
        status=200,
    )
    from datetime import datetime
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    issues = client.search_issues_in_project(
        "PROJ", datetime(2026, 5, 1), issue_types=["Task", "Bug"]
    )
    keys = [i["key"] for i in issues]
    assert "PROJ-1" in keys and "PROJ-3" in keys
    assert "PROJ-2" not in keys


# ---------- Phase 2 backfill: JQL construction --------------------

@responses.activate
def test_jira_search_appends_exclude_labels_clause():
    """When exclude_labels is non-empty, JQL gains AND labels NOT IN (...)."""
    captured: dict = {}

    def _capture(request):
        captured["jql"] = request.url
        return (200, {}, '{"issues": [], "total": 0}')

    responses.add_callback(
        responses.GET,
        "https://jira.example.com/rest/api/2/search",
        callback=_capture,
        content_type="application/json",
    )
    from datetime import datetime
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    client.search_issues_in_project(
        "PROJ", datetime(2026, 5, 1), exclude_labels=["backfill", "data-entry"],
    )
    assert "PROJ" in captured["jql"]
    # URL-encoded — check the literal jql attribute on the client instead
    assert 'labels NOT IN ("backfill", "data-entry")' in client._last_jql


def test_jira_search_no_label_clause_when_exclude_labels_empty():
    """exclude_labels=None or [] preserves the original JQL exactly."""
    from datetime import datetime
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    # We don't need an actual HTTP roundtrip — just trigger the JQL build
    # by mocking a no-op _get.
    captured: list = []
    def _stub_get(path, params=None):
        captured.append(params.get("jql", ""))
        return {"issues": [], "total": 0}
    client._get = _stub_get  # type: ignore

    client.search_issues_in_project("PROJ", datetime(2026, 5, 1))
    assert "labels NOT IN" not in captured[0]

    client.search_issues_in_project(
        "PROJ", datetime(2026, 5, 1), exclude_labels=[]
    )
    assert "labels NOT IN" not in captured[1]

    client.search_issues_in_project(
        "PROJ", datetime(2026, 5, 1), exclude_labels=None
    )
    assert "labels NOT IN" not in captured[2]


def test_jira_search_escapes_special_chars_in_exclude_labels():
    """Label values with quotes/backslashes must not break the JQL string."""
    from datetime import datetime
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    captured: list = []
    def _stub_get(path, params=None):
        captured.append(params.get("jql", ""))
        return {"issues": [], "total": 0}
    client._get = _stub_get  # type: ignore
    client.search_issues_in_project(
        "PROJ", datetime(2026, 5, 1),
        exclude_labels=['bad"label', "back\\slash"],
    )
    jql = captured[0]
    # Both special characters are properly escaped — JQL string isn't broken
    assert 'bad\\"label' in jql
    assert "back\\\\slash" in jql


# ---------- Phase 2 backfill: resolve_custom_field_id ------------

@responses.activate
def test_resolve_custom_field_id_basic():
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/field",
        json=[
            {"id": "customfield_10042", "name": "Baseline end date"},
            {"id": "customfield_10041", "name": "Baseline start date"},
            {"id": "duedate", "name": "Due Date"},
        ],
        status=200,
    )
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    assert client.resolve_custom_field_id("Baseline end date") == "customfield_10042"
    # case + whitespace insensitive
    assert client.resolve_custom_field_id("baseline END date") == "customfield_10042"
    assert client.resolve_custom_field_id("  Baseline end date  ") == "customfield_10042"


@responses.activate
def test_resolve_custom_field_id_returns_none_when_absent():
    responses.add(
        responses.GET,
        "https://jira.example.com/rest/api/2/field",
        json=[{"id": "customfield_10001", "name": "Some Other Field"}],
        status=200,
    )
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    assert client.resolve_custom_field_id("Baseline end date") is None
    assert client.resolve_custom_field_id("") is None
    assert client.resolve_custom_field_id(None) is None  # type: ignore


@responses.activate
def test_resolve_custom_field_id_caches_after_first_call():
    """Second call should NOT hit /rest/api/2/field again."""
    call_count = {"n": 0}
    def _cb(_request):
        call_count["n"] += 1
        return (200, {}, '[{"id": "customfield_10042", "name": "Baseline end date"}]')
    responses.add_callback(
        responses.GET, "https://jira.example.com/rest/api/2/field",
        callback=_cb, content_type="application/json",
    )
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    client.resolve_custom_field_id("Baseline end date")
    client.resolve_custom_field_id("Baseline end date")
    client.resolve_custom_field_id("anything else")
    assert call_count["n"] == 1


# ---------- Phase 2 backfill: search_issues_by_activity_date -----

@responses.activate
def test_search_by_activity_date_builds_correct_jql():
    from datetime import date

    captured: list = []
    def _cb(request):
        captured.append(request.url)
        return (200, {}, '{"issues": [], "total": 0}')

    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/field",
        json=[{"id": "customfield_10042", "name": "Baseline end date"}],
        status=200,
    )
    responses.add_callback(
        responses.GET, "https://jira.example.com/rest/api/2/search",
        callback=_cb, content_type="application/json",
    )
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    client.search_issues_by_activity_date(
        "PROJ", "Baseline end date",
        date(2026, 2, 2), date(2026, 2, 8),
    )
    # Field resolver was called, ID cached
    assert client._last_field_id == "customfield_10042"
    # JQL well-formed
    jql = client._last_jql
    assert 'project = "PROJ"' in jql
    assert '"Baseline end date" >= "2026-02-02"' in jql
    assert '"Baseline end date" <= "2026-02-08"' in jql


def test_search_by_activity_date_requires_field_name():
    from datetime import date
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    with pytest.raises(ValueError, match="field_name"):
        client.search_issues_by_activity_date(
            "PROJ", "", date(2026, 2, 2), date(2026, 2, 8),
        )


@responses.activate
def test_search_by_activity_date_survives_field_endpoint_failure():
    """If /rest/api/2/field is rate-limited, the search should still try
    (JIRA can resolve the name server-side). _last_field_id ends up None."""
    from datetime import date
    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/field",
        status=429, json={"message": "Rate limit exceeded"},
    )
    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/search",
        json={"issues": [], "total": 0}, status=200,
    )
    client = JiraClient(
        base_url="https://jira.example.com", token="tok", retry_total=0,
    )
    # Should not raise — falls back gracefully
    client.search_issues_by_activity_date(
        "PROJ", "Baseline end date",
        date(2026, 2, 2), date(2026, 2, 8),
    )
    assert client._last_field_id is None
    # JQL still uses the field name (JIRA resolves server-side)
    assert '"Baseline end date"' in client._last_jql


# ---------- Phase 2 backfill: collect_engineer_activity_for_backfill ----

@responses.activate
def test_collect_for_backfill_creates_synthetic_records_per_assignee():
    """One ticket with Baseline end date in the window → one record
    per mapped engineer involved (assignee + reporter, deduplicated)."""
    from datetime import date
    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/field",
        json=[{"id": "customfield_10042", "name": "Baseline end date"}],
        status=200,
    )
    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/search",
        json={
            "issues": [{
                "key": "PROJ-501",
                "fields": {
                    "summary": "Implement IDM SAML flow",
                    "status": {"name": "Done"},
                    "issuetype": {"name": "Task"},
                    "assignee": {"name": "rahul.k", "displayName": "Rahul Kumar"},
                    "reporter": {"name": "rahul.k", "displayName": "Rahul Kumar"},
                    "customfield_10042": "2026-02-06",
                    "labels": ["backfill"],
                },
            }],
            "total": 1,
        },
        status=200,
    )
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    engineers = [{"name": "Rahul Kumar", "knox_id": "rahul.k"}]
    activity = client.collect_engineer_activity_for_backfill(
        "PROJ", "Baseline end date", date(2026, 2, 2), engineers,
    )
    assert "rahul.k" in activity.by_engineer
    records = activity.by_engineer["rahul.k"]
    # Single record (assignee == reporter dedup)
    assert len(records) == 1
    rec = records[0]
    assert rec.task_id == "PROJ-501"
    assert rec.task_title == "Implement IDM SAML flow"
    assert rec.task_status == "Done"
    # Timestamp is the Baseline end date, NOT today
    assert rec.timestamp == "2026-02-06"
    assert rec.activity_kind == "completed"


@responses.activate
def test_collect_for_backfill_separate_assignee_and_reporter():
    """Different people for assignee + reporter → two records, one each."""
    from datetime import date
    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/field",
        json=[{"id": "customfield_10042", "name": "Baseline end date"}],
        status=200,
    )
    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/search",
        json={
            "issues": [{
                "key": "PROJ-502",
                "fields": {
                    "summary": "Pair-programmed feature",
                    "status": {"name": "Done"},
                    "issuetype": {"name": "Task"},
                    "assignee": {"name": "rahul.k", "displayName": "Rahul Kumar"},
                    "reporter": {"name": "vishal.s", "displayName": "Vishal Shakya"},
                    "customfield_10042": "2026-02-05",
                },
            }],
            "total": 1,
        },
        status=200,
    )
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    engineers = [
        {"name": "Rahul Kumar", "knox_id": "rahul.k"},
        {"name": "Vishal Shakya", "knox_id": "vishal.s"},
    ]
    activity = client.collect_engineer_activity_for_backfill(
        "PROJ", "Baseline end date", date(2026, 2, 2), engineers,
    )
    assert set(activity.by_engineer.keys()) == {"rahul.k", "vishal.s"}
    assert len(activity.by_engineer["rahul.k"]) == 1
    assert len(activity.by_engineer["vishal.s"]) == 1


@responses.activate
def test_collect_for_backfill_unmapped_authors():
    """Tickets with assignees not in the engineer mapping go to unmapped."""
    from datetime import date
    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/field",
        json=[{"id": "customfield_10042", "name": "Baseline end date"}],
        status=200,
    )
    responses.add(
        responses.GET, "https://jira.example.com/rest/api/2/search",
        json={
            "issues": [{
                "key": "PROJ-503",
                "fields": {
                    "summary": "Ticket from stranger",
                    "status": {"name": "Done"},
                    "issuetype": {"name": "Task"},
                    "assignee": {"name": "unknown.user", "displayName": "Stranger"},
                    "reporter": None,
                    "customfield_10042": "2026-02-04",
                },
            }],
            "total": 1,
        },
        status=200,
    )
    client = JiraClient(base_url="https://jira.example.com", token="tok")
    activity = client.collect_engineer_activity_for_backfill(
        "PROJ", "Baseline end date", date(2026, 2, 2), [],
    )
    assert activity.by_engineer == {}
    assert len(activity.unmapped_authors) == 1
    assert activity.unmapped_authors[0].display_name == "Stranger"


# ---------- Confluence URL parsing ----------

def test_parse_url_pageid():
    out = _parse_page_url("https://confluence.example.com/pages/viewpage.action?pageId=98765")
    assert out == {"page_id": "98765"}


def test_parse_url_spaces_pageid():
    """Modern /spaces/SPACE/pages/ID/Title URL: extract ALL three identifiers
    so the fetcher can prefer the title-based query form (POC pattern,
    avoids /rest/api/content/{id} which is rate-limited at some sites)."""
    out = _parse_page_url("https://confluence.example.com/spaces/SPACE/pages/12345/My+Page")
    assert out == {"space_key": "SPACE", "page_id": "12345", "title": "My Page"}


def test_parse_url_spaces_pageid_no_trailing_title():
    """Same shape but without the trailing title segment — falls back to
    page_id-only result. Caller must use id-based fetch."""
    out = _parse_page_url("https://confluence.example.com/spaces/SPACE/pages/12345")
    assert out == {"space_key": "SPACE", "page_id": "12345"}


def test_parse_url_real_user_shape():
    """Actual URL shape reported by user (Project+Status+2026 with pluses)."""
    out = _parse_page_url(
        "https://confluence.hippo.net/spaces/0987878/pages/7878667/Project+Status+2026"
    )
    assert out == {
        "space_key": "0987878",
        "page_id": "7878667",
        "title": "Project Status 2026",
    }


def test_parse_url_display_title():
    out = _parse_page_url("https://confluence.example.com/display/SPACE/My+Page+Title")
    assert out == {"space_key": "SPACE", "title": "My Page Title"}


def test_parse_url_unknown_shape():
    out = _parse_page_url("https://example.com/random/path")
    assert out == {}


# ---------- Confluence whoami ----------

@responses.activate
def test_confluence_whoami():
    """whoami() must hit /rest/api/content (not /user/current or /space) —
    corporate Confluence DC instances apply per-endpoint rate-limit overrides
    to those paths. /content is the only family verified to be reliably open."""
    responses.add(
        responses.GET,
        "https://confluence.example.com/rest/api/content",
        json={
            "size": 1,
            "results": [{
                "id": "12345",
                "type": "page",
                "title": "Some Page",
            }],
        },
        status=200,
    )
    # disable per-call delay for fast tests
    client = ConfluenceClient(
        base_url="https://confluence.example.com", token="tok",
        request_delay_seconds=0,
    )
    me = client.whoami()
    assert me["content_visible"] == 1
    assert me["first_page"]["id"] == "12345"
    assert me["first_page"]["title"] == "Some Page"


# ---------- Confluence parser ----------

SAMPLE_PAGE_HTML = """
<h1>Project Overview</h1>
<p>This project builds firmware for the next-gen SSD controller.</p>

<h2>Milestones</h2>
<table>
  <tbody>
    <tr>
      <th>Milestone</th><th>Quarter</th><th>Planned Date</th><th>Priority</th>
      <th>Status</th><th>Dependency</th><th>Description</th><th>Remark</th>
    </tr>
    <tr>
      <td>M1 — Vendor SDK</td><td>Q2-2026</td><td>2026-05-15</td><td>P1</td>
      <td>In-progress</td><td></td><td>SDK headers integrated</td><td></td>
    </tr>
    <tr>
      <td>M2 — Beta release</td><td>Q3-2026</td><td>2026-08-01</td><td>P1</td>
      <td>Pending</td><td>M1</td><td>Beta build deployed</td><td>Tentative</td>
    </tr>
  </tbody>
</table>

<h2>Functional Requirements</h2>
<p>FR-1: Secure boot</p>
<p>FR-2: Wear-levelling v3</p>
"""


def test_parse_milestones_page_extracts_overview_and_table():
    """Milestones page parser: captures Project Overview + the milestones table.
    Uses the legacy 8-column SAMPLE_PAGE_HTML to verify backwards compatibility."""
    page = {"title": "SSD Firmware v3",
            "body": {"storage": {"value": SAMPLE_PAGE_HTML}}}
    parsed = ConfluenceClient.parse_milestones_page(page)

    assert parsed.title == "SSD Firmware v3"
    assert "next-gen SSD controller" in parsed.overview
    assert len(parsed.milestones) == 2

    m1 = parsed.milestones[0]
    assert m1.name == "M1 — Vendor SDK"
    assert m1.quarter == "Q2-2026"
    assert m1.planned_date == "2026-05-15"
    assert m1.priority == "P1"
    assert m1.status == "In-progress"
    assert m1.description == "SDK headers integrated"

    m2 = parsed.milestones[1]
    assert m2.name == "M2 — Beta release"
    assert m2.dependency == "M1"
    assert m2.remark == "Tentative"


CANONICAL_7COL_PAGE_HTML = """
<h1>Project Overview</h1>
<p>SSD firmware for the next-gen controller.</p>
<h2>Milestones</h2>
<table>
  <tbody>
    <tr>
      <th>Milestone</th><th>Planned Date</th><th>Priority</th>
      <th>Status</th><th>Dependency</th><th>Description</th><th>Remark</th>
    </tr>
    <tr>
      <td>M1 — Vendor SDK</td><td>2026-05-15</td><td>P1</td>
      <td>In-progress</td><td>vendor delivery</td>
      <td>SDK headers integrated; example code compiles</td>
      <td>Vendor delivery delayed 2 weeks</td>
    </tr>
    <tr>
      <td>M2 — Wear-level</td><td>2026-06-30</td><td>P2</td>
      <td>Pending</td><td>M1 (SDK headers)</td>
      <td>Wear-levelling v3 passing perf tests</td>
      <td></td>
    </tr>
  </tbody>
</table>
"""


def test_parse_milestones_page_canonical_7col_format():
    """Canonical 7-column format (Phase 1 recommended): Milestone | Planned Date
    | Priority | Status | Dependency | Description | Remark. Quarter
    intentionally absent (derivable from Planned Date).
    """
    page = {"title": "SSDFW Milestones",
            "body": {"storage": {"value": CANONICAL_7COL_PAGE_HTML}}}
    parsed = ConfluenceClient.parse_milestones_page(page)

    assert "SSD firmware" in parsed.overview
    assert len(parsed.milestones) == 2

    m1 = parsed.milestones[0]
    assert m1.name == "M1 — Vendor SDK"
    assert m1.planned_date == "2026-05-15"
    assert m1.priority == "P1"
    assert m1.status == "In-progress"
    assert m1.dependency == "vendor delivery"
    assert "SDK headers integrated" in m1.description
    assert "delayed 2 weeks" in m1.remark
    assert m1.quarter == ""  # not in canonical 7-col

    m2 = parsed.milestones[1]
    assert m2.name == "M2 — Wear-level"
    assert m2.priority == "P2"
    assert m2.dependency == "M1 (SDK headers)"
    assert m2.remark == ""   # explicitly empty cell


SLIM_TABLE_HTML = """
<h1>Project Overview</h1>
<p>Whitepaper on memory tiering for our research group.</p>
<h2>Milestones</h2>
<table>
  <tbody>
    <tr><th>Milestone</th><th>Planned Date</th><th>Status</th><th>Description</th></tr>
    <tr><td>M1 — Lit review</td><td>2026-04-30</td><td>Done</td>
        <td>30+ papers reviewed, key findings summarised</td></tr>
    <tr><td>M2 — Draft v1</td><td>2026-06-15</td><td>In-progress</td>
        <td>First full draft circulated to co-authors</td></tr>
  </tbody>
</table>
<blockquote><em>M2 — priority P1, dependency: M1</em></blockquote>
"""


def test_parse_milestones_page_slim_4col_fallback():
    """Slim 4-column format (fallback for projects where 7-column alignment
    is awkward in their Confluence theme). Optional columns parse to empty."""
    page = {"title": "Memory Tiering Research",
            "body": {"storage": {"value": SLIM_TABLE_HTML}}}
    parsed = ConfluenceClient.parse_milestones_page(page)

    assert "memory tiering" in parsed.overview
    assert len(parsed.milestones) == 2
    assert parsed.milestones[0].name == "M1 — Lit review"
    assert parsed.milestones[0].status == "Done"
    assert parsed.milestones[0].planned_date == "2026-04-30"
    assert "30+ papers reviewed" in parsed.milestones[0].description
    # Slim format leaves these empty — that's expected
    assert parsed.milestones[0].quarter == ""
    assert parsed.milestones[0].priority == ""
    assert parsed.milestones[0].dependency == ""


def test_parse_milestones_page_no_table_warns():
    """Page with no table on it should produce a warning (and empty list)."""
    html = "<h1>Project Overview</h1><p>This page has no table at all.</p>"
    page = {"title": "Bare", "body": {"storage": {"value": html}}}
    parsed = ConfluenceClient.parse_milestones_page(page)
    assert parsed.milestones == []
    assert any("table" in w.lower() for w in parsed.parse_warnings)


def test_parse_milestones_page_finds_table_without_heading():
    """If the page has a table but no 'Milestones' heading, parser should
    still find it (the whole page IS the milestones page)."""
    html = """
    <table>
      <tr><th>Milestone</th><th>Planned Date</th><th>Status</th><th>Description</th></tr>
      <tr><td>M1</td><td>2026-05-01</td><td>Pending</td><td>x</td></tr>
    </table>
    """
    page = {"title": "MS", "body": {"storage": {"value": html}}}
    parsed = ConfluenceClient.parse_milestones_page(page)
    assert len(parsed.milestones) == 1
    assert parsed.milestones[0].name == "M1"


def test_parse_milestones_page_handles_empty_body():
    page = {"title": "Empty", "body": {"storage": {"value": ""}}}
    parsed = ConfluenceClient.parse_milestones_page(page)
    assert parsed.milestones == []
    assert parsed.parse_warnings


# ---------- FR page parser ----------

FR_PAGE_HTML = """
<h1>Project Overview</h1>
<p>Build firmware for a next-generation SSD controller.</p>
<h2>Functional Requirements</h2>
<p>FR-1: Secure boot</p>
<p>FR-2: Wear-levelling v3</p>
<p>FR-3: Power-loss recovery</p>
"""


def test_parse_fr_page_extracts_overview_and_fr_text():
    page = {"title": "SSDFW Functional Requirements",
            "body": {"storage": {"value": FR_PAGE_HTML}}}
    parsed = ConfluenceClient.parse_fr_page(page)

    assert parsed.title == "SSDFW Functional Requirements"
    assert "next-generation SSD controller" in parsed.overview
    assert "FR-1" in parsed.functional_requirements
    assert "FR-2" in parsed.functional_requirements
    assert "FR-3" in parsed.functional_requirements
    assert parsed.parse_warnings == []


def test_parse_fr_page_with_no_explicit_heading():
    """If the FR page has no 'Functional Requirements' heading, the whole
    body becomes the FR text (minus any captured Overview section)."""
    html = "<p>FR-1: Secure boot</p><p>FR-2: Wear-levelling</p>"
    page = {"title": "X", "body": {"storage": {"value": html}}}
    parsed = ConfluenceClient.parse_fr_page(page)
    assert "FR-1" in parsed.functional_requirements
    assert "FR-2" in parsed.functional_requirements


def test_parse_fr_page_handles_empty_body():
    page = {"title": "Empty", "body": {"storage": {"value": ""}}}
    parsed = ConfluenceClient.parse_fr_page(page)
    assert parsed.parse_warnings


# ---------- Extra page parser ----------

def test_parse_extra_page_truncates_long_body():
    """Long pages must be truncated to max_chars and flagged."""
    long_body = "A" * 10000
    html = f"<p>{long_body}</p>"
    page = {"title": "Vendor SDK Notes",
            "body": {"storage": {"value": html}}}
    parsed = ConfluenceClient.parse_extra_page(page, max_chars=200)
    assert parsed.truncated is True
    assert len(parsed.body_text) > 200  # body_text + truncation marker
    assert "[... truncated ...]" in parsed.body_text
    assert parsed.title == "Vendor SDK Notes"


def test_parse_extra_page_short_body_not_truncated():
    html = "<p>Short notes about the architecture.</p>"
    page = {"title": "Arch", "body": {"storage": {"value": html}}}
    parsed = ConfluenceClient.parse_extra_page(page, max_chars=200)
    assert parsed.truncated is False
    assert "Short notes" in parsed.body_text


def test_parse_extra_page_handles_empty_body():
    page = {"title": "Empty", "body": {"storage": {"value": ""}}}
    parsed = ConfluenceClient.parse_extra_page(page)
    assert parsed.parse_warnings


def test_parse_milestones_table_skips_empty_rows():
    html = """
    <table>
      <tr><th>Milestone</th><th>Status</th></tr>
      <tr><td></td><td></td></tr>
      <tr><td>M1</td><td>Done</td></tr>
    </table>
    """
    soup = BeautifulSoup(html, "html.parser")
    warnings = []
    rows = _parse_milestones_table(soup.find("table"), warnings)
    assert len(rows) == 1
    assert rows[0].name == "M1"


# ---------- Phase A: completion_date column -----------------------------

def test_parse_milestones_table_recognises_completion_date_column():
    """New 'Completion Date' column populates MilestoneRow.completion_date."""
    html = """
    <table>
      <tr><th>Milestone</th><th>Planned Date</th><th>Completion Date</th><th>Status</th></tr>
      <tr><td>M1</td><td>2026-04-30</td><td>2026-04-28</td><td>Done</td></tr>
      <tr><td>M2</td><td>2026-06-15</td><td></td><td>In-progress</td></tr>
    </table>
    """
    soup = BeautifulSoup(html, "html.parser")
    warnings = []
    rows = _parse_milestones_table(soup.find("table"), warnings)
    assert len(rows) == 2
    assert rows[0].name == "M1"
    assert rows[0].planned_date == "2026-04-30"
    assert rows[0].completion_date == "2026-04-28"
    assert rows[1].name == "M2"
    assert rows[1].planned_date == "2026-06-15"
    # In-progress row has empty completion cell — completion_date should be ""
    assert rows[1].completion_date == ""


def test_parse_milestones_table_completion_date_synonyms():
    """Header variants 'Actual End', 'Actual End Date', 'Completed', 'Completed On'
    all map to completion_date."""
    for header in ("Completion Date", "Completion", "Completed",
                   "Completed On", "Actual End", "Actual End Date"):
        html = f"""
        <table>
          <tr><th>Milestone</th><th>Planned Date</th><th>{header}</th><th>Status</th></tr>
          <tr><td>M1</td><td>2026-04-30</td><td>2026-04-28</td><td>Done</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        rows = _parse_milestones_table(soup.find("table"), [])
        assert len(rows) == 1, f"failed for header {header!r}"
        assert rows[0].completion_date == "2026-04-28", \
            f"failed for header {header!r}: got {rows[0].completion_date!r}"


def test_parse_milestones_table_completion_does_not_steal_planned_date():
    """'Completion Date' contains 'date' as a substring; the parser must NOT
    let it claim the planned-date slot — Planned Date column wins."""
    html = """
    <table>
      <tr><th>Milestone</th><th>Completion Date</th><th>Planned Date</th><th>Status</th></tr>
      <tr><td>M1</td><td>2026-04-28</td><td>2026-04-30</td><td>Done</td></tr>
    </table>
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = _parse_milestones_table(soup.find("table"), [])
    assert len(rows) == 1
    assert rows[0].planned_date == "2026-04-30"
    assert rows[0].completion_date == "2026-04-28"


def test_parse_milestones_table_legacy_table_without_completion_column():
    """Tables without a Completion Date column still parse — completion_date
    stays at its default empty string. No regression."""
    html = """
    <table>
      <tr><th>Milestone</th><th>Planned Date</th><th>Status</th><th>Description</th></tr>
      <tr><td>M1</td><td>2026-04-30</td><td>Done</td><td>desc text</td></tr>
    </table>
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = _parse_milestones_table(soup.find("table"), [])
    assert len(rows) == 1
    assert rows[0].name == "M1"
    assert rows[0].planned_date == "2026-04-30"
    assert rows[0].status == "Done"
    assert rows[0].description == "desc text"
    assert rows[0].completion_date == ""
