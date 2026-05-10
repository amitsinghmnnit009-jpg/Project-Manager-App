"""Phase 2 server-rendered UI (POC).

Adds HTML routes on top of the existing Phase 1 REST API. Same FastAPI
process, no new build pipeline. Disabled by setting PM_UI_ENABLED=false.

Screens (per PHASE2_FUNCTIONAL_REQUIREMENTS.md §4):
- W1: /portfolio skeleton  ← THIS STEP
- W2: /portfolio populated (project list + needs-attention card)
- W3: /projects/{code} header + status + sparkline
- W4: /projects/{code} report rendering + past-reports list + triggers
- W5: /projects/{code}/compare
- W6: /admin
"""
