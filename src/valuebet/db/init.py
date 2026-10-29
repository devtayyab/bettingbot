"""Create tables and (on Postgres/Timescale) the odds hypertable + continuous view.

For the real deployment use Alembic (migrations/). This helper is for fast local
bring-up, tests, and safe additive column auto-migrations on startup.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import text

from ..logging import get_logger
from .models import Base
from .session import get_engine

log = get_logger("db.init")


def ensure_schema_columns(db_engine=None) -> None:
    """Safely add additive nullable columns if missing on an existing database."""
    eng = db_engine or get_engine()
    try:
        insp = sa.inspect(eng)
        tables = set(insp.get_table_names())
        with eng.begin() as conn:
            if "signals" in tables:
                sig_cols = {c["name"] for c in insp.get_columns("signals")}
                if "event_name" not in sig_cols:
                    conn.execute(text("ALTER TABLE signals ADD COLUMN event_name VARCHAR(255)"))
                if "home_odds" not in sig_cols:
                    conn.execute(text("ALTER TABLE signals ADD COLUMN home_odds FLOAT"))
                if "draw_odds" not in sig_cols:
                    conn.execute(text("ALTER TABLE signals ADD COLUMN draw_odds FLOAT"))
                if "away_odds" not in sig_cols:
                    conn.execute(text("ALTER TABLE signals ADD COLUMN away_odds FLOAT"))
                if "all_market_odds_json" not in sig_cols:
                    conn.execute(text("ALTER TABLE signals ADD COLUMN all_market_odds_json TEXT"))
            if "bets" in tables:
                bet_cols = {c["name"] for c in insp.get_columns("bets")}
                if "final_score" not in bet_cols:
                    conn.execute(text("ALTER TABLE bets ADD COLUMN final_score VARCHAR(64)"))
                if "settled_at" not in bet_cols:
                    ts_type = "TIMESTAMPTZ" if eng.url.drivername.startswith("postgresql") else "TIMESTAMP"
                    conn.execute(text(f"ALTER TABLE bets ADD COLUMN settled_at {ts_type}"))
    except Exception as exc:  # noqa: BLE001
        log.warning("schema_column_sync_skipped", error=str(exc))


def init_db(create_hypertable: bool = True) -> None:
    eng = get_engine()
    Base.metadata.create_all(eng)
    ensure_schema_columns(eng)
    if not create_hypertable or not eng.url.drivername.startswith("postgresql"):
        return
    with eng.begin() as conn:
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
