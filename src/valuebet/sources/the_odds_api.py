"""The Odds API integration (https://the-odds-api.com/).

Provides live and prematch market odds for Betfair, Pinnacle, and other sharp bookmakers
without requiring direct exchange API credentials or certs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..core.models import MarketSnapshot, MarketStatus, Quote, SettlementRule, Sport
from ..logging import get_logger

log = get_logger("source.the_odds_api")

_BASE = "https://api.the-odds-api.com/v4"


def _parse_commence_time(raw: str | None) -> datetime | None:
    """Parse The Odds API's ISO-8601 commence_time (e.g. '2026-09-04T18:30:00Z')."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None

# Map our Sport enum to The Odds API sport keys
_SPORT_KEYS = {
    Sport.SOCCER: ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_uefa_champs_league"],
    Sport.TENNIS: ["tennis_atp_wimbledon", "tennis_wta_wimbledon", "tennis_atp_us_open", "tennis_wta_us_open"],
    Sport.BASKETBALL: ["basketball_nba", "basketball_euroleague", "basketball_ncaab"],
    Sport.AMERICAN_FOOTBALL: ["americanfootball_nfl", "americanfootball_ncaaf"],
    Sport.BASEBALL: ["baseball_mlb"],
    Sport.ICE_HOCKEY: ["icehockey_nhl"],
    Sport.CRICKET: ["cricket_ipl", "cricket_big_bash"],
    Sport.RUGBY_LEAGUE: ["rugbyleague_nrl"],
    Sport.RUGBY_UNION: ["rugbyunion_six_nations"],
    Sport.GOLF: ["golf_pga_championship"],
    Sport.MMA: ["mma_mixed_martial_arts"],
    Sport.BOXING: ["boxing_boxing"],
    Sport.VOLLEYBALL: ["volleyball_indoor"],
    Sport.HANDBALL: ["handball_champions_league"],
    Sport.DARTS: ["darts_pdc_world_championship"],
    Sport.ESPORTS: ["csgo_esl", "dota2_international"],
    Sport.TABLE_TENNIS: ["table_tennis"],
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
        now = datetime.now(UTC)
        # Diagnostics: distinguish "the API returned nothing" from "our bookmaker
        # key is not in the response", which look identical from the outside.
        events_seen = 0
        events_without_target = 0
        events_without_h2h = 0
        books_available: set[str] = set()

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
                    log.error("the_odds_api_error", sport_key=sport_key,
                              status=res.status_code, body=res.text[:500])
                    continue

                # The API meters usage in response headers; running out is a silent
                # cause of empty scans.
                remaining = res.headers.get("x-requests-remaining")
                if remaining is not None:
                    try:
                        if int(remaining) < 100:
                            log.warning("the_odds_api_quota_low",
                                        requests_remaining=int(remaining),
                                        used=res.headers.get("x-requests-used"))
                    except ValueError:
                        pass

                events = res.json()
                for ev in events:
                    event_id = ev.get("id", "")
                    home_team = ev.get("home_team", "Home")
                    away_team = ev.get("away_team", "Away")
                    start_time = _parse_commence_time(ev.get("commence_time")) or now
                    
                    # Look for our target bookmaker in the event's bookmakers list
                    bookmakers = ev.get("bookmakers", [])
                    events_seen += 1
                    books_available.update(
                        bm.get("key", "") for bm in bookmakers if bm.get("key")
                    )
                    target_bm = next(
                        (bm for bm in bookmakers if bm.get("key") == self.target_bookmaker),
                        None,
                    )
                    if not target_bm:
                        events_without_target += 1
                        continue

                    h2h_market = next(
                        (m for m in target_bm.get("markets", []) if m.get("key") == "h2h"),
                        None,
                    )
                    if not h2h_market:
                        events_without_h2h += 1
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
                                back_liquidity=None,
                                lay_liquidity=None,
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
                            start_time=start_time,
                            # The Odds API exposes no volume or book depth. These
                            # stay None rather than being invented: a hard-coded
                            # 10000/5000 silently satisfied every market-health gate
                            # (min_total_matched, min_liquidity) instead of skipping
                            # a check we have no data for.
                            total_matched=None,
                            quotes=quotes,
                            is_suspended=False,
                            settlement_rule=SettlementRule.REGULATION_TIME,
                        )
                    )
            except Exception as e:
                log.error("the_odds_api_fetch_failed", sport_key=sport_key, error=str(e))

        log.info(
            "the_odds_api_fetch",
            source=self.name,
            bookmaker_key=self.target_bookmaker,
            sport=sport.value,
            live=live,
            events_seen=events_seen,
            markets=len(snapshots),
            events_without_target_bookmaker=events_without_target,
            events_without_h2h=events_without_h2h,
        )
        # A configured key that appears in no event is a config error, not "no
        # value today" — name it, and list what the feed actually offers.
        if events_seen and not snapshots:
            log.warning(
                "bookmaker_key_not_in_feed",
                source=self.name,
                bookmaker_key=self.target_bookmaker,
                sport=sport.value,
                events_seen=events_seen,
                available_bookmakers=sorted(books_available)[:40],
                msg="no market could be built for this bookmaker key; check the key spelling and region",
            )
        return snapshots
