"""Structured JSONL logger.

One log file per category, rotated by size. Every log line is one JSON object
on one line, easy to grep/parse. No prose; parseable telemetry only.
"""
from __future__ import annotations
import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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


def echo_external_call(source: str, method: str, path: str,
                       params: Optional[dict], *,
                       status, duration: float,
                       summary: Optional[dict] = None,
                       error: str = "") -> None:
    """One-line STDERR echo of an external HTTP call (JIRA / Confluence).

    Called from each client's _log_call() so the user can see live what's
    being asked of the external system while running CLI commands, instead
    of having to tail logs/external_calls.jsonl after the fact.

    Gated at the call site by cfg.logging.echo_external_calls_to_stderr.
    Writes to stderr so it doesn't interfere with stdout-piped CLI output
    (JSON dumps, report markdown, etc.). ASCII-only — no fancy arrows —
    so Windows codepages (cp1252) render it correctly.

    Format examples:
        [JIRA] GET /rest/api/2/search  jql=project = MAICTJ AND ...  -> 200 in 0.84s  (issues=12, total=12)
        [JIRA] GET /rest/api/2/issue/MAICTJ-3/comment                -> 200 in 0.31s  (comments=4)
        [JIRA] GET /rest/api/2/myself                                -> 200 in 0.22s  (user=alice)
        [CONFLUENCE] GET /rest/api/content  title=Project Status     -> 200 in 0.42s  (result_count=1)
        [JIRA] GET /rest/api/2/search  jql=...                        -> 429 in 0.18s  ERROR: Rate limit exceeded
    """
    label = f"[{source.upper()}]"
    line = f"{label} {method} {path}"
    p = params or {}
    if "jql" in p:
        line += f"  jql={p['jql']}"
    else:
        # Show useful short params; skip noisy ones like 'expand' that bloat the line.
        short = ", ".join(
            f"{k}={v}" for k, v in p.items()
            if k not in ("expand", "fields") and v not in (None, "")
        )
        if short:
            line += f"  {short}"
    if error:
        status_str = str(status) if status is not None else "ERR"
        line += f"  -> {status_str} in {duration}s  ERROR: {error[:200]}"
    else:
        line += f"  -> {status} in {duration}s"
        if summary:
            # Skip list-valued fields (first_keys, first_titles) — they're useful
            # in the JSONL log but visually noisy in a one-line terminal echo.
            kv = ", ".join(
                f"{k}={v}" for k, v in summary.items()
                if not isinstance(v, (list, dict))
            )
            if kv:
                line += f"  ({kv})"
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        # Echo is a best-effort debug aid — never let a stderr write
        # failure (e.g. closed stream in some test runners) propagate.
        pass


def external_call_log() -> logging.Logger:
    """Full per-call log of every JIRA / Confluence HTTP call.

    One JSONL line per call, written to `logs/external_calls.jsonl`. Each
    line records: source ('jira'|'confluence'), method, path, query_params,
    JQL (when present), status, duration_seconds, and a result_summary
    keyed off the response shape (issue_count + first keys for searches,
    comment/worklog count for sub-resource fetches, page id+title for
    Confluence content).

    Used by `python manage.py show-last-external-calls` for ad-hoc inspection
    when debugging 'why didn't issue X show up' / 'what did we ask JIRA?'.

    Gated by `config.logging.log_full_external_calls` (default True while
    the app is stabilising — flip to False once stable). The compact retry/
    error logs in `system.jsonl` continue regardless — disabling this only
    drops the verbose per-call log."""
    return get_logger("external_calls")
