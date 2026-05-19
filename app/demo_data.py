"""Simulated demo data for manager presentations.

Three ASPICE/automotive-flavoured projects seeded into the same DB as real
project data.  The UI shows them alongside real projects; for a demo you
navigate to these instead of MAICTJ.

Demo project codes — used by clear-demo to identify and delete ONLY these:
  DEMO-ASPICE  — ASPICE Level 2 Compliance, Propulsion Control Unit (Amber/AtRisk)
  DEMO-HWINT   — HW-SW Interface Validation, BMS (Green/OnTrack)
  DEMO-SYSVAL  — AUTOSAR BSW Integration, ECU Stack (Red/Delayed)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

DEMO_PROJECT_CODES = {"DEMO-ASPICE", "DEMO-HWINT", "DEMO-SYSVAL"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monday(d: date) -> date:
    """Return the Monday on or before d."""
    return d - timedelta(days=d.weekday())


def _weeks_ago(n: int) -> date:
    """Monday of the week that was n weeks before the current week."""
    today = date(2026, 5, 19)
    return _monday(today) - timedelta(weeks=n)


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 0, 0)


# ---------------------------------------------------------------------------
# DEMO-ASPICE  — ASPICE L2 Compliance, Propulsion Control Unit
#   Status: Amber / AtRisk / 33%
#   12 milestones  (4 Done, 3 In-progress [2 overdue], 3 Pending, 2 Delayed)
# ---------------------------------------------------------------------------

_ASPICE_MILESTONES_JSON = [
    {
        "name": "Requirements Elicitation (SRS-001)",
        "planned_date": "2025-11-30",
        "completion_date": "2025-11-28",
        "tl_declared_status": "Done",
        "ai_verification": "Verified",
        "evidence": "JIRA tasks REQ-11..REQ-17 closed by 2025-11-27; weekly report 2025-12-01 confirms sign-off.",
    },
    {
        "name": "System Architecture Design",
        "planned_date": "2025-12-31",
        "completion_date": "2025-12-30",
        "tl_declared_status": "Done",
        "ai_verification": "Verified",
        "evidence": "SAD-001 document committed; JIRA ARCH-22 closed 2025-12-28, within ±7 days of completion_date.",
    },
    {
        "name": "Software Requirements Specification (SRS)",
        "planned_date": "2026-01-31",
        "completion_date": "2026-01-30",
        "tl_declared_status": "Done",
        "ai_verification": "Verified",
        "evidence": "SRS v1.3 baselined; JIRA SWE-45 closed 2026-01-29. Evidence aligns with completion_date.",
    },
    {
        "name": "Software Architecture Design (SAD)",
        "planned_date": "2026-02-28",
        "completion_date": "2026-03-03",
        "tl_declared_status": "Done",
        "ai_verification": "Inconclusive",
        "evidence": "completion_date 2026-03-03 is 3 days after planned. No JIRA closure event found within ±14 days — insufficient JIRA evidence to confirm or deny. TL declaration accepted.",
    },
    {
        "name": "Unit Testing Plan Preparation",
        "planned_date": "2026-03-15",
        "completion_date": None,
        "tl_declared_status": "In-progress",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "Component Integration Testing (CIT)",
        "planned_date": "2026-03-31",
        "completion_date": None,
        "tl_declared_status": "In-progress",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "Software Integration Testing (SIT)",
        "planned_date": "2026-04-30",
        "completion_date": None,
        "tl_declared_status": "Delayed",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "ASPICE Assessment Preparation",
        "planned_date": "2026-05-15",
        "completion_date": None,
        "tl_declared_status": "Delayed",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "Internal Audit and Gap Analysis",
        "planned_date": "2026-05-31",
        "completion_date": None,
        "tl_declared_status": "Pending",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "ASPICE External Assessment",
        "planned_date": "2026-06-15",
        "completion_date": None,
        "tl_declared_status": "Pending",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "Corrective Actions Implementation",
        "planned_date": "2026-06-30",
        "completion_date": None,
        "tl_declared_status": "Pending",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "ASPICE Level 2 Certification",
        "planned_date": "2026-07-15",
        "completion_date": None,
        "tl_declared_status": "Pending",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
]

_ASPICE_REPORTS = {
    _weeks_ago(5): """\
# Weekly Project Report — ASPICE L2 Compliance (PCU)

**Week of:** {week}  |  **Project:** DEMO-ASPICE

## Accomplishments

- **Software Architecture Design (SAD) review completed.** Final review cycle for SAD v2.1 concluded; 3 open review comments resolved and document baselined to project repository.
- **SWE.3 work product checklist prepared.** E2 drafted compliance evidence traceability for 14 ASPICE SWE.3 work products; 11/14 fully satisfied, 3 partially addressed.
- **Unit test environment setup started.** Test harness configured for BSW communication modules; basic smoke test suite executing without errors.

## Tasks Worked On

**Design / Compliance**
- E2: Finalized SWE.3 work product evidence checklist; reviewed SWE.2 architectural design outputs against ASPICE PAM v3.1 criteria.
- E1: Resolved 3 review findings on SAD v2.1; committed final baseline to repository (commit c4ab812).

**Testing**
- E3: Configured GTest-based unit test harness for CAN driver BSW modules; 12 smoke test cases passing.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| SIT milestone (2026-04-30) slipping — CIT behind schedule | TL | 2026-05-15 |
| ASPICE assessment vendor not yet contracted | TL | 2026-05-01 |

## Next Week Plan

- E1: Begin Component Integration Test (CIT) execution for CAN stack.
- E2: Start internal gap analysis document for SWE.4 (Unit Verification).
- E3: Author 20 additional unit test cases for CAN driver.
- TL: Initiate vendor selection process for ASPICE external assessment.

## Highlights / Things to Watch

### Progress signals
- SAD baseline is a critical gate for downstream ASPICE work products — milestone completion unlocks SIT scope definition.

### Risks to watch
- **SIT milestone (2026-04-30)** is now officially Delayed. CIT is still In-progress. Downstream assessment preparation is at risk.
""",

    _weeks_ago(4): """\
# Weekly Project Report — ASPICE L2 Compliance (PCU)

**Week of:** {week}  |  **Project:** DEMO-ASPICE

## Accomplishments

