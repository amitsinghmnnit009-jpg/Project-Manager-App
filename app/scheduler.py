"""APScheduler — daily status recompute + weekly aggregation triggers.

(Step 9 — STUB.)

Phase 1 plan:
- One BackgroundScheduler running inside the FastAPI process
- Daily job: trigger status compute for all projects
- Per-project job: trigger weekly aggregation N minutes after each project's cutoff
- Per-project job: send pre-cutoff and post-cutoff reminders
"""
from __future__ import annotations


def start_scheduler() -> None:
    """Wire up all jobs; call from FastAPI startup."""
    raise NotImplementedError("Step 9")


def stop_scheduler() -> None:
    """Graceful shutdown; call from FastAPI shutdown."""
    raise NotImplementedError("Step 9")
