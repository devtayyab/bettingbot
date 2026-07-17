"""Thin persistence layer between domain objects and the ORM."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import MarketSnapshot, ValueSignal
from .models import Bet, BookmakerLimitEvent, Event, Market, OddsSnapshot, Signal
from ..core.results import MockResultResolver, BetOutcome

# Statuses for which a signal is considered "still live" and must not be re-created.
OPEN_STATUSES = ("detected", "approved")


def save_snapshots(session: Session, snapshots: list[MarketSnapshot]) -> int:
    """Persist every quote in every snapshot to the time-series table."""
    rows = 0
    for snap in snapshots:
        for q in snap.quotes:
            session.add(
                OddsSnapshot(
                    captured_at=q.captured_at,
                    event_id=_safe_event_id(snap.event_id),
                    market_type=snap.market_type,
                    source=q.source,
                    selection=q.selection,
                    decimal_odds=q.decimal_odds,
                    liquidity=q.liquidity,
                )
            )
            rows += 1
    return rows


def has_open_signal(session: Session, event_id: int, market_type: str, selection: str) -> bool:
    """True if a still-live signal already exists for this event/market/selection,
    so repeated polls don't create duplicate rows for the same opportunity."""
    stmt = (
        select(Signal.id)
        .where(
            Signal.event_id == event_id,
            Signal.market_type == market_type,
            Signal.selection == selection,
            Signal.status.in_(OPEN_STATUSES),
        )
        .limit(1)
    )
    return session.scalar(stmt) is not None


def save_signal(session: Session, sig: ValueSignal) -> Signal | None:
    """Persist a signal, or return None if an equivalent open signal already exists."""
    event_id = _safe_event_id(sig.event_id)
    if has_open_signal(session, event_id, sig.market_type, sig.selection):
        return None
    row = Signal(
        event_id=event_id,
        market_type=sig.market_type,
        selection=sig.selection,
        sport=sig.sport.value,
        fair_prob=sig.fair_prob,
        confirm_prob=sig.confirm_prob,
        target_odds=sig.target_odds,
        edge=sig.edge,
        recommended_stake=sig.recommended_stake,
        status="detected",
        detected_at=sig.detected_at,
    )
    session.add(row)
    session.flush()
    return row


def open_signals(session: Session, status: str | None = None) -> list[Signal]:
    stmt = select(Signal).order_by(Signal.edge.desc())
    if status:
        stmt = stmt.where(Signal.status == status)
    return list(session.scalars(stmt))


def record_bet(
    session: Session,
    signal: Signal,
    placed_odds: float,
    stake: float,                   # accepted stake (what bookmaker took)
    dry_run: bool,
    note: str | None = None,
    requested_stake: float | None = None,  # Feature 3: what we asked for
) -> Bet:
    actual_edge = (signal.fair_prob * placed_odds) - 1.0 if signal.fair_prob else None
    _requested = requested_stake if requested_stake is not None else stake
    bet = Bet(
        signal_id=signal.id,
        selection=signal.selection,
        placed_odds=placed_odds,
        requested_stake=_requested,     # Feature 3
        stake=stake,
        actual_edge=actual_edge,
        outcome="pending",
        dry_run=dry_run,
        note=note,
        placed_at=datetime.now(timezone.utc),
    )
    signal.status = "placed"
    session.add(bet)
    session.flush()
    return bet


def pnl_summary(session: Session) -> dict:
    """Realised P&L over settled bets, plus open exposure."""
    bets = list(session.scalars(select(Bet)))
    settled = [b for b in bets if b.outcome in {"won", "lost", "void"} and b.profit is not None]
    realised = sum(b.profit for b in settled)
    staked = sum(b.stake for b in settled)
    open_exposure = sum(b.stake for b in bets if b.outcome == "pending")
    roi = (realised / staked) if staked else 0.0
    return {
        "bets_total": len(bets),
        "bets_settled": len(settled),
        "realised_pnl": round(realised, 2),
        "total_staked_settled": round(staked, 2),
        "roi": round(roi, 4),
        "open_exposure": round(open_exposure, 2),
    }


def get_event_exposure(session: Session, event_id: int) -> float:
    """Return the total staked amount for all bets on a given event."""
    stmt = (
        select(Bet.stake)
        .join(Signal)
        .where(Signal.event_id == event_id)
        .where(Bet.outcome.in_(("pending", "won", "lost")))
    )
    stakes = session.scalars(stmt).all()
    return sum(stakes) if stakes else 0.0


