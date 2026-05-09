"""Loads and validates config.json.

Single source of truth for runtime configuration. Other modules call
`get_config()` and read attributes; never re-parse the file.

Phase 1 used config.yaml originally; switched to JSON because YAML's
indentation rules surprise non-developer admins editing the file by
hand. Pydantic is the validation layer either way — JSON's stricter
syntax just produces clearer error messages when the file is malformed.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional, Literal
from functools import lru_cache
import json
from pydantic import BaseModel, Field, field_validator


# --- Sub-schemas ----------------------------------------------------------

class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "gpt-oss:latest"
    embed_model: str = "nomic-embed-text"
    timeout_seconds: int = 600


class OpenAIConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "gpt-oss:latest"
    embed_model: str = "nomic-embed-text"
    timeout_seconds: int = 600
    api_key: str = ""
    custom_headers: dict[str, str] = Field(default_factory=dict)


class LLMConfig(BaseModel):
    provider: Literal["ollama", "openai"] = "ollama"
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    temperature: float = 0.2


class JiraConfig(BaseModel):
    base_url: str = ""
    token: str = ""
    api_version: str = "2"
    verify_ssl: bool = True
    ca_bundle: str = ""
    enable_http_logging: bool = False
    # urllib3 Retry total. 0 = no retries (1 attempt only). 6 = up to 7 attempts.
    retry_total: int = 6


class ConfluenceConfig(BaseModel):
    base_url: str = ""
    token: str = ""
    verify_ssl: bool = True
    ca_bundle: str = ""
    enable_http_logging: bool = False
    request_delay_seconds: float = 10.0
    # Confluence DC rate-limits aggressively — a high retry count can burn
    # the per-token bucket faster than it refills. Default low.
    retry_total: int = 1
    # Each extra context page (per project) is truncated to this many
    # characters before being inserted into the Status Engine prompt. Keeps
    # the context window predictable regardless of how long the page is.
    extra_page_max_chars: int = 3000


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./app.db"


class LoggingConfig(BaseModel):
    directory: str = "./logs"
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    rotate_max_bytes: int = 10 * 1024 * 1024
    rotate_backups: int = 10
    # When True (default for now while the app is stabilising), every LLM
    # call writes its FULL system_prompt + user_prompt + raw response to
    # logs/llm_prompts.jsonl. Inspect via `manage.py show-last-llm-call`.
    # Set to False in production / once prompts have stabilised — the
    # 200-char excerpt in logs/ai_compute.jsonl + AIComputeLog DB row are
    # still kept, so the audit trail remains intact.
    log_full_llm_prompts: bool = True
    # When True (default while stabilising), every JIRA / Confluence HTTP
    # call writes a structured line to logs/external_calls.jsonl with the
    # full path + query params (including JQL) + result summary + duration.
    # Inspect via `manage.py show-last-external-calls`. Set to False once
    # stable — the high-level engine summaries + retry/error logs in
    # system.jsonl continue regardless.
    log_full_external_calls: bool = True


class SchedulerConfig(BaseModel):
    status_recompute_cadence: Literal["daily", "hourly", "manual"] = "daily"
    weekly_aggregation_offset_minutes: int = 5
    timezone: str = "Asia/Kolkata"


class EmailConfig(BaseModel):
    mock: bool = True
    mock_log_path: str = "./logs/sent_emails.jsonl"
    smtp_host: str = ""
    smtp_port: int = 25
    from_address: str = "project-manager-app@noreply.local"


class RemindersConfig(BaseModel):
    hours_before_cutoff: int = 24
    hours_after_cutoff: int = 4


class ReportsConfig(BaseModel):
    retention_weeks: int = 4
    default_template_path: str = "./data/report_template_default.md"


class EngineersConfig(BaseModel):
    mapping_file: str = "./data/engineer_project_mapping.json"


class HolidaysConfig(BaseModel):
    calendar_file: str = "./data/holidays_ist_2026.json"
    default_calendar_id: str = "default"


class APIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class ProjectConfig(BaseModel):
    code: str
    name: str
    type: str = "general"
    description: str = ""
    owning_tl: str = ""
    owning_pgm: str = ""
    start_date: Optional[str] = None
    planned_end_date: Optional[str] = None
    jira_project_key: str
    # --- Confluence pages (FR §A.1.6 / CONFLUENCE_TEMPLATE_PHASE1.md) ---
    # Two structured pages are required: one for milestones, one for FRs.
    # Plus zero or more optional extra context pages whose body is added to
    # the prompt as supplementary background (truncated per
    # confluence.extra_page_max_chars).
    confluence_milestones_url: str
    confluence_fr_url: str
    confluence_extra_pages: list[str] = Field(default_factory=list)
    # --- Schedule + filtering ---
    weekly_cutoff: str = "Mon 13:00"
    week_boundary: Literal["monday", "sunday"] = "monday"
    recompute_cadence: Optional[Literal["daily", "hourly", "manual"]] = None
    issue_types: list[str] = Field(
        default_factory=lambda: ["Task", "Sub-task", "Story", "Bug"]
    )
    chronic_threshold: int = 3
    holiday_calendar_id: str = "default"


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    jira: JiraConfig = Field(default_factory=JiraConfig)
    confluence: ConfluenceConfig = Field(default_factory=ConfluenceConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    reminders: RemindersConfig = Field(default_factory=RemindersConfig)
    reports: ReportsConfig = Field(default_factory=ReportsConfig)
    engineers: EngineersConfig = Field(default_factory=EngineersConfig)
    holidays: HolidaysConfig = Field(default_factory=HolidaysConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    projects: list[ProjectConfig] = Field(default_factory=list)


# --- Loader ---------------------------------------------------------------

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@lru_cache(maxsize=1)
def get_config(path: Optional[Path] = None) -> AppConfig:
    """Load and validate config.json. Cached after first call.

    JSON parse errors include line:column markers, so a malformed file
    surfaces a precise location in the traceback rather than a vague
    structural complaint. Empty file is treated as `{}` (validation
    then falls through to defaults for every section).
    """
    p = path or CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        text = f.read()
    raw = json.loads(text) if text.strip() else {}
    if not isinstance(raw, dict):
        raise TypeError(
            f"Config file must contain a JSON object at the top level, got {type(raw).__name__}"
        )
    return AppConfig(**raw)


def reload_config() -> AppConfig:
    """Force a fresh load (clears the lru_cache)."""
    get_config.cache_clear()
    return get_config()
