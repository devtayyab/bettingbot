"""Pipeline orchestration: build sources, scan for value, persist snapshots + signals.

Source selection is config-driven. If live credentials are absent we fall back to
the deterministic mock sources so the whole system is runnable for development and
for validating the pilot's plumbing before API keys exist.
"""

from __future__ import annotations

from functools import lru_cache

from .config import get_settings
from .core.models import Sport
from .db.repository import save_signal, save_snapshots
from .db.session import session_scope
from .engine.value_engine import ValueEngine
from .logging import get_logger
from .sources.base import OddsSource
from .sources.mock import demo_sources

log = get_logger("pipeline")


@lru_cache(maxsize=1)
def build_sources() -> tuple[OddsSource, OddsSource, OddsSource]:
    """Return (reference, confirmation, target) = (Betfair, Pinnacle, Stoiximan)."""
    s = get_settings()
    have_betfair = bool(s.betfair_app_key and s.betfair_username)
    have_pinnacle = bool(s.pinnacle_username)

    if not (have_betfair and have_pinnacle):
        log.warning("using_mock_sources", reason="missing Betfair/Pinnacle credentials")
        betfair, pinnacle, stoiximan = demo_sources()
        return betfair, pinnacle, stoiximan

    from .sources.betfair import BetfairSource
    from .sources.pinnacle import PinnacleSource

    # The target book has no API; for detection we read its public odds via the same
    # OddsSource interface. For the pilot we reuse the mock target until a read-only
    # Stoiximan odds reader is wired in.
    _, _, stoiximan = demo_sources()
    return BetfairSource(), PinnacleSource(), stoiximan


def run_scan(sport: Sport, live: bool = False) -> int:
    """One full scan cycle. Returns the number of NEW signals persisted (deduped)."""
    reference, confirmation, target = build_sources()
    engine = ValueEngine(reference, confirmation, target)

    # Single fetch: the engine returns both the raw snapshots (for storage/CLV)
    # and the detected signals, so we never re-hit the source APIs.
    result = engine.scan(sport, live)

    new_signals = 0
    with session_scope() as session:
        rows = save_snapshots(session, result.snapshots)
        for sig in result.signals:
            if save_signal(session, sig) is not None:
                new_signals += 1
    log.info(
        "scan_persisted",
        sport=sport.value,
        live=live,
        odds_rows=rows,
        detected=len(result.signals),
        new_signals=new_signals,
    )
    return new_signals
