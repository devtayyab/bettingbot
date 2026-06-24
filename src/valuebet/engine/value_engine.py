"""Value detection engine.

Pipeline for each target-book market:
  1. Align the target market (Stoiximan) to the Betfair reference and Pinnacle.
  2. De-vig Betfair to get the fair probability per selection (the reference).
  3. Favorite filter: only selections with fair_prob >= FAVORITE_MIN_PROB.
  4. Edge: edge = fair_prob * target_odds - 1. Require edge >= EDGE_THRESHOLD.
  5. Confirmation: de-vig Pinnacle; require its fair prob to agree with Betfair
     within CONFIRMATION_TOLERANCE. A book offering a great price that BOTH sharps
     disagree with is a trap, not value.
  6. Size with fractional Kelly, capped at MAX_STAKE.

The engine is pure given its inputs (sources + settings) and returns ValueSignals;
persistence is the caller's job.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import Settings, get_settings
from ..core import odds_math
from ..core.models import MarketSnapshot, ScanResult, Sport, ValueSignal
from ..logging import get_logger
from ..sources.base import OddsSource
from .matching import match_markets, selection_key

log = get_logger("engine")


class ValueEngine:
    def __init__(
        self,
        reference: OddsSource,   # Betfair
        confirmation: OddsSource,  # Pinnacle
        target: OddsSource,        # Stoiximan (book we'd bet into)
        settings: Settings | None = None,
    ) -> None:
        self.reference = reference
        self.confirmation = confirmation
        self.target = target
        self.settings = settings or get_settings()

    def scan(self, sport: Sport, live: bool = False) -> ScanResult:
        """Fetch every source once, detect value, and return both the raw snapshots
        (for persistence/CLV) and the signals. Sources are hit exactly once here."""
        ref_markets = self.reference.fetch_markets(sport, live)
        conf_markets = self.confirmation.fetch_markets(sport, live)
        target_markets = self.target.fetch_markets(sport, live)

        signals: list[ValueSignal] = []
        for tgt in target_markets:
            ref = match_markets(tgt, ref_markets)
            if ref is None:
                continue
            conf = match_markets(tgt, conf_markets)
            signals.extend(self._evaluate_market(sport, tgt, ref, conf))
        log.info("scan_complete", sport=sport.value, live=live, signals=len(signals))
        return ScanResult(
            snapshots=ref_markets + conf_markets + target_markets, signals=signals
        )

    def _evaluate_market(
        self,
        sport: Sport,
        target: MarketSnapshot,
        reference: MarketSnapshot,
        confirmation: MarketSnapshot | None,
    ) -> list[ValueSignal]:
        s = self.settings
        ref_quotes = reference.quotes
        ref_fair = odds_math.devig(
            [q.decimal_odds for q in ref_quotes], odds_math.DevigMethod.MULTIPLICATIVE
        )
        ref_fair_by_key = {
            selection_key(q.selection): p for q, p in zip(ref_quotes, ref_fair)
        }

        conf_fair_by_key: dict[str, float] = {}
        if confirmation is not None:
            conf_fair = odds_math.devig(
                [q.decimal_odds for q in confirmation.quotes], odds_math.DevigMethod.SHIN
            )
            conf_fair_by_key = {
                selection_key(q.selection): p
                for q, p in zip(confirmation.quotes, conf_fair)
            }

        out: list[ValueSignal] = []
        now = datetime.now(timezone.utc)
        for tq in target.quotes:
            key = selection_key(tq.selection)
            fair_prob = ref_fair_by_key.get(key)
            if fair_prob is None:
                continue

            # 3. Favorites only.
            if fair_prob < s.favorite_min_prob:
                continue

            # 4. Edge threshold against the target book's offered odds.
            e = odds_math.edge(fair_prob, tq.decimal_odds)
            if e < s.edge_threshold:
                continue

            # 5. Sharp confirmation (Pinnacle agrees with Betfair).
            confirm_prob = conf_fair_by_key.get(key)
            if confirm_prob is None:
                # No Pinnacle price for this selection. When confirmation is
                # required (default) we will NOT bet on the reference alone.
                if s.require_confirmation:
                    log.info("no_confirmation_skip", selection=tq.selection)
                    continue
            elif abs(confirm_prob - fair_prob) > s.confirmation_tolerance:
                log.info(
                    "confirmation_rejected",
                    selection=tq.selection,
                    betfair=round(fair_prob, 4),
                    pinnacle=round(confirm_prob, 4),
                )
                continue

            # 6. Size it.
            stake = odds_math.kelly_stake(
                fair_prob,
                tq.decimal_odds,
                bankroll=s.bankroll,
                fraction=s.kelly_fraction,
                max_stake=s.max_stake,
            )
            if stake <= 0:
                continue

            out.append(
                ValueSignal(
                    event_id=target.event_id,
                    market_id=target.market_id,
                    market_type=target.market_type,
                    selection=tq.selection,
                    sport=sport,
                    fair_prob=fair_prob,
                    confirm_prob=confirm_prob,
                    target_odds=tq.decimal_odds,
                    edge=e,
                    recommended_stake=stake,
                    detected_at=now,
                )
            )
            log.info(
                "value_signal",
                selection=tq.selection,
                edge=round(e, 4),
                fair_prob=round(fair_prob, 4),
                target_odds=tq.decimal_odds,
                stake=stake,
            )
        return out
