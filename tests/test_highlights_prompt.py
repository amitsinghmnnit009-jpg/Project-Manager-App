"""Tests for app.engines._highlights_prompt — shared Prompt 2 helpers
(section finding, splicing, stripping, full-prompt rendering).

Same flavour as test_aggregation_prompt.py — pure-function tests on the
helpers, no DB / LLM / network needed.
"""
from __future__ import annotations

from app.engines._highlights_prompt import (
    PROMPT_FILE, PROMPT_VERSION,
    HIGHLIGHTS_HEADING_RE, _find_highlights_span,
    splice_highlights, empty_highlights_section, render_full_prompt,
)


# ------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------

def test_prompt_constants():
    assert PROMPT_FILE == "highlights_comparison_v1"
    assert PROMPT_VERSION == "HighlightsComparison/v1"


# ------------------------------------------------------------------------
# Heading regex
# ------------------------------------------------------------------------

def test_heading_regex_matches_canonical_form():
    assert HIGHLIGHTS_HEADING_RE.search("## Highlights / Things to Watch")


def test_heading_regex_case_insensitive():
    assert HIGHLIGHTS_HEADING_RE.search("## HIGHLIGHTS / things to watch")
    assert HIGHLIGHTS_HEADING_RE.search("## highlights")


def test_heading_regex_tolerant_to_trailing_text():
    assert HIGHLIGHTS_HEADING_RE.search("## Highlights and Things to Watch")
    assert HIGHLIGHTS_HEADING_RE.search("## Highlights — for PGM")


def test_heading_regex_does_not_match_level3():
    """### Highlights inside a category header (e.g. inside the spliced
    output) must NOT be matched as the section heading."""
    assert HIGHLIGHTS_HEADING_RE.search("### Highlights") is None


def test_heading_regex_requires_word_boundary():
    """Must not match `## Highlighted projects` or similar."""
    assert HIGHLIGHTS_HEADING_RE.search("## Highlighted projects") is None


# ------------------------------------------------------------------------
# _find_highlights_span
# ------------------------------------------------------------------------

REPORT_WITH_EMPTY_HIGHLIGHTS = """\
# Weekly Project Report: Demo
**Week of:** 2026-05-04

## Accomplishments
- did thing A

## In-progress / Ongoing work
- working on B

## Risks and Blockers
- vendor delay

## Next-week plan
- finish B

## Highlights / Things to Watch

"""

REPORT_HIGHLIGHTS_HAS_CONTENT = """\
# Weekly Project Report: Demo
**Week of:** 2026-05-04

## Accomplishments
- did thing A

## Highlights / Things to Watch

### Missed commitments
- last week's plan to ship B did not materialise

### Carried-over risks/blockers
- vendor delay continues

## Footer notes

trailer text
"""

REPORT_NO_HIGHLIGHTS = """\
# Weekly Project Report: Demo

## Accomplishments
- did thing A

## In-progress / Ongoing work
- working on B
"""


def test_find_span_when_section_is_last():
    """Highlights at end of report; content_end is len(markdown)."""
    span = _find_highlights_span(REPORT_WITH_EMPTY_HIGHLIGHTS)
    assert span is not None
    h_start, h_end, c_start, c_end = span
    assert REPORT_WITH_EMPTY_HIGHLIGHTS[h_start:h_end] == "## Highlights / Things to Watch"
    assert c_start == h_end
    assert c_end == len(REPORT_WITH_EMPTY_HIGHLIGHTS)


def test_find_span_when_followed_by_another_section():
    """Highlights followed by `## Footer notes`; content_end is just before
    the next heading."""
    span = _find_highlights_span(REPORT_HIGHLIGHTS_HAS_CONTENT)
    assert span is not None
    _, _, c_start, c_end = span
    # Content should end just before `## Footer notes`
    assert REPORT_HIGHLIGHTS_HAS_CONTENT[c_end:].lstrip().startswith("## Footer notes")
    # Content should include the existing highlights body
    content = REPORT_HIGHLIGHTS_HAS_CONTENT[c_start:c_end]
    assert "Missed commitments" in content
    assert "vendor delay continues" in content


def test_find_span_returns_none_when_no_section():
    assert _find_highlights_span(REPORT_NO_HIGHLIGHTS) is None


# ------------------------------------------------------------------------
# splice_highlights
# ------------------------------------------------------------------------

NEW_HIGHLIGHTS = """\
### Missed commitments
- (none)

### Carried-over risks/blockers
- vendor delay (also present last week)

### Newly raised risks/blockers
- (none)

### Notably absent items
- (none)
"""


def test_splice_into_empty_section_appends_content():
    out = splice_highlights(REPORT_WITH_EMPTY_HIGHLIGHTS, NEW_HIGHLIGHTS)
    # All sections still present
    assert "## Accomplishments" in out
    assert "## Highlights / Things to Watch" in out
    assert "vendor delay (also present last week)" in out
    # Heading not duplicated
    assert out.count("## Highlights / Things to Watch") == 1


