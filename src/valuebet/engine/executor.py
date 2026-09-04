"""Execution orchestrator: picks up signals, applies limits, and executes placement."""

from __future__ import annotations

import atexit
import threading

from ..config import get_settings
from ..db.repository import get_event_exposure, open_signals, record_bet, save_limit_event
from ..db.session import session_scope
from ..logging import get_logger
from ..notifier import Notifier
from ..placement.base import BetPlacer, PlacementRequest, PlacementStatus
from ..placement.bet365 import Bet365Placer
from ..placement.stoiximan import StoiximanPlacer

log = get_logger("executor")


class PlacementRouter:
    """Owns one placer per bookmaker, shared process-wide.

    The placers hold a live Chromium session. Building a new router per execution
    cycle (the scheduler runs one every 30s) leaked a browser per placement, so the
    instances are cached here and closed once at exit.
    """

    _instance: PlacementRouter | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.placers: dict[str, BetPlacer] = {
            "stoiximan": StoiximanPlacer(headless=True),
            "bet365": Bet365Placer(headless=True),
        }

    @classmethod
    def shared(cls) -> PlacementRouter:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
                atexit.register(cls._instance.close_all)
            return cls._instance

    def get_placer(self, bookmaker: str) -> BetPlacer | None:
        return self.placers.get(bookmaker)

    def close_all(self) -> None:
        for name, placer in self.placers.items():
            try:
                placer.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("placer_close_failed", bookmaker=name, error=str(exc))


class Executor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.router = PlacementRouter.shared()
        self.notifier = Notifier()

    def execute_pending(self) -> int:
        """Finds open signals and attempts to place them if exposure limits allow.

        Returns the number of REAL bets struck at a bookmaker. Dry runs are recorded
        as paper bets and are not counted here.
        """
        s = self.settings
        placed_count = 0
        outcomes: dict[str, int] = {}

        def tally(reason: str) -> None:
            outcomes[reason] = outcomes.get(reason, 0) + 1

        # If human approval is required, we only act on "approved" signals.
        # Otherwise, we can auto-bet "detected" signals.
        target_status = "approved" if s.placement_require_approval else "detected"

        with session_scope() as session:
            signals = open_signals(session, status=target_status)
            if not signals:
                # Debug, not info: this is the normal state between opportunities,
                # and the scheduler runs this every 30 seconds.
                log.debug("no_signals_to_execute", waiting_for_status=target_status)
                return 0

            log.info("execution_cycle_started", candidates=len(signals),
                     status=target_status, dry_run=s.placement_dry_run)

            for sig in signals:
                # 1. Correlated Bets Grouping / Over-exposure check
                current_exposure = get_event_exposure(session, sig.event_id)
                available_exposure = s.max_event_exposure - current_exposure

                if available_exposure <= 0:
                    log.info("execution_skipped_exposure", selection=sig.selection, event_id=sig.event_id, exposure=current_exposure)
                    sig.status = "rejected"
                    tally("skipped_exposure")
                    continue

                # Cap the stake to the available exposure to prevent over-betting the same match.
                stake = min(sig.recommended_stake, available_exposure)
                if stake < 0.50:  # Minimum acceptable stake for most books
                    log.info("execution_skipped_min_stake", selection=sig.selection, stake=stake)
                    sig.status = "rejected"
                    tally("skipped_min_stake")
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
                bookmaker_name = getattr(sig, "target_bookmaker", None) or "stoiximan"
                placer = self.router.get_placer(bookmaker_name)
                if not placer:
                    log.error("no_placer_configured", bookmaker=bookmaker_name)
                    sig.status = "rejected"
                    tally("no_placer")
                    continue

                log.info("attempting_placement", selection=sig.selection, stake=stake, bookmaker=bookmaker_name)
                # A single placer blowing up must not roll back the whole cycle and
                # lose every other signal's status update.
                try:
                    res = placer.place(req)
                except Exception as exc:  # noqa: BLE001
                    log.error("placer_raised", bookmaker=bookmaker_name,
                              selection=sig.selection, error=str(exc))
                    sig.status = "failed"
                    tally("placer_exception")
                    continue

                # 4. Record Result
                if res.status == PlacementStatus.DRY_RUN:
                    # No money was staked. Keep a paper record, but never report it
                    # as a placed bet or notify as if one exists at the bookmaker.
                    record_bet(
                        session=session, signal=sig,
                        placed_odds=res.placed_odds or sig.target_odds,
                        stake=res.accepted_stake,
                        requested_stake=res.requested_stake,
                        dry_run=True, note=res.message,
                        bookmaker=bookmaker_name, verified=False,
                    )
                    log.info("paper_bet_recorded", selection=sig.selection,
                             stake=res.accepted_stake)
                    tally("paper")
                    continue

                if res.is_real_bet:
                    record_bet(
                        session=session, signal=sig,
                        placed_odds=res.placed_odds or sig.target_odds,
                        stake=res.accepted_stake,
                        requested_stake=res.requested_stake,
                        dry_run=False, note=res.message,
                        bookmaker=bookmaker_name,
                        verified=res.verified_on_platform,
                    )
                    save_limit_event(
                        session, bookmaker=bookmaker_name,
                        requested_stake=res.requested_stake,
                        accepted_stake=res.accepted_stake,
                        was_rejected=False, note=res.message,
                    )
                    if res.verified_on_platform:
                        placed_count += 1
                        tally("placed_verified")
                        self.notifier.notify_bet_placed(sig.selection, res.placed_odds, res.stake)
                    else:
                        tally("unconfirmed")
                        log.error("bet_unverified_at_bookmaker",
                                  selection=sig.selection, stake=res.accepted_stake,
                                  msg=res.message)
                        self.notifier.notify_bet_failed(
                            sig.selection,
                            f"UNCONFIRMED — check {bookmaker_name} manually: {res.message}",
                        )
                    continue

                log.warning("placement_failed", selection=sig.selection,
                            status=res.status.value, msg=res.message)
                sig.status = "failed"
                tally(f"failed_{res.status.value}")
                if res.status == PlacementStatus.REJECTED:
                    save_limit_event(
                        session, bookmaker=bookmaker_name,
                        requested_stake=res.requested_stake,
                        accepted_stake=0.0, was_rejected=True, note=res.message,
                    )
                self.notifier.notify_bet_failed(sig.selection, res.message)

            log.info(
                "execution_cycle_complete",
                candidates=len(signals),
                placed_verified=placed_count,
                outcomes=outcomes,
            )

        return placed_count
