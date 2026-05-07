"""Shared HTTP session builder with 429-aware retry.

Adapted from WR-Project/http_session.py — battle-tested against corporate
JIRA DC + Confluence DC. Key behaviours:

- urllib3 Retry with exponential backoff (cap 60s)
- 429 handling: parses x-ratelimit-interval-seconds + x-ratelimit-fillrate
  to compute the right wait, falling back to Retry-After (capped 120s),
  then a hard default
- POST/PUT 429 NEVER auto-retried — caller raises RateLimitError
- Corporate CA bundle support
- Optional HTTP request/response logging hook (writes to system.jsonl)
"""
from __future__ import annotations
import math
from typing import Callable
import urllib3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.utils.logging import system_log


# Defaults — tunable but proven safe in the POC
_FALLBACK_WAIT = 10.0      # seconds — used when no rate-limit headers present
_MAX_RETRY_AFTER = 120.0   # ceiling so a bogus header never hangs forever


class RateLimitError(RuntimeError):
    """Raised when a write operation hits 429.

    The HTTP layer never auto-retries POST/PUT/PATCH on 429 (rate-limited
    writes need application-level decisions, not blind retries). Callers
    catch this, wait `retry_after` seconds, and decide whether to retry.
    """
    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


def rate_limit_wait(headers) -> float:
    """Compute a sensible retry-after from response headers.

    Priority:
      1. Retry-After (if sane, < 1 hour)
      2. x-ratelimit-interval-seconds / x-ratelimit-fillrate (token refill gap)
      3. _FALLBACK_WAIT
    """
    ra = headers.get("Retry-After")
    if ra:
        try:
            v = float(ra)
            if v < 3600:
                return min(v, _MAX_RETRY_AFTER)
        except ValueError:
            pass

    interval = headers.get("x-ratelimit-interval-seconds")
    fillrate = headers.get("x-ratelimit-fillrate")
    if interval and fillrate:
        try:
            wait = math.ceil(float(interval) / float(fillrate))
            return min(float(wait), _MAX_RETRY_AFTER)
        except (ValueError, ZeroDivisionError):
            pass

    return _FALLBACK_WAIT


class _LoggingRetry(Retry):
    """urllib3 Retry that uses rate-limit headers and logs each attempt
    to the system JSONL log.
    """

    def is_retry(self, method, status_code, has_retry_after=False):
        # Never auto-retry write operations on 429 — caller handles those.
        if status_code == 429 and (method or "").upper() in ("PUT", "POST", "PATCH"):
            return False
        return super().is_retry(method, status_code, has_retry_after=has_retry_after)

    def get_retry_after(self, response) -> float | None:
        if response is None:
            return None

        if response.status != 429:
            value = super().get_retry_after(response)
            return min(value, _MAX_RETRY_AFTER) if value is not None else None

        return rate_limit_wait(response.headers)

    def increment(self, method=None, url=None, response=None, error=None, *a, **kw):
        # Fail-fast: if the server says the rate-limit bucket has fillrate=0,
        # there is nothing to wait for. Each retry just burns tokens that
        # aren't being replaced — stop immediately so we don't dig deeper.
        if response is not None and response.status == 429:
            fillrate_header = response.headers.get("x-ratelimit-fillrate")
            if fillrate_header is not None:
                try:
                    if float(fillrate_header) == 0:
                        log = system_log()
                        log.error(
                            f"http giving up on {method} → 429 (fillrate=0)",
                            extra={
                                "event": "http_rate_limit_exhausted",
                                "method": method,
                                "url": url,
                                "interval": response.headers.get("x-ratelimit-interval-seconds"),
                                "fillrate": fillrate_header,
                                "hint": (
                                    "Server-side rate-limit bucket is empty with no refill. "
                                    "Wait at least 1 hour or contact your Confluence/JIRA admin "
                                    "to check your token's quota."
                                ),
                            },
                        )
                        # Exhaust retry budget so urllib3 stops here
                        kw["_pool"] = kw.get("_pool")
                        # Force a non-retry by saying we have 0 attempts left
                        from urllib3.exceptions import MaxRetryError
                        raise MaxRetryError(
                            pool=kw.get("_pool"),
                            url=url,
                            reason=Exception(
                                "Rate-limit bucket exhausted (fillrate=0). "
                                "Wait for refill or check token quota with admin."
                            ),
                        )
                except ValueError:
                    pass

        new = super().increment(
            method=method, url=url, response=response, error=error, *a, **kw
        )

        log = system_log()
        attempts_left = new.total if new.total is not None else None

        if response is not None:
            extra = {
                "event": "http_retry",
                "method": method,
                "url": url,
                "status": response.status,
                "attempts_left": attempts_left,
            }
            if response.status == 429:
                extra["interval"] = response.headers.get(
                    "x-ratelimit-interval-seconds"
                )
                extra["fillrate"] = response.headers.get("x-ratelimit-fillrate")
                extra["wait"] = rate_limit_wait(response.headers)
            log.warning(f"http retry on {method} → {response.status}", extra=extra)
        else:
            log.warning(
                f"http retry on {method} (connection error)",
                extra={
                    "event": "http_retry",
                    "method": method,
                    "url": url,
                    "error": str(error),
                    "attempts_left": attempts_left,
                },
            )
        return new


def _log_response_hook(response: requests.Response, *args, **kwargs):
    """requests Session response hook — logs each request/response pair when
    enable_http_logging=True. Useful for diagnosing flaky JIRA/Confluence calls.
    """
    log = system_log()
    req = response.request
    log.debug(
        f"{req.method} {req.url} → {response.status_code}",
        extra={
            "event": "http_request",
            "method": req.method,
            "url": req.url,
            "status": response.status_code,
            "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
        },
    )


def build_session(
    *,
    verify_ssl: bool,
    ca_bundle: str,
    headers: dict,
    enable_http_logging: bool = False,
    retry_total: int = 6,
) -> requests.Session:
    """Build a requests.Session with retry + corporate-CA + optional logging.

    Args:
        verify_ssl: Whether to verify the TLS certificate
        ca_bundle: Optional path to a corporate CA bundle (overrides verify_ssl)
        headers: Headers attached to every request (including Authorization)
        enable_http_logging: When True, every request/response is logged
        retry_total: Max retries per request. 0 means single attempt only.
                     Lower values for endpoints with aggressive rate limits.
    """
    s = requests.Session()
    s.headers.update(headers)
    s.verify = ca_bundle if ca_bundle else verify_ssl

    if not verify_ssl and not ca_bundle:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Cap connect/read retries at retry_total — they shouldn't exceed the global
    # budget. status retries also bound by retry_total.
    retry = _LoggingRetry(
        total=retry_total,
        connect=min(3, retry_total),
        read=min(3, retry_total),
        status=retry_total,
        backoff_factor=2.0,
        backoff_max=60,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]),
        respect_retry_after_header=True,
        raise_on_status=True,
    )

    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    if enable_http_logging:
        s.hooks["response"].append(_log_response_hook)

    return s
