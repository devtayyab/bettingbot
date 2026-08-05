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
from .notifier import Notifier
from .sources.base import OddsSource
from .sources.mock import demo_sources

log = get_logger("pipeline")


@lru_cache(maxsize=1)
def build_sources() -> tuple[OddsSource, OddsSource, list[OddsSource], OddsSource]:
    """Return (reference, confirmation, targets_list, stream)."""
    s = get_settings()
    have_odds_api = bool(s.the_odds_api_key)
    have_betfair = bool(s.betfair_app_key and s.betfair_username)
    have_pinnacle = bool(s.pinnacle_username)

    if have_odds_api:
        log.info("using_the_odds_api_source")
        from .sources.the_odds_api import TheOddsAPISource

        bf  = TheOddsAPISource(target_bookmaker="betfair_ex_uk", name="betfair")
        pin = TheOddsAPISource(target_bookmaker="pinnacle",      name="pinnacle")
        # Betano = Stoiximan (same Kaizen Gaming platform, same odds feed).
        # We use Betano via The Odds API as the target bookmaker to scan for value.
        # When a signal is found, placement still hits the real Stoiximan account.
        tgt = TheOddsAPISource(target_bookmaker="betano_uk",        name="stoiximan")

        try:
            from .sources.betfair_stream import BetfairStreamSource
            stream = BetfairStreamSource(bf)
        except Exception:
            stream = bf  # fallback: use plain API source as stream

        return bf, pin, [tgt], stream

    if not (have_betfair and have_pinnacle):
        log.warning("using_mock_sources", reason="missing Betfair/Pinnacle credentials")
        betfair, pinnacle, stoiximan = demo_sources()
        from .sources.betfair_stream import BetfairStreamSource
        return betfair, pinnacle, [stoiximan], BetfairStreamSource(betfair)

    from .sources.betfair import BetfairSource
    from .sources.pinnacle import PinnacleSource
    from .sources.stoiximan import StoiximanSource
    from .sources.betfair_stream import BetfairStreamSource

    bf = BetfairSource()
    targets = [StoiximanSource(headless=True)]
    return bf, PinnacleSource(), targets, BetfairStreamSource(bf)


def run_scan(sport: Sport, live: bool = False) -> int:
    """One full scan cycle. Returns the number of NEW signals persisted (deduped)."""
    reference, confirmation, targets, stream = build_sources()
    
    # Start stream lazily on first live scan
    if live and not stream._is_running:
        stream.start(sport)
        
    actual_reference = stream if live else reference
    engine = ValueEngine(actual_reference, confirmation, targets)

    # Single fetch: the engine returns both the raw snapshots (for storage/CLV)
    # and the detected signals, so we never re-hit the source APIs.
    result = engine.scan(sport, live)

    new_signals = 0
    notifier = Notifier()
    with session_scope() as session:
        rows = save_snapshots(session, result.snapshots)
        for sig in result.signals:
            if save_signal(session, sig) is not None:
                new_signals += 1
                notifier.notify_signal_detected(sig)
    log.info(
        "scan_persisted",
        sport=sport.value,
        live=live,
        odds_rows=rows,
        detected=len(result.signals),
        new_signals=new_signals,
    )
    return new_signals
