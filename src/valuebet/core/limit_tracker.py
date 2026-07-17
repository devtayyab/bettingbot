"""Bookmaker Limit Tracker — Feature 4.

Tracks stake reductions and bet rejections per bookmaker.
When a bookmaker consistently accepts less than requested, the account is
likely being limited. This module records each event and exposes:
  - acceptance_rate()  → accepted_stake / requested_stake over last N bets
  - is_likely_limited() → True when the account is being materially limited

A limit event is recorded by the placement worker after each attempt.
The API exposes a /limits endpoint so the operator can monitor account health.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class LimitEvent:
    """One stake-acceptance measurement from a single bet attempt."""
    bookmaker: str
    requested_stake: float
    accepted_stake: float
    was_rejected: bool          # True if the bet was refused entirely
    placed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    note: str = ""

    @property
    def acceptance_ratio(self) -> float:
        """accepted / requested. 0.0 for a full rejection."""
        if self.was_rejected or self.requested_stake <= 0:
            return 0.0
        return self.accepted_stake / self.requested_stake


class BookmakerLimitTracker:
    """In-memory tracker for bookmaker stake-acceptance history.

    In production, events are also persisted to the DB (see repository.py).
    This in-memory layer provides fast queries for the running engine.

    Typical usage:
        tracker = BookmakerLimitTracker()
        tracker.record(LimitEvent("stoiximan", requested=10, accepted=2, rejected=False))
        if tracker.is_likely_limited("stoiximan"):
            log.warning("stoiximan_account_likely_limited")
    """

    # Flags an account as likely limited when the rolling acceptance rate
    # drops below this threshold.
    LIMIT_THRESHOLD = 0.70      # < 70% acceptance rate → likely limited
    MIN_SAMPLES = 5             # need at least this many bets to flag

    def __init__(self) -> None:
        self._events: dict[str, list[LimitEvent]] = {}

    def record(self, event: LimitEvent) -> None:
        """Record a new stake-acceptance measurement."""
        self._events.setdefault(event.bookmaker, []).append(event)

    def acceptance_rate(self, bookmaker: str, last_n: int = 20) -> Optional[float]:
        """Rolling acceptance rate over the last `last_n` bets.

        Returns None if there are fewer than MIN_SAMPLES observations.
        """
        events = self._events.get(bookmaker, [])[-last_n:]
        if len(events) < self.MIN_SAMPLES:
            return None
        total_requested = sum(e.requested_stake for e in events)
        if total_requested <= 0:
            return None
        total_accepted = sum(e.accepted_stake for e in events)
        return total_accepted / total_requested

    def is_likely_limited(self, bookmaker: str, last_n: int = 20) -> bool:
        """Return True when the rolling acceptance rate is below the threshold."""
        rate = self.acceptance_rate(bookmaker, last_n)
        return rate is not None and rate < self.LIMIT_THRESHOLD

    def rejection_count(self, bookmaker: str, last_n: int = 20) -> int:
        """Number of full rejections in the last `last_n` bets."""
        events = self._events.get(bookmaker, [])[-last_n:]
        return sum(1 for e in events if e.was_rejected)

    def summary(self, bookmaker: str) -> dict:
        """Return a summary dict for the /limits API endpoint."""
        events = self._events.get(bookmaker, [])
        rate = self.acceptance_rate(bookmaker)
        return {
            "bookmaker": bookmaker,
            "total_bets": len(events),
            "acceptance_rate": round(rate, 4) if rate is not None else None,
            "is_likely_limited": self.is_likely_limited(bookmaker),
            "rejections_last_20": self.rejection_count(bookmaker),
            "last_event": events[-1].placed_at.isoformat() if events else None,
        }

    def all_summaries(self) -> list[dict]:
        """Return summaries for all tracked bookmakers."""
        return [self.summary(bk) for bk in self._events]


# Singleton — shared between the engine and the API layer.
_limit_tracker: Optional[BookmakerLimitTracker] = None


def get_limit_tracker() -> BookmakerLimitTracker:
    global _limit_tracker
    if _limit_tracker is None:
        _limit_tracker = BookmakerLimitTracker()
    return _limit_tracker