- **CIT execution started.** Component Integration Test cases for CAN communication stack (TCU_COMM_001–TCU_COMM_010) executed; 8/10 passed, 2 defects raised (ASPICE-201, ASPICE-202).
- **SWE.4 gap analysis first draft.** E2 documented 9 gaps in unit-level verification coverage against ASPICE PAM v3.1 SWE.4 criteria.
- **Test plan for E2E protection module prepared.** 22 boundary test cases authored and reviewed; ready for execution next week.

## Tasks Worked On

**Testing**
- E1: Executed 10 CIT test cases for CAN stack; 2 defects raised and filed in JIRA.
- E3: Authored E2E protection module test plan; 22 test cases reviewed and approved.

**Compliance**
- E2: Drafted SWE.4 gap analysis; identified 9 gaps requiring corrective work products.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| 2 open CIT defects (ASPICE-201, ASPICE-202) — root cause under analysis | E1 | 2026-04-27 |
| SWE.4 gap count (9) higher than expected — may need additional sprint | E2 | 2026-05-10 |
| ASPICE assessment vendor still not contracted | TL | 2026-05-01 |

## Next Week Plan

- E1: Root cause + fix for ASPICE-201, ASPICE-202; continue CIT (test cases 11–20).
- E2: Begin drafting corrective work products for SWE.4 gaps.
- E3: Execute E2E protection module test plan (22 cases).
- TL: Complete vendor selection; send assessment SOW for review.

## Highlights / Things to Watch

### Carried-over risks
- **[Carried over ×1] ASPICE assessment vendor** — still uncontracted. Decision needed this week or assessment schedule must be revised.

### New risks
- SWE.4 gap count of 9 is higher than the 3–4 originally estimated. Corrective work products may require a dedicated 2-week sprint, pushing internal audit preparation further.
""",

    _weeks_ago(3): """\
# Weekly Project Report — ASPICE L2 Compliance (PCU)

**Week of:** {week}  |  **Project:** DEMO-ASPICE

## Accomplishments

- **ASPICE-201 and ASPICE-202 resolved.** Root cause was an off-by-one error in CAN message ID filtering; fix verified by re-test. CIT continues.
- **CIT test cases 11–22 executed.** 10/12 passed; 2 new defects raised (ASPICE-215, ASPICE-216 — both low severity).
- **E2E protection module — all 22 test cases passed.** E3 completed full boundary test suite with 0 defects.
- **Assessment vendor selected.** TL confirmed vendor agreement with Automotive Quality Partners; assessment scheduled 2026-06-20.

## Tasks Worked On

**Testing**
- E1: Fixed CAN message ID filter (ASPICE-201/202); re-ran tests — both pass. Executed CIT cases 11–22.
- E3: Completed E2E protection module boundary test suite; 22/22 passed.

**Compliance**
- E2: Drafted corrective work product for SWE.4 gap #1 (traceability matrix update).
- TL: Executed ASPICE vendor agreement; sent SOW to procurement for approval.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| 2 open low-severity CIT defects (ASPICE-215, ASPICE-216) | E1 | 2026-05-10 |
| Assessment preparation milestone (2026-05-15) tight | E2 | 2026-05-15 |

## Next Week Plan

- E1: Fix ASPICE-215, ASPICE-216; complete CIT cases 23–30.
- E2: Draft corrective work products for SWE.4 gaps 2–5.
- E3: Begin Software Integration Test (SIT) environment setup.
- TL: Track procurement SOW approval; confirm 2026-06-20 assessment date.

## Highlights / Things to Watch

### Progress signals
- E2E protection module full pass is a strong quality signal; same engineer (E3) transitioning to SIT environment setup.
- Vendor confirmed — assessment timeline now has a hard date (2026-06-20). All preparation work must complete by 2026-06-10.

### Risks to watch
- **Assessment preparation milestone (2026-05-15)** is only 3 weeks away. SWE.4 corrective work products not yet complete. Parallel tracking needed.
""",

    _weeks_ago(2): """\
# Weekly Project Report — ASPICE L2 Compliance (PCU)

**Week of:** {week}  |  **Project:** DEMO-ASPICE

## Accomplishments

- **CIT 90% complete.** 27/30 planned test cases executed; 3 remaining are blocked pending hardware fixture availability (filed as ASPICE-220 — blocker).
- **SWE.4 corrective work products 3/9 completed.** E2 delivered traceability matrix, unit test specification template, and review record template.
- **SIT environment baseline ready.** E3 set up integration test environment; basic smoke tests pass.

## Tasks Worked On

**Testing**
- E1: Executed CIT cases 23–27; encountered test fixture blocker on cases 28–30 (ASPICE-220).
- E3: Configured SIT environment; validated communication between CAN and LIN stacks in simulation mode.

**Compliance**
- E2: Delivered 3 SWE.4 corrective work products; 6 remaining in draft.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| **BLOCKER**: Test fixture unavailable for CIT cases 28–30 (ASPICE-220) | TL | 2026-05-12 |
| SWE.4 remaining 6 work products — deadline pressure | E2 | 2026-05-15 |
| Assessment preparation still not started | TL | 2026-05-15 |

## Next Week Plan

- TL: Resolve fixture availability (ASPICE-220); escalate if no progress by Mon.
- E1: Prepare CIT closure report pending fixture resolution.
- E2: Complete SWE.4 corrective work products 4–9.
- E3: Begin SIT test case execution (first 15 cases).

## Highlights / Things to Watch

### Carried-over risks
- **[Carried over ×2] Assessment preparation** — milestone 2026-05-15 will be missed. Assessment is 2026-06-20; without preparation starting this week, corrective action window shrinks critically.

### New blockers
- **ASPICE-220 test fixture blocker** — first hard blocker this project. 3 CIT cases can't execute without the fixture; CIT cannot be formally closed.
""",

    _weeks_ago(1): """\
# Weekly Project Report — ASPICE L2 Compliance (PCU)

**Week of:** {week}  |  **Project:** DEMO-ASPICE

## Accomplishments

- **Test fixture procured; CIT cases 28–30 executed and passed.** ASPICE-220 resolved; CIT formally closed with 30/30 cases executed, 2 low-severity open defects accepted as known limitations.
- **SWE.4 corrective work products 8/9 complete.** E2 delivered 5 more work products; only the regression test specification remains.
- **SIT test cases 1–15 executed.** 13/15 passed; 2 defects raised (ASPICE-228, ASPICE-229 — both medium severity).
- **Assessment preparation brief started.** TL drafted project status summary for ASPICE external assessors.

## Tasks Worked On

**Testing**
- E1: Closed CIT with fixture arrival; executed cases 28–30 (all pass); authored CIT closure report.
- E3: Executed SIT cases 1–15; raised 2 medium defects.

