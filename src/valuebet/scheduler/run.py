"""Scheduler process: polls odds at the configured cadences and runs scans.

Pre-match markets are scanned every POLL_INTERVAL_PREMATCH seconds; live markets
every POLL_INTERVAL_LIVE seconds. Each sport is scanned independently.
"""

from __future__ import annotations

import signal as os_signal
import time

from apscheduler.schedulers.blocking import BlockingScheduler

from ..config import get_settings
from ..core.models import Sport
from ..db.repository import settle_pending_bets, update_clv_for_pending_bets
from ..db.session import session_scope
from ..engine.executor import Executor, PlacementRouter
from ..logging import configure_logging, get_logger
from ..pipeline import run_scan

log = get_logger("scheduler")

SPORTS = list(Sport)

_EXECUTOR: Executor | None = None


def _executor() -> Executor:
    """One Executor for the process: it owns the browser sessions."""
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = Executor()
    return _EXECUTOR


def _scan_all(live: bool) -> None:
    """Scan every sport, reporting one summary line for the whole cycle."""
    label = "live" if live else "prematch"
    started = time.monotonic()
    new_signals = 0
    failed: list[str] = []
    for sport in SPORTS:
        try:
            new_signals += run_scan(sport, live=live)
        except Exception as exc:  # noqa: BLE001
            failed.append(sport.value)
            log.error(f"{label}_scan_failed", sport=sport.value, error=str(exc))
    log.info(
        f"{label}_scan_cycle_complete",
        sports=len(SPORTS),
        new_signals=new_signals,
        failed_sports=failed,
        elapsed_seconds=round(time.monotonic() - started, 1),
    )


def _scan_prematch() -> None:
    _scan_all(live=False)


def _scan_live() -> None:
    _scan_all(live=True)


def _execute_bets() -> None:
    try:
        placed = _executor().execute_pending()
        if placed > 0:
            log.info("execution_cycle_complete", placed=placed)
    except Exception as exc:
        log.error("execution_cycle_failed", error=str(exc))


def _track_clv() -> None:
    try:
        with session_scope() as session:
            updated = update_clv_for_pending_bets(session)
            if updated > 0:
                log.info("clv_tracked", updated=updated)
    except Exception as exc:
        log.error("clv_tracking_failed", error=str(exc))


def _settle_bets() -> None:
    """Settlement is manual until a real results feed exists.

    `settle_pending_bets` is deliberately a no-op; the only resolver in the tree
    generates random scores, and running it here wrote invented wins/losses and
    profit into the bets table. Settle via POST /bets/{id}/settle in the meantime.
    """
    try:
        with session_scope() as session:
            settled = settle_pending_bets(session)
            if settled > 0:
                log.info("bets_settled", count=settled)
    except Exception as exc:
        log.error("bet_settlement_failed", error=str(exc))


def main() -> None:
    configure_logging()
    s = get_settings()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(_scan_prematch, "interval", seconds=s.poll_interval_prematch, id="prematch")
    scheduler.add_job(_scan_live, "interval", seconds=s.poll_interval_live, id="live")
    scheduler.add_job(_execute_bets, "interval", seconds=30, id="executor")
    scheduler.add_job(_track_clv, "interval", minutes=5, id="clv_tracker")
    scheduler.add_job(_settle_bets, "interval", minutes=15, id="settlement")

    def _shutdown(*_: object) -> None:
        # Close the shared Chromium sessions, or they outlive the scheduler.
        try:
            PlacementRouter.shared().close_all()
        except Exception as exc:  # noqa: BLE001
            log.warning("placer_shutdown_failed", error=str(exc))
        scheduler.shutdown(wait=False)

    for sig in (os_signal.SIGINT, os_signal.SIGTERM):
        os_signal.signal(sig, _shutdown)

    # Full effective configuration at boot. The settings that decide whether any
    # real bet can reach a bookmaker (dry-run, approval, demo fallback, session
    # cookies) belong in the log at startup, not only in someone's .env.
    from ..placement.session_store import get_cookie_status

    cookies = get_cookie_status()
    log.info(
        "scheduler_start",
        env=s.env,
        prematch_interval=s.poll_interval_prematch,
        live_interval=s.poll_interval_live,
        placement_dry_run=s.placement_dry_run,
        placement_require_approval=s.placement_require_approval,
        placement_bookmaker=s.placement_bookmaker,
        odds_api_target_bookmaker=s.odds_api_target_bookmaker,
        allow_demo_fallback=s.allow_demo_fallback,
        require_confirmation=s.require_confirmation,
        edge_threshold=s.edge_threshold,
        max_stake=s.max_stake,
        max_event_exposure=s.max_event_exposure,
        has_session_cookies=cookies.get("has_cookies"),
        cookie_age_hours=cookies.get("age_hours"),
        has_odds_api_key=bool(s.the_odds_api_key),
        notifications_enabled=bool(s.telegram_bot_token and s.telegram_chat_id),
    )
    if s.placement_dry_run:
        log.warning(
            "dry_run_mode",
            msg="PLACEMENT_DRY_RUN=true — signals will be recorded as paper bets "
                "and NO bet will be placed at any bookmaker",
        )
    if s.allow_demo_fallback:
        log.warning(
            "demo_fallback_enabled",
            msg="ALLOW_DEMO_FALLBACK=true — empty scans will persist FAKE signals "
                "that cannot be placed anywhere",
        )
    if not cookies.get("has_cookies"):
        log.warning(
            "no_session_cookies",
            msg="no saved Stoiximan session; placement will fail until you run "
                "`python -m valuebet.cli login-stoiximan` or import cookies",
        )
    # Run one immediate pre-match scan so there is data on boot.
    _scan_prematch()
    scheduler.start()


if __name__ == "__main__":
    main()
