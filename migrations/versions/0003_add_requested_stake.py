"""add requested_stake and bookmaker_limit_events table

Revision ID: 0003_requested_stake
Revises: 9e344607e0e5
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_requested_stake"
down_revision = "9e344607e0e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bets", sa.Column("requested_stake", sa.Float(), nullable=True))
    op.create_table(
        "bookmaker_limit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("bookmaker", sa.String(32), index=True, nullable=False),
        sa.Column("requested_stake", sa.Float(), nullable=False),
        sa.Column("accepted_stake", sa.Float(), nullable=False),
        sa.Column("acceptance_ratio", sa.Float(), nullable=False),
        sa.Column("was_rejected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bookmaker_limit_events")
    op.drop_column("bets", "requested_stake")
