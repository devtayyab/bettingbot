"""Add event_name on signals and final_score/settled_at on bets.

Revision ID: 0006_event_name_and_scores
Revises: 0005_signal_market_odds
Create Date: 2026-10-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_event_name_and_scores"
down_revision: Union[str, None] = "0005_signal_market_odds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    sig_cols = [c["name"] for c in insp.get_columns("signals")]
    bet_cols = [c["name"] for c in insp.get_columns("bets")]

    with op.batch_alter_table("signals") as batch_op:
        if "event_name" not in sig_cols:
            batch_op.add_column(sa.Column("event_name", sa.String(255), nullable=True))

    with op.batch_alter_table("bets") as batch_op:
        if "final_score" not in bet_cols:
            batch_op.add_column(sa.Column("final_score", sa.String(64), nullable=True))
        if "settled_at" not in bet_cols:
            batch_op.add_column(sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bets") as batch_op:
        batch_op.drop_column("settled_at")
        batch_op.drop_column("final_score")

    with op.batch_alter_table("signals") as batch_op:
        batch_op.drop_column("event_name")