**Compliance**
- E2: Delivered SWE.4 corrective work products 4–8; started regression test specification.
- TL: Drafted assessor project status brief (3 pages).

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| 2 medium-severity SIT defects (ASPICE-228, ASPICE-229) — under analysis | E3 | 2026-05-19 |
| SWE.4 work product #9 (regression test spec) not yet complete | E2 | 2026-05-20 |

## Next Week Plan

- E1: Root cause for ASPICE-228, ASPICE-229; continue SIT (cases 16–25).
- E2: Complete regression test specification; submit all 9 SWE.4 work products for review.
- E3: Re-test after E1's fixes; SIT cases 16–25 execution.
- TL: Complete assessor brief; send to vendor for preview.

## Highlights / Things to Watch

### Progress signals
- CIT closure is a major gate; project can now focus fully on SIT and assessment preparation.
- 8/9 SWE.4 corrective work products delivered — strong momentum; final one should land by end of week.

### Risks to watch
- **Assessment is 2026-06-20 — 5 weeks away.** SIT still in progress; 15/30 cases remaining + 2 open defects. Timeline is achievable but leaves no buffer for surprises.
""",

    _weeks_ago(0): """\
# Weekly Project Report — ASPICE L2 Compliance (PCU)

**Week of:** {week}  |  **Project:** DEMO-ASPICE

## Accomplishments

- **All 9 SWE.4 corrective work products submitted for review.** E2 completed regression test specification; full package submitted to TL for approval.
- **SIT cases 16–25 executed.** 9/10 passed; ASPICE-231 raised (medium severity, CAN timeout under load).
- **ASPICE-228, ASPICE-229 root cause identified.** Both trace to a signal debounce timing issue in the power management SWC; fix in progress.

## Tasks Worked On

**Testing**
- E1: RCA for ASPICE-228/229 — debounce timing issue found; fix coded and in review.
- E3: Executed SIT cases 16–25; 1 new defect (ASPICE-231 — CAN timeout under load condition).

**Compliance**
- E2: Completed regression test spec; submitted full SWE.4 corrective package.
- TL: Reviewed SWE.4 package; 7/9 approved, 2 require minor updates (comments provided to E2).

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| ASPICE-228/229 fix in review — must close before SIT completion | E1 | 2026-05-22 |
| ASPICE-231 (CAN timeout) — may require architectural investigation | E1/E3 | 2026-05-26 |
| SWE.4 work products #3, #7 need minor updates | E2 | 2026-05-21 |

## Next Week Plan

- E1: Complete fix for ASPICE-228/229; investigate ASPICE-231 CAN timeout.
- E2: Update SWE.4 work products #3 and #7 per TL comments; resubmit.
- E3: Complete SIT cases 26–30; begin SIT closure report.
- TL: Finalize assessor brief; send preview to vendor.

## Highlights / Things to Watch

### Progress signals
- SWE.4 corrective package nearly approved (7/9). Once fully approved, the major compliance gate for SWE.4 is satisfied.
- SIT 25/30 cases complete — 5 remaining. On track to close SIT this week if ASPICE-228/229 fix merges cleanly.

### Risks to watch
- **[New] ASPICE-231 CAN timeout under load** — if this requires an architectural change it could delay SIT closure significantly. Severity assessment needed this week.
- Assessment 2026-06-20 is 4 weeks away. All SIT cases and SWE.4 approvals must complete by 2026-06-07 to leave 2 weeks for assessor preparation.
""",
}

_ASPICE_STATUS_HISTORY = [
    # (computed_at, prior_health, new_health, prior_sched, new_sched, prior_pct, new_pct)
    (datetime(2026, 1, 20, 9, 0), "InsufficientEvidence", "Green", "InsufficientEvidence", "OnTrack", None, 8),
    (datetime(2026, 2, 3, 9, 0), "Green", "Green", "OnTrack", "OnTrack", 8, 17),
    (datetime(2026, 2, 24, 9, 0), "Green", "Green", "OnTrack", "OnTrack", 17, 25),
    (datetime(2026, 3, 10, 9, 0), "Green", "Amber", "OnTrack", "AtRisk", 25, 33),
    (datetime(2026, 4, 7, 9, 0), "Amber", "Amber", "AtRisk", "Slipping", 33, 33),
    (datetime(2026, 4, 28, 9, 0), "Amber", "Amber", "Slipping", "AtRisk", 33, 33),
]

DEMO_ASPICE = {
    "project": {
        "code": "DEMO-ASPICE",
        "name": "ASPICE L2 Compliance — Propulsion Control Unit",
        "type": "compliance",
        "description": "Achieve ASPICE Level 2 certification for the PCU software development process. Covers SWE.1–SWE.6 process areas.",
        "owning_tl": "Rajesh Kumar",
        "owning_pgm": "Priya Sharma",
        "start_date": date(2025, 10, 1),
        "planned_end_date": date(2026, 7, 15),
        "confluence_milestones_url": "https://confluence.example.com/spaces/ASPICE/pages/1001/PCU+Milestones",
        "confluence_fr_url": "https://confluence.example.com/spaces/ASPICE/pages/1002/PCU+Functional+Requirements",
        "confluence_extra_pages_json": [],
        "jira_project_key": "ASPICE",
        "weekly_cutoff": "Mon 13:00",
        "issue_types_json": ["Task", "Bug", "Story"],
        "chronic_threshold": 3,
        "holiday_calendar_id": "default",
    },
    "status": {
        "overall_health": "Amber",
        "schedule_status": "AtRisk",
        "completion_pct": 33,
        "milestones_json": _ASPICE_MILESTONES_JSON,
        "rationale": (
            "4 of 12 milestones are TL-declared Done (SRS, Architecture, SWE-SRS, SAD), giving 33% completion. "
            "However, CIT and SIT are both behind schedule — CIT was planned for 2026-03-31 and SIT for 2026-04-30, "
            "both marked Delayed. The ASPICE external assessment is confirmed for 2026-06-20, leaving a tight 4-week "
            "window for completing SIT (5 cases remaining), resolving open defects (ASPICE-228/229/231), and obtaining "
            "full SWE.4 approval. Confidence is Medium because JIRA activity confirms work is in progress, but the "
            "remaining scope vs available time makes slippage likely without sustained pace."
        ),
        "confidence": "Medium",
        "prompt_version": "ProjectStatusReasoning/v3",
        "llm_mode_used": "openai",
    },
    "status_history": _ASPICE_STATUS_HISTORY,
    "reports": _ASPICE_REPORTS,
}


# ---------------------------------------------------------------------------
# DEMO-HWINT  — HW-SW Interface Validation, Battery Management System
#   Status: Green / OnTrack / 62%
#   8 milestones  (5 Done, 2 In-progress, 1 Pending)
# ---------------------------------------------------------------------------

_HWINT_MILESTONES_JSON = [
    {
        "name": "Interface Control Document (ICD) Definition",
        "planned_date": "2025-12-15",
        "completion_date": "2025-12-13",
        "tl_declared_status": "Done",
        "ai_verification": "Verified",
        "evidence": "ICD v1.0 signed off; JIRA ICD-08 closed 2025-12-12, within ±7 days of completion_date.",
    },
    {
        "name": "Simulation Environment Setup",
        "planned_date": "2026-01-15",
        "completion_date": "2026-01-16",
        "tl_declared_status": "Done",
        "ai_verification": "Verified",
        "evidence": "JIRA SIM-14 and SIM-15 closed 2026-01-15; simulation smoke test results in weekly report 2026-01-19.",
    },
    {
        "name": "Hardware Component Validation",
        "planned_date": "2026-02-15",
        "completion_date": "2026-02-14",
        "tl_declared_status": "Done",
        "ai_verification": "Verified",
        "evidence": "JIRA HCV-31 closed 2026-02-13; hardware validation report attached to milestone.",
    },
    {
        "name": "Communication Protocol Testing",
        "planned_date": "2026-03-15",
        "completion_date": "2026-03-19",
        "tl_declared_status": "Done",
        "ai_verification": "Inconclusive",
        "evidence": "completion_date 2026-03-19 is 4 days after planned. JIRA has no closure event within ±14 days — insufficient evidence to confirm or deny. TL declaration accepted.",
    },
    {
        "name": "Signal Interface Verification",
        "planned_date": "2026-04-15",
        "completion_date": "2026-04-14",
        "tl_declared_status": "Done",
        "ai_verification": "Verified",
        "evidence": "JIRA SIG-44 closed 2026-04-13; weekly report 2026-04-20 mentions signal verification sign-off.",
    },
    {
        "name": "Fault Injection Testing",
        "planned_date": "2026-06-01",
        "completion_date": None,
        "tl_declared_status": "In-progress",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "System Integration Test (SIT)",
        "planned_date": "2026-07-01",
        "completion_date": None,
        "tl_declared_status": "In-progress",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "Final Validation Report",
        "planned_date": "2026-08-01",
        "completion_date": None,
        "tl_declared_status": "Pending",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
]

_HWINT_REPORTS = {
    _weeks_ago(4): """\
