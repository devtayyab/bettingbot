"""Add market odds columns to signals for 1X2 box display.

Revision ID: 0005_signal_market_odds
Revises: 0004_accounts_and_signal_fields
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_signal_market_odds"
down_revision: Union[str, None] = "0004_accounts_and_signal_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    sig_cols = [c["name"] for c in insp.get_columns("signals")]

    with op.batch_alter_table("signals") as batch_op:
        if "home_odds" not in sig_cols:
            batch_op.add_column(sa.Column("home_odds", sa.Float(), nullable=True))
        if "draw_odds" not in sig_cols:
            batch_op.add_column(sa.Column("draw_odds", sa.Float(), nullable=True))
        if "away_odds" not in sig_cols:
            batch_op.add_column(sa.Column("away_odds", sa.Float(), nullable=True))
        if "all_market_odds_json" not in sig_cols:
            batch_op.add_column(sa.Column("all_market_odds_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("signals") as batch_op:
        batch_op.drop_column("all_market_odds_json")
        batch_op.drop_column("away_odds")
        batch_op.drop_column("draw_odds")
        batch_op.drop_column("home_odds")
