"""Execution orchestrator: picks up signals, applies limits, and executes placement."""

from __future__ import annotations

from typing import Protocol

from ..config import get_settings
from ..db.repository import get_event_exposure, open_signals, record_bet
from ..db.session import session_scope
from ..logging import get_logger
from ..notifier import Notifier
from ..placement.base import PlacementRequest, PlacementResult, BetPlacer
from ..placement.stoiximan import StoiximanPlacer
from ..placement.bet365 import Bet365Placer

log = get_logger("executor")


class PlacementRouter:
    def __init__(self) -> None:
        self.placers: dict[str, BetPlacer] = {
            "stoiximan": StoiximanPlacer(headless=True),
            "bet365": Bet365Placer(headless=True),
        }

    def get_placer(self, bookmaker: str) -> BetPlacer | None:
        return self.placers.get(bookmaker)


class Executor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.router = PlacementRouter()
        self.notifier = Notifier()

    def execute_pending(self) -> int:
        """Finds open signals and attempts to place them if exposure limits allow."""
        s = self.settings
        placed_count = 0
        
        # If human approval is required, we only act on "approved" signals.
        # Otherwise, we can auto-bet "detected" signals.
        target_status = "approved" if s.placement_require_approval else "detected"

        with session_scope() as session:
            signals = open_signals(session, status=target_status)
            if not signals:
                return 0

            for sig in signals:
                # 1. Correlated Bets Grouping / Over-exposure check
                current_exposure = get_event_exposure(session, sig.event_id)
                available_exposure = s.max_event_exposure - current_exposure
                
                if available_exposure <= 0:
                    log.info("execution_skipped_exposure", selection=sig.selection, event=sig.event_id, exposure=current_exposure)
                    sig.status = "rejected"
                    continue
                
                # Cap the stake to the available exposure to prevent over-betting the same match.
                stake = min(sig.recommended_stake, available_exposure)
                if stake < 0.50:  # Minimum acceptable stake for most books
                    log.info("execution_skipped_min_stake", selection=sig.selection, stake=stake)
                    sig.status = "rejected"
                    continue
                
                # 2. Placement Request
                # Price protection: we accept a slightly lower odds (e.g. 1 tick drop)
                # but reject if it dropped too far below our edge threshold.
                min_acceptable_odds = max(sig.target_odds * 0.98, 1.01)
                
                req = PlacementRequest(
                    event_id=str(sig.event_id),
                    market_type=sig.market_type,
                    selection=sig.selection,
                    target_odds=sig.target_odds,
                    stake=stake,
                    min_odds=min_acceptable_odds,
                )
                
                # 3. Execute via Router
                placer = self.router.get_placer(sig.target_bookmaker)
                if not placer:
                    log.error("no_placer_configured", bookmaker=sig.target_bookmaker)
                    sig.status = "rejected"
                    continue
                    
                log.info("attempting_placement", selection=sig.selection, stake=stake, bookmaker=sig.target_bookmaker)
                res = placer.place(req)
                
                # 4. Record Result
                if res.success and res.placed_odds is not None:
                    record_bet(
                        session=session,
                        signal=sig,
                        placed_odds=res.placed_odds,
                        stake=res.stake,
                        dry_run=res.dry_run,
                        note=res.message,
                    )
                    placed_count += 1
                    if not res.dry_run:
                        self.notifier.notify_bet_placed(sig.selection, res.placed_odds, res.stake)
                else:
                    log.warning("placement_failed", selection=sig.selection, msg=res.message)
                    sig.status = "failed"
                    if not res.dry_run:
                        self.notifier.notify_bet_failed(sig.selection, res.message)
                    
        return placed_count
