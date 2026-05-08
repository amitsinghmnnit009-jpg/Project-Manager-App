"""Print the structured fields of a saved Prompt 3 run.

Each run of scripts/test_status_prompt.py writes a JSONL record to
logs/prompt3_<code>_<ts>.jsonl. This helper finds that record and prints
the AI's structured output (overall_health, schedule_status, completion_pct,
confidence, rationale, milestones, evidence_cited) plus run metadata
(prompt version, LLM mode, duration, token counts, validation issues).

The script resolves logs/ relative to the repo root (NOT the current working
directory), so it works no matter where you invoke it from.

Usage:
    python scripts/show_last_prompt3_result.py
        # prints the most recent prompt3_*.jsonl in logs/

    python scripts/show_last_prompt3_result.py --project-code ASPICE
        # prints the most recent run for a specific project code

    python scripts/show_last_prompt3_result.py --file logs/prompt3_ASPICE_20260509T140523Z.jsonl
        # reads a specific file (path can be absolute or relative to repo root)

    python scripts/show_last_prompt3_result.py --list
        # only list candidate files; do not read any
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows so prompt content (em-dashes, non-ASCII
# project titles, Korean Confluence titles, etc.) prints correctly. The
# console codepage on Windows is cp1252 by default, which mangles e.g.
# em-dash and curly quotes that the LLM faithfully copies through.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="Show parsed fields from a saved Prompt 3 run.")
    parser.add_argument("--project-code", default=None,
                        help="Filter to runs of this project code (case-sensitive on Linux/macOS)")
    parser.add_argument("--file", default=None,
                        help="Read this specific file (absolute or relative to repo root)")
    parser.add_argument("--list", action="store_true",
                        help="Only list candidate files; do not read any")
    args = parser.parse_args()

    logs_dir = ROOT / "logs"

    # ---- Resolve target file --------------------------------------------
    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = ROOT / target
        if not target.exists():
            print(f"[FAIL] File not found: {target}")
            sys.exit(1)
    else:
        if not logs_dir.exists():
            print(f"[FAIL] logs/ directory does not exist at {logs_dir}")
            sys.exit(1)
        pattern = (
            f"prompt3_{args.project_code}_*.jsonl"
            if args.project_code else "prompt3_*.jsonl"
        )
        candidates = sorted(logs_dir.glob(pattern))
        if args.list:
            print(f"Looking in: {logs_dir}")
            print(f"Pattern:    {pattern}")
            print(f"Found {len(candidates)} file(s):")
            for f in candidates:
                print(f"  {f.name}  ({f.stat().st_size} bytes)")
            sys.exit(0)
        if not candidates:
            print(f"[FAIL] No files matching {pattern!r} in {logs_dir}")
            print()
            print(f"Contents of {logs_dir}:")
            entries = sorted(logs_dir.iterdir()) if logs_dir.exists() else []
            if not entries:
                print("  (empty)")
            else:
                for f in entries:
                    print(f"  {f.name}")
            sys.exit(1)
        target = candidates[-1]

    # ---- Load and print --------------------------------------------------
    print(f"Reading: {target}")
    print()

    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[FAIL] Could not parse {target}: {type(e).__name__}: {e}")
        sys.exit(1)

    print("=== Run metadata ===")
    print(f"  project_code:      {data.get('project_code')}")
    print(f"  prompt_version:    {data.get('prompt_version')}")
    print(f"  llm_mode:          {data.get('llm_mode')}")
    print(f"  duration_seconds:  {data.get('duration_seconds')}")
    print(f"  prompt_tokens:     {data.get('prompt_tokens')}")
    print(f"  completion_tokens: {data.get('completion_tokens')}")
    print(f"  ts (UTC):          {data.get('ts')}")
    print()

    p = data.get("parsed_response")
    if p is None:
        print("[!] This run has no parsed_response — the LLM did not return valid JSON.")
        issues = data.get("validation_issues") or []
        if issues:
            print()
            print("Validation issues:")
            for i in issues:
                print(f"  - {i}")
        raw = data.get("raw_response", "") or ""
        if raw:
            print()
            print("Raw response (first 1000 chars):")
            print(raw[:1000])
        sys.exit(0)

    print("=== Top-level outputs ===")
    print(f"  overall_health:  {p.get('overall_health')}")
    print(f"  schedule_status: {p.get('schedule_status')}")
    print(f"  completion_pct:  {p.get('completion_pct')}")
    print(f"  confidence:      {p.get('confidence')}")
    print()

    print("=== rationale ===")
    print(p.get("rationale") or "(missing)")
    print()

    milestones = p.get("milestones") or []
    print(f"=== milestones ({len(milestones)}) ===")
    print(json.dumps(milestones, indent=2, ensure_ascii=False))
    print()

    evidence = p.get("evidence_cited") or []
    print(f"=== evidence_cited ({len(evidence)}) ===")
    if not evidence:
        print("  (empty)")
    else:
        for item in evidence:
            print(f"  - {item}")
    print()

    issues = data.get("validation_issues") or []
    if issues:
        print(f"=== validation_issues ({len(issues)}) ===")
        for i in issues:
            print(f"  - {i}")
    else:
        print("=== validation_issues ===")
        print("  (none — output passed schema validation)")


if __name__ == "__main__":
    main()
