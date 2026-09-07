"""Thin persistence layer between domain objects and the ORM."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import MarketSnapshot, ValueSignal
from ..logging import get_logger
from .models import Bet, BookmakerLimitEvent, Event, OddsSnapshot, Signal

log = get_logger("db.repository")

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
                    lay_odds=q.lay_odds,
                    back_liquidity=q.back_liquidity,
                    lay_liquidity=q.lay_liquidity,
                    total_matched=snap.total_matched,
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
        # Not an error: repeated polls re-detect the same opportunity. Logged so a
        # scan reporting "0 new signals" is distinguishable from finding nothing.
        log.debug("signal_deduped", event_id=event_id, selection=sig.selection,
                  market_type=sig.market_type)
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
    log.info(
        "signal_saved",
        signal_id=row.id,
        event_id=event_id,
        selection=sig.selection,
        sport=sig.sport.value,
        target_bookmaker=sig.target_bookmaker,
        target_odds=sig.target_odds,
        edge=round(sig.edge, 4),
        recommended_stake=sig.recommended_stake,
    )
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
    bookmaker: str = "stoiximan",
    verified: bool = False,
) -> Bet:
    """Persist a placement attempt.

    The signal status distinguishes what actually happened at the book, because
    "placed" previously covered dry runs too — so the dashboard showed bets as
    placed that had never been sent anywhere:

      placed      real money on, verified in the book's own bet list
      unconfirmed real attempt, but we could not verify it — operator must check
      paper       dry run: a paper-trading record only, nothing was staked
    """
    actual_edge = (signal.fair_prob * placed_odds) - 1.0 if signal.fair_prob else None
    _requested = requested_stake if requested_stake is not None else stake
    bet = Bet(
        signal_id=signal.id,
        book=bookmaker,
        selection=signal.selection,
        placed_odds=placed_odds,
        requested_stake=_requested,     # Feature 3
        stake=stake,
        actual_edge=actual_edge,
        outcome="pending",
        dry_run=dry_run,
        note=note,
        placed_at=datetime.now(UTC),
    )
    if dry_run:
        signal.status = "paper"
    elif verified:
        signal.status = "placed"
    else:
        signal.status = "unconfirmed"
    session.add(bet)
    session.flush()

    log.info(
        "bet_recorded",
        bet_id=bet.id,
        signal_id=signal.id,
        signal_status=signal.status,
        bookmaker=bookmaker,
        selection=signal.selection,
        placed_odds=placed_odds,
        requested_stake=_requested,
        accepted_stake=stake,
        dry_run=dry_run,
        verified_on_platform=verified,
        actual_edge=round(actual_edge, 4) if actual_edge is not None else None,
        note=note,
    )
    if not dry_run and not verified:
        # Real money may be committed without us having confirmed it at the book.
        # This is the case that has to be impossible to miss in the logs.
        log.error(
            "bet_recorded_unverified",
            bet_id=bet.id,
            bookmaker=bookmaker,
            selection=signal.selection,
            stake=stake,
            msg="bet was NOT found at the bookmaker — verify manually before trusting P&L",
        )
    return bet


def pnl_summary(session: Session) -> dict:
    """Realised P&L over settled bets, plus open exposure.

    Dry-run (paper) bets are reported separately and never counted in the real
    figures — mixing them in is what made a dry-run-only deployment look like it
    had live bets and live exposure.
    """
    all_bets = list(session.scalars(select(Bet)))
    bets = [b for b in all_bets if not b.dry_run]
    paper = [b for b in all_bets if b.dry_run]
    settled = [b for b in bets if b.outcome in {"won", "lost", "void"} and b.profit is not None]
    realised = sum((b.profit or 0.0) for b in settled)
    staked = sum((b.stake or 0.0) for b in settled)
    open_exposure = sum((b.stake or 0.0) for b in bets if b.outcome == "pending")
    unconfirmed_ids = set(
        session.scalars(select(Signal.id).where(Signal.status == "unconfirmed")).all()
    )
    unconfirmed = [b for b in bets if b.signal_id in unconfirmed_ids]
    roi = (realised / staked) if staked else 0.0
    return {
        "bets_total": len(bets),
        "bets_settled": len(settled),
        "realised_pnl": round(realised, 2),
        "total_staked_settled": round(staked, 2),
        "roi": round(roi, 4),
        "open_exposure": round(open_exposure, 2),
        # Paper trail from dry runs — informational only, no money involved.
        "paper_bets": len(paper),
        "paper_exposure": round(sum(b.stake for b in paper if b.outcome == "pending"), 2),
        # Real attempts we could not verify at the bookmaker. Check these manually.
        "unconfirmed_bets": len(unconfirmed),
    }


def get_event_exposure(session: Session, event_id: int) -> float:
    """Return the total staked amount for all bets on a given event."""
    stmt = (
        select(Bet.stake)
        .join(Signal)
        .where(Signal.event_id == event_id)
        # Dry-run bets stake nothing, so they must not consume real exposure.
        .where(Bet.dry_run.is_(False))
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
    from datetime import timedelta

    from ..core.odds_math import implied_prob, midpoint_prob

    now = datetime.now(UTC)
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
    log.info("clv_updated", candidates=len(rows), updated=updated)
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
        placed_at=datetime.now(UTC),
    )
    session.add(event)
    session.flush()
    log.debug("limit_event_saved", bookmaker=bookmaker, requested=requested_stake,
              accepted=accepted_stake, ratio=event.acceptance_ratio,
              rejected=was_rejected)
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


def settle_pending_bets(session: Session) -> int:
    """Auto-settle bets from a real result feed.

    Intentionally a no-op: the only resolver in the codebase is MockResultResolver,
    which invents scores with random.randint. Wiring that into the scheduler would
    write fabricated wins, losses and profit into the bets table and report them as
    realised P&L. Settlement is manual (POST /bets/{id}/settle) until a real
    results feed is integrated here.
    """
    return 0