# Weekly Project Report — HW-SW Interface Validation (BMS)

**Week of:** {week}  |  **Project:** DEMO-HWINT

## Accomplishments

- **Signal Interface Verification preparation complete.** All 35 test cases authored for analog signal integrity, digital I/O, and SPI communication channels; reviewed and approved.
- **Communication Protocol Testing formally closed.** Minor rework on CAN FD bit-timing parameters completed; closure report signed.
- **Fault injection test strategy drafted.** E2 authored fault injection test approach document; reviewed and approved at milestone kickoff.

## Tasks Worked On

**Testing**
- E1: Completed test case authoring for Signal Interface Verification; 35 cases ready for execution.
- E2: Authored fault injection test strategy; closed Communication Protocol Testing milestone.

## Risks and Blockers

No active blockers this week.

## Next Week Plan

- E1: Begin Signal Interface Verification execution (analog signal integrity tests first).
- E2: Set up fault injection harness; align with hardware team on test bench configuration.

## Highlights / Things to Watch

### Progress signals
- Communication Protocol Testing closed successfully — 4/8 milestones now Done, project at 50% completion.
- No open defects carried over from previous milestone; clean handoff to signal verification phase.
""",

    _weeks_ago(3): """\
# Weekly Project Report — HW-SW Interface Validation (BMS)

**Week of:** {week}  |  **Project:** DEMO-HWINT

## Accomplishments

- **Signal Interface Verification 80% complete.** 28/35 test cases executed; 26 passed, 2 defects raised (HWINT-55, HWINT-56 — both related to analog offset calibration at low temperature).
- **Fault injection harness configured.** E2 validated fault injection tool chain; 3 initial power-rail fault tests executed successfully.

## Tasks Worked On

**Testing**
- E1: Executed signal verification cases 1–28; 2 defects filed.
- E2: Configured fault injection harness; ran 3 power-rail fault injection tests.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| HWINT-55/56 — analog offset calibration defects require NTC thermistor characterization | E1 | 2026-04-28 |

## Next Week Plan

- E1: Fix and re-test HWINT-55/56; complete remaining signal verification cases (29–35).
- E2: Continue fault injection testing (voltage-drop and overcurrent scenarios).

## Highlights / Things to Watch

### Progress signals
- 80% through Signal Interface Verification with only 2 minor defects is a strong quality signal for this project phase.

### Risks to watch
- HWINT-55/56 require NTC thermistor characterization — lab access needed. Confirm lab booking by Mon.
""",

    _weeks_ago(2): """\
# Weekly Project Report — HW-SW Interface Validation (BMS)

**Week of:** {week}  |  **Project:** DEMO-HWINT

## Accomplishments

- **Signal Interface Verification milestone closed.** HWINT-55/56 fixed (calibration table updated) and re-tested; all 35 test cases now passing. Milestone formally Done.
- **Fault injection: 15 scenarios executed.** Voltage sag, overcurrent, and open-circuit fault scenarios tested; 14/15 passed. One unexpected reset behaviour observed (HWINT-60, medium severity).

## Tasks Worked On

**Testing**
- E1: Completed HWINT-55/56 fix (thermistor calibration table); closed all 35 signal verification cases; authored closure report.
- E2: Executed 15 fault injection scenarios; raised HWINT-60 for unexpected reset.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| HWINT-60 unexpected reset in overcurrent scenario — root cause unknown | E2 | 2026-05-07 |

## Next Week Plan

- E1: Support E2 on HWINT-60 RCA; start SIT test case authoring.
- E2: Root cause HWINT-60; continue fault injection (short-circuit and ground-shift scenarios).

## Highlights / Things to Watch

### Progress signals
- Signal Interface Verification Done — 5/8 milestones complete; project at 62% with no schedule overruns.
- Fault injection progressing well (15/40 planned scenarios done in first 2 weeks).
""",

    _weeks_ago(1): """\
