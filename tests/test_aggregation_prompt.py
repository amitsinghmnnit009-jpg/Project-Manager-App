"""Tests for app.engines._aggregation_prompt — the shared rendering helpers
used by the Aggregation Engine. These don't need a DB or any mocks.
"""
from __future__ import annotations
from app.clients.jira_client import ActivityRecord
from app.engines._aggregation_prompt import (
    _clean,
    extract_section_names,
    render_template_sections_block,
    render_raw_inputs_block,
)


# ---------- _clean ----------

def test_clean_removes_nbsp_variants():
    """JIRA frequently embeds NBSP variants in date/time strings. The cleaner
    must replace NO-BREAK SPACE (U+00A0), NARROW NO-BREAK SPACE (U+202F),
    FIGURE SPACE (U+2007), THIN SPACE (U+2009) with regular ASCII space, and
    strip ZERO-WIDTH SPACE (U+200B) entirely."""
    assert _clean("11:40 am") == "11:40 am"   # NO-BREAK SPACE
    assert _clean("11:40 am") == "11:40 am"   # NARROW NO-BREAK SPACE
    assert _clean("11:40 am") == "11:40 am"   # FIGURE SPACE
    assert _clean("11:40 am") == "11:40 am"   # THIN SPACE
    assert _clean("invisible​join") == "invisiblejoin"  # ZWS stripped


def test_clean_handles_none_and_non_string():
    assert _clean(None) == ""
    assert _clean(42) == "42"
    assert _clean("") == ""


def test_clean_passes_through_normal_text():
    assert _clean("Hello world") == "Hello world"
    assert _clean("MAICTJ-30 — Done") == "MAICTJ-30 — Done"   # em-dash unaffected


# ---------- extract_section_names ----------

def test_extract_section_names_finds_h2_only():
    """Level-1 (#) is the report title; only level-2 (##) lines are sections."""
    template = """
# Weekly Project Report: {project_name}

## Accomplishments

- one bullet

## In-progress / Ongoing work

## Risks and Blockers

### Sub-heading should NOT count

## Next-week plan
""".strip()
    sections = extract_section_names(template)
    assert sections == [
        "Accomplishments",
        "In-progress / Ongoing work",
        "Risks and Blockers",
        "Next-week plan",
    ]


def test_extract_section_names_handles_template_with_no_sections():
    assert extract_section_names("# Just a title\n\nNo sections.") == []


def test_render_template_sections_block_emits_h2_lines():
    out = render_template_sections_block(["A", "B and C", "D"])
    assert out == "## A\n## B and C\n## D"


def test_render_template_sections_block_empty_list():
    assert render_template_sections_block([]) == "(template has no sections)"


# ---------- render_raw_inputs_block ----------

def _rec(**kw):
    """Tiny helper to build an ActivityRecord with sensible defaults."""
    defaults = dict(
        task_id="X-1", task_title="Task title", task_status="In Progress",
        task_assignee=None, activity_kind="comment",
        author_name="Alice E", author_id="alice.e",
        timestamp="2026-05-08T10:00:00.000+0530",
        detail="some content", url="",
    )
    defaults.update(kw)
    return ActivityRecord(**defaults)


def test_render_raw_inputs_anonymises_engineers_as_E1_E2():
    """First engineer in iteration order → E1, second → E2, etc.
    No knox_id or display name should appear in the output."""
    activity = {
        "alice.eng": [_rec(author_id="alice.eng", detail="comment from alice")],
        "bob.coder": [_rec(author_id="bob.coder", detail="comment from bob")],
    }
    out = render_raw_inputs_block(activity)
    assert "--- Engineer E1 ---" in out
    assert "--- Engineer E2 ---" in out
    # Knox ids must NOT appear as engineer labels
    assert "--- Engineer alice.eng ---" not in out
    assert "--- Engineer bob.coder ---" not in out


def test_render_raw_inputs_block_can_disable_anonymisation_for_debug():
    """anonymise=False is for diagnostic / debug only; production always anonymises."""
    activity = {"alice.eng": [_rec(author_id="alice.eng", detail="x")]}
    out = render_raw_inputs_block(activity, anonymise=False)
    assert "--- Engineer alice.eng ---" in out


def test_render_raw_inputs_strips_nbsp_from_detail_strings():
    """JIRA's NBSP in dates etc. must not reach the prompt — it confuses
    terminals + Markdown renderers and the LLM faithfully echoes it."""
    activity = {"alice.eng": [
        _rec(detail="actual end date 04/May/26 11:40 am recorded")
    ]}
    out = render_raw_inputs_block(activity)
    assert "11:40 am" in out                 # NBSP became regular space
    assert "11:40 am" not in out        # original NBSP gone
    assert "11:40 am" not in out


def test_render_raw_inputs_groups_records_by_task():
    """Multiple records for the same task should appear under one Task: heading."""
    activity = {"alice.eng": [
        _rec(task_id="X-1", activity_kind="comment", detail="comment a"),
        _rec(task_id="X-1", activity_kind="worklog", detail="2h"),
        _rec(task_id="X-2", activity_kind="comment", detail="comment b",
             task_title="Other task"),
    ]}
    out = render_raw_inputs_block(activity)
    # One Task: line per task
    assert out.count("Task: X-1 —") == 1
    assert out.count("Task: X-2 —") == 1
    # All three details present
    assert "comment a" in out and "2h" in out and "comment b" in out


def test_render_raw_inputs_separates_record_kinds_under_task():
    """Comments / worklogs / status_changes / updates / creations each
    get their own labelled sub-section under the task."""
    activity = {"alice.eng": [
        _rec(activity_kind="comment", detail="reviewed"),
        _rec(activity_kind="worklog", detail="3h"),
        _rec(activity_kind="status_change",
             detail="status: 'In Progress' -> 'Done'"),
    ]}
    out = render_raw_inputs_block(activity)
    assert "Comments added this week:" in out
    assert "Work-logs added this week:" in out
    assert "Status changes:" in out


def test_render_raw_inputs_empty_input():
    assert render_raw_inputs_block({}) == "(no engineer activity recorded for this week)"
