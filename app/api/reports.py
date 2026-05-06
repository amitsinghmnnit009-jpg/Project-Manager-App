"""Reports router (Step 11 — STUB)."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/projects/{project_id}/reports", tags=["reports"])

# TODO Step 11:
# GET  /api/projects/{id}/reports?week=YYYY-WNN
# GET  /api/projects/{id}/reports?from=...&to=...
# POST /api/projects/{id}/reports/{week}:regenerate
