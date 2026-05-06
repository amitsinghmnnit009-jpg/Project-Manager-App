"""Engineer registry — load engineer ↔ project mapping (Step 5 — STUB).

Reads ./data/engineer_project_mapping.json (path from config) and provides
lookup helpers. Reload requires app restart in Phase 1.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Engineer:
    name: str
    knox_id: str


def load_engineer_mapping() -> dict:
    """Read mapping JSON file. Returns {engineers: [...], assignments: [...]}."""
    raise NotImplementedError("Step 5")


def engineers_on_project(project_code: str) -> list[Engineer]:
    """Return engineers assigned to the given project."""
    raise NotImplementedError("Step 5")


def projects_for_engineer(knox_id: str) -> list[str]:
    """Return project codes the engineer is assigned to."""
    raise NotImplementedError("Step 5")


def is_known_engineer(jira_user_field: dict) -> Engineer | None:
    """Match a JIRA user dict (accountId/key/name/email) against the mapping.
    Returns the Engineer or None if unmapped (caller logs warning per FR §14).
    """
    raise NotImplementedError("Step 5")