# Weekly Project Report — HW-SW Interface Validation (BMS)

**Week of:** {week}  |  **Project:** DEMO-HWINT

## Accomplishments

- **HWINT-60 root caused and fixed.** Reset was caused by a race condition in the watchdog supervisor during transient overcurrent; fix deployed and re-tested, HWINT-60 closed.
- **Fault injection: 28/40 scenarios complete.** Ground-shift and reverse-polarity scenarios added to execution; all passing.
- **SIT test cases 1–20 authored.** E1 completed first batch of System Integration Test cases covering end-to-end BMS charge/discharge cycles.

## Tasks Worked On

**Testing**
- E2: RCA and fix for HWINT-60; executed 13 additional fault injection scenarios.
- E1: Authored SIT test cases 1–20.

## Risks and Blockers

No active blockers. Fault injection on track; SIT authoring ahead of schedule.

## Next Week Plan

- E2: Complete fault injection (scenarios 29–40); author closure report.
- E1: Author SIT test cases 21–40; begin SIT environment integration.

## Highlights / Things to Watch

### Progress signals
- Fault injection 28/40 complete with only 1 resolved defect — strong indication of hardware-software interface robustness.
- SIT test cases 20/40 authored while fault injection still running — good parallel progress.
""",

    _weeks_ago(0): """\
# Weekly Project Report — HW-SW Interface Validation (BMS)

**Week of:** {week}  |  **Project:** DEMO-HWINT

## Accomplishments

- **Fault Injection Testing milestone: 37/40 scenarios done.** 3 remaining are hardware-specific (require dedicated test bench; booked for next Mon–Tue).
- **SIT test cases 1–40 authored.** Complete SIT test case suite authored; under TL review.
- **SIT environment integration complete.** E1 confirmed BMS ECU communicates correctly with test bench in SIT configuration.

## Tasks Worked On

**Testing**
- E2: Executed fault injection scenarios 29–37; 3 final scenarios scheduled for next week.
- E1: Authored SIT cases 21–40; completed SIT environment integration.

## Risks and Blockers

No active blockers. 3 fault injection scenarios pending bench availability (already scheduled).

## Next Week Plan

- E2: Complete final 3 fault injection scenarios; author and submit closure report.
- E1: Begin SIT execution (cases 1–15) following TL approval of test case suite.

## Highlights / Things to Watch

### Missed commitments
- **E2: Fault injection closure report not submitted.** Last week's plan committed E2 to author and submit the fault injection closure report this week. Fault injection reached 37/40 scenarios but the closure report was NOT produced — it is carried forward to next week. The 3 remaining scenarios (hardware bench dependency) also slipped. This is a missed commitment vs. the plan stated in the prior week's report.

### Progress signals
- Project is on track overall. SIT test suite fully authored and environment ready — execution can start as soon as TL approves.
- 37/40 fault injection scenarios complete with zero open defects; closure expected early next week once bench is available.
""",
}

_HWINT_STATUS_HISTORY = [
    (datetime(2026, 1, 5, 9, 0), "InsufficientEvidence", "Green", "InsufficientEvidence", "OnTrack", None, 12),
    (datetime(2026, 1, 25, 9, 0), "Green", "Green", "OnTrack", "OnTrack", 12, 25),
    (datetime(2026, 2, 20, 9, 0), "Green", "Green", "OnTrack", "OnTrack", 25, 37),
    (datetime(2026, 3, 22, 9, 0), "Green", "Green", "OnTrack", "OnTrack", 37, 50),
    (datetime(2026, 4, 20, 9, 0), "Green", "Green", "OnTrack", "OnTrack", 50, 62),
]

DEMO_HWINT = {
    "project": {
        "code": "DEMO-HWINT",
        "name": "HW-SW Interface Validation — Battery Management System",
        "type": "validation",
        "description": "Validate the hardware-software interface of the BMS ECU, covering signal integrity, communication protocols, and fault injection robustness.",
        "owning_tl": "Anita Desai",
        "owning_pgm": "Priya Sharma",
        "start_date": date(2025, 11, 1),
        "planned_end_date": date(2026, 8, 1),
        "confluence_milestones_url": "https://confluence.example.com/spaces/HWINT/pages/2001/BMS+Milestones",
        "confluence_fr_url": "https://confluence.example.com/spaces/HWINT/pages/2002/BMS+Functional+Requirements",
        "confluence_extra_pages_json": [],
        "jira_project_key": "HWINT",
        "weekly_cutoff": "Mon 13:00",
        "issue_types_json": ["Task", "Bug"],
        "chronic_threshold": 3,
        "holiday_calendar_id": "default",
    },
    "status": {
        "overall_health": "Green",
        "schedule_status": "OnTrack",
        "completion_pct": 62,
        "milestones_json": _HWINT_MILESTONES_JSON,
        "rationale": (
            "5 of 8 milestones are TL-declared Done (ICD, Simulation Environment, HW Component Validation, "
            "Communication Protocol Testing, Signal Interface Verification), giving 62% completion. "
            "No milestone is overdue — the next two (Fault Injection Testing, planned 2026-06-01; SIT, planned 2026-07-01) "
            "are both In-progress and tracking to schedule. Fault injection is 37/40 scenarios complete with zero open defects; "
            "SIT environment is ready and test cases are authored. Confidence is High."
        ),
        "confidence": "High",
        "prompt_version": "ProjectStatusReasoning/v3",
        "llm_mode_used": "openai",
    },
    "status_history": _HWINT_STATUS_HISTORY,
    "reports": _HWINT_REPORTS,
}


# ---------------------------------------------------------------------------
# DEMO-SYSVAL  — AUTOSAR BSW Integration, ECU Software Stack
#   Status: Red / Delayed / 20%
#   10 milestones  (2 Done, 2 In-progress, 3 Delayed, 1 Blocked, 2 Pending)
# ---------------------------------------------------------------------------

_SYSVAL_MILESTONES_JSON = [
    {
        "name": "AUTOSAR Stack Configuration Baseline",
        "planned_date": "2026-01-15",
        "completion_date": "2026-01-13",
        "tl_declared_status": "Done",
        "ai_verification": "Verified",
        "evidence": "JIRA BSW-12 closed 2026-01-12; stack configuration baseline committed to repository.",
    },
    {
        "name": "Basic Software (BSW) Module Testing",
        "planned_date": "2026-02-28",
        "completion_date": "2026-03-14",
        "tl_declared_status": "Done",
        "ai_verification": "Inconclusive",
        "evidence": "completion_date 2026-03-14 is 14 days after planned_date 2026-02-28. JIRA has no closure event within ±14 days — boundary case. TL declaration accepted but confidence is low.",
    },
    {
        "name": "Communication Matrix Validation (CAN/LIN)",
        "planned_date": "2026-03-15",
        "completion_date": None,
        "tl_declared_status": "Delayed",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "Diagnostic Protocol Testing (UDS/OBD-II)",
        "planned_date": "2026-03-31",
        "completion_date": None,
        "tl_declared_status": "Delayed",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "Memory Stack Validation (NvM/MemIf/Fee)",
        "planned_date": "2026-04-15",
        "completion_date": None,
        "tl_declared_status": "Delayed",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "MCAL Driver Integration",
        "planned_date": "2026-04-30",
        "completion_date": None,
        "tl_declared_status": "Blocked",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "End-to-End Signal Validation",
        "planned_date": "2026-05-31",
        "completion_date": None,
        "tl_declared_status": "In-progress",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "Bootloader Validation",
        "planned_date": "2026-06-15",
        "completion_date": None,
        "tl_declared_status": "In-progress",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "ECU Integration Testing",
        "planned_date": "2026-06-30",
        "completion_date": None,
        "tl_declared_status": "Pending",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
    {
        "name": "System Release Readiness Review",
        "planned_date": "2026-07-15",
        "completion_date": None,
        "tl_declared_status": "Pending",
        "ai_verification": "NotApplicable",
        "evidence": "TL declaration accepted as-is — no verification required for non-Done status",
    },
]

_SYSVAL_REPORTS = {
    _weeks_ago(4): """\
