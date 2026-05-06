"""Email notifications — mocked SMTP in Phase 1 (Step 10 — STUB).

Mock mode (config.email.mock = True): writes "would have sent" entries to
the JSONL file at config.email.mock_log_path. No real SMTP connection.

When email.mock = False (post-Phase-1), uses the SMTP config to actually send.
"""
from __future__ import annotations


def send_engineer_reminder(
    knox_id: str,
    name: str,
    project_code: str,
    week_of: str,
    type: str,    # 'pre_cutoff' | 'post_cutoff'
) -> None:
    """Send (or mock) a reminder email to an engineer who hasn't updated JIRA."""
    raise NotImplementedError("Step 10 — implement mock-first SMTP")
