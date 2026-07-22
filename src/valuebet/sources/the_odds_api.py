"""The Odds API integration (https://the-odds-api.com/).

Provides live and prematch market odds for Betfair, Pinnacle, and other sharp bookmakers
without requiring direct exchange API credentials or certs.
"""

from __future__ import annotations

from datetime import datetime, timezone
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..core.models import MarketSnapshot, MarketStatus, Quote, SettlementRule, Sport
from ..logging import get_logger

log = get_logger("source.the_odds_api")

_BASE = "https://api.the-odds-api.com/v4"

# Map our Sport enum to The Odds API sport keys
_SPORT_KEYS = {
    Sport.SOCCER: ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_uefa_champs_league"],
    Sport.TENNIS: ["tennis_atp_wimbledon", "tennis_wta_wimbledon"],
    Sport.BASKETBALL: ["basketball_nba", "basketball_euroleague"],
}


class TheOddsAPISource:
    def __init__(self, target_bookmaker: str = "betfair_ex_uk", name: str | None = None) -> None:
        self._settings = get_settings()
        self.target_bookmaker = target_bookmaker
        self.name = name or ("betfair" if "betfair" in target_bookmaker else target_bookmaker)
        self._client = httpx.Client(base_url=_BASE, timeout=15.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        api_key = self._settings.the_odds_api_key
        if not api_key:
            log.warning("the_odds_api_key_missing", source=self.name)
            return []

        sport_keys = _SPORT_KEYS.get(sport, ["upcoming"])
        snapshots: list[MarketSnapshot] = []
        now = datetime.now(timezone.utc)

        for sport_key in sport_keys:
            try:
                res = self._client.get(
                    f"/sports/{sport_key}/odds/",
                    params={
                        "apiKey": api_key,
                        "regions": "eu,uk",
                        "markets": "h2h",
                        "oddsFormat": "decimal",
                    },
                )
                if res.status_code != 200:
                    log.error("the_odds_api_error", status=res.status_code, body=res.text)
                    continue

                events = res.json()
                for ev in events:
                    event_id = ev.get("id", "")
                    home_team = ev.get("home_team", "Home")
                    away_team = ev.get("away_team", "Away")
                    
                    # Look for our target bookmaker in the event's bookmakers list
                    bookmakers = ev.get("bookmakers", [])
                    target_bm = next(
                        (bm for bm in bookmakers if bm.get("key") == self.target_bookmaker or self.target_bookmaker in bm.get("key", "")),
                        None,
                    )
                    if not target_bm:
                        continue

                    h2h_market = next(
                        (m for m in target_bm.get("markets", []) if m.get("key") == "h2h"),
                        None,
                    )
                    if not h2h_market:
                        continue

                    quotes: list[Quote] = []
                    for outcome in h2h_market.get("outcomes", []):
                        name = outcome.get("name")
                        price = outcome.get("price")
                        if not name or not price:
                            continue
                        
                        quotes.append(
                            Quote(
                                source=self.name,
                                selection=name,
                                decimal_odds=float(price),
                                lay_odds=None,
                                back_liquidity=5000.0,  # Default liquidity fallback
                                lay_liquidity=5000.0,
                                captured_at=now,
                            )
                        )

                    if not quotes:
                        continue

                    snapshots.append(
                        MarketSnapshot(
                            event_id=event_id,
                            market_id=f"oddsapi-{event_id}",
                            market_type="MATCH_ODDS",
                            sport=sport,
                            status=MarketStatus.LIVE if live else MarketStatus.PREMATCH,
                            start_time=now,
                            total_matched=10000.0,  # Default matched volume fallback
                            quotes=quotes,
                            is_suspended=False,
                            settlement_rule=SettlementRule.REGULATION_TIME,
                        )
                    )
            except Exception as e:
                log.error("the_odds_api_fetch_failed", sport_key=sport_key, error=str(e))

        log.info("the_odds_api_fetch", source=self.name, sport=sport.value, markets=len(snapshots), live=live)
        return snapshots
