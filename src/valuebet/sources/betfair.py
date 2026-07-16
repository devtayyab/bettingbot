"""Betfair Exchange API source.

Uses betfairlightweight for cert-based (non-interactive) login and the betting
endpoints. The Exchange gives us back-prices we treat as the sharpest reference.

Requires: BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD, cert + key paths.
Docs: https://developer.betfair.com/
"""

from __future__ import annotations

from datetime import datetime, timezone

from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..core.models import MarketSnapshot, MarketStatus, Quote, Sport
from ..logging import get_logger

log = get_logger("source.betfair")

# Betfair event type ids.
_EVENT_TYPE = {Sport.SOCCER: "1", Sport.TENNIS: "2", Sport.BASKETBALL: "7522"}


class BetfairSource:
    name = "betfair"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = None  # lazily created betfairlightweight.APIClient

    def _ensure_login(self):
        import betfairlightweight  # imported lazily so the package is optional in tests

        if self._client is None:
            s = self._settings
            self._client = betfairlightweight.APIClient(
                username=s.betfair_username,
                password=s.betfair_password,
                app_key=s.betfair_app_key,
                certs=None if not s.betfair_cert_path else (s.betfair_cert_path, s.betfair_key_path),
            )

        # Re-use the existing session across polls; only log in once. Betfair
        # sessions expire after ~hours of inactivity, so refresh with keep_alive.
        if not self._client.session_token:
            if self._settings.betfair_cert_path:
                self._client.login()
            else:
                self._client.login_interactive()
        else:
            try:
                self._client.keep_alive()
            except Exception:  # noqa: BLE001 - session lapsed; do a fresh login
                self._client.login_interactive()
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        from betfairlightweight import filters

        client = self._ensure_login()
        event_type_id = _EVENT_TYPE[sport]

        market_filter = filters.market_filter(
            event_type_ids=[event_type_id],
            market_type_codes=["MATCH_ODDS"],
            in_play_only=live,
        )
        catalogues = client.betting.list_market_catalogue(
            filter=market_filter,
            market_projection=["RUNNER_DESCRIPTION", "EVENT", "MARKET_START_TIME"],
            max_results=100,
        )
        if not catalogues:
            return []

        market_ids = [c.market_id for c in catalogues]
        books = client.betting.list_market_book(
            market_ids=market_ids,
            price_projection=filters.price_projection(price_data=["EX_BEST_OFFERS"]),
        )
        book_by_id = {b.market_id: b for b in books}

        snapshots: list[MarketSnapshot] = []
        now = datetime.now(timezone.utc)
        for cat in catalogues:
            book = book_by_id.get(cat.market_id)
            if not book:
                continue
            runner_name = {r.selection_id: r.runner_name for r in cat.runners}
            quotes: list[Quote] = []
            for runner in book.runners:
                back = runner.ex.available_to_back
                lay = runner.ex.available_to_lay
                if not back or not lay:
                    continue
                best_back = back[0]
                best_lay = lay[0]
                quotes.append(
                    Quote(
                        source=self.name,
                        selection=runner_name.get(runner.selection_id, str(runner.selection_id)),
                        decimal_odds=best_back.price,
                        lay_odds=best_lay.price,
                        back_liquidity=best_back.size,
                        lay_liquidity=best_lay.size,
                        captured_at=now,
                    )
                )
            if not quotes:
                continue
            snapshots.append(
                MarketSnapshot(
                    event_id=cat.event.id,
                    market_id=cat.market_id,
                    market_type="MATCH_ODDS",
                    sport=sport,
                    status=MarketStatus.LIVE if live else MarketStatus.PREMATCH,
                    start_time=cat.market_start_time or now,
                    total_matched=book.total_matched,
                    quotes=quotes,
                )
            )
        log.info("betfair_fetch", sport=sport.value, markets=len(snapshots), live=live)
        return snapshots
