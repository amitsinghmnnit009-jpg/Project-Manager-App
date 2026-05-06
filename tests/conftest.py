"""pytest fixtures and test setup."""
from __future__ import annotations
import os
import sys
from pathlib import Path

# Make `app` importable from tests
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Ensure tests use a test config / test DB so dev DB isn't clobbered
os.environ.setdefault("PMA_TEST_MODE", "1")
