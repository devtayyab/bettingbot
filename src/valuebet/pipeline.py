"""Pipeline orchestration: build sources, scan for value, persist snapshots + signals.

Source selection is config-driven. If live credentials are absent we fall back to
the deterministic mock sources so the whole system is runnable for development and
for validating the pilot's plumbing before API keys exist.
"""

from __future__ import annotations

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
        # The target book's prices are read from ODDS_API_TARGET_BOOKMAKER but the
        # bet is placed on PLACEMENT_BOOKMAKER. When those are different books the
        # detected price does not exist on the site we bet into: the placement
        # worker looks for odds the book never offered, so it either abandons on
        # price protection or clicks the wrong selection. Betano and Stoiximan are
        # sister Kaizen brands but are separate markets with separate prices.
        if s.odds_api_target_bookmaker.split("_")[0] != s.placement_bookmaker:
            log.warning(
                "target_bookmaker_mismatch",
                quoting=s.odds_api_target_bookmaker,
                placing_on=s.placement_bookmaker,
                msg="signals are priced from a different book than the one we bet on; "
                    "placement will usually fail price protection",
            )
        tgt = TheOddsAPISource(
            target_bookmaker=s.odds_api_target_bookmaker,
            name=s.placement_bookmaker,
        )

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
    from .sources.betfair_stream import BetfairStreamSource
    from .sources.pinnacle import PinnacleSource
    from .sources.stoiximan import StoiximanSource

    bf = BetfairSource()
    targets = [StoiximanSource(headless=True)]
    return bf, PinnacleSource(), targets, BetfairStreamSource(bf)


def run_scan(sport: Sport, live: bool = False) -> int:
    """One full scan cycle. Returns the number of NEW signals persisted (deduped)."""
    reference, confirmation, targets, stream = build_sources()
    
    # Start stream lazily on first live scan
    if live and not stream._is_running:
        try:
            stream.start(sport)
        except Exception:
            pass
        
    actual_reference = stream if live else reference
    engine = ValueEngine(actual_reference, confirmation, targets)

    # Single fetch: the engine returns both the raw snapshots (for storage/CLV)
    # and the detected signals, so we never re-hit the source APIs.
    result = engine.scan(sport, live)

    # A scan finding nothing is the normal case: value is rare. Substituting mock
    # data here used to persist fabricated signals ("Team A", "Real Madrid" at
    # invented odds) as if they were real opportunities — they exist at no
    # bookmaker, so approving one and pressing Place could never produce a bet.
    if len(result.signals) == 0 and get_settings().allow_demo_fallback:
        log.warning(
            "fallback_to_demo_sources",
            sport=sport.value,
            msg="ALLOW_DEMO_FALLBACK is on: persisting FAKE signals that cannot be placed",
        )
        bf_mock, pin_mock, stx_mock = demo_sources()
        mock_engine = ValueEngine(bf_mock, pin_mock, [stx_mock])
        result = mock_engine.scan(sport, live)

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
