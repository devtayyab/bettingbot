"""Add Account, AccountNote, BetAllocation tables and new Signal fields.

Revision ID: 0004_accounts_and_signal_fields
Revises: 9e344607e0e5
Create Date: 2026-09-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_accounts_and_signal_fields"
down_revision: Union[str, None] = "0003_requested_stake"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New columns on signals ────────────────────────────────────────────────
    with op.batch_alter_table("signals") as batch_op:
        batch_op.add_column(sa.Column("is_live", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("event_start_time", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("max_bet", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("variables_complete", sa.Boolean(), nullable=False, server_default="1"))

    # ── accounts ─────────────────────────────────────────────────────────────
    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False, index=True),
        sa.Column("bookmaker", sa.String(64), nullable=False, server_default="stoiximan"),
        sa.Column("percentage_share", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("initial_deposit", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("is_paused", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── account_notes ─────────────────────────────────────────────────────────
    op.create_table(
        "account_notes",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── bet_allocations ───────────────────────────────────────────────────────
    op.create_table(
        "bet_allocations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("signals.id"), nullable=False, index=True),
        sa.Column("account_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("allocated_stake", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("signal_id", "account_id", name="uq_allocation_signal_account"),
    )


def downgrade() -> None:
    op.drop_table("bet_allocations")
    op.drop_table("account_notes")
    op.drop_table("accounts")

    with op.batch_alter_table("signals") as batch_op:
        batch_op.drop_column("variables_complete")
        batch_op.drop_column("max_bet")
        batch_op.drop_column("event_start_time")
        batch_op.drop_column("is_live")