def _safe_event_id(raw: str | int) -> int:
    """Source event ids are strings; map them to a stable int for storage.

    For real Betfair ids ("1.234567") we strip non-digits; ids without digits hash
    deterministically via SHA-256 (NOT builtin hash(), which is salted per-process
    and would assign the same event different ids across runs, breaking joins/dedup).
    """
    if isinstance(raw, int):
        return raw
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits:
        return int(digits[:18])
    h = hashlib.sha256(str(raw).encode()).hexdigest()
    return int(h[:15], 16)  # 60 bits, comfortably within BIGINT


def update_clv_for_pending_bets(session: Session) -> int:
    """Find pending bets for events starting within 10 minutes, calculate CLV, and save."""
    from .models import Event
    from ..core.odds_math import implied_prob, midpoint_prob
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    target_time = now + timedelta(minutes=10)

    # Find pending bets with no CLV where the event starts soon
    stmt = (
        select(Bet, Signal, Event)
        .join(Signal, Bet.signal_id == Signal.id)
        .join(Event, Signal.event_id == Event.id)
        .where(Bet.outcome == "pending")
        .where(Bet.clv == None)
        .where(Event.start_time <= target_time)
    )
    
    rows = session.execute(stmt).all()
    updated = 0

    for bet, signal, event in rows:
        # Get the latest Betfair odds snapshot for this selection
        snap_stmt = (
            select(OddsSnapshot)
            .where(OddsSnapshot.event_id == event.id)
            .where(OddsSnapshot.source == "betfair")
            .where(OddsSnapshot.selection == signal.selection)
            .order_by(OddsSnapshot.captured_at.desc())
            .limit(1)
        )
        snap = session.scalar(snap_stmt)
        if snap:
            # Reconstruct fair probability at closing
            try:
                if snap.lay_odds is not None:
                    closing_prob = midpoint_prob(snap.decimal_odds, snap.lay_odds)
                else:
                    closing_prob = implied_prob(snap.decimal_odds)
                
                # CLV = (Placed Odds / Closing Fair Odds) - 1
                closing_fair_odds = 1.0 / closing_prob if closing_prob > 0 else 0
                if closing_fair_odds > 0:
                    bet.clv = (bet.placed_odds / closing_fair_odds) - 1.0
                    updated += 1
            except ValueError:
                pass

    if updated > 0:
        session.flush()
        
    return updated


# ---------------------------------------------------------------------------
# Feature 4: Bookmaker Limit Tracking — DB persistence
# ---------------------------------------------------------------------------

def save_limit_event(
    session: Session,
    bookmaker: str,
    requested_stake: float,
    accepted_stake: float,
    was_rejected: bool,
    note: str | None = None,
) -> BookmakerLimitEvent:
    """Persist one stake-acceptance record to the DB."""
    ratio = (accepted_stake / requested_stake) if requested_stake > 0 else 0.0
    event = BookmakerLimitEvent(
        bookmaker=bookmaker,
        requested_stake=requested_stake,
        accepted_stake=accepted_stake,
        acceptance_ratio=round(ratio, 4),
        was_rejected=was_rejected,
        note=note,
        placed_at=datetime.now(timezone.utc),
    )
    session.add(event)
    session.flush()
    return event


def bookmaker_limit_summary(session: Session, bookmaker: str, last_n: int = 50) -> dict:
    """Query DB for the last N limit events and compute aggregate acceptance stats."""
    stmt = (
        select(BookmakerLimitEvent)
        .where(BookmakerLimitEvent.bookmaker == bookmaker)
        .order_by(BookmakerLimitEvent.placed_at.desc())
        .limit(last_n)
    )
    events = list(session.scalars(stmt))
    if not events:
        return {"bookmaker": bookmaker, "total_events": 0,
                "acceptance_rate": None, "is_likely_limited": False}

    total_requested = sum(e.requested_stake for e in events)
    total_accepted = sum(e.accepted_stake for e in events)
    rate = (total_accepted / total_requested) if total_requested > 0 else 0.0
    rejections = sum(1 for e in events if e.was_rejected)
    return {
        "bookmaker": bookmaker,
        "total_events": len(events),
        "acceptance_rate": round(rate, 4),
        "is_likely_limited": rate < 0.70 and len(events) >= 5,
        "rejection_count": rejections,
        "last_event": events[0].placed_at.isoformat() if events else None,
    }


def all_bookmaker_limit_summaries(session: Session) -> list[dict]:
    """Return limit summaries for all bookmakers that have events."""
    stmt = select(BookmakerLimitEvent.bookmaker).distinct()
    bookmakers = list(session.scalars(stmt))
    return [bookmaker_limit_summary(session, bk) for bk in bookmakers]
