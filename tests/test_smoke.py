"""Smoke tests — verify the scaffold is importable and config loads."""
from __future__ import annotations


def test_import_app():
    import app
    assert app.__version__ == "0.1.0"


def test_config_loads():
    from app.config import get_config
    cfg = get_config()
    assert cfg.llm.provider in ("ollama", "openai")
    assert cfg.api.port == 8000


def test_prompts_load():
    from app.prompts import load_prompt
    sys_prompt, user_prompt = load_prompt("weekly_aggregation_v1")
    assert "consolidated" in sys_prompt.lower()
    assert "{project_name}" in user_prompt

    sys_prompt, user_prompt = load_prompt("highlights_comparison_v1")
    assert "missed commitments" in sys_prompt.lower()

    # v1 still on disk for audit reproducibility (it's the version used in
    # any historical runs). v2 is the current production prompt.
    sys_prompt, user_prompt = load_prompt("project_status_reasoning_v1")
    assert "insufficient" in sys_prompt.lower()
    assert "{confluence_milestones_block}" in user_prompt

    # v2 must include the extra-context-pages placeholder + the strict rule
    # that prevents the AI from deriving milestones / FRs from extra pages.
    sys_prompt_v2, user_prompt_v2 = load_prompt("project_status_reasoning_v2")
    assert "insufficient" in sys_prompt_v2.lower()
    assert "{confluence_milestones_block}" in user_prompt_v2
    assert "{extra_context_pages_block}" in user_prompt_v2
    assert "{fr_page_overview}" in user_prompt_v2
    assert "{milestones_page_overview}" in user_prompt_v2
    assert "supplementary" in sys_prompt_v2.lower()
    # The asymmetric trust rule is critical — must survive prompt edits
    assert "asymmetric" in sys_prompt_v2.lower() or "tl-declared" in sys_prompt_v2.lower()


def test_dates_module():
    from app.utils.dates import week_of, parse_cutoff
    from datetime import date
    monday = week_of(date(2026, 5, 7))  # Thursday
    assert monday.weekday() == 0  # Monday

    weekday, t = parse_cutoff("Mon 13:00")
    assert weekday == 0
    assert t.hour == 13


def test_health_endpoint():
    from fastapi.testclient import TestClient
    from app.api.main import app
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
