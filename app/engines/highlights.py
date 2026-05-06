"""Highlights Engine — Prompt 2 (Step 7 — STUB).

For one project for one week:
1. Load this week's draft consolidated report (no Highlights section yet)
2. Load last week's published consolidated report (or detect first-week)
3. Run Prompt 2 via LLM client
4. Splice the result into the Highlights / Things to Watch section
5. Persist updated WeeklyReport row
"""
from __future__ import annotations
from datetime import date


def run_highlights(project_code: str, week_of: date) -> dict:
    """Generate the Highlights section for the current week's report."""
    raise NotImplementedError("Step 7 — wire prior+current reports → Prompt 2 → splice")