# Weekly Project Report — AUTOSAR BSW Integration (ECU Stack)

**Week of:** {week}  |  **Project:** DEMO-SYSVAL

## Accomplishments

- **Communication Matrix Validation: first 12 CAN signals validated.** Out of 48 CAN signals in the communication matrix, 12 tested for correct DBC encoding; 10 passed, 2 failed (SYSVAL-41, SYSVAL-42 — DLC mismatch on diagnostic frames).
- **UDS session layer: basic session management functional.** Default, extended, and programming sessions switching correctly; 6/18 UDS service test cases passing.

## Tasks Worked On

**Testing**
- E1: CAN signal validation (12 signals); 2 defects filed.
- E2: UDS session management testing (6/18 cases).

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| MCAL SPI driver delivery from silicon vendor delayed | TL | 2026-04-30 |
| Communication matrix validation significantly behind plan (12/48 signals in week 2) | E1 | 2026-05-15 |

## Next Week Plan

- E1: Fix SYSVAL-41/42; validate CAN signals 13–30.
- E2: Continue UDS testing (cases 7–18); start OBD-II mode testing.
- TL: Escalate MCAL SPI driver delay to silicon vendor account manager.

## Highlights / Things to Watch

### New risks
- **MCAL SPI driver delay** — this is a blocking dependency for MCAL Driver Integration milestone (2026-04-30). If not received by 2026-04-15, that milestone will be Blocked.
- **CAN validation pace** — 12/48 signals in 2 weeks. At this pace, validation will finish 4 weeks late; Communication Matrix Validation milestone (2026-03-15) is already Delayed.
""",

    _weeks_ago(3): """\
# Weekly Project Report — AUTOSAR BSW Integration (ECU Stack)

**Week of:** {week}  |  **Project:** DEMO-SYSVAL

## Accomplishments

- **CAN signal validation: 30/48 signals done.** SYSVAL-41/42 fixed; validation pace improved. 10 signals remain failing due to incorrect endianness — root cause found (ARXML generation script bug).
- **UDS testing: 14/18 service cases done.** OBD Mode 01 and 03 functional; Mode 07 pending (requires live ECU with stored faults).

## Tasks Worked On

**Testing**
- E1: Fixed SYSVAL-41/42; validated 18 more CAN signals; identified endianness root cause.
- E2: UDS testing 14/18 cases; started OBD-II mode testing.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| **BLOCKER: MCAL SPI driver still not received** from silicon vendor | TL | 2026-04-30 |
| ARXML script endianness bug affects 10 CAN signals — fix requires ARXML regeneration | E1 | 2026-04-28 |
| NvM memory layout mismatch discovered — Memory Stack Validation will be impacted | E3 | 2026-04-30 |

## Next Week Plan

- E1: Fix ARXML script; regenerate and re-validate affected 10 CAN signals.
- E2: Complete UDS testing; begin LIN matrix validation.
- TL: Hard escalation on MCAL SPI driver — vendor committed to 2026-04-25, still not delivered.
- E3: Assess NvM memory layout mismatch impact; estimate rework scope.

## Highlights / Things to Watch

### Carried-over risks
- **[Carried over ×2] MCAL SPI driver delay** — now officially 2 weeks past committed date. MCAL Driver Integration milestone (2026-04-30) will be Blocked unless driver arrives by Mon.

### New issues
- **NvM memory layout mismatch** — root cause: BSW configuration generated from an old ARXML version. May require full NvM reconfiguration. Scope assessment needed urgently.
""",

    _weeks_ago(2): """\
# Weekly Project Report — AUTOSAR BSW Integration (ECU Stack)

**Week of:** {week}  |  **Project:** DEMO-SYSVAL

## Accomplishments

- **CAN signal validation complete.** ARXML script fixed; all 48 signals validated — 45 pass, 3 open defects (endianness-related, SYSVAL-51/52/53).
- **UDS testing complete: 18/18 service cases done.** All OBD-II modes tested; 2 open issues (SYSVAL-54, SYSVAL-55 — edge cases under low-voltage supply).
- **NvM layout rework scoped.** E3 assessed impact: full NvM layer reconfiguration required; estimated 3-week effort.

## Tasks Worked On

**Testing**
- E1: ARXML script fix; CAN signal re-validation for 10 signals; 3 defects remain open.
- E2: Completed UDS testing; 2 edge-case defects raised.
- E3: Scoped NvM rework — 3-week effort estimate prepared and presented to TL.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| **BLOCKER: MCAL SPI driver still not received** (now 3 weeks overdue) | TL | Escalated |
| NvM rework 3 weeks: Memory Stack Validation will slip to 2026-05-30 | E3 | 2026-05-30 |
| 3 open CAN endianness defects (SYSVAL-51/52/53) | E1 | 2026-05-10 |

