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
    out = _parse_page_url("https://confluence.example.com/spaces/SPACE/pages/12345/My+Page")
    assert out == {"space_key": "SPACE", "page_id": "12345"}


def test_parse_url_display_title():
    out = _parse_page_url("https://confluence.example.com/display/SPACE/My+Page+Title")
    assert out == {"space_key": "SPACE", "title": "My Page Title"}


def test_parse_url_unknown_shape():
    out = _parse_page_url("https://example.com/random/path")
    assert out == {}


# ---------- Confluence whoami ----------

@responses.activate
def test_confluence_whoami():
    responses.add(
        responses.GET,
        "https://confluence.example.com/rest/api/user/current",
        json={"displayName": "Alice", "userKey": "alice"},
        status=200,
    )
    # disable per-call delay for fast tests
    client = ConfluenceClient(
        base_url="https://confluence.example.com", token="tok",
        request_delay_seconds=0,
    )
    me = client.whoami()
    assert me["displayName"] == "Alice"


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


def test_parse_project_page_extracts_all_sections():
    page = {"title": "SSD Firmware v3",
            "body": {"storage": {"value": SAMPLE_PAGE_HTML}}}
    parsed = ConfluenceClient.parse_project_page(page)

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

    assert "FR-1" in parsed.functional_requirements
    assert "FR-2" in parsed.functional_requirements
    assert parsed.parse_warnings == []


def test_parse_project_page_handles_missing_milestones_section():
    html = "<h1>Overview</h1><p>just text</p><h2>Functional Requirements</h2><p>FR-1</p>"
    page = {"title": "X", "body": {"storage": {"value": html}}}
    parsed = ConfluenceClient.parse_project_page(page)
    assert parsed.milestones == []
    assert any("Milestones" in w for w in parsed.parse_warnings)


def test_parse_project_page_handles_empty_body():
    page = {"title": "Empty", "body": {"storage": {"value": ""}}}
    parsed = ConfluenceClient.parse_project_page(page)
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
