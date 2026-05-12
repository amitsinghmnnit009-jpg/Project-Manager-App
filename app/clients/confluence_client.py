"""Confluence Data Center REST client (read-only for Phase 1).

Adapted from WR-Project/confluence_client.py — write operations stripped
since Phase 1 only reads project pages.

Two main public methods:
- get_page_by_url(url)   → fetch a Confluence page by its display URL
- parse_project_page(...)  → extract Milestones table + Functional Requirements
                              section + Project Overview from storage-format HTML
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote
import re
import time

import requests
from bs4 import BeautifulSoup

from app.clients.http_session import build_session, rate_limit_wait
from app.config import get_config
from app.utils.logging import echo_external_call, external_call_log, system_log, sync_log


# ---------- Public dataclasses --------------------------------------------

@dataclass
class MilestoneRow:
    """One row from a Milestones table.

    Fields beyond name/planned_date/status/description are optional — the
    slim 4-column template only fills the four required fields, and the
    legacy 8-column template fills all eight. Either parses fine.
    """
    name: str = ""
    quarter: str = ""
    planned_date: str = ""
    completion_date: str = ""   # actual completion date — TL-asserted
    priority: str = ""
    status: str = ""        # TL-declared
    dependency: str = ""
    description: str = ""
    remark: str = ""


@dataclass
class MilestonesPageContent:
    """Parsed content of a project's *Milestones* page (Template A).

    The whole page is the milestones source. Parser locates the first table
    on the page (or in any section) and extracts rows. If a 'Project Overview'
    heading is present, its text is captured separately for prompt context.
    """
    title: str = ""
    overview: str = ""
    milestones: list[MilestoneRow] = field(default_factory=list)
    raw_html: str = ""
    parse_warnings: list[str] = field(default_factory=list)


@dataclass
class FRsPageContent:
    """Parsed content of a project's *Functional Requirements* page (Template B).

    Free-form. Whole body becomes `functional_requirements` text. If a
    'Project Overview' heading is present, its text is captured separately.
    """
    title: str = ""
    overview: str = ""
    functional_requirements: str = ""
    raw_html: str = ""
    parse_warnings: list[str] = field(default_factory=list)


@dataclass
class ExtraPageContent:
    """Parsed content of an *extra context* page (Template C).

    Page body is treated as supplementary background — extracted as plain
    text, truncated to `max_chars`. The AI is instructed not to derive
    milestones or FRs from these pages; they only inform rationale.
    """
    title: str = ""
    body_text: str = ""
    truncated: bool = False
    raw_html: str = ""
    parse_warnings: list[str] = field(default_factory=list)




# ---------- URL parsing ---------------------------------------------------

def _parse_page_url(url: str) -> dict:
    """Extract identifiers from a Confluence page URL.

    Returns a dict with any combination of: space_key, page_id, title.
    The fetcher prefers (space_key + title) over page_id alone — corporate
    Confluence DC instances often rate-limit /rest/api/content/{id}/...
    aggressively while leaving /rest/api/content?title=...&spaceKey=...
    permissive (POC's verified pattern). Extracting the title from URLs
    that contain it lets us stay on the safe path.

    Supported shapes:
      .../pages/viewpage.action?pageId=12345
      .../display/SPACE/Page+Title
      .../spaces/SPACE/pages/12345/Page+Title
    """
    parsed = urlparse(url)
    path = parsed.path
    qs = parse_qs(parsed.query)

    # ?pageId=...   (no title in URL — must use page_id form)
    if "pageId" in qs:
        return {"page_id": qs["pageId"][0]}

    # /spaces/SPACE/pages/12345/Page+Title
    # All three identifiers present; trailing title segment is optional.
    m = re.search(r"/spaces/([^/]+)/pages/(\d+)(?:/([^/?#]+))?", path)
    if m:
        out = {"space_key": m.group(1), "page_id": m.group(2)}
        title_seg = m.group(3)
        if title_seg:
            out["title"] = unquote(title_seg).replace("+", " ")
        return out

    # /display/SPACE/Page+Title
    m = re.search(r"/display/([^/]+)/(.+?)(?:[?#]|$)", path)
    if m:
        return {
            "space_key": m.group(1),
            "title": unquote(m.group(2)).replace("+", " "),
        }

    return {}


# ---------- Client --------------------------------------------------------

class ConfluenceClient:
    """Thin REST wrapper for Confluence Data Center.

    Bearer PAT authentication. Read-only methods only in Phase 1.
    Per-call delay is configurable to stay under rate limits.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        ca_bundle: str = "",
        enable_http_logging: bool = False,
        request_delay_seconds: float = 10.0,
        retry_total: int = 1,
    ):
        if not base_url:
            raise ValueError("Confluence base_url is required (set confluence.base_url in config.json)")
        if not token:
            raise ValueError("Confluence token is required (set confluence.token in config.json)")

        base = base_url.rstrip("/")
        # Atlassian Cloud puts Confluence at /wiki — DC usually doesn't.
        if "atlassian.net" in base and not base.endswith("/wiki"):
            base = base + "/wiki"
        self.base = base
        self.delay = request_delay_seconds

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Referer": base,
            "Origin": base,
        }
        self.s = build_session(
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
            headers=headers,
            enable_http_logging=enable_http_logging,
            retry_total=retry_total,
        )

    # ---------- low-level ------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """GET helper with optional full-call logging.

        When config.logging.log_full_external_calls is True, every call
        writes one JSONL line to logs/external_calls.jsonl with method,
        path, query params, status, duration, and a result summary keyed
        off the response shape (page id+title for /content fetches, count
        for collections). Inspected via `manage.py show-last-external-calls`.

        The pre-call sleep (request_delay_seconds) runs BEFORE the timer
        starts so the duration reflects actual HTTP time, not throttling.
        """
        if self.delay:
            time.sleep(self.delay)
        t0 = time.time()
        try:
            r = self.s.get(f"{self.base}{path}", params=params, timeout=30)
        except Exception as e:
            self._log_call(path, params, status=None,
                           duration=round(time.time() - t0, 3),
                           error=f"{type(e).__name__}: {e}")
            raise
        duration = round(time.time() - t0, 3)
        try:
            r.raise_for_status()
        except Exception:
            self._log_call(path, params, status=r.status_code, duration=duration,
                           error=(r.text or "")[:300])
            raise
        data = r.json()
        self._log_call(path, params, status=r.status_code, duration=duration,
                       response=data)
        return data

    def _log_call(self, path: str, params: Optional[dict],
                  *, status, duration: float,
                  response=None, error: str = ""):
        """Per-call logging for Confluence — JSONL audit trail + stderr echo.

        Two independent outputs, each with its own config gate:
          - logs/external_calls.jsonl (gated by log_full_external_calls)
          - one-line stderr echo      (gated by echo_external_calls_to_stderr)
        """
        cfg = get_config()

        # ---- Build the result summary once (used by stderr echo + JSONL) ----
        summary: Optional[dict] = None
        if not error and isinstance(response, dict):
            # Keyed off the response shape — collection vs single page.
            if "results" in response:
                results = response.get("results") or []
                summary = {
                    "result_count": len(results),
                    "size": response.get("size", len(results)),
                    "first_titles": [
                        (r.get("title") or "")[:60] for r in results[:5]
                    ],
                }
            elif "id" in response and "title" in response:
                summary = {
                    "page_id": response.get("id"),
                    "title": (response.get("title") or "")[:80],
                    "type": response.get("type", ""),
                }

        # ---- Live stderr echo (independent of JSONL log) -------------------
        if cfg.logging.echo_external_calls_to_stderr:
            echo_external_call(
                "confluence", "GET", path, params,
                status=status, duration=duration,
                summary=summary, error=error,
            )

        # ---- JSONL audit trail --------------------------------------------
        if not cfg.logging.log_full_external_calls:
            return

        extra: dict = {
            "event": "confluence_call",
            "source": "confluence",
            "method": "GET",
            "path": path,
            "query_params": dict(params or {}),
            "status": status,
            "duration_seconds": duration,
        }
        if error:
            extra["error"] = error
        elif summary is not None:
            extra["result_summary"] = summary
        external_call_log().info("confluence call", extra=extra)

    # ---------- whoami / health -----------------------------------------

    def whoami(self) -> dict:
        """Verify the token works by fetching one piece of content (minimal data).

        Endpoint history:
          - /rest/api/user/current  → blocked by per-endpoint rate-limit
                                      (corporate WAF/admin policy: anti-enumeration)
          - /rest/api/space         → also blocked by the same policy on at least
                                      one verified DC instance (limit=0, fillrate=0)
          - /rest/api/content       → works. This is what WR-Project uses for all
                                      its real reads; matching its endpoint family
                                      avoids the per-endpoint rate-limit overrides.

        Returns the first visible content item (or empty if none accessible).
        """
        data = self._get("/rest/api/content", {"limit": 1})
        first = (data.get("results") or [{}])[0]
        return {
            "content_visible": data.get("size", 0),
            "first_page": {
                "id": first.get("id", ""),
                "title": first.get("title", ""),
                "type": first.get("type", ""),
            },
        }

    # ---------- page fetch ----------------------------------------------

    def get_page_by_id(self, page_id: str, expand: str = "body.storage,version") -> dict:
        return self._get(f"/rest/api/content/{page_id}", {"expand": expand})

    def get_page_by_title(self, space_key: str, title: str,
                          expand: str = "body.storage,version") -> dict:
        data = self._get(
            "/rest/api/content",
            {"title": title, "spaceKey": space_key, "expand": expand, "limit": 5},
        )
        results = data.get("results", [])
        if not results:
            raise LookupError(
                f"Confluence page not found by title={title!r} in space={space_key!r}"
            )
        return results[0]

    def get_page_by_url(self, url: str, expand: str = "body.storage,version") -> dict:
        """Fetch a page given its display URL.

        Routing rule: prefer the (space_key + title) query form over the
        path-form /rest/api/content/{id}. Many corporate Confluence DC
        instances apply per-endpoint rate-limit overrides to /content/{id}/...
        while leaving /rest/api/content?title=...&spaceKey=... permissive
        (this matches the WR-Project POC's working pattern). When the URL
        gives us all three identifiers, we ignore page_id and use title.

        Fallback: if only a pageId is available (e.g. viewpage.action?pageId=N),
        we use /rest/api/content/{id}. If that path is locked at your site,
        the only fix is to use a URL that includes the title.
        """
        sys = system_log()
        ids = _parse_page_url(url)
        if not ids:
            raise ValueError(f"Could not extract page id or space+title from URL: {url}")

        sys.info("confluence get_page_by_url", extra={"url": url, "parsed": ids})

        # Preferred: title-based query form (POC pattern, avoids /content/{id})
        if "space_key" in ids and "title" in ids:
            return self.get_page_by_title(ids["space_key"], ids["title"], expand=expand)
        # Fallback: id-based path form (only when title isn't in the URL)
        if "page_id" in ids:
            return self.get_page_by_id(ids["page_id"], expand=expand)
        raise ValueError(f"URL parsed but missing identifiers: {ids}")

    # ---------- parsing -------------------------------------------------

    @staticmethod
    def parse_milestones_page(page: dict) -> MilestonesPageContent:
        """Parse a page that IS the Milestones page (Template A).

        Strategy:
          1. If a 'Project Overview' heading is present, capture its text.
          2. Locate the milestones table — first try the section under any
             'Milestones' heading; fall back to the first <table> on the page.
          3. Parse rows via _parse_milestones_table (slim 4-col AND legacy
             8-col formats both supported by header-name matching).
        """
        title = page.get("title", "")
        body_html = page.get("body", {}).get("storage", {}).get("value", "") or ""
        result = MilestonesPageContent(title=title, raw_html=body_html)

        if not body_html:
            result.parse_warnings.append("Page body is empty.")
            return result

        soup = BeautifulSoup(body_html, "html.parser")

        # Optional: capture overview if present
        overview_block = _find_section_content(soup, "overview")
        if overview_block:
            result.overview = _to_text(overview_block)

        # Try the milestones section first; fall back to first table on page.
        table = None
        ms_block = _find_section_content(soup, "milestone")
        if ms_block:
            table = _find_table_in_block(ms_block)

        if table is None:
            # No 'Milestones' heading — assume the whole page is milestones.
            # Take the first table.
            table = soup.find("table")
            if table is None:
                result.parse_warnings.append(
                    "Milestones page has no table (looked under 'Milestones' "
                    "heading and as the first table on the page)."
                )
                return result

        result.milestones = _parse_milestones_table(table, result.parse_warnings)
        return result

    @staticmethod
    def parse_fr_page(page: dict) -> FRsPageContent:
        """Parse a page that IS the Functional Requirements page (Template B).

        Free-form. Strategy:
          1. If a 'Project Overview' heading is present, capture its text.
          2. If a 'Functional Requirements' (or 'Requirements') heading is
             present, take its section as the FR text.
          3. Otherwise, treat the whole body as FR text.
        """
        title = page.get("title", "")
        body_html = page.get("body", {}).get("storage", {}).get("value", "") or ""
        result = FRsPageContent(title=title, raw_html=body_html)

        if not body_html:
            result.parse_warnings.append("Page body is empty.")
            return result

        soup = BeautifulSoup(body_html, "html.parser")

        overview_block = _find_section_content(soup, "overview")
        if overview_block:
            result.overview = _to_text(overview_block)

        # Prefer the Functional Requirements section; fall back to whole body.
        fr_block = _find_section_content(soup, "requirement")
        if fr_block:
            result.functional_requirements = _to_text(fr_block)
        else:
            # Whole body is FR text. Strip the overview heading + content if
            # we already captured it separately, to avoid duplication.
            if result.overview:
                result.functional_requirements = _body_text_excluding_section(
                    soup, "overview"
                )
            else:
                result.functional_requirements = soup.get_text(
                    separator="\n", strip=True
                )
            if not result.functional_requirements.strip():
                result.parse_warnings.append("FR page body is empty after parsing.")

        return result

    @staticmethod
    def parse_extra_page(page: dict, max_chars: int = 3000) -> ExtraPageContent:
        """Parse a supplementary context page (Template C).

        Whole body becomes plain text, truncated to `max_chars`. Sets
        `truncated=True` when truncation actually occurred. Used for the
        optional extra context pages — the AI is instructed to use these
        as background only and not derive milestones or FRs from them.
        """
        title = page.get("title", "")
        body_html = page.get("body", {}).get("storage", {}).get("value", "") or ""
        result = ExtraPageContent(title=title, raw_html=body_html)

        if not body_html:
            result.parse_warnings.append("Page body is empty.")
            return result

        soup = BeautifulSoup(body_html, "html.parser")
        text = soup.get_text(separator="\n", strip=True)

        if len(text) > max_chars:
            result.body_text = text[:max_chars] + "\n\n[... truncated ...]"
            result.truncated = True
        else:
            result.body_text = text

        return result


# ---------- Parsing helpers (module-level) -------------------------------

def _to_text(elements) -> str:
    """Join a list of BeautifulSoup elements into plain text, preserving paragraphs."""
    parts: list[str] = []
    for el in elements:
        if hasattr(el, "get_text"):
            t = el.get_text(separator=" ", strip=True)
            if t:
                parts.append(t)
        else:
            s = str(el).strip()
            if s:
                parts.append(s)
    return "\n\n".join(parts)


def _find_section_content(soup, heading_substring: str):
    """Find a heading whose text contains `heading_substring` (case-insensitive)
    and return all sibling elements until the next heading. Returns None if
    no matching heading exists. Used by the page parsers to optionally locate
    a 'Project Overview' / 'Milestones' / 'Functional Requirements' section.
    """
    needle = heading_substring.lower()
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        if needle in h.get_text(strip=True).lower():
            content = []
            for sib in h.find_next_siblings():
                if sib.name and re.match(r"^h[1-6]$", sib.name):
                    break
                content.append(sib)
            return content
    return None


def _find_table_in_block(block) -> "object | None":
    """Locate a <table> inside a block of BeautifulSoup elements (which may
    include the table directly, or wrap it in a div / macro). Returns the
    Tag, or None."""
    if not block:
        return None
    direct = next((c for c in block if getattr(c, "name", None) == "table"), None)
    if direct is not None:
        return direct
    for c in block:
        if hasattr(c, "find"):
            t = c.find("table")
            if t is not None:
                return t
    return None


def _body_text_excluding_section(soup, heading_substring: str) -> str:
    """Return the page body as plain text, excluding the section under the
    heading matching `heading_substring`. Used by parse_fr_page when the
    overview was captured separately and we don't want to duplicate it in
    the FR text."""
    needle = heading_substring.lower()
    skip_until_next_heading = False
    parts: list[str] = []
    for el in soup.children:
        # Skip the matched heading and its content until the next heading
        if hasattr(el, "name") and el.name and re.match(r"^h[1-6]$", el.name):
            if needle in el.get_text(strip=True).lower():
                skip_until_next_heading = True
                continue
            else:
                skip_until_next_heading = False
        if skip_until_next_heading:
            continue
        if hasattr(el, "get_text"):
            t = el.get_text(separator=" ", strip=True)
            if t:
                parts.append(t)
        else:
            s = str(el).strip()
            if s:
                parts.append(s)
    return "\n\n".join(parts)


def _parse_milestones_table(table, warnings: list[str]) -> list[MilestoneRow]:
    """Parse a milestones <table>. The header row identifies columns by name."""
    rows = table.find_all("tr")
    if not rows:
        warnings.append("Milestones table contains no rows.")
        return []

    # First row holds headers (th OR td)
    header_cells = rows[0].find_all(["th", "td"])
    if not header_cells:
        warnings.append("Milestones table header row is empty.")
        return []

    headers = [c.get_text(strip=True).lower() for c in header_cells]
    column_map: dict[str, int] = {}
    for i, h in enumerate(headers):
        if h in ("milestone", "name", "title"):
            column_map.setdefault("name", i)
        elif "quarter" in h:
            column_map["quarter"] = i
        # NOTE: completion-date branch must come BEFORE planned-date because
        # "completion date" contains the substring "date" which would
        # otherwise be claimed by the planned-date rule.
        elif ("completion" in h or "completed" in h
              or "actual end" in h or h == "actual end date"):
            column_map["completion_date"] = i
        elif "planned" in h or h in ("date", "target date", "target"):
            column_map.setdefault("planned_date", i)
        elif "priority" in h:
            column_map["priority"] = i
        elif "status" in h:
            column_map["status"] = i
        elif "depend" in h:
            column_map["dependency"] = i
        elif "description" in h or h == "desc":
            column_map["description"] = i
        elif "remark" in h or "note" in h or "comment" in h:
            column_map["remark"] = i

    if "name" not in column_map:
        warnings.append("Milestones table header has no 'Milestone' / 'Name' column.")

    out: list[MilestoneRow] = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        def get(key: str) -> str:
            idx = column_map.get(key)
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx].get_text(separator=" ", strip=True)

        m = MilestoneRow(
            name=get("name"),
            quarter=get("quarter"),
            planned_date=get("planned_date"),
            completion_date=get("completion_date"),
            priority=get("priority"),
            status=get("status"),
            dependency=get("dependency"),
            description=get("description"),
            remark=get("remark"),
        )
        # Skip empty rows
        if any([m.name, m.description, m.status]):
            out.append(m)
    return out
