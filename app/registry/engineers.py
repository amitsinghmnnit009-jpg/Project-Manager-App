"""Engineer registry — load engineer ↔ project mapping from JSON file.

Reads `data/engineer_project_mapping.json` (path from config.engineers.mapping_file)
and provides lookup helpers for the engines.

The mapping is in-memory only (NOT synced to a DB table) — engineers in
Phase 1 are referenced from the JSON file directly. This avoids a
redundant abstraction: there's only one master list, edited by the admin,
read at app start. Reload requires `reload_engineer_mapping()` (or app
restart). Promotion to a DB table can happen in a later phase if needed.

Normalisation pattern matches `app/clients/jira_client._classify_author`:
NFKC + whitespace-collapsed + lowercased — so a JIRA user's
`name="rahul22.k"` matches a mapping `knox_id="Rahul22.K  "` (trailing
spaces, mixed case, NBSP, etc.).
"""
from __future__ import annotations
import json
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from app.config import get_config
from app.utils.logging import system_log


@dataclass(frozen=True)
class Engineer:
    """One engineer, as defined in the mapping file."""
    name: str
    knox_id: str


@dataclass
class EngineerMapping:
    """Parsed mapping with pre-built lookup indexes.

    All keys in `by_knox` and `by_project` are normalised (lowercase,
    whitespace-collapsed). Use `_norm()` before lookup.
    """
    engineers: list[Engineer] = field(default_factory=list)
    by_knox: dict[str, list[str]] = field(default_factory=dict)       # knox_norm -> [project_codes]
    by_project: dict[str, list[Engineer]] = field(default_factory=dict)  # code_norm -> [Engineer]
    file_path: str = ""
    parse_warnings: list[str] = field(default_factory=list)


# ---------- Normalisation ------------------------------------------------

def _norm(s: Optional[str]) -> str:
    """Match the JIRA matcher's normalisation: NFKC + whitespace-collapsed
    + lowercased. Empty/None -> empty string."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = " ".join(s.split())
    return s.lower()


# ---------- Loader -------------------------------------------------------

@lru_cache(maxsize=1)
def load_engineer_mapping() -> EngineerMapping:
    """Read the mapping JSON file and build lookup tables. Cached.

    Use `reload_engineer_mapping()` to force a re-read after editing the
    file in a long-running process. The Phase 1 default is to require an
    app restart, but the helper is exposed for tests + admin tools.
    """
    log = system_log()
    cfg = get_config()
    path = cfg.engineers.mapping_file

    result = EngineerMapping(file_path=path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        log.error(
            "engineer mapping file not found",
            extra={"event": "registry_engineers_missing_file", "path": path},
        )
        result.parse_warnings.append(f"File not found: {path}")
        return result
    except json.JSONDecodeError as e:
        log.error(
            "engineer mapping file failed to parse",
            extra={"event": "registry_engineers_parse_error", "path": path, "error": str(e)},
        )
        result.parse_warnings.append(f"JSON parse error: {e}")
        return result

    if not isinstance(raw, dict):
        result.parse_warnings.append(
            f"Expected a JSON object at top level, got {type(raw).__name__}"
        )
        return result

    # Engineers — ignore '_' documentation keys and entries missing required fields
    engineers: list[Engineer] = []
    seen_knox: set[str] = set()
    for e in raw.get("engineers", []) or []:
        if not isinstance(e, dict):
            continue
        name = e.get("name")
        knox_id = e.get("knox_id")
        if not name or not knox_id:
            result.parse_warnings.append(f"Engineer entry missing name/knox_id: {e!r}")
            continue
        knox_norm = _norm(knox_id)
        if knox_norm in seen_knox:
            result.parse_warnings.append(
                f"Duplicate knox_id (case-insensitive): {knox_id!r} — keeping the first"
            )
            continue
        seen_knox.add(knox_norm)
        engineers.append(Engineer(name=name, knox_id=knox_id))

    # Assignments — knox_norm -> [project_codes]
    by_knox: dict[str, list[str]] = {}
    for asn in raw.get("assignments", []) or []:
        if not isinstance(asn, dict):
            continue
        knox_id = asn.get("knox_id")
        projs = asn.get("projects") or []
        if not knox_id or not isinstance(projs, list):
            result.parse_warnings.append(f"Assignment entry malformed: {asn!r}")
            continue
        clean = [str(p) for p in projs if p]
        knox_norm = _norm(knox_id)
        if knox_norm in by_knox:
            # Merge if the same engineer has multiple assignment entries (shouldn't
            # happen with a well-formed file but be tolerant)
            existing = by_knox[knox_norm]
            for p in clean:
                if p not in existing:
                    existing.append(p)
        else:
            by_knox[knox_norm] = clean

    # Reverse index: project_code (normalised) -> [Engineer]
    by_project: dict[str, list[Engineer]] = {}
    for eng in engineers:
        knox_norm = _norm(eng.knox_id)
        for p in by_knox.get(knox_norm, []):
            by_project.setdefault(_norm(p), []).append(eng)

    result.engineers = engineers
    result.by_knox = by_knox
    result.by_project = by_project

    log.info(
        "engineer mapping loaded",
        extra={
            "event": "registry_engineers_loaded",
            "path": path,
            "engineer_count": len(engineers),
            "assignment_count": len(by_knox),
            "projects_with_engineers": len(by_project),
            "parse_warnings": len(result.parse_warnings),
        },
    )
    return result


def reload_engineer_mapping() -> EngineerMapping:
    """Force a fresh load (clears the lru_cache). For tests / admin tools."""
    load_engineer_mapping.cache_clear()
    return load_engineer_mapping()


# ---------- Lookups ------------------------------------------------------

def engineers_on_project(project_code: str) -> list[Engineer]:
    """Return engineers assigned to the given project (case-insensitive
    match on the project code). Empty list if none."""
    mapping = load_engineer_mapping()
    return list(mapping.by_project.get(_norm(project_code), []))


def projects_for_engineer(knox_id: str) -> list[str]:
    """Return project codes the engineer is assigned to (case-insensitive
    knox_id match). Empty list if not in the mapping."""
    mapping = load_engineer_mapping()
    return list(mapping.by_knox.get(_norm(knox_id), []))


def is_known_engineer(jira_user_field: dict) -> Optional[Engineer]:
    """Match a JIRA user object against the mapping.

    Tries the JIRA user object's accountId / key / name / emailAddress
    against the knox_id lookup, then displayName against the engineer-name
    lookup. Returns the matched Engineer or None.

    Mirrors the matching policy of `app.clients.jira_client._classify_author`
    so engines and the JIRA collector reach the same conclusion about
    "is this person known?"
    """
    if not jira_user_field:
        return None

    mapping = load_engineer_mapping()

    # Try ID-shaped fields against the knox lookup
    for field_name in ("accountId", "key", "name", "emailAddress"):
        value = jira_user_field.get(field_name)
        if not value:
            continue
        normed = _norm(value)
        if normed in mapping.by_knox:
            for eng in mapping.engineers:
                if _norm(eng.knox_id) == normed:
                    return eng

    # Fall back to displayName against the name lookup
    display_name = jira_user_field.get("displayName")
    if display_name:
        normed_name = _norm(display_name)
        for eng in mapping.engineers:
            if _norm(eng.name) == normed_name:
                return eng

    return None
