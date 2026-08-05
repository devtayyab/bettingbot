"""Database engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings

def _create_db_engine():
    s = get_settings()
    db_url = s.database_url
    connect_args = {}
    if "sqlite" in db_url:
        connect_args = {"check_same_thread": False}
    try:
        eng = create_engine(db_url, pool_pre_ping=True, future=True, connect_args=connect_args)
        with eng.connect() as conn:
            pass
        return eng
    except Exception:
        # Fallback to SQLite zero-infra for dev/demo when local PostgreSQL is not running
        fallback_url = "sqlite:///./valuebet.db"
        return create_engine(fallback_url, pool_pre_ping=True, future=True, connect_args={"check_same_thread": False})


engine = _create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
