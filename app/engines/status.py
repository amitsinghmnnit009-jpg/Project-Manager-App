"""Status Engine — Prompt 3 (Step 8 — STUB).

For one project:
1. Load project metadata
2. Fetch Confluence page (via ConfluenceClient) → milestones + FRs
3. Fetch JIRA snapshot (via JiraClient) → counts + recent activity
4. Load last N consolidated weekly reports
5. Run Prompt 3 via LLM client
6. Parse JSON output
7. Persist as ProjectStatus (current) + append to ProjectStatusHistory if changed
"""
from __future__ import annotations


def run_status_compute(project_code: str) -> dict:
    """Compute current project status (health, schedule, completion %, milestones)."""
    raise NotImplementedError("Step 8 — wire Confluence + JIRA + reports → Prompt 3 → DB")
