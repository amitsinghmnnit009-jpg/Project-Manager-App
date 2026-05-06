"""Versioned prompt templates loaded from .txt files.

Each prompt file uses a simple convention: starts with `SYSTEM:` then the
system prompt, then `USER:` then the user-prompt template (with {placeholders}).
"""
from __future__ import annotations
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(version_name: str) -> tuple[str, str]:
    """Load a prompt file by name (e.g. 'weekly_aggregation_v1').
    Returns (system_prompt, user_prompt_template).
    """
    p = PROMPTS_DIR / f"{version_name}.txt"
    if not p.exists():
        raise FileNotFoundError(f"Prompt template not found: {p}")
    text = p.read_text(encoding="utf-8")

    if "SYSTEM:" not in text or "USER:" not in text:
        raise ValueError(f"Prompt {version_name} must contain 'SYSTEM:' and 'USER:' markers")

    sys_part, _, rest = text.partition("SYSTEM:")
    sys_text, _, user_text = rest.partition("USER:")
    return sys_text.strip(), user_text.strip()
