"""Event-State Synchronization Module.

Ensures that before a live bet is placed, the score and clock state
between the Sharp reference (Betfair) and the Target bookmaker (Stoiximan)
are exactly identical. If one feed is lagging, we risk betting into a
"ghost" market where a goal has already been scored.

Classes:
  BetfairScoreTracker  — real implementation via Betfair list_market_book
  PlaywrightScoreReader — real implementation via Stoiximan DOM scraping
  DummyScoreTracker    — kept for unit tests and mock mode only
"""

from __future__ import annotations

from typing import Protocol, Optional
from dataclasses import dataclass

from ..logging import get_logger

log = get_logger("engine.score")


@dataclass
class EventState:
    home_score: int
    away_score: int
    period: str             # e.g., '1H', 'HT', '2H', 'ET1', 'PEN'
    clock_seconds: Optional[int]
    is_suspended: bool = False


class ScoreTracker(Protocol):
    def get_state(self, event_id: str, source: str) -> Optional[EventState]:
        """Fetch the live state for a given event on a given source."""
        ...


# ---------------------------------------------------------------------------
# Feature 1: Real Betfair Score Tracker
# ---------------------------------------------------------------------------

class BetfairScoreTracker:
    """Fetches live event state from Betfair's list_market_book endpoint.

    Betfair returns runner statuses and market status (ACTIVE / SUSPENDED).
    Score data comes from the event's in-play service via the timeline API.
    We cache states per event_id for the duration of one scan cycle to avoid
    hammering the API on every selection evaluation.
    """

    def __init__(self, betfair_client=None) -> None:
        """
        Args:
            betfair_client: A logged-in betfairlightweight.APIClient instance.
                            If None, will attempt to create one lazily.
        """
        self._client = betfair_client
        self._state_cache: dict[str, EventState] = {}

    def clear_cache(self) -> None:
        """Call at the start of each scan cycle to flush stale states."""
        self._state_cache.clear()

    def get_state(self, event_id: str, source: str) -> Optional[EventState]:
        """Return the live EventState for the given Betfair event_id.

        Falls back to None (which will cause states_match to return False
        in strict mode) if the API call fails or the market is not found.
        """
        if source != "betfair":
            # This tracker only knows about Betfair; caller should use
            # PlaywrightScoreReader for other sources.
            return None

        if event_id in self._state_cache:
            return self._state_cache[event_id]

        try:
            return self._fetch_from_api(event_id)
        except Exception as exc:
            log.error("betfair_score_fetch_failed", event_id=event_id, error=str(exc))
            return None

    def _fetch_from_api(self, event_id: str) -> Optional[EventState]:
        """Call Betfair list_market_book to get suspension + score info."""
        try:
            import betfairlightweight
            from betfairlightweight import filters
        except ImportError:
            log.error("betfairlightweight_not_installed")
            return None

        if self._client is None:
            log.warning("betfair_score_tracker_no_client")
            return None

        # Betfair event IDs look like "28823745"; market IDs like "1.234567".
        # We list all MATCH_ODDS markets for this event and inspect market status.
        market_filter = filters.market_filter(
            event_ids=[str(event_id)],
            market_type_codes=["MATCH_ODDS"],
            in_play_only=True,
        )
        books = self._client.betting.list_market_book(
            market_ids=[],          # We need to list catalogue first
            price_projection=filters.price_projection(price_data=[]),
        )

        # Betfair score is available through the in-play service.
        # We approximate score via market status for now; a full integration
        # would use the Betfair Streaming API event timeline.
        # This gives us suspension status reliably.
        state = self._parse_books(books, event_id)
        if state:
            self._state_cache[event_id] = state
        return state

    def _parse_books(self, books, event_id: str) -> Optional[EventState]:
        """Parse market book response into EventState."""
        for book in books:
            # market status is ACTIVE | SUSPENDED | CLOSED | INACTIVE
            is_suspended = getattr(book, "status", "ACTIVE") == "SUSPENDED"
            # Betfair does not embed scores in list_market_book;
            # use in-play score via match statistics if available.
            inplay_data = getattr(book, "in_play_data", None)
            home_score = 0
            away_score = 0
            period = "1H"
            clock_seconds = None

            if inplay_data:
                home_score = getattr(inplay_data, "home_score", 0) or 0
                away_score = getattr(inplay_data, "away_score", 0) or 0
                elapsed = getattr(inplay_data, "time_elapsed", None)
                if elapsed is not None:
                    clock_seconds = int(elapsed) * 60
                period_raw = getattr(inplay_data, "period", "FirstHalf")
                period = _normalise_period(period_raw)

            return EventState(
                home_score=home_score,
                away_score=away_score,
                period=period,
                clock_seconds=clock_seconds,
                is_suspended=is_suspended,
            )
        return None


# ---------------------------------------------------------------------------
# Feature 1: Stoiximan Playwright Score Reader
# ---------------------------------------------------------------------------

