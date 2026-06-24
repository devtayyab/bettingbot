"""Scheduler process: polls odds at the configured cadences and runs scans.

Pre-match markets are scanned every POLL_INTERVAL_PREMATCH seconds; live markets
every POLL_INTERVAL_LIVE seconds. Each sport is scanned independently.
"""

from __future__ import annotations

import signal as os_signal

from apscheduler.schedulers.blocking import BlockingScheduler

from ..config import get_settings
from ..core.models import Sport
from ..logging import configure_logging, get_logger
from ..pipeline import run_scan

log = get_logger("scheduler")

SPORTS = [Sport.SOCCER, Sport.TENNIS, Sport.BASKETBALL]


def _scan_prematch() -> None:
    for sport in SPORTS:
        try:
            run_scan(sport, live=False)
        except Exception as exc:  # noqa: BLE001
            log.error("prematch_scan_failed", sport=sport.value, error=str(exc))


def _scan_live() -> None:
    for sport in SPORTS:
        try:
            run_scan(sport, live=True)
        except Exception as exc:  # noqa: BLE001
            log.error("live_scan_failed", sport=sport.value, error=str(exc))


def main() -> None:
    configure_logging()
    s = get_settings()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(_scan_prematch, "interval", seconds=s.poll_interval_prematch, id="prematch")
    scheduler.add_job(_scan_live, "interval", seconds=s.poll_interval_live, id="live")

    for sig in (os_signal.SIGINT, os_signal.SIGTERM):
        os_signal.signal(sig, lambda *_: scheduler.shutdown(wait=False))

    log.info(
        "scheduler_start",
        prematch_interval=s.poll_interval_prematch,
        live_interval=s.poll_interval_live,
    )
    # Run one immediate pre-match scan so there is data on boot.
    _scan_prematch()
    scheduler.start()


if __name__ == "__main__":
    main()
