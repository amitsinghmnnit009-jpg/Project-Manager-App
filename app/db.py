"""SQLAlchemy engine + session factory + reset helper.

Phase 1 uses SQLite. To switch to PostgreSQL later, change `database.url`
in config.yaml — no code changes needed.
"""
from __future__ import annotations
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from app.config import get_config


Base = declarative_base()

_cfg = get_config()
_engine = create_engine(
    _cfg.database.url,
    echo=False,
    future=True,
    # SQLite-specific: allow access from multiple threads (FastAPI + scheduler)
    connect_args={"check_same_thread": False} if _cfg.database.url.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope() -> Session:
    """Provide a transactional scope around a series of operations."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def reset_database() -> None:
    """Drop all tables and recreate from current models. DEV ONLY."""
    # Import models so SQLAlchemy registers them on Base.metadata
    from app import models  # noqa: F401
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)


def init_database() -> None:
    """Create tables if they don't already exist (idempotent)."""
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=_engine)
