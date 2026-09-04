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
from .score import CompositeScoreTracker, DummyScoreTracker, BetfairScoreTracker, PlaywrightScoreReader, states_match
from ..db.session import session_scope
from ..db.repository import get_event_exposure, _safe_event_id

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
        if self.settings.env == "dev":
            self.score_tracker = DummyScoreTracker()
        else:
            bf_client = getattr(self.reference, "_client", None)
            self.score_tracker = CompositeScoreTracker(
                BetfairScoreTracker(bf_client),
                PlaywrightScoreReader(headless=True)
            )

    def scan(self, sport: Sport, live: bool = False) -> ScanResult:
        """Fetch every source once, detect value, and return both the raw snapshots
        (for persistence/CLV) and the signals. Sources are hit exactly once here."""
        self._drop_tally: dict[str, int] = {}
        ref_markets = self.reference.fetch_markets(sport, live)
        conf_markets = self.confirmation.fetch_markets(sport, live)

        log.info(
            "sources_fetched",
            sport=sport.value,
            live=live,
            reference=self.reference.name,
            reference_markets=len(ref_markets),
            confirmation=self.confirmation.name,
            confirmation_markets=len(conf_markets),
        )
        if not ref_markets:
            # Without a reference price there is no fair probability, so nothing
            # can be evaluated no matter how many target markets we pull.
            log.warning(
                "no_reference_markets",
                sport=sport.value,
                live=live,
                source=self.reference.name,
                msg="no signals are possible this cycle without reference prices",
            )
        if not conf_markets and self.settings.require_confirmation:
            log.warning(
                "no_confirmation_markets",
                sport=sport.value,
                source=self.confirmation.name,
                msg="REQUIRE_CONFIRMATION is on, so every signal will be skipped",
            )

        all_snapshots = ref_markets + conf_markets
        signals: list[ValueSignal] = []

        for target_src in self.targets:
            target_markets = target_src.fetch_markets(sport, live)
            fetched = len(target_markets)
            desynced = 0
            unmatched_ref = 0
            unmatched_conf = 0
            evaluated = 0

            if not target_markets:
                log.warning(
                    "no_target_markets",
                    sport=sport.value,
                    live=live,
                    source=target_src.name,
                    msg="target book returned no markets; check the bookmaker key and API quota",
                )

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
                        desynced += 1
                        log.info(
                            "event_desynced_skipping",
                            event_id=t_mkt.event_id,
                            source=target_src.name,
                            reference_state=str(ref_state),
                            target_state=str(tar_state),
                        )
                target_markets = synced_target_markets

            all_snapshots.extend(target_markets)
            src_signals = 0
            unmatched_sample: list[dict] = []
            for tgt in target_markets:
                ref = match_markets(tgt, ref_markets)
                if ref is None:
                    unmatched_ref += 1
                    if len(unmatched_sample) < 3:
                        unmatched_sample.append({
                            "event_id": tgt.event_id,
                            "target": sorted(selection_key(s) for s in tgt.selections()),
                        })
                    continue
                conf = match_markets(tgt, conf_markets)
                if conf is None:
                    unmatched_conf += 1
                evaluated += 1
                found = self._evaluate_market(sport, tgt, ref, conf)
                src_signals += len(found)
                signals.extend(found)

            # The funnel: every market lost between fetch and signal is accounted
            # for here, so "0 signals" names the stage that dropped them instead of
            # leaving the operator to guess.
            log.info(
                "target_scan_funnel",
                sport=sport.value,
                live=live,
                source=target_src.name,
                fetched=fetched,
                dropped_desynced=desynced,
                dropped_no_reference_match=unmatched_ref,
                unmatched_examples=unmatched_sample,
                missing_confirmation_match=unmatched_conf,
                evaluated=evaluated,
                signals=src_signals,
            )

        log.info("scan_complete", sport=sport.value, live=live,
                 signals=len(signals), drops=self._drop_tally)
        return ScanResult(
            snapshots=all_snapshots, signals=signals
        )

    def _bump(self, reason: str) -> None:
        """Count a market-level rejection into the current scan's aggregate."""
        tally = getattr(self, "_drop_tally", None)
        if tally is None:
            tally = self._drop_tally = {}
        tally[reason] = tally.get(reason, 0) + 1

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
            log.debug("market_health_rejected", reason="low_total_matched",
                      value=reference.total_matched, threshold=min_total_matched)
            self._bump("market_low_total_matched")
            return []

        # Feature 2: Suspension detection
        if reference.is_suspended or target.is_suspended:
            log.debug("market_suspended", event_id=target.event_id)
            self._bump("market_suspended")
            return []

        ref_quotes = reference.quotes
        raw_probs = []
        for q in ref_quotes:
            if q.lay_odds is not None:
                # 2. Market Health - Spread & Liquidity per selection
                spread = (q.lay_odds - q.decimal_odds) / q.decimal_odds
                if spread > max_spread:
                    log.debug("health_rejected", reason="spread_too_high",
                              selection=q.selection, spread=spread, max_spread=max_spread)
                    self._bump("market_spread_too_high")
                    return []
                
                if (q.back_liquidity is not None and q.back_liquidity < min_liquidity) or \
                   (q.lay_liquidity is not None and q.lay_liquidity < min_liquidity):
                    log.debug("health_rejected", reason="low_liquidity",
                              selection=q.selection, min_liquidity=min_liquidity)
                    self._bump("market_low_liquidity")
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
        # Per-selection drop reasons. Logging each one individually would drown the
        # log at scan volume, so they are tallied and reported once per market —
        # enough to tell "no value today" from "every selection fails one gate".
        drops: dict[str, int] = {}
        best_edge: float | None = None
        is_live = target.status == MarketStatus.LIVE
        required_edge = sport_cfg["live_edge_threshold"] if is_live else sport_cfg["edge_threshold"]
        max_allowed_latency = (
            sport_cfg["max_live_latency_seconds"] if is_live
            else sport_cfg["max_prematch_latency_seconds"]
        )

        tally = getattr(self, "_drop_tally", None)
        if tally is None:
            tally = self._drop_tally = {}

        def drop(reason: str) -> None:
            drops[reason] = drops.get(reason, 0) + 1
            tally[reason] = tally.get(reason, 0) + 1

        for tq in target.quotes:
            key = selection_key(tq.selection)
            fair_prob = ref_fair_by_key.get(key)
            if fair_prob is None:
                # The market matched but this individual selection's name did not.
                drop("no_reference_price")
                log.debug("selection_unmatched", selection=tq.selection, key=key,
                          available=sorted(ref_fair_by_key))
                continue

            # 3. Favorites only.
            if fair_prob < s.favorite_min_prob:
                drop("below_favorite_min_prob")
                continue

            # 4. Edge threshold against the target book's offered odds.
            e = odds_math.edge(fair_prob, tq.decimal_odds)
            
            if best_edge is None or e > best_edge:
                best_edge = e
            if e < required_edge:
                drop("edge_below_threshold")
                continue

            # Latency protection (stale odds rejection for both live and prematch)
            latency = (now - tq.captured_at).total_seconds()
            if latency > max_allowed_latency:
                log.debug("stale_odds_rejected", selection=tq.selection, latency=latency, max_allowed=max_allowed_latency, live=is_live)
                drop("stale_odds")
                continue

            # Target quote liquidity check
            if tq.back_liquidity is not None and tq.back_liquidity < min_liquidity:
                log.debug("health_rejected", reason="target_low_liquidity", selection=tq.selection, liquidity=tq.back_liquidity)
                drop("target_low_liquidity")
                continue

            # 5. Sharp confirmation (Pinnacle agrees with Betfair).
            confirm_prob = conf_fair_by_key.get(key)
            if confirm_prob is None:
                # No Pinnacle price for this selection. When confirmation is
                # required (default) we will NOT bet on the reference alone.
                if s.require_confirmation:
                    log.debug("no_confirmation_skip", selection=tq.selection)
                    drop("no_confirmation_price")
                    continue
            elif abs(confirm_prob - fair_prob) > s.confirmation_tolerance:
                log.debug(
                    "confirmation_rejected",
                    selection=tq.selection,
                    betfair=round(fair_prob, 4),
                    pinnacle=round(confirm_prob, 4),
                )
                drop("confirmation_disagrees")
                continue

            # 6. Size it.
            # Feature 6: Event exposure cap (Correlated Bets)
            try:
                with session_scope() as session:
                    current_exposure = get_event_exposure(session, _safe_event_id(target.event_id))
            except Exception:
                current_exposure = 0.0

            if current_exposure >= s.max_event_exposure:
                log.info("event_exposure_cap_reached", event_id=target.event_id,
                         current=current_exposure, cap=s.max_event_exposure)
                return []  # Cap reached, skip all further selections for this event

            # Calculate fractional Kelly stake using dynamic bookmaker balance
            dynamic_bankroll = self.wallet.get_balance(tq.source)
            if dynamic_bankroll < 5.0:
                log.warning("insufficient_funds", bookmaker=tq.source, balance=dynamic_bankroll)
                drop("insufficient_funds")
                continue

            stake = kelly_stake(
                fair_prob=fair_prob,
                offered_odds=tq.decimal_odds,
                bankroll=dynamic_bankroll,
                fraction=s.kelly_fraction,
                max_stake=s.max_stake,
            )
            if stake <= 0:
                log.debug("zero_stake_skipped", selection=tq.selection,
                         fair_prob=round(fair_prob, 4), odds=tq.decimal_odds,
                         bankroll=dynamic_bankroll)
                drop("zero_stake")
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

        if not out and drops:
            log.debug(
                "market_no_signals",
                event_id=target.event_id,
                sport=sport.value,
                source=getattr(target.quotes[0], "source", None) if target.quotes else None,
                selections=len(target.quotes),
                live=is_live,
                best_edge=round(best_edge, 4) if best_edge is not None else None,
                required_edge=required_edge,
                drops=drops,
            )
        return out
