"""Aggregation Engine — Prompt 1 (Step 6 — STUB).

For one project for one week:
1. Load JIRA activity for the week (via JiraClient)
2. Group by engineer (using engineer registry)
3. Format raw inputs into the prompt template
4. Run Prompt 1 via LLM client
5. Persist result as a WeeklyReport row
"""
from __future__ import annotations
from datetime import date


def run_weekly_aggregation(project_code: str, week_of: date, *, regenerate: bool = False) -> dict:
    """Run the weekly aggregation pipeline for one project for one week."""
    raise NotImplementedError("Step 6 — wire JIRA → grouping → Prompt 1 → DB")
