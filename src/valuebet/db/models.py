"""SQLAlchemy ORM models.

`odds_snapshot` is intended to be a TimescaleDB hypertable (see migrations) so the
high-volume, append-only odds feed scales independently of the relational tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# 64-bit ids in Postgres (sequence-backed autoincrement), but plain INTEGER on
# SQLite so the column becomes a rowid alias and autoincrements for local dev/tests.
PK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Cross-source identity: store each source's native id so we can join feeds.
    betfair_event_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    pinnacle_event_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    markets: Mapped[list[Market]] = relationship(back_populates="event")

    __table_args__ = (UniqueConstraint("betfair_event_id", name="uq_event_betfair"),)


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    market_type: Mapped[str] = mapped_column(String(64))   # MATCH_ODDS / 1X2 / ...
    status: Mapped[str] = mapped_column(String(16), default="prematch")
    betfair_market_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    event: Mapped[Event] = relationship(back_populates="markets")


class OddsSnapshot(Base):
    """One row per (source, selection) per capture. High volume — hypertable.

    Natural composite PK (no surrogate id) keyed on the partitioning column
    `captured_at`, which is exactly what a Timescale hypertable wants and also
    avoids autoincrement quirks on SQLite for local dev.
    """

    __tablename__ = "odds_snapshot"

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    market_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    selection: Mapped[str] = mapped_column(String(128), primary_key=True)
    decimal_odds: Mapped[float] = mapped_column(Float)
    lay_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    back_liquidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lay_liquidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_matched: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_odds_event_source_time", "event_id", "source", "captured_at"),
    )


class Signal(Base):
    """A detected value opportunity and its lifecycle."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(BigInteger, index=True)
    market_type: Mapped[str] = mapped_column(String(64))
    selection: Mapped[str] = mapped_column(String(128))
    sport: Mapped[str] = mapped_column(String(32))

    fair_prob: Mapped[float] = mapped_column(Float)
    confirm_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target_odds: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float, index=True)
    recommended_stake: Mapped[float] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(16), default="detected", index=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    bet: Mapped[Optional["Bet"]] = relationship(back_populates="signal", uselist=False)


class Bet(Base):
    """A bet that was actually placed (or attempted) on the target book."""

    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), unique=True)
    book: Mapped[str] = mapped_column(String(32), default="stoiximan")
    selection: Mapped[str] = mapped_column(String(128))
    placed_odds: Mapped[float] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float)
    actual_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    clv: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # pending / won / lost / void / failed
    outcome: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dry_run: Mapped[bool] = mapped_column(default=True)
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    signal: Mapped[Signal] = relationship(back_populates="bet")
