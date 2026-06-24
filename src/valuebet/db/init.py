"""Create tables and (on Postgres/Timescale) the odds hypertable + continuous view.

For the real deployment use Alembic (migrations/). This helper is for fast local
bring-up and tests.
"""

from __future__ import annotations

from sqlalchemy import text

from ..logging import get_logger
from .models import Base
from .session import engine

log = get_logger("db.init")


def init_db(create_hypertable: bool = True) -> None:
    Base.metadata.create_all(engine)
    if not create_hypertable or not engine.url.drivername.startswith("postgresql"):
        return
    with engine.begin() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            conn.execute(
                text(
                    "SELECT create_hypertable('odds_snapshot', 'captured_at', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
            )
            log.info("hypertable_ready", table="odds_snapshot")
        except Exception as exc:  # noqa: BLE001 - timescale may be absent; tables still work
            log.warning("hypertable_skipped", error=str(exc))


if __name__ == "__main__":
    from ..logging import configure_logging

    configure_logging()
    init_db()
    log.info("db_initialised")