def test_splice_replaces_existing_content():
    """Re-run safety: an existing Highlights section is REPLACED, not appended."""
    out = splice_highlights(REPORT_HIGHLIGHTS_HAS_CONTENT, NEW_HIGHLIGHTS)
    # Old highlights body is gone
    assert "last week's plan to ship B did not materialise" not in out
    # New highlights body present
    assert "vendor delay (also present last week)" in out
    # Following section preserved
    assert "## Footer notes" in out
    assert "trailer text" in out
    # Heading not duplicated
    assert out.count("## Highlights / Things to Watch") == 1


def test_splice_appends_section_when_missing():
    """No Highlights heading → append a new one with canonical name."""
    out = splice_highlights(REPORT_NO_HIGHLIGHTS, NEW_HIGHLIGHTS)
    assert "## Highlights / Things to Watch" in out
    assert "vendor delay" in out
    # Original sections preserved
    assert "## Accomplishments" in out
    assert "## In-progress / Ongoing work" in out


def test_splice_handles_empty_content():
    """Splicing empty body still leaves the heading + a clean section."""
    out = splice_highlights(REPORT_WITH_EMPTY_HIGHLIGHTS, "")
    assert "## Highlights / Things to Watch" in out
    # No leftover NEW_HIGHLIGHTS body — naturally true since we passed ""


def test_splice_strips_surrounding_whitespace_in_content():
    """Body whitespace shouldn't bleed into the report formatting."""
    out = splice_highlights(REPORT_WITH_EMPTY_HIGHLIGHTS, "\n\n   - hello\n\n")
    assert "- hello" in out


def test_splice_does_not_match_level3_subheadings():
    """If the LLM's spliced content uses ### (level-3) subheadings, those
    must NOT be confused with the next section boundary."""
    out = splice_highlights(REPORT_WITH_EMPTY_HIGHLIGHTS, NEW_HIGHLIGHTS)
    # Level-3 subheadings inside Highlights section all preserved
    for sub in ("### Missed commitments", "### Carried-over risks/blockers",
                "### Newly raised risks/blockers", "### Notably absent items"):
        assert sub in out


# ------------------------------------------------------------------------
# empty_highlights_section
# ------------------------------------------------------------------------

def test_empty_strips_existing_content():
    out = empty_highlights_section(REPORT_HIGHLIGHTS_HAS_CONTENT)
    # Heading preserved
    assert "## Highlights / Things to Watch" in out
    # Old body gone
    assert "Missed commitments" not in out
    assert "vendor delay continues" not in out
    # Following section preserved
    assert "## Footer notes" in out
    assert "trailer text" in out


def test_empty_idempotent_when_already_empty():
    """Running on a report with empty Highlights doesn't mangle structure."""
    out = empty_highlights_section(REPORT_WITH_EMPTY_HIGHLIGHTS)
    assert "## Highlights / Things to Watch" in out
    assert out.count("## Highlights / Things to Watch") == 1


def test_empty_returns_unchanged_when_no_section():
    assert empty_highlights_section(REPORT_NO_HIGHLIGHTS) == REPORT_NO_HIGHLIGHTS


# ------------------------------------------------------------------------
# render_full_prompt
# ------------------------------------------------------------------------

def test_render_full_prompt_with_prior_week():
    sys_p, user_p = render_full_prompt(
        project_name="MAICTJ",
        this_week_date="2026-05-04",
        last_week_date="2026-04-27",
        last_week_full_report="## Some prior content",
        this_week_draft_report="## Some current content",
    )
    # System prompt contains the four-category instruction
    assert "MISSED COMMITMENTS" in sys_p
    assert "CARRIED-OVER" in sys_p
    # User prompt contains all our values
    assert "PROJECT: MAICTJ" in user_p
    assert "THIS WEEK: 2026-05-04" in user_p
    assert "LAST WEEK: 2026-04-27" in user_p
    assert "## Some prior content" in user_p
    assert "## Some current content" in user_p


def test_render_full_prompt_first_week_uses_placeholders():
    """No prior week → placeholders signal the LLM to emit the standard
    'first report' line per Prompt 2's spec."""
    sys_p, user_p = render_full_prompt(
        project_name="MAICTJ",
        this_week_date="2026-05-04",
        last_week_date=None,
        last_week_full_report=None,
        this_week_draft_report="## Some current content",
    )
    assert "(no prior week)" in user_p
    assert "(no prior-week report exists for this project)" in user_p
    # System prompt should still carry the first-week instruction
    assert "First report for this project" in sys_p


def test_render_full_prompt_treats_blank_last_week_as_first():
    """An existing-but-blank last_week_full_report should be normalised to
    the same placeholder as missing — defensive."""
    _, user_p = render_full_prompt(
        project_name="X",
        this_week_date="2026-05-04",
        last_week_date="2026-04-27",
        last_week_full_report="    \n  \n",
        this_week_draft_report="content",
    )
    assert "(no prior-week report exists for this project)" in user_p
