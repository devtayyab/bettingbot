"""Pinnacle API source — used as the sharp confirmation signal.

Pinnacle is a low-margin, high-limit book whose lines are widely treated as the
market consensus "true" price. We de-vig its lines and require them to agree with
Betfair before trusting an edge.

Auth is HTTP Basic. Docs: https://pinnacleapi.github.io/
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..core.models import MarketSnapshot, MarketStatus, Quote, Sport
from ..logging import get_logger

log = get_logger("source.pinnacle")

_BASE = "https://api.pinnacle.com"
# Pinnacle sport ids.
_SPORT_ID = {Sport.SOCCER: 29, Sport.TENNIS: 33, Sport.BASKETBALL: 4}


class PinnacleSource:
    name = "pinnacle"

    def __init__(self) -> None:
        s = get_settings()
        self._auth = (s.pinnacle_username, s.pinnacle_password)
        self._client = httpx.Client(base_url=_BASE, auth=self._auth, timeout=15.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        sport_id = _SPORT_ID[sport]
        # 1) Pull fixtures, 2) pull odds, 3) join on event id.
        fixtures = self._client.get(
            "/v1/fixtures", params={"sportId": sport_id, "isLive": int(live)}
        ).json()
        odds = self._client.get(
            "/v1/odds", params={"sportId": sport_id, "oddsFormat": "Decimal", "isLive": int(live)}
        ).json()

        fixture_by_id = {
            ev["id"]: ev
            for league in fixtures.get("league", [])
            for ev in league.get("events", [])
        }

        now = datetime.now(timezone.utc)
        snapshots: list[MarketSnapshot] = []
        for league in odds.get("leagues", []):
            for ev in league.get("events", []):
                fixture = fixture_by_id.get(ev["id"])
                if not fixture:
                    continue
                moneyline = next(
                    (p["moneyline"] for p in ev.get("periods", []) if p.get("number") == 0 and "moneyline" in p),
                    None,
                )
                if not moneyline:
                    continue
                quotes: list[Quote] = []
                home = fixture.get("home", "Home")
                away = fixture.get("away", "Away")
                if "home" in moneyline:
                    quotes.append(Quote(self.name, home, moneyline["home"], now))
                if "draw" in moneyline:
                    quotes.append(Quote(self.name, "Draw", moneyline["draw"], now))
                if "away" in moneyline:
                    quotes.append(Quote(self.name, away, moneyline["away"], now))
                if not quotes:
                    continue
                snapshots.append(
                    MarketSnapshot(
                        event_id=str(ev["id"]),
                        market_id=f"pin-{ev['id']}",
                        market_type="MATCH_ODDS",
                        sport=sport,
                        status=MarketStatus.LIVE if live else MarketStatus.PREMATCH,
                        start_time=now,
                        quotes=quotes,
                    )
                )
        log.info("pinnacle_fetch", sport=sport.value, markets=len(snapshots), live=live)
        return snapshots