## Next Week Plan

- TL: Executive escalation on MCAL SPI driver — loop in project manager.
- E1: Fix SYSVAL-51/52/53 (endianness); prepare CAN closure report once resolved.
- E2: Fix SYSVAL-54/55; prepare UDS/OBD closure report.
- E3: Begin NvM layer reconfiguration; new ARXML generation with correct memory layout.

## Highlights / Things to Watch

### Carried-over blockers
- **[Carried over ×3] MCAL SPI driver** — now 3 weeks overdue. MCAL Driver Integration milestone is formally Blocked. Without the driver, End-to-End Signal Validation and ECU Integration Testing cannot start on time. Project manager loop-in requested.

### Schedule impact
- Revised plan: even with driver arriving Mon, MCAL Integration needs 4 weeks → milestone slips to 2026-06-01. This cascades: ECU Integration Testing (2026-06-30) becomes very tight.
""",

    _weeks_ago(1): """\
# Weekly Project Report — AUTOSAR BSW Integration (ECU Stack)

**Week of:** {week}  |  **Project:** DEMO-SYSVAL

## Accomplishments

- **MCAL SPI driver received.** After executive escalation, silicon vendor delivered MCAL SPI driver v2.3.1 on Friday. MCAL Driver Integration unblocked.
- **SYSVAL-51/52/53 (CAN endianness) resolved and re-tested.** All 48 CAN signals now passing; Communication Matrix Validation closure report submitted.
- **NvM reconfiguration: Week 1/3 complete.** E3 regenerated ARXML with corrected memory layout; BSW stack recompiling cleanly.

## Tasks Worked On

**Testing / Integration**
- E1: Fixed SYSVAL-51/52/53; CAN closure report submitted to TL.
- E2: Fixed SYSVAL-54/55 (UDS edge cases); UDS/OBD closure report submitted.
- E3: Week 1 of NvM reconfiguration — ARXML regenerated, BSW stack recompiling.
- TL: Executive escalation resulted in MCAL SPI driver delivery on Friday.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| MCAL Driver Integration: 4-week effort now starting — milestone won't close before 2026-06-01 | E1/E3 | 2026-06-01 |
| NvM reconfiguration 2 weeks remaining | E3 | 2026-05-28 |
| UDS SYSVAL-54/55 fix needs regression testing on hardware | E2 | 2026-05-19 |

## Next Week Plan

- E1: Begin MCAL SPI driver integration; MCAL ADC/PWM driver porting (week 1/4).
- E3: NvM reconfiguration week 2: NvM config tables + MemIf/Fee layer updates.
- E2: Hardware regression test for SYSVAL-54/55 fix; close UDS/OBD milestone.
- TL: Review and approve CAN + UDS closure reports.

## Highlights / Things to Watch

### Progress signals
- MCAL SPI driver delivery unblocks the largest schedule risk. Executive escalation was the right call.
- CAN and UDS testing both now pending closure report approval — two milestones can close this week if TL approves.

### Risks to watch
- **Revised schedule**: MCAL Integration June 1, E2E Signal Validation June 30, ECU Integration Testing July 31 (was June 30). System Release Readiness Review will be August 15 vs planned July 15 — a 4-week overall slip.
""",

    _weeks_ago(0): """\
# Weekly Project Report — AUTOSAR BSW Integration (ECU Stack)

**Week of:** {week}  |  **Project:** DEMO-SYSVAL

## Accomplishments

- **MCAL ADC driver integration complete.** E1 integrated and verified ADC driver; 8/8 test cases passing. SPI, PWM, and CAN controllers remaining (3 weeks).
- **NvM reconfiguration: Week 2/3 complete.** E3 updated NvM config tables and MemIf/Fee layer; stack compiling with new layout; basic NvM read/write functional.
- **UDS/OBD milestone: closure report approved.** TL signed off; Diagnostic Protocol Testing formally Done.

## Tasks Worked On

**Integration**
- E1: MCAL ADC driver integration and verification (8 test cases).
- E3: NvM config tables + MemIf/Fee layer; basic NvM read/write validated on target hardware.

**Compliance / Closure**
- E2: UDS/OBD closure report approved; milestone formally closed (update pending TL milestone page update).
- TL: Reviewed and approved CAN and UDS closure reports; updated milestone page for Diagnostic Protocol Testing.

## Risks and Blockers

| Risk | Owner | ETA |
|---|---|---|
| SPI/PWM/CAN controller integration still 3 weeks out | E1 | 2026-06-01 |
| NvM full validation (stress + retention tests) not yet scheduled | E3 | 2026-05-30 |

## Next Week Plan

- E1: MCAL SPI controller integration (week 2/4 of MCAL integration).
- E3: Week 3/3 NvM reconfiguration: stress tests + 10k write-cycle retention validation.
- E2: Start End-to-End Signal Validation test cases (authoring phase).
- TL: Update milestone page to reflect Diagnostic Protocol Testing Done.

## Highlights / Things to Watch

### Progress signals
- UDS/OBD milestone formally closed — 3rd Done milestone; project moves from 10% to 20% but remains at Red/Delayed overall due to 3 Delayed milestones (Communication Matrix, Memory Stack, MCAL Integration) behind original schedule.