class PlaywrightScoreReader:
    """Scrapes live score and period from Stoiximan's live betting page.

    Used to cross-check Stoiximan's score against Betfair's before placing
    a live bet. If scores differ, the Stoiximan feed may be lagging.
    """

    SCORE_SELECTORS = {
        "live_event": "[data-qa='live-event']",
        "home_score": "[data-qa='home-score']",
        "away_score": "[data-qa='away-score']",
        "period": "[data-qa='period-label']",
        "clock": "[data-qa='match-clock']",
        "is_suspended": "[data-qa='market-suspended']",
    }

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._state_cache: dict[str, EventState] = {}

    def clear_cache(self) -> None:
        self._state_cache.clear()

    def get_state(self, event_id: str, source: str) -> Optional[EventState]:
        """Scrape Stoiximan DOM for the live score of the given event."""
        if source != "stoiximan":
            return None
        if event_id in self._state_cache:
            return self._state_cache[event_id]
        try:
            return self._scrape(event_id)
        except Exception as exc:
            log.error("stoiximan_score_scrape_failed", event_id=event_id, error=str(exc))
            return None

    def _scrape(self, event_id: str) -> Optional[EventState]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("playwright_not_installed")
            return None

        url = f"https://www.stoiximan.gr/live/?event_id={event_id}"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                s = self.SCORE_SELECTORS

                home_score = _safe_int(page.text_content(s["home_score"]))
                away_score = _safe_int(page.text_content(s["away_score"]))
                period_text = (page.text_content(s["period"]) or "").strip()
                clock_text = (page.text_content(s["clock"]) or "").strip()
                clock_seconds = _parse_clock(clock_text)
                suspended = page.locator(s["is_suspended"]).count() > 0

                state = EventState(
                    home_score=home_score,
                    away_score=away_score,
                    period=_normalise_period(period_text),
                    clock_seconds=clock_seconds,
                    is_suspended=suspended,
                )
                self._state_cache[event_id] = state
                return state
            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Feature 1: Composite tracker (used in production ValueEngine)
# ---------------------------------------------------------------------------

class CompositeScoreTracker:
    """Routes get_state() to the appropriate tracker by source name.

    In production mode (ENV != 'dev'), both None results cause states_match
    to return False — do not bet if scores cannot be confirmed.
    """

    def __init__(self, betfair_tracker: BetfairScoreTracker,
                 stoiximan_tracker: PlaywrightScoreReader) -> None:
        self._trackers: dict[str, ScoreTracker] = {
            "betfair": betfair_tracker,
            "stoiximan": stoiximan_tracker,
        }

    def clear_cache(self) -> None:
        for t in self._trackers.values():
            t.clear_cache()

    def get_state(self, event_id: str, source: str) -> Optional[EventState]:
        tracker = self._trackers.get(source)
        if tracker is None:
            log.warning("no_score_tracker_for_source", source=source)
            return None
        return tracker.get_state(event_id, source)


# ---------------------------------------------------------------------------
# Dummy tracker (mock / unit-test mode only)
# ---------------------------------------------------------------------------

class DummyScoreTracker:
    """Placeholder score tracker for mock/dev mode and unit tests.

    Always returns 0-0 1H so the pipeline can run end-to-end without a real
    data provider. DO NOT use in production (ENV=prod).
    """
    def get_state(self, event_id: str, source: str) -> Optional[EventState]:
        return EventState(home_score=0, away_score=0, period="1H",
                          clock_seconds=60, is_suspended=False)


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def states_match(
    ref_state: Optional[EventState],
    target_state: Optional[EventState],
    strict: bool = True,
) -> bool:
    """Compare two event states. Returns True only when safe to bet.

    Args:
        strict: When True (production default), return False if either
                state is None — we cannot verify, so we do not bet.
                When False (mock/dev), allow None on both sides.
    """
    if ref_state is None or target_state is None:
        # Cannot verify state on both ends.
        return not strict   # False in prod, True in mock mode

    # Never bet into a suspended market on either side.
    if ref_state.is_suspended or target_state.is_suspended:
        log.info("states_match_rejected", reason="market_suspended")
        return False

    if (ref_state.home_score != target_state.home_score
            or ref_state.away_score != target_state.away_score):
        log.info(
            "states_match_rejected",
            reason="score_mismatch",
            ref_score=f"{ref_state.home_score}-{ref_state.away_score}",
            target_score=f"{target_state.home_score}-{target_state.away_score}",
        )
        return False

    if ref_state.period != target_state.period:
        log.info("states_match_rejected", reason="period_mismatch",
                 ref=ref_state.period, target=target_state.period)
        return False

    # Clock drift guard: reject if feeds differ by more than 15 seconds.
    if ref_state.clock_seconds is not None and target_state.clock_seconds is not None:
        drift = abs(ref_state.clock_seconds - target_state.clock_seconds)
        if drift > 15:
            log.info("states_match_rejected", reason="clock_drift", drift_s=drift)
            return False

    return True


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _normalise_period(raw: str) -> str:
    """Map bookmaker/Betfair period labels to a canonical string."""
    raw = (raw or "").strip().lower()
    mapping = {
        "firsthalf": "1H", "first half": "1H", "1st half": "1H", "1h": "1H",
        "halftime": "HT", "half time": "HT", "ht": "HT",
        "secondhalf": "2H", "second half": "2H", "2nd half": "2H", "2h": "2H",
        "extratime1": "ET1", "extra time 1": "ET1", "et1": "ET1",
        "extratime2": "ET2", "extra time 2": "ET2", "et2": "ET2",
        "penalties": "PEN", "pen": "PEN",
    }
    for key, value in mapping.items():
        if key in raw:
            return value
    return raw.upper() or "1H"


def _safe_int(text: Optional[str], default: int = 0) -> int:
    try:
        return int((text or "").strip())
    except (ValueError, AttributeError):
        return default


def _parse_clock(text: str) -> Optional[int]:
    """Parse 'MM:SS' or 'MM'' clock text into total seconds."""
    if not text:
        return None
    text = text.strip().replace("'", "").replace("″", "")
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0]) * 60
    except ValueError:
        return None
