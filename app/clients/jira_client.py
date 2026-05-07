"""JIRA Data Center REST client.

Adapted from WR-Project/jira_client.py with these Phase 1 changes:
- Configurable issue-type filter per project (no hardcoded Task/Sub-task)
- Returns structured outputs ready for Aggregation + Status engines
- Uses the app's JSONL logger directly (no per-call log callback)
- Engineer mapping passed in (knox_id + name); unmapped JIRA users captured

Two main public methods:
- collect_engineer_activity(...) → input for Prompt 1 (Weekly Aggregation)
- get_project_snapshot(...)      → input for Prompt 3 (Project Status Reasoning)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from app.clients.http_session import build_session
from app.utils.logging import system_log, sync_log


# Changelog fields that create noise without adding meaning. Drop before LLM.
_NOISY_FIELDS = {
    "Rank", "rank",
    "RemoteIssueLink", "Link",
    "WorklogId", "timeestimate", "timespent", "timeoriginalestimate",
    "Attachment",
    "description",
    "Sprint",
    "Epic Link", "epic link",
    "Parent Link",
    "Flagged",
    "labels", "Labels",
}


# ---------- Public dataclasses ---------------------------------------------

@dataclass
class ActivityRecord:
    """One activity event on a JIRA task in a given window."""
    task_id: str
    task_title: str
    task_status: str
    task_assignee: Optional[str]
    activity_kind: str   # 'created' | 'updated' | 'status_change' | 'comment' | 'worklog'
    author_name: str
    author_id: str
    timestamp: str
    detail: str = ""
    url: str = ""


@dataclass
class UnmappedAuthor:
    """A JIRA user observed in activity but not present in our engineer mapping."""
    display_name: str
    user_id: str
    email: str = ""


@dataclass
class JiraEngineerActivity:
    """Result of `collect_engineer_activity` — input shape for Prompt 1."""
    project_key: str
    week_of: date
    by_engineer: dict[str, list[ActivityRecord]] = field(default_factory=dict)  # knox_id → records
    unmapped_authors: list[UnmappedAuthor] = field(default_factory=list)


@dataclass
class JiraProjectSnapshot:
    """Result of `get_project_snapshot` — input shape for Prompt 3."""
    project_key: str
    snapshot_at: datetime
    total_tasks: int
    by_status: dict[str, int]
    overdue_count: int
    stale_count: int       # not updated in 14 days
    recent_activity: list[dict]    # last 14 days, brief: id/title/status/last_activity/excerpt


# ---------- ADF flattener (for newer JIRA comment payloads) ---------------

def _adf_to_text(node) -> str:
    """Flatten Atlassian Document Format dicts to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(_adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return str(node)

    t = node.get("type", "")
    if t == "text":
        return node.get("text", "")

    inner = _adf_to_text(node.get("content", []))
    if t in {"paragraph", "heading", "listItem", "bulletList", "orderList"}:
        return inner + "\n"
    return inner


# ---------- Helpers --------------------------------------------------------

def _normalise_issue_type(name: str) -> str:
    return (name or "").lower().replace("-", "").replace("_", "").replace(" ", "")


def _matches_issue_types(issuetype_name: str, allowed: Optional[list[str]]) -> bool:
    """Return True if the issue type matches one of the allowed types,
    or if `allowed` is None/empty (= no filter)."""
    if not allowed:
        return True
    n = _normalise_issue_type(issuetype_name)
    return any(n == _normalise_issue_type(a) for a in allowed)


def _is_within(timestamp_str: str, since: datetime) -> bool:
    """True if a JIRA ISO timestamp is at-or-after `since`."""
    if not timestamp_str:
        return False
    try:
        # JIRA returns ISO with offset, e.g. '2026-05-01T10:00:00.000+0530'
        # datetime.fromisoformat handles ±HH:MM but not ±HHMM — normalise.
        ts = timestamp_str
        if len(ts) >= 5 and ts[-5] in ("+", "-") and ":" not in ts[-5:]:
            ts = ts[:-2] + ":" + ts[-2:]
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            return dt >= since.replace(tzinfo=None)
        if since.tzinfo is None:
            return dt.replace(tzinfo=None) >= since
        return dt >= since
    except (ValueError, TypeError):
        return False


def _truncate(text: str, n: int) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[:n] + "…"


# ---------- Client --------------------------------------------------------

class JiraClient:
    """Thin REST wrapper for JIRA Data Center.

    Uses Bearer PAT authentication. All methods raise `requests.HTTPError`
    on non-retriable HTTP failures (the underlying session retries 429/5xx).
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        api_version: str = "2",
        verify_ssl: bool = True,
        ca_bundle: str = "",
        enable_http_logging: bool = False,
    ):
        if not base_url:
            raise ValueError("JIRA base_url is required (set jira.base_url in config.yaml)")
        if not token:
            raise ValueError("JIRA token is required (set jira.token in config.yaml)")

        self.base = base_url.rstrip("/")
        self.api = f"/rest/api/{api_version}"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self.s = build_session(
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
            headers=headers,
            enable_http_logging=enable_http_logging,
        )

    # ---------- low-level ------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        r = self.s.get(f"{self.base}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    # ---------- whoami / health -----------------------------------------

    def whoami(self) -> dict:
        """Verify the token works. Returns the JIRA user object."""
        return self._get(f"{self.api}/myself")

    # ---------- search --------------------------------------------------

    def search_issues_in_project(
        self,
        project_key: str,
        since: datetime,
        issue_types: Optional[list[str]] = None,
        fields: Optional[str] = None,
        expand: Optional[str] = None,
    ) -> list[dict]:
        """Paginated JQL search. Default JQL: project = X AND updated >= since."""
        since_str = since.strftime("%Y-%m-%d")
        jql = f'project = "{project_key}" AND updated >= "{since_str}" ORDER BY updated DESC'

        out: list[dict] = []
        start = 0
        page = 100
        while True:
            data = self._get(
                f"{self.api}/search",
                {
                    "jql": jql,
                    "startAt": start,
                    "maxResults": page,
                    "fields": fields or "summary,status,assignee,issuetype,created,updated,reporter,duedate",
                    "expand": expand or "changelog",
                },
            )
            issues = data.get("issues", [])
            out.extend(issues)
            if start + len(issues) >= data.get("total", 0) or not issues:
                break
            start += len(issues)

        if issue_types:
            out = [
                i for i in out
                if _matches_issue_types(
                    (i.get("fields", {}).get("issuetype", {}) or {}).get("name", ""),
                    issue_types,
                )
            ]
        return out

    def get_comments(self, issue_key: str) -> list[dict]:
        data = self._get(
            f"{self.api}/issue/{issue_key}/comment", {"maxResults": 100}
        )
        return data.get("comments", [])

    def get_worklogs(self, issue_key: str) -> list[dict]:
        data = self._get(
            f"{self.api}/issue/{issue_key}/worklog", {"maxResults": 200}
        )
        return data.get("worklogs", [])

    # ---------- engineer activity (Aggregation Engine input) -----------

    def collect_engineer_activity(
        self,
        project_key: str,
        week_of: date,
        engineers: list[dict],     # list of {"name": ..., "knox_id": ...}
        issue_types: Optional[list[str]] = None,
    ) -> JiraEngineerActivity:
        """Collect comments + work-logs + status changes authored by mapped
        engineers within the report week (Mon..Sun starting from `week_of`).

        Returns a structured grouping by engineer (knox_id → records).
        Unmapped JIRA users encountered are captured separately so admin can
        be warned (per FR §14).
        """
        log = sync_log()
        sys = system_log()

        # Build case-insensitive, whitespace-trimmed lookup tables.
        # Knox IDs and display names are routinely written with different
        # capitalisation in JIRA vs the mapping file ("Rahul Kumar" vs
        # "Rahul kumar"); matching has to be tolerant of that.
        def _norm(s: str) -> str:
            return (s or "").strip().lower()

        lookup_by_knox: dict[str, dict] = {_norm(e["knox_id"]): e for e in engineers}
        lookup_by_name: dict[str, dict] = {_norm(e["name"]): e for e in engineers}

        # Window: report week is 7 days starting at week_of (00:00 IST).
        # We search for issues updated since week_of - 1d to be safe with TZs.
        since = datetime.combine(week_of - timedelta(days=1), datetime.min.time())
        week_start = datetime.combine(week_of, datetime.min.time())
        week_end = week_start + timedelta(days=7)

        result = JiraEngineerActivity(project_key=project_key, week_of=week_of)
        unmapped_seen: dict[str, UnmappedAuthor] = {}

        sys.info(
            "jira collect_engineer_activity start",
            extra={"project": project_key, "week_of": str(week_of),
                   "engineer_count": len(engineers), "issue_types": issue_types},
        )

        try:
            issues = self.search_issues_in_project(project_key, since, issue_types)
        except requests.HTTPError as e:
            log.error("jira search failed", extra={"project": project_key, "error": str(e)})
            raise

        def _classify_author(u: dict) -> tuple[Optional[str], str, str]:
            """Returns (knox_id_or_None, display_name, user_id).

            Matches the JIRA author against the engineer mapping using
            case-insensitive, whitespace-trimmed comparison on:
              1. accountId / key / username
              2. emailAddress
              3. displayName
            """
            if not u:
                return None, "", ""
            a_id = u.get("accountId") or u.get("key") or u.get("name") or ""
            a_name = u.get("displayName") or u.get("name") or ""
            email = u.get("emailAddress", "") or ""

            # 1 + 2: ID-shaped fields → look up by knox_id
            for cand in (a_id, email):
                if cand:
                    e = lookup_by_knox.get(_norm(cand))
                    if e:
                        return e["knox_id"], a_name, a_id

            # 3: display name → look up by name
            if a_name:
                e = lookup_by_name.get(_norm(a_name))
                if e:
                    return e["knox_id"], a_name, a_id

            return None, a_name, a_id

        def _add(knox_id: str, rec: ActivityRecord):
            result.by_engineer.setdefault(knox_id, []).append(rec)

        def _record_unmapped(name: str, uid: str, email: str = ""):
            key = uid or name
            if key and key not in unmapped_seen:
                unmapped_seen[key] = UnmappedAuthor(display_name=name, user_id=uid, email=email)

        def _in_week(ts: str) -> bool:
            """True if a JIRA timestamp falls within this report week."""
            return _is_within(ts, week_start) and not _is_within(ts, week_end)

        for issue in issues:
            key = issue["key"]
            f = issue["fields"]
            itype = (f.get("issuetype") or {}).get("name", "")
            summary = f.get("summary", "")
            status = (f.get("status") or {}).get("name", "")
            assignee = (f.get("assignee") or {}).get("displayName")
            url = f"{self.base}/browse/{key}"

            # Creation
            created_ts = f.get("created", "")
            knox, name, uid = _classify_author(f.get("reporter") or {})
            if _in_week(created_ts):
                if knox:
                    _add(knox, ActivityRecord(
                        task_id=key, task_title=summary, task_status=status,
                        task_assignee=assignee, activity_kind="created",
                        author_name=name, author_id=uid, timestamp=created_ts, url=url,
                    ))
                elif name or uid:
                    _record_unmapped(name, uid)

            # Changelog
            for history in issue.get("changelog", {}).get("histories", []):
                ts = history.get("created", "")
                if not _in_week(ts):
                    continue
                knox, name, uid = _classify_author(history.get("author") or {})
                if not knox:
                    if name or uid:
                        _record_unmapped(name, uid)
                    continue

                for item in history.get("items", []):
                    fld = (item.get("field", "") or "").strip()
                    frm = item.get("fromString") or ""
                    to = item.get("toString") or ""
                    if fld in _NOISY_FIELDS or fld.startswith("customfield_"):
                        continue
                    if frm.strip() == to.strip():
                        continue
                    kind = "status_change" if fld.lower() == "status" else "updated"
                    detail = f"{fld}: {_truncate(frm, 120)!r} → {_truncate(to, 120)!r}" if (frm or to) else fld
                    _add(knox, ActivityRecord(
                        task_id=key, task_title=summary, task_status=status,
                        task_assignee=assignee, activity_kind=kind,
                        author_name=name, author_id=uid, timestamp=ts, detail=detail, url=url,
                    ))

            # Comments (extra API call per issue)
            try:
                for c in self.get_comments(key):
                    ts = c.get("created", "")
                    if not _in_week(ts):
                        continue
                    knox, name, uid = _classify_author(c.get("author") or {})
                    if not knox:
                        if name or uid:
                            _record_unmapped(name, uid)
                        continue
                    body = c.get("body")
                    text = _adf_to_text(body) if isinstance(body, dict) else str(body or "")
                    _add(knox, ActivityRecord(
                        task_id=key, task_title=summary, task_status=status,
                        task_assignee=assignee, activity_kind="comment",
                        author_name=name, author_id=uid, timestamp=ts,
                        detail=_truncate(text, 500), url=url,
                    ))
            except requests.HTTPError as e:
                sys.warning("jira get_comments failed", extra={"task": key, "error": str(e)})

            # Work-logs (extra API call per issue)
            try:
                for w in self.get_worklogs(key):
                    ts = w.get("started", "") or w.get("created", "")
                    if not _in_week(ts):
                        continue
                    knox, name, uid = _classify_author(w.get("author") or {})
                    if not knox:
                        if name or uid:
                            _record_unmapped(name, uid)
                        continue
                    time_spent = w.get("timeSpent", "")
                    comment = w.get("comment")
                    text = _adf_to_text(comment) if isinstance(comment, dict) else str(comment or "")
                    detail = f"{time_spent} — {text}".strip(" —")
                    _add(knox, ActivityRecord(
                        task_id=key, task_title=summary, task_status=status,
                        task_assignee=assignee, activity_kind="worklog",
                        author_name=name, author_id=uid, timestamp=ts,
                        detail=_truncate(detail, 500), url=url,
                    ))
            except requests.HTTPError as e:
                sys.warning("jira get_worklogs failed", extra={"task": key, "error": str(e)})

        result.unmapped_authors = list(unmapped_seen.values())

        sys.info(
            "jira collect_engineer_activity done",
            extra={
                "project": project_key,
                "week_of": str(week_of),
                "issues_scanned": len(issues),
                "engineers_with_activity": len(result.by_engineer),
                "unmapped_authors": len(result.unmapped_authors),
            },
        )
        return result

    # ---------- project snapshot (Status Engine input) -----------------

    def get_project_snapshot(
        self,
        project_key: str,
        issue_types: Optional[list[str]] = None,
        recent_window_days: int = 14,
        stale_threshold_days: int = 14,
    ) -> JiraProjectSnapshot:
        """Compute current project snapshot for status reasoning.

        - Counts tasks by status
        - Counts overdue (past due_date and not Done)
        - Counts stale (not updated in N days)
        - Returns up to ~30 most recently active tasks for the recent_activity field
        """
        sys = system_log()
        # Pull a generous window to get current task picture.
        # We use a 90-day window; a project with no activity in 90d has nothing to say.
        since = datetime.utcnow() - timedelta(days=90)

        issues = self.search_issues_in_project(
            project_key, since, issue_types,
            fields="summary,status,duedate,updated,issuetype",
            expand=None,
        )

        now = datetime.utcnow()
        recent_threshold = now - timedelta(days=recent_window_days)
        stale_threshold = now - timedelta(days=stale_threshold_days)

        by_status: dict[str, int] = {}
        overdue = 0
        stale = 0
        recent_activity: list[dict] = []

        for issue in issues:
            f = issue["fields"]
            key = issue["key"]
            status = (f.get("status") or {}).get("name", "Unknown")
            by_status[status] = by_status.get(status, 0) + 1

            updated = f.get("updated", "")
            duedate = f.get("duedate", "")
            is_done = status.lower() in ("done", "closed", "resolved", "cancelled")

            if duedate and not is_done:
                try:
                    due_dt = datetime.fromisoformat(duedate)
                    if due_dt < now:
                        overdue += 1
                except ValueError:
                    pass

            if updated and not is_done:
                if not _is_within(updated, stale_threshold):
                    stale += 1

            if updated and _is_within(updated, recent_threshold):
                recent_activity.append({
                    "id": key,
                    "title": f.get("summary", ""),
                    "status": status,
                    "last_activity": updated,
                })

        # Trim recent activity to the most recent 30 to keep prompt size sane.
        recent_activity.sort(key=lambda r: r["last_activity"], reverse=True)
        recent_activity = recent_activity[:30]

        snapshot = JiraProjectSnapshot(
            project_key=project_key,
            snapshot_at=now,
            total_tasks=len(issues),
            by_status=by_status,
            overdue_count=overdue,
            stale_count=stale,
            recent_activity=recent_activity,
        )
        sys.info(
            "jira snapshot done",
            extra={
                "project": project_key,
                "total_tasks": snapshot.total_tasks,
                "overdue": overdue,
                "stale": stale,
            },
        )
        return snapshot
