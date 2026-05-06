"""Admin router (Step 11 — STUB)."""
from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])

# TODO Step 11:
# GET   /admin/config
# PATCH /admin/config
# GET   /admin/templates/report
# PUT   /admin/templates/report
# GET   /admin/holidays
# PUT   /admin/holidays
# GET   /admin/logs/ai-computes
# GET   /admin/logs/reminders
# GET   /admin/logs/sync
