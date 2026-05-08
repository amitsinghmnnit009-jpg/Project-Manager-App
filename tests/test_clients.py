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