### Risks to watch
- Overall project is approximately **4 weeks behind original plan**. Revised schedule (System Release Readiness August 15 vs July 15) has been communicated to PGM. No further slippage expected if MCAL integration completes on time.
""",
}

_SYSVAL_STATUS_HISTORY = [
    (datetime(2026, 1, 22, 9, 0), "InsufficientEvidence", "Green", "InsufficientEvidence", "OnTrack", None, 10),
    (datetime(2026, 2, 15, 9, 0), "Green", "Green", "OnTrack", "OnTrack", 10, 20),
    (datetime(2026, 3, 22, 9, 0), "Green", "Amber", "OnTrack", "AtRisk", 20, 20),
    (datetime(2026, 4, 5, 9, 0), "Amber", "Red", "AtRisk", "Delayed", 20, 20),
    (datetime(2026, 4, 22, 9, 0), "Red", "Red", "Delayed", "Delayed", 20, 20),
]

DEMO_SYSVAL = {
    "project": {
        "code": "DEMO-SYSVAL",
        "name": "AUTOSAR BSW Integration — ECU Software Stack",
        "type": "integration",
        "description": "Integrate and validate the AUTOSAR Basic Software stack on the target ECU, covering BSW modules, MCAL drivers, diagnostic protocols, and memory stack.",
        "owning_tl": "Suresh Nair",
        "owning_pgm": "Priya Sharma",
        "start_date": date(2025, 12, 1),
        "planned_end_date": date(2026, 7, 15),
        "confluence_milestones_url": "https://confluence.example.com/spaces/SYSVAL/pages/3001/ECU+Stack+Milestones",
        "confluence_fr_url": "https://confluence.example.com/spaces/SYSVAL/pages/3002/ECU+Stack+Functional+Requirements",
        "confluence_extra_pages_json": [],
        "jira_project_key": "SYSVAL",
        "weekly_cutoff": "Mon 13:00",
        "issue_types_json": ["Task", "Bug", "Story"],
        "chronic_threshold": 3,
        "holiday_calendar_id": "default",
    },
    "status": {
        "overall_health": "Red",
        "schedule_status": "Delayed",
        "completion_pct": 20,
        "milestones_json": _SYSVAL_MILESTONES_JSON,
        "rationale": (
            "2 of 10 milestones are Done (AUTOSAR Stack Configuration Baseline, BSW Module Testing), giving 20% completion. "
            "3 milestones are formally Delayed — Communication Matrix Validation (planned 2026-03-15, now 65 days overdue), "
            "Diagnostic Protocol Testing (planned 2026-03-31, 49 days overdue), and Memory Stack Validation (planned 2026-04-15, "
            "34 days overdue). MCAL Driver Integration (planned 2026-04-30) is Blocked pending silicon vendor SPI driver delivery "
            "— unblocked this week after executive escalation; integration now estimated to complete 2026-06-01. Overall project is "
            "approximately 4 weeks behind the original schedule. Confidence is Medium — revised schedule communicated to PGM."
        ),
        "confidence": "Medium",
        "prompt_version": "ProjectStatusReasoning/v3",
        "llm_mode_used": "openai",
    },
    "status_history": _SYSVAL_STATUS_HISTORY,
    "reports": _SYSVAL_REPORTS,
}


# ---------------------------------------------------------------------------
# Simulated JIRA activity — returned by the /jira-activity route for demo
# projects instead of making a real JIRA API call.
# Each entry: id, title, status, last_activity (YYYY-MM-DD string).
# ---------------------------------------------------------------------------

DEMO_JIRA_ACTIVITY: dict[str, list[dict]] = {
    "DEMO-ASPICE": [
        {"id": "ASPICE-231", "title": "CAN timeout under load — SIT case 21", "status": "Open", "last_activity": "2026-05-17"},
        {"id": "ASPICE-229", "title": "SIT failure: power management SWC signal debounce", "status": "In Progress", "last_activity": "2026-05-18"},
        {"id": "ASPICE-228", "title": "SIT case 12 — debounce timing in PDU router", "status": "In Progress", "last_activity": "2026-05-18"},
        {"id": "ASPICE-220", "title": "Test fixture procurement for CIT cases 28–30", "status": "Done", "last_activity": "2026-05-11"},
        {"id": "SWE4-REG", "title": "SWE.4 regression test specification (work product #9)", "status": "In Review", "last_activity": "2026-05-18"},
        {"id": "SWE4-WP7", "title": "SWE.4 corrective work product #7 — minor update pending", "status": "In Progress", "last_activity": "2026-05-19"},
        {"id": "ASPICE-216", "title": "CIT low-severity defect — message ID boundary", "status": "Done", "last_activity": "2026-05-05"},
        {"id": "ASPICE-215", "title": "CIT low-severity defect — frame padding", "status": "Done", "last_activity": "2026-05-05"},
    ],
    "DEMO-HWINT": [
        {"id": "HWINT-FI-38", "title": "Fault injection scenario 38 — short-circuit (scheduled Mon)", "status": "Open", "last_activity": "2026-05-19"},
        {"id": "HWINT-FI-39", "title": "Fault injection scenario 39 — ground-shift (scheduled Mon)", "status": "Open", "last_activity": "2026-05-19"},
        {"id": "HWINT-FI-40", "title": "Fault injection scenario 40 — open-circuit (scheduled Tue)", "status": "Open", "last_activity": "2026-05-19"},
        {"id": "HWINT-SIT-40", "title": "SIT test case suite (cases 1–40) — under TL review", "status": "In Review", "last_activity": "2026-05-18"},
        {"id": "HWINT-SIT-ENV", "title": "SIT environment integration to BMS test bench", "status": "Done", "last_activity": "2026-05-15"},
        {"id": "HWINT-60", "title": "Overcurrent scenario: watchdog supervisor race condition", "status": "Done", "last_activity": "2026-05-12"},
        {"id": "HWINT-56", "title": "Analog offset calibration at low temperature (NTC)", "status": "Done", "last_activity": "2026-05-07"},
        {"id": "HWINT-55", "title": "Signal integrity: analog channel 3 offset at -40°C", "status": "Done", "last_activity": "2026-05-07"},
    ],
    "DEMO-SYSVAL": [
        {"id": "BSW-SPI-01", "title": "MCAL SPI controller integration — week 2/4", "status": "In Progress", "last_activity": "2026-05-19"},
        {"id": "BSW-NVM-01", "title": "NvM reconfiguration: stress tests + 10k-write retention", "status": "In Progress", "last_activity": "2026-05-18"},
        {"id": "BSW-E2E-01", "title": "End-to-End Signal Validation test case authoring", "status": "In Progress", "last_activity": "2026-05-18"},
        {"id": "BSW-MCAL-ADC", "title": "MCAL ADC driver integration and verification", "status": "Done", "last_activity": "2026-05-15"},
        {"id": "SYSVAL-54", "title": "UDS low-voltage edge case — service 0x11 response timeout", "status": "Done", "last_activity": "2026-05-14"},
        {"id": "SYSVAL-55", "title": "UDS low-voltage edge case — session rollback on undervoltage", "status": "Done", "last_activity": "2026-05-14"},
        {"id": "SYSVAL-53", "title": "CAN signal endianness — diagnostic frame DLC mismatch", "status": "Done", "last_activity": "2026-05-12"},
        {"id": "SYSVAL-51", "title": "CAN signal endianness — ARXML byte-order mismatch", "status": "Done", "last_activity": "2026-05-11"},
    ],
}


# ---------------------------------------------------------------------------
# Top-level list used by seed_demo / clear_demo
# ---------------------------------------------------------------------------

ALL_DEMO_PROJECTS = [DEMO_ASPICE, DEMO_HWINT, DEMO_SYSVAL]
