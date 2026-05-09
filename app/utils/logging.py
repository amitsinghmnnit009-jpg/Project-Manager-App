"""Structured JSONL logger.

One log file per category, rotated by size. Every log line is one JSON object
on one line, easy to grep/parse. No prose; parseable telemetry only.
"""
from __future__ import annotations
import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_config


_loggers: dict[str, logging.Logger] = {}


class _JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # tz-aware UTC: datetime.utcnow() is deprecated in Python 3.12+.
        # Format keeps the trailing 'Z' for backward-compatibility with any
        # existing JSONL log readers / dashboards.
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach any extra fields the caller passed
        for k, v in record.__dict__.items():
            if k in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
            ):
                continue
            payload[k] = v
        return json.dumps(payload, default=str)


def _build_logger(name: str) -> logging.Logger:
    cfg = get_config()
    log_dir = Path(cfg.logging.directory)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"pma.{name}")
    logger.setLevel(cfg.logging.level)
    logger.propagate = False

    if logger.handlers:
        return logger  # already configured

    file_path = log_dir / f"{name}.jsonl"
    handler = logging.handlers.RotatingFileHandler(
        file_path,
        maxBytes=cfg.logging.rotate_max_bytes,
        backupCount=cfg.logging.rotate_backups,
        encoding="utf-8",
    )
    handler.setFormatter(_JsonlFormatter())
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get or build a logger writing to logs/<name>.jsonl. Names should be lowercase short strings."""
    if name not in _loggers:
        _loggers[name] = _build_logger(name)
    return _loggers[name]


# Convenience helpers — pre-built loggers for the standard categories
def system_log() -> logging.Logger:
    return get_logger("system")


def ai_compute_log() -> logging.Logger:
    return get_logger("ai_compute")


def sync_log() -> logging.Logger:
    return get_logger("sync")


def reminder_log() -> logging.Logger:
    return get_logger("reminder")


def prompt_log() -> logging.Logger:
    """Full prompt + raw response per LLM call. Written by every LLM client's
    complete()/embed() method.

    Separate file (`logs/llm_prompts.jsonl`) from `ai_compute.jsonl` because
    full prompts are large (a few KB each) and would bloat the audit-trail
    log. This file is rotated by size like every other category — defaults
    in config.json's `logging` section bound the on-disk footprint.

    Used by `python manage.py show-last-llm-call` for ad-hoc inspection."""
    return get_logger("llm_prompts")
