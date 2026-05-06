"""Status router (Step 11 — STUB)."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/projects/{project_id}/status", tags=["status"])

# TODO Step 11:
# GET  /api/projects/{id}/status
# GET  /api/projects/{id}/status/history
# POST /api/projects/{id}/status:refresh
