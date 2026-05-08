"""Project registry — sync from config.json to DB at startup (Step 5 — STUB).

Reads `projects` list from config.json and upserts into the `projects`
table. Provides lookup helpers for other modules.
"""
from __future__ import annotations
from typing import Optional
from app.config import ProjectConfig


def sync_projects_from_config() -> int:
    """Read config.json projects list, upsert into DB. Returns count synced."""
    raise NotImplementedError("Step 5")


def get_project_by_code(code: str) -> Optional[dict]:
    raise NotImplementedError("Step 5")


def list_projects() -> list[dict]:
    raise NotImplementedError("Step 5")
