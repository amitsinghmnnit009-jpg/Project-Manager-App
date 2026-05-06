"""Standalone Prompt 3 (Project Status Reasoning) test — Step 4.

Runs Prompt 3 against ONE real project's data, end-to-end, with no
infrastructure around it. Lets you validate the riskiest prompt before
we wrap a server around it.

Usage (after Step 2 + Step 3 are complete):
    python scripts/test_status_prompt.py --project-code SSDFW

Outputs:
    - The full prompt sent to the LLM (prints + saves to logs/)
    - The raw LLM response (prints + saves)
    - Parsed JSON validated against the expected schema
    - Verdict: pass/fail per the schema + sanity checks

This script does NOT touch the DB or scheduler. It hits JIRA, Confluence,
and the LLM directly.
"""
from __future__ import annotations
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Standalone Prompt 3 test.")
    parser.add_argument("--project-code", required=True, help="Project code from config.yaml")
    parser.add_argument("--save-to", default="./logs/prompt3_test.jsonl",
                        help="Where to save the request+response payload")
    args = parser.parse_args()

    print(f"Phase 1 Step 4 — standalone Prompt 3 test for project {args.project_code!r}")
    print("[stub] Will:")
    print("  1. Load project config")
    print("  2. Fetch Confluence page (parse milestones + FRs)")
    print("  3. Fetch JIRA snapshot (counts + recent activity)")
    print("  4. Load last 4 weekly reports (or note if none yet)")
    print("  5. Render Prompt 3 from project_status_reasoning_v1.txt")
    print("  6. Send to LLM (per config.llm.provider)")
    print("  7. Parse the JSON response, validate, print verdict")
    print()
    print("To be implemented after Step 2 (clients) and Step 3 (LLM client).")
    sys.exit(0)


if __name__ == "__main__":
    main()
