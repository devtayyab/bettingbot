"""initial schema + timescale hypertable for odds_snapshot

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("sport", sa.String(32), index=True),
        sa.Column("name", sa.String(255)),
        sa.Column("start_time", sa.DateTime(timezone=True), index=True),
        sa.Column("betfair_event_id", sa.String(64)),
        sa.Column("pinnacle_event_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("betfair_event_id", name="uq_event_betfair"),
    )
    op.create_table(
        "markets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("events.id"), index=True),
        sa.Column("market_type", sa.String(64)),
        sa.Column("status", sa.String(16), server_default="prematch"),
        sa.Column("betfair_market_id", sa.String(64)),
    )
    op.create_table(
        "odds_snapshot",
        sa.Column("captured_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), primary_key=True),
        sa.Column("market_type", sa.String(64), primary_key=True),
        sa.Column("selection", sa.String(128), primary_key=True),
        sa.Column("decimal_odds", sa.Float()),
        sa.Column("liquidity", sa.Float(), nullable=True),
    )
    op.create_index("ix_odds_event_source_time", "odds_snapshot",
                    ["event_id", "source", "captured_at"])
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), index=True),
        sa.Column("market_type", sa.String(64)),
        sa.Column("selection", sa.String(128)),
        sa.Column("sport", sa.String(32)),
        sa.Column("fair_prob", sa.Float()),
        sa.Column("confirm_prob", sa.Float(), nullable=True),
        sa.Column("target_odds", sa.Float()),
        sa.Column("edge", sa.Float(), index=True),
        sa.Column("recommended_stake", sa.Float()),
        sa.Column("status", sa.String(16), server_default="detected", index=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    op.create_table(
        "bets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("signal_id", sa.BigInteger(), sa.ForeignKey("signals.id"), unique=True),
        sa.Column("book", sa.String(32), server_default="stoiximan"),
        sa.Column("selection", sa.String(128)),
        sa.Column("placed_odds", sa.Float()),
        sa.Column("stake", sa.Float()),
        sa.Column("outcome", sa.String(16), server_default="pending", index=True),
        sa.Column("profit", sa.Float(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), server_default=sa.true()),
        sa.Column("placed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("note", sa.String(512), nullable=True),
    )

    # Turn the odds feed into a Timescale hypertable (no-op if extension absent).
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute(
        "SELECT create_hypertable('odds_snapshot', 'captured_at', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.drop_table("bets")
    op.drop_table("signals")
    op.drop_index("ix_odds_event_source_time", table_name="odds_snapshot")
    op.drop_table("odds_snapshot")
    op.drop_table("markets")
    op.drop_table("events")
