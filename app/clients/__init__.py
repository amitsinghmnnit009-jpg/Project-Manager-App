"""External REST API integrations (JIRA + Confluence).

Adapted from the WR-Project POC's client patterns: direct `requests` with a
custom session handling 429-aware retry, exponential backoff, corporate-CA
support, and rate-limit header parsing.
"""
from app.clients.http_session import build_session, RateLimitError, rate_limit_wait
from app.clients.jira_client import (
    JiraClient,
    ActivityRecord,
    JiraEngineerActivity,
    JiraProjectSnapshot,
    UnmappedAuthor,
)
from app.clients.confluence_client import (
    ConfluenceClient,
    MilestonesPageContent,
    FRsPageContent,
    ExtraPageContent,
    MilestoneRow,
)

__all__ = [
    "build_session", "RateLimitError", "rate_limit_wait",
    "JiraClient", "ActivityRecord", "JiraEngineerActivity",
    "JiraProjectSnapshot", "UnmappedAuthor",
    "ConfluenceClient",
    "MilestonesPageContent", "FRsPageContent", "ExtraPageContent",
    "MilestoneRow",
]


def get_jira_client():
    """Build a JiraClient from current config.json. Cached factory could be
    added later if construction cost becomes noticeable."""
    from app.config import get_config
    cfg = get_config().jira
    return JiraClient(
        base_url=cfg.base_url,
        token=cfg.token,
        api_version=cfg.api_version,
        verify_ssl=cfg.verify_ssl,
        ca_bundle=cfg.ca_bundle,
        enable_http_logging=cfg.enable_http_logging,
        retry_total=cfg.retry_total,
    )


def get_confluence_client():
    """Build a ConfluenceClient from current config.json."""
    from app.config import get_config
    cfg = get_config().confluence
    return ConfluenceClient(
        base_url=cfg.base_url,
        token=cfg.token,
        verify_ssl=cfg.verify_ssl,
        ca_bundle=cfg.ca_bundle,
        enable_http_logging=cfg.enable_http_logging,
        request_delay_seconds=cfg.request_delay_seconds,
        retry_total=cfg.retry_total,
    )
