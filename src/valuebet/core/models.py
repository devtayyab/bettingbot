"""Domain models shared across services. Pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Sport(str, Enum):
    SOCCER = "soccer"
    TENNIS = "tennis"
    BASKETBALL = "basketball"
    AMERICAN_FOOTBALL = "american_football"
    BASEBALL = "baseball"
    ICE_HOCKEY = "ice_hockey"
    CRICKET = "cricket"
    RUGBY_LEAGUE = "rugby_league"
    RUGBY_UNION = "rugby_union"
    GOLF = "golf"
    MMA = "mma"
    BOXING = "boxing"
    VOLLEYBALL = "volleyball"
    HANDBALL = "handball"
    DARTS = "darts"
    ESPORTS = "esports"
    TABLE_TENNIS = "table_tennis"


class MarketStatus(str, Enum):
    PREMATCH = "prematch"
    LIVE = "live"
    CLOSED = "closed"


class SignalStatus(str, Enum):
    DETECTED = "detected"          # value found, awaiting approval / placement
    APPROVED = "approved"          # human approved (if approval required)
    PLACED = "placed"              # bet placed on Stoiximan
    REJECTED = "rejected"          # human rejected or filter failed downstream
    EXPIRED = "expired"            # odds moved before placement


class SettlementRule(str, Enum):
    """Settlement rule for a market — must match exactly across sources.

    REGULATION_TIME: 90 min only (no extra time). Most football 1X2 markets.
    INCLUDING_OT:    Result after extra time / overtime counts.
    QUALIFICATION:   Advances in the competition (two-legged tie aggregate).
    SETS:            Winner determined by sets (tennis, volleyball).
    POINTS:          Total points / games market.
    UNKNOWN:         Rule could not be determined; matching will be conservative.
    """
    REGULATION_TIME = "regulation_time"
    INCLUDING_OT = "including_ot"
    QUALIFICATION = "qualification"
    SETS = "sets"
    POINTS = "points"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Quote:
    """A single price for one selection from one source at a point in time."""

    source: str                    # "betfair" | "pinnacle" | "stoiximan"
    selection: str                 # canonical selection name (e.g. home team)
    decimal_odds: float
    captured_at: datetime
    # For exchanges:
    lay_odds: float | None = None
    back_liquidity: float | None = None
    lay_liquidity: float | None = None
    # For fixed-odds, we might still just use back_liquidity as available stake.


@dataclass(frozen=True)
class MarketSnapshot:
    """All quotes for one market from one source, at one capture instant."""

    event_id: str
    market_id: str
    market_type: str               # e.g. "MATCH_ODDS", "1X2"
    sport: Sport
    status: MarketStatus
    start_time: datetime
    total_matched: float | None = None
    quotes: list[Quote] = field(default_factory=list)
    # Feature 2: Suspension detection — True when Betfair suspends the market
    # (e.g., goal scored, match interruption). Never bet into a suspended market.
    is_suspended: bool = False
    # Feature 5: Settlement rule — must match exactly between reference and target.
    settlement_rule: SettlementRule = SettlementRule.UNKNOWN

    def selections(self) -> list[str]:
        return [q.selection for q in self.quotes]

    def quote_for(self, selection: str) -> Quote | None:
        for q in self.quotes:
            if q.selection == selection:
                return q
        return None


@dataclass(frozen=True)
class ScanResult:
    """Output of one engine scan: the raw snapshots fetched (for storage/CLV) and
    the value signals detected. Fetching happens once, here, so callers never
    re-hit the source APIs to persist odds."""

    snapshots: list["MarketSnapshot"]
    signals: list["ValueSignal"]


@dataclass(frozen=True)
class ValueSignal:
    """A detected value opportunity, ready for sizing/placement."""

    event_id: str
    market_id: str
    market_type: str
    selection: str
    sport: Sport
    fair_prob: float               # de-vigged reference probability (Betfair)
    confirm_prob: float | None     # de-vigged Pinnacle probability (confirmation)
    target_bookmaker: str          # e.g. 'stoiximan', 'bet365'
    target_odds: float             # odds offered by the target book
    edge: float                    # expected ROI per unit stake at target_odds
    recommended_stake: float
    detected_at: datetime
