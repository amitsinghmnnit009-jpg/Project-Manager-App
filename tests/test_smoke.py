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

    sys_prompt, user_prompt = load_prompt("project_status_reasoning_v1")
    assert "insufficient" in sys_prompt.lower()
    assert "{confluence_milestones_block}" in user_prompt


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
