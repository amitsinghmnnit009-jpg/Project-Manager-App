"""Shared building blocks for Weekly Aggregation (Prompt 1).

Single source of truth for:
- Loading the report template (currently global default; per-project
  override is FR §B.2.2 and tracked for a follow-up — when added, the
  caller passes a per_project_path to load_report_template())
- Extracting section names from the template's `## Heading` lines
- Anonymising engineers as E1, E2, ... (per FR §B "no engineer names")
- Rendering raw JIRA activity into the prompt's RAW INPUTS block
- Building the full Prompt 1 (system + user) text

Module-private prefix (`_`) is convention only — anything that needs to
render exactly the same prompt the engine renders should import from here.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

from app.config import get_config
from app.prompts import load_prompt


PROMPT_FILE = "weekly_aggregation_v1"
PROMPT_VERSION = "WeeklyAggregation/v1"


# JIRA frequently embeds NBSP variants (U+00A0 NO-BREAK SPACE, U+202F NARROW
# NO-BREAK SPACE) in rendered date/time strings — e.g. "04/May/26 11:40 am".
# When the LLM reproduces those characters in the report, terminals print them
# as 'NNBSP'/garbage and Markdown renderers may collapse or misalign them. Map
# any NBSP variant back to a regular ASCII space before the value reaches the
# prompt so the LLM never sees them in the first place.
_NBSP_VARIANTS = str.maketrans({
    " ": " ",   # NO-BREAK SPACE
    " ": " ",   # NARROW NO-BREAK SPACE
    " ": " ",   # FIGURE SPACE
    " ": " ",   # THIN SPACE
    "​": "",    # ZERO WIDTH SPACE — strip entirely (invisible)
})


def _clean(s) -> str:
    """Normalise a string before it lands in the prompt: NBSP→space, strip
    zero-width chars. Returns '' for None / non-strings."""
    if s is None:
        return ""
    return str(s).translate(_NBSP_VARIANTS)


# ---------- Template loading + section extraction ------------------------

def load_report_template(per_project_path: Optional[str] = None) -> str:
    """Load the report template markdown.

    Resolution order:
      per_project_path (when set, FR §B.2.2 override) →
      config.reports.default_template_path (global default)

    Relative paths are resolved against the repo root (the `Project-Manager-App/`
    directory) since the config typically uses paths like `./data/...`.
    """
    cfg = get_config()
    path_str = per_project_path or cfg.reports.default_template_path
    path = Path(path_str)
    if not path.is_absolute():
        # Resolve relative to the repo root (this file lives at app/engines/)
        path = Path(__file__).resolve().parent.parent.parent / path
    return path.read_text(encoding="utf-8")


# Match `## Heading text` markdown headings (level 2). Level 1 (#) is the
# overall report title and is not a section.
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")


def extract_section_names(template_md: str) -> list[str]:
    """Extract section names from the template's `## Heading` lines, in order.

    Example input:
        # Weekly Project Report: ...
        ## Accomplishments
        - bullets
        ## In-progress / Ongoing work
        - bullets

    Returns: ["Accomplishments", "In-progress / Ongoing work", ...]
    """
    sections: list[str] = []
    for line in template_md.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            sections.append(m.group(1))
    return sections


def render_template_sections_block(sections: list[str]) -> str:
    """Render the section list for the prompt's TEMPLATE block.

    The LLM is instructed to use these exact names in this exact order.
    """
    if not sections:
        return "(template has no sections)"
    return "\n".join(f"## {s}" for s in sections)


# ---------- Raw-inputs rendering ----------------------------------------

def render_raw_inputs_block(activity_by_engineer: dict, anonymise: bool = True) -> str:
    """Format JIRA activity per engineer per task for the RAW INPUTS block.

    Args:
        activity_by_engineer: dict mapping knox_id → list[ActivityRecord]
                              (the JiraEngineerActivity.by_engineer field)
        anonymise: When True (default, production behaviour), engineers
                   are labelled E1, E2, ... so the prompt can't leak names.
                   When False (debugging), the real knox_id is shown.

    Output format example:

        --- Engineer E1 ---
        Task: PROJ-123 — Fix retry logic (status: In Progress)
          Comments added this week:
            [2026-05-08] reduced retry count to 1
          Work-logs added this week:
            [2026-05-08] 2h
          Status changes:
            [2026-05-09] In Progress -> Blocked

        --- Engineer E2 ---
        ...
    """
    if not activity_by_engineer:
        return "(no engineer activity recorded for this week)"

    # Build a stable knox_id → "E1"/"E2"/... mapping for anonymisation.
    # Iteration order is dict-insertion order (Python 3.7+), which mirrors
    # the order JIRA returned engineers in — stable across reruns.
    engineer_codes = {
        knox_id: f"E{i}"
        for i, knox_id in enumerate(activity_by_engineer.keys(), 1)
    }

    blocks: list[str] = []
    for knox_id, records in activity_by_engineer.items():
        label = engineer_codes[knox_id] if anonymise else knox_id
        block_lines: list[str] = [f"--- Engineer {label} ---"]

        # Group this engineer's records by task so each task's signals
        # (comments, worklogs, status changes) appear together.
        by_task: dict[str, list] = {}
        for r in records:
            by_task.setdefault(r.task_id, []).append(r)

        for task_id, recs in by_task.items():
            first = recs[0]
            block_lines.append(
                f"Task: {task_id} — {_clean(first.task_title)}"
                f" (status: {_clean(first.task_status)})"
            )

            comments = [r for r in recs if r.activity_kind == "comment"]
            worklogs = [r for r in recs if r.activity_kind == "worklog"]
            status_changes = [r for r in recs if r.activity_kind == "status_change"]
            updates = [r for r in recs if r.activity_kind == "updated"]
            creations = [r for r in recs if r.activity_kind == "created"]

            if comments:
                block_lines.append("  Comments added this week:")
                for c in comments:
                    ts = (c.timestamp or "")[:10]
                    block_lines.append(f"    [{ts}] {_clean(c.detail)}")
            if worklogs:
                block_lines.append("  Work-logs added this week:")
                for w in worklogs:
                    ts = (w.timestamp or "")[:10]
                    block_lines.append(f"    [{ts}] {_clean(w.detail)}")
            if status_changes:
                block_lines.append("  Status changes:")
                for sc in status_changes:
                    ts = (sc.timestamp or "")[:10]
                    block_lines.append(f"    [{ts}] {_clean(sc.detail)}")
            if updates:
                block_lines.append("  Other updates:")
                for u in updates:
                    ts = (u.timestamp or "")[:10]
                    block_lines.append(f"    [{ts}] {_clean(u.detail)}")
            if creations:
                block_lines.append("  Created this week:")
                for cr in creations:
                    ts = (cr.timestamp or "")[:10]
                    block_lines.append(f"    [{ts}] {_clean(cr.task_title)}")

        blocks.append("\n".join(block_lines))

    return "\n\n".join(blocks)


# ---------- Full prompt assembly ----------------------------------------

def render_full_prompt(
    *,
    project_name: str,
    project_type: str,
    project_overview: str,
    week_of_str: str,
    sections: list[str],
    activity_by_engineer: dict,
) -> tuple[str, str]:
    """Load Prompt 1 and render with all placeholders filled.

    Returns (system_prompt, user_prompt). All keyword-only so callers can't
    silently transpose project_name and project_type.
    """
    sys_prompt, user_template = load_prompt(PROMPT_FILE)
    user_prompt = user_template.format(
        project_name=project_name,
        project_type=project_type or "general",
        project_overview=project_overview or "(none)",
        week_of=week_of_str,
        template_sections_block=render_template_sections_block(sections),
        raw_inputs_block=render_raw_inputs_block(activity_by_engineer),
    )
    return sys_prompt, user_prompt
