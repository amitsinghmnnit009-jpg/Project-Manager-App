"""Shared building blocks for Highlights / Things-to-Watch (Prompt 2).

Single source of truth for:
- Loading Prompt 2's template
- Locating the `## Highlights / Things to Watch` section in a consolidated
  weekly report (tolerant of casing, trailing-text variations, and ## vs ###)
- Splicing AI-generated highlights into that section (insert OR replace)
- Stripping existing highlights content (so re-runs don't feed the LLM its
  own prior output)
- Building the full Prompt 2 (system + user) text

Both the production engine (engines/highlights.py) and any future standalone
test script should import from here so the prompt + splice logic stays in
exactly one place. Module-private prefix (`_`) is convention only.
"""
from __future__ import annotations
import re
from typing import Optional, Tuple

from app.prompts import load_prompt


PROMPT_FILE = "highlights_comparison_v1"
PROMPT_VERSION = "HighlightsComparison/v1"


# Match a level-2 markdown heading whose text starts with "highlights"
# (case-insensitive). Tolerates variations like:
#   ## Highlights / Things to Watch
#   ## Highlights and Things to Watch
#   ## HIGHLIGHTS
# Does NOT match level-3+ headings (### Highlights inside a category).
HIGHLIGHTS_HEADING_RE = re.compile(
    r"^##[ \t]+highlights\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

# Match the start of any subsequent level-2 heading after the highlights one.
# Used to find the END of the Highlights section's content.
NEXT_LEVEL2_RE = re.compile(r"^##[ \t]+", re.MULTILINE)


# ---------- Section finder ------------------------------------------------

def _find_highlights_span(markdown: str) -> Optional[Tuple[int, int, int, int]]:
    """Locate the Highlights section.

    Returns (heading_start, heading_end, content_start, content_end) where:
      - heading_start..heading_end is the `## Highlights ...` line
      - content_start = heading_end (just after the heading line)
      - content_end is the index of the next level-2 heading, or len(markdown)
        if Highlights is the last section

    Returns None when no Highlights heading exists.
    """
    m = HIGHLIGHTS_HEADING_RE.search(markdown)
    if not m:
        return None
    heading_start, heading_end = m.span()

    # Search for the next level-2 heading AFTER the highlights heading
    rest = markdown[heading_end:]
    nxt = NEXT_LEVEL2_RE.search(rest)
    if nxt:
        # Convert offset into the original string. nxt.start() is the index of
        # the '#' on the next heading line; we want everything up to (not
        # including) that line, so trim the trailing newline that precedes it.
        content_end = heading_end + nxt.start()
        # Walk back over a single trailing newline so we don't keep a blank
        # line tightly bound to the next section's heading.
        if content_end > 0 and markdown[content_end - 1] == "\n":
            content_end -= 1
    else:
        content_end = len(markdown)

    return heading_start, heading_end, heading_end, content_end


# ---------- Splice + strip helpers --------------------------------------

def splice_highlights(markdown: str, highlights_content: str) -> str:
    """Insert or REPLACE the content under the `## Highlights ...` heading.

    - If the heading exists, replace everything from the line after the
      heading up to the next `## ` heading (or EOF) with `highlights_content`.
    - If the heading does NOT exist, append a new section at the end of the
      report using the canonical name "## Highlights / Things to Watch".

    The supplied `highlights_content` is stripped of leading/trailing
    whitespace before being inserted; surrounding blank lines are added so
    the result is well-formed markdown.
    """
    body = (highlights_content or "").strip()
    span = _find_highlights_span(markdown)

    if span is None:
        # No Highlights section yet — append.
        prefix = markdown.rstrip("\n")
        if prefix:
            return f"{prefix}\n\n## Highlights / Things to Watch\n\n{body}\n"
        return f"## Highlights / Things to Watch\n\n{body}\n"

    heading_start, heading_end, content_start, content_end = span
    before = markdown[:heading_end]
    after = markdown[content_end:]

    # Wrap the new content with surrounding blank lines so the section is
    # visually distinct from neighbours regardless of source spacing.
    new_section = f"\n\n{body}\n"
    if after and not after.startswith("\n"):
        new_section += "\n"

    return before + new_section + after


def empty_highlights_section(markdown: str) -> str:
    """Return `markdown` with the Highlights section's content cleared.

    Heading is preserved when present (we want the LLM to see the section
    structure of THIS WEEK's draft report). Used to feed the LLM a clean
    "draft report with Highlights section empty" input on re-runs of Step 7,
    so the LLM never sees its own prior output and just echoes it back.

    If no Highlights section exists, returns the markdown unchanged.
    """
    span = _find_highlights_span(markdown)
    if span is None:
        return markdown

    _, heading_end, _, content_end = span
    before = markdown[:heading_end]
    after = markdown[content_end:]
    # Single blank line between heading and the next section (or EOF).
    sep = "\n\n" if after else "\n"
    return before + sep + after


# ---------- Full prompt assembly ----------------------------------------

def render_full_prompt(
    *,
    project_name: str,
    this_week_date: str,
    last_week_date: Optional[str],
    last_week_full_report: Optional[str],
    this_week_draft_report: str,
) -> tuple[str, str]:
    """Load Prompt 2 and render with all placeholders filled.

    `last_week_date` and `last_week_full_report` may be None for a project's
    very first reported week. In that case the prompt's spec triggers the
    LLM to output exactly: "First report for this project — no prior week
    to compare against." (Prompt 2's system instructions handle this case.)

    Returns (system_prompt, user_prompt). Keyword-only to prevent silent
    arg-order mistakes.
    """
    sys_prompt, user_template = load_prompt(PROMPT_FILE)
    user_prompt = user_template.format(
        project_name=project_name,
        this_week_date=this_week_date,
        last_week_date=last_week_date or "(no prior week)",
        last_week_full_report=(
            last_week_full_report
            if last_week_full_report and last_week_full_report.strip()
            else "(no prior-week report exists for this project)"
        ),
        this_week_draft_report=this_week_draft_report,
    )
    return sys_prompt, user_prompt
