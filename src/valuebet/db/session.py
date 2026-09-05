"""Database engine and session factory.

The engine is created lazily on first use. Building it at import time meant a
missing driver or an unreachable database took down every module that merely
imported this one, including the test suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from ..logging import get_logger

log = get_logger("db.session")

_engine = None
_session_factory = None


def _create_db_engine():
    s = get_settings()
    db_url = s.database_url
    connect_args = {}
    if "sqlite" in db_url:
        connect_args = {"check_same_thread": False}
    try:
        eng = create_engine(db_url, pool_pre_ping=True, future=True, connect_args=connect_args)
        with eng.connect():
            pass
        return eng
    except Exception as exc:
        # Silently switching to a local SQLite file means every write goes somewhere
        # nothing else reads — the app looks healthy while the configured database
        # stays empty. Fail loudly unless the operator opted into the dev fallback.
        if getattr(s, "strict_database", True):
            raise RuntimeError(
                f"cannot connect to DATABASE_URL ({db_url!r}): {exc}. "
                "Fix the database, or set STRICT_DATABASE=false to allow the "
                "local SQLite dev fallback."
            ) from exc
        log.warning(
            "database_fallback_to_sqlite",
            configured_url=db_url,
            error=str(exc),
            msg="writing to ./valuebet.db — data will NOT be in the configured database",
        )
        return create_engine(
            "sqlite:///./valuebet.db",
            pool_pre_ping=True,
            future=True,
            connect_args={"check_same_thread": False},
        )


def get_engine():
    global _engine
    if _engine is None:
        _engine = _create_db_engine()
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _session_factory


def __getattr__(name: str):
    """Keep `from .session import engine, SessionLocal` working, but lazily."""
    if name == "engine":
        return get_engine()
    if name == "SessionLocal":
        return get_session_factory()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
