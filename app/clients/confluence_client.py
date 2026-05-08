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
from app.utils.logging import system_log, sync_log


# ---------- Public dataclasses --------------------------------------------

@dataclass
class MilestoneRow:
    """One row from the Milestones table on the project's Confluence page."""
    name: str = ""
    quarter: str = ""
    planned_date: str = ""
    priority: str = ""
    status: str = ""        # TL-declared
    dependency: str = ""
    description: str = ""
    remark: str = ""


@dataclass
class ProjectPageContent:
    """Parsed content of a project's Confluence page — input for Prompt 3."""
    title: str = ""
    overview: str = ""
    milestones: list[MilestoneRow] = field(default_factory=list)
    functional_requirements: str = ""
    raw_html: str = ""
    parse_warnings: list[str] = field(default_factory=list)


# ---------- URL parsing ---------------------------------------------------

def _parse_page_url(url: str) -> dict:
    """Extract identifiers from a Confluence page URL.

    Supported shapes:
      .../pages/viewpage.action?pageId=12345
      .../display/SPACE/Page+Title
      .../spaces/SPACE/pages/12345/Page+Title
    """
    parsed = urlparse(url)
    path = parsed.path
    qs = parse_qs(parsed.query)

    # ?pageId=...
    if "pageId" in qs:
        return {"page_id": qs["pageId"][0]}

    # /spaces/SPACE/pages/12345/...
    m = re.search(r"/spaces/([^/]+)/pages/(\d+)", path)
    if m:
        return {"space_key": m.group(1), "page_id": m.group(2)}

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
            raise ValueError("Confluence base_url is required (set confluence.base_url in config.yaml)")
        if not token:
            raise ValueError("Confluence token is required (set confluence.token in config.yaml)")

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
        if self.delay:
            time.sleep(self.delay)
        r = self.s.get(f"{self.base}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

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
        """Fetch a page given its display URL. Routes to id-based or title-based lookup."""
        sys = system_log()
        ids = _parse_page_url(url)
        if not ids:
            raise ValueError(f"Could not extract page id or space+title from URL: {url}")

        sys.info("confluence get_page_by_url", extra={"url": url, "parsed": ids})

        if "page_id" in ids:
            return self.get_page_by_id(ids["page_id"], expand=expand)
        if "space_key" in ids and "title" in ids:
            return self.get_page_by_title(ids["space_key"], ids["title"], expand=expand)
        raise ValueError(f"URL parsed but missing identifiers: {ids}")

    # ---------- parsing -------------------------------------------------

    @staticmethod
    def parse_project_page(page: dict) -> ProjectPageContent:
        """Extract Project Overview + Milestones table + Functional Requirements
        from a page returned by get_page_by_url / get_page_by_id.

        Tolerant to structural variation: locates sections by heading text
        (case-insensitive substring match). Returns warnings for missing
        sections rather than raising.
        """
        title = page.get("title", "")
        body_html = (
            page.get("body", {}).get("storage", {}).get("value", "")
            or ""
        )

        result = ProjectPageContent(title=title, raw_html=body_html)

        if not body_html:
            result.parse_warnings.append("Page body is empty.")
            return result

        soup = BeautifulSoup(body_html, "html.parser")

        # Walk top-level elements; group content by heading
        headings = soup.find_all(re.compile(r"^h[1-6]$"))
        if not headings:
            result.parse_warnings.append("Page contains no headings; cannot locate sections.")
            return result

        sections: dict[str, list] = {}
        for h in headings:
            label = h.get_text(strip=True).lower()
            content = []
            for sib in h.find_next_siblings():
                if sib.name and re.match(r"^h[1-6]$", sib.name):
                    break
                content.append(sib)
            sections[label] = content

        # --- Overview ----
        for label, content in sections.items():
            if "overview" in label:
                result.overview = _to_text(content)
                break

        # --- Milestones table ----
        milestones_block = None
        for label, content in sections.items():
            if "milestone" in label:
                milestones_block = content
                break
        if milestones_block is None:
            result.parse_warnings.append("No 'Milestones' section heading found.")
        else:
            table = next((c for c in milestones_block if getattr(c, "name", None) == "table"), None)
            if table is None:
                # Sometimes the table is wrapped in a div
                for c in milestones_block:
                    if hasattr(c, "find"):
                        t = c.find("table")
                        if t:
                            table = t
                            break
            if table is None:
                result.parse_warnings.append("'Milestones' section has no table.")
            else:
                result.milestones = _parse_milestones_table(table, result.parse_warnings)

        # --- Functional Requirements ----
        fr_block = None
        for label, content in sections.items():
            if "functional" in label and ("requirement" in label or "req" in label):
                fr_block = content
                break
        if fr_block is None:
            for label, content in sections.items():
                # Looser match for variations like "FRs", "Requirements"
                if "requirement" in label:
                    fr_block = content
                    break
        if fr_block is None:
            result.parse_warnings.append("No 'Functional Requirements' section heading found.")
        else:
            result.functional_requirements = _to_text(fr_block)

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
