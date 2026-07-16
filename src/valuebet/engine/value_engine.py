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
from ..core.models import MarketSnapshot, MarketStatus, ScanResult, Sport, ValueSignal
from ..logging import get_logger
from ..sources.base import OddsSource
from .matching import match_markets, selection_key
from ..core.odds_math import (
    fair_odds,
    kelly_stake,
)
from ..core.wallet import MockWalletManager
from .score import DummyScoreTracker, states_match

log = get_logger("engine.value")


class ValueEngine:
    def __init__(
        self,
        reference: OddsSource,   # Betfair
        confirmation: OddsSource,  # Pinnacle
        targets: list[OddsSource], # Target bookies to bet into (e.g. [Stoiximan, Bet365])
        settings: Settings | None = None,
    ) -> None:
        self.reference = reference
        self.confirmation = confirmation
        self.targets = targets
        self.settings = settings or get_settings()
        self.wallet = MockWalletManager()
        self.score_tracker = DummyScoreTracker()

    def scan(self, sport: Sport, live: bool = False) -> ScanResult:
        """Fetch every source once, detect value, and return both the raw snapshots
        (for persistence/CLV) and the signals. Sources are hit exactly once here."""
        ref_markets = self.reference.fetch_markets(sport, live)
        conf_markets = self.confirmation.fetch_markets(sport, live)
        
        all_snapshots = ref_markets + conf_markets
        signals: list[ValueSignal] = []

        for target_src in self.targets:
            target_markets = target_src.fetch_markets(sport, live)

            if live:
                # For live betting, we must ensure score synchronisation
                # otherwise we abort the scan for desynced events.
                synced_target_markets = []
                for t_mkt in target_markets:
                    ref_state = self.score_tracker.get_state(t_mkt.event_id, self.reference.name)
                    tar_state = self.score_tracker.get_state(t_mkt.event_id, target_src.name)
                    if states_match(ref_state, tar_state):
                        synced_target_markets.append(t_mkt)
                    else:
                        log.debug("event_desynced_skipping", event_id=t_mkt.event_id)
                target_markets = synced_target_markets

            all_snapshots.extend(target_markets)
            for tgt in target_markets:
                ref = match_markets(tgt, ref_markets)
                if ref is None:
                    continue
                conf = match_markets(tgt, conf_markets)
                signals.extend(self._evaluate_market(sport, tgt, ref, conf))
                
        log.info("scan_complete", sport=sport.value, live=live, signals=len(signals))
        return ScanResult(
            snapshots=all_snapshots, signals=signals
        )

    def _evaluate_market(
        self,
        sport: Sport,
        target: MarketSnapshot,
        reference: MarketSnapshot,
        confirmation: MarketSnapshot | None,
    ) -> list[ValueSignal]:
        s = self.settings
        sport_cfg = self.settings.get_sport_config(sport.value)
        min_total_matched = sport_cfg["min_total_matched"]
        max_spread = sport_cfg["max_spread"]
        min_liquidity = sport_cfg["min_liquidity"]
        
        # 1. Market Health - Total Matched Volume
        if reference.total_matched is not None and reference.total_matched < min_total_matched:
            log.info("market_health_rejected", reason="low_total_matched", value=reference.total_matched)
            return []

        ref_quotes = reference.quotes
        raw_probs = []
        for q in ref_quotes:
            if q.lay_odds is not None:
                # 2. Market Health - Spread & Liquidity per selection
                spread = (q.lay_odds - q.decimal_odds) / q.decimal_odds
                if spread > max_spread:
                    log.info("health_rejected", reason="spread_too_high", selection=q.selection, spread=spread)
                    return []
                
                if (q.back_liquidity is not None and q.back_liquidity < min_liquidity) or \
                   (q.lay_liquidity is not None and q.lay_liquidity < min_liquidity):
                    log.info("health_rejected", reason="low_liquidity", selection=q.selection)
                    return []
                    
                raw_probs.append(odds_math.midpoint_prob(q.decimal_odds, q.lay_odds))
            else:
                raw_probs.append(odds_math.implied_prob(q.decimal_odds))

        ref_fair = odds_math.devig_from_probs(raw_probs, odds_math.DevigMethod.MULTIPLICATIVE)
        ref_fair_by_key = {
            selection_key(q.selection): p for q, p in zip(ref_quotes, ref_fair)
        }

        conf_fair_by_key: dict[str, float] = {}
        if confirmation is not None:
            conf_raw = [odds_math.implied_prob(q.decimal_odds) for q in confirmation.quotes]
            conf_fair = odds_math.devig_from_probs(conf_raw, odds_math.DevigMethod.SHIN)
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
            
            # Use higher threshold for live markets
            is_live = (target.status == MarketStatus.LIVE)
            required_edge = sport_cfg["live_edge_threshold"] if is_live else sport_cfg["edge_threshold"]
            
            if e < required_edge:
                continue

            # Latency protection for live markets
            if is_live:
                latency = (now - tq.captured_at).total_seconds()
                if latency > s.max_live_latency_seconds:
                    log.info("stale_odds_rejected", selection=tq.selection, latency=latency)
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
            # Calculate fractional Kelly stake using dynamic bookmaker balance
            dynamic_bankroll = self.wallet.get_balance(tq.source)
            if dynamic_bankroll < 5.0:
                log.warning("insufficient_funds", bookmaker=tq.source, balance=dynamic_bankroll)
                continue
                
            stake = kelly_stake(
                fair_prob=fair_prob,
                offered_odds=tq.decimal_odds,
                bankroll=dynamic_bankroll,
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
                    target_bookmaker=tq.source,
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
