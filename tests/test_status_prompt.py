"""Tests for app.engines._status_prompt — the v3 prompt renderers + validator.

Targets the Phase B Completion Date integration:
- render_milestones_block must include completion_date when populated
- PROMPT_FILE and PROMPT_VERSION must point to v3 (the file we ship)
- validate_prompt3_json must accept optional completion_date in milestone
  dicts (and reject malformed values)
"""
from __future__ import annotations

from app.clients.confluence_client import MilestoneRow
from app.engines._status_prompt import (
    PROMPT_FILE, PROMPT_VERSION,
    render_milestones_block, validate_prompt3_json, load_prompt,
)


# ---------- Prompt version + file ----------

def test_prompt_constants_point_to_v3():
    assert PROMPT_FILE == "project_status_reasoning_v3"
    assert PROMPT_VERSION == "ProjectStatusReasoning/v3"


def test_prompt_v3_file_exists_and_loads():
    sys_prompt, user_template = load_prompt(PROMPT_FILE)
    # Substantial system prompt
    assert "ASYMMETRIC MILESTONE TRUST MODEL" in sys_prompt
    # New v3 section
    assert "COMPLETION-DATE CROSS-CHECK" in sys_prompt
    # Placeholders for substitution remain in the user template
    assert "{confluence_milestones_block}" in user_template


# ---------- render_milestones_block — Completion Date integration ----------

def test_render_milestones_block_includes_completion_date_when_set():
    """When MilestoneRow.completion_date is populated, the rendered line
    shows it right after planned= so the AI sees both dates together."""
    rows = [
        MilestoneRow(
            name="M1 Design freeze",
            planned_date="2026-04-15",
            completion_date="2026-04-12",
            status="Done",
            description="Design signed off",
        ),
    ]
    out = render_milestones_block(rows)
    assert "M1 Design freeze" in out
    assert "planned=2026-04-15" in out
    assert "completion_date=2026-04-12" in out
    assert "tl_declared_status=Done" in out
    # Order check — completion_date should appear after planned, before tl_declared_status
    assert out.index("planned=2026-04-15") < out.index("completion_date=2026-04-12")
    assert out.index("completion_date=2026-04-12") < out.index("tl_declared_status=Done")


def test_render_milestones_block_omits_completion_date_when_empty():
    """Tables that don't have the column (or unfilled cells) → no completion_date
    field appears in the rendered line. No 'completion_date=' noise."""
    rows = [
        MilestoneRow(
            name="M2 Build complete",
            planned_date="2026-05-20",
            completion_date="",   # empty
            status="In-progress",
            description="API integration in progress",
        ),
    ]
    out = render_milestones_block(rows)
    assert "M2 Build complete" in out
    assert "completion_date" not in out


def test_render_milestones_block_mixed_rows():
    """Some milestones have completion_date, others don't — only the ones
    that do should show it in their line."""
    rows = [
        MilestoneRow(name="M1", planned_date="2026-04-15",
                     completion_date="2026-04-12", status="Done",
                     description="done early"),
        MilestoneRow(name="M2", planned_date="2026-05-20",
                     completion_date="",  status="In-progress",
                     description="ongoing"),
    ]
    out = render_milestones_block(rows)
    lines = out.split("\n")
    assert "completion_date=2026-04-12" in lines[0]   # M1 has it
    assert "completion_date" not in lines[1]           # M2 doesn't


# ---------- Validator: completion_date acceptance ----------

def _base_response() -> dict:
    """Minimal valid Prompt 3 response shape."""
    return {
        "overall_health": "Amber",
        "schedule_status": "AtRisk",
        "completion_pct": 40,
        "milestones": [
            {
                "name": "M1", "planned_date": "2026-04-15",
                "tl_declared_status": "In-progress",
                "ai_verification": "NotApplicable",
                "evidence": "ok",
            },
        ],
        "rationale": "Something specific happened.",
        "confidence": "Medium",
        "evidence_cited": ["M1"],
    }


def test_validator_accepts_milestone_with_completion_date_string():
    resp = _base_response()
    resp["milestones"][0]["completion_date"] = "2026-04-12"
    ok, issues = validate_prompt3_json(resp)
    assert ok is True, issues


def test_validator_accepts_milestone_with_completion_date_null():
    """Per v3 prompt rule 4b: LLM sets completion_date to null when the
    Confluence row had no value. Validator must accept that."""
    resp = _base_response()
    resp["milestones"][0]["completion_date"] = None
    ok, issues = validate_prompt3_json(resp)
    assert ok is True, issues


def test_validator_accepts_milestone_without_completion_date_field_at_all():
    """Backwards-compat: response that omits completion_date entirely is
    still valid (legacy v2 LLM behavior must not regress)."""
    resp = _base_response()
    assert "completion_date" not in resp["milestones"][0]
    ok, issues = validate_prompt3_json(resp)
    assert ok is True, issues


def test_validator_rejects_completion_date_that_is_not_string_or_null():
    """An LLM returning a numeric or dict completion_date is malformed."""
    resp = _base_response()
    resp["milestones"][0]["completion_date"] = 20260412  # number, not string
    ok, issues = validate_prompt3_json(resp)
    assert ok is False
    assert any("completion_date" in i for i in issues), issues
