"""The Odds API integration (https://the-odds-api.com/).

Provides live and prematch market odds for Betfair, Pinnacle, and other sharp bookmakers
across Match Winner (h2h), Over/Under Totals (totals), and Handicap Spreads (spreads).
"""

from __future__ import annotations

import time
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


def _fmt_signed_line(pt: float) -> str:
    if pt == 0:
        return "0"
    return f"{pt:+g}"


# Map our Sport enum to The Odds API sport keys
_SPORT_KEYS = {
    Sport.SOCCER: [
        # Major domestic leagues
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_germany_bundesliga",
        "soccer_italy_serie_a",
        "soccer_france_ligue_one",
        "soccer_portugal_primeira_liga",
        "soccer_netherlands_eredivisie",
        # European cups
        "soccer_uefa_champs_league",
        "soccer_uefa_europa_league",
        "soccer_uefa_europa_conference_league",
        # International competitions
        "soccer_fifa_world_cup",
        "soccer_uefa_nations_league",
        "soccer_uefa_euro_qualification",
        "soccer_uefa_european_championship",
        "soccer_conmebol_copa_america",
        # More domestic
        "soccer_turkey_super_league",
        "soccer_greece_super_league",
        "soccer_denmark_superliga",
    ],
    Sport.TENNIS: [
        "tennis_atp_wimbledon",
        "tennis_wta_wimbledon",
        "tennis_atp_us_open",
        "tennis_wta_us_open",
        "tennis_atp_french_open",
        "tennis_wta_french_open",
        "tennis_atp_australian_open",
        "tennis_wta_australian_open",
    ],
    Sport.BASKETBALL: [
        "basketball_nba",
        "basketball_euroleague",
        "basketball_ncaab",
        "basketball_nba_preseason",
    ],
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
    Sport.TABLE_TENNIS: [],
}

_ODDS_CACHE: dict[str, tuple[float, list[dict], str | None, str | None]] = {}
_ODDS_CACHE_TTL = 180.0  # 3 minutes cache across sources

_ACTIVE_SPORTS_CACHE: tuple[float, set[str]] | None = None
_ACTIVE_SPORTS_TTL = 1800.0  # 30 minutes

# Short circuit when quota hits 0 so we don't spam 40+ requests per scan cycle
_QUOTA_EXHAUSTED_UNTIL: tuple[str, float] | None = None


def _get_active_sport_keys(client: httpx.Client, api_key: str) -> set[str]:
    """Query /v4/sports/ (FREE, 0 quota credits) to get the list of currently active leagues."""
    global _ACTIVE_SPORTS_CACHE, _QUOTA_EXHAUSTED_UNTIL
    now = time.time()
    if _ACTIVE_SPORTS_CACHE and (now - _ACTIVE_SPORTS_CACHE[0]) < _ACTIVE_SPORTS_TTL:
        return _ACTIVE_SPORTS_CACHE[1]

    try:
        res = client.get("/sports/", params={"apiKey": api_key})
        rem = res.headers.get("x-requests-remaining")
        if rem is not None and str(rem).isdigit() and int(rem) <= 0:
            _QUOTA_EXHAUSTED_UNTIL = (api_key, now + 300.0)
            log.error("the_odds_api_quota_exhausted", requests_remaining=0)
            return set()
        if res.status_code == 200:
            data = res.json()
            active_keys = {
                item.get("key", "")
                for item in data
                if item.get("active") and not item.get("has_outrights")
            }
            active_keys.discard("")
            _ACTIVE_SPORTS_CACHE = (now, active_keys)
            log.info("the_odds_api_active_sports_updated", count=len(active_keys))
            return active_keys
        else:
            log.warning("the_odds_api_sports_check_failed", status=res.status_code)
    except Exception as exc:
        log.warning("the_odds_api_sports_check_error", error=str(exc))

    if _ACTIVE_SPORTS_CACHE:
        return _ACTIVE_SPORTS_CACHE[1]
    return set()


class TheOddsAPISource:
    def __init__(self, target_bookmaker: str = "betfair_ex_uk", name: str | None = None) -> None:
        self._settings = get_settings()
        self.target_bookmaker = target_bookmaker
        self.name = name or ("betfair" if "betfair" in target_bookmaker else target_bookmaker)
        self._client = httpx.Client(base_url=_BASE, timeout=15.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        global _QUOTA_EXHAUSTED_UNTIL
        self._settings = get_settings()
        api_key = self._settings.the_odds_api_key
        if not api_key:
            log.warning("the_odds_api_key_missing", source=self.name)
            return []

        if _QUOTA_EXHAUSTED_UNTIL and _QUOTA_EXHAUSTED_UNTIL[0] == api_key and time.time() < _QUOTA_EXHAUSTED_UNTIL[1]:
            log.warning("the_odds_api_skipped_quota_exhausted", source=self.name, sport=sport.value)
            return []

        sport_keys = _SPORT_KEYS.get(sport, ["upcoming"])
        if not sport_keys:
            return []

        active_keys = _get_active_sport_keys(self._client, api_key)
        if _QUOTA_EXHAUSTED_UNTIL and _QUOTA_EXHAUSTED_UNTIL[0] == api_key and time.time() < _QUOTA_EXHAUSTED_UNTIL[1]:
            return []
        if active_keys:
            sport_keys = [k for k in sport_keys if k in active_keys]
            if not sport_keys:
                return []

        snapshots: list[MarketSnapshot] = []
        now = datetime.now(UTC)
        events_seen = 0
        events_without_target = 0
        books_available: set[str] = set()

        configured_markets = [
            m.strip().lower()
            for m in (self._settings.odds_api_markets or "h2h").split(",")
            if m.strip()
        ]
        if not configured_markets:
            configured_markets = ["h2h"]

        api_market_list = list(dict.fromkeys(configured_markets))
        if "h2h" in api_market_list and self._settings.odds_api_fetch_lay and "h2h_lay" not in api_market_list:
            api_market_list.append("h2h_lay")
        markets_param = ",".join(api_market_list)

        for sport_key in sport_keys:
            cache_key = f"{sport_key}:eu,uk:{markets_param}"
            cached = _ODDS_CACHE.get(cache_key)
            if cached and (time.time() - cached[0]) < _ODDS_CACHE_TTL:
                events = cached[1]
                remaining = cached[2]
                used = cached[3]
            else:
                try:
                    res = self._client.get(
                        f"/sports/{sport_key}/odds/",
                        params={
                            "apiKey": api_key,
                            "regions": "eu,uk",
                            "markets": markets_param,
                            "oddsFormat": "decimal",
                        },
                    )
                    remaining = res.headers.get("x-requests-remaining")
                    used = res.headers.get("x-requests-used")
                    if res.status_code in (401, 429):
                        _QUOTA_EXHAUSTED_UNTIL = (api_key, time.time() + 300.0)
                        log.error(
                            "the_odds_api_quota_or_auth_error",
                            sport_key=sport_key,
                            status=res.status_code,
                            remaining=remaining,
                            body=res.text[:300],
                        )
                        break
                    if res.status_code != 200:
                        log.error(
                            "the_odds_api_error",
                            sport_key=sport_key,
                            status=res.status_code,
                            body=res.text[:500],
                        )
                        continue

                    events = res.json()
                    _ODDS_CACHE[cache_key] = (time.time(), events, remaining, used)
                except Exception as exc:
                    log.error("the_odds_api_request_failed", sport_key=sport_key, error=str(exc))
                    continue

            if remaining is not None:
                try:
                    rem_int = int(remaining)
                    if rem_int <= 0:
                        _QUOTA_EXHAUSTED_UNTIL = (api_key, time.time() + 300.0)
                    elif rem_int < 100:
                        log.warning(
                            "the_odds_api_quota_low",
                            requests_remaining=rem_int,
                            used=used,
                        )
                except ValueError:
                    pass

            for ev in events:
                event_id = ev.get("id", "")
                home_team = ev.get("home_team", "Home")
                away_team = ev.get("away_team", "Away")
                event_name = f"{home_team} vs {away_team}"
                start_time = _parse_commence_time(ev.get("commence_time")) or now

                is_in_play = (start_time <= now) and ((now - start_time).total_seconds() < 4 * 3600)
                if live and not is_in_play:
                    continue
                if not live and is_in_play:
                    continue

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

                bm_markets = target_bm.get("markets", [])
                mkt_status = MarketStatus.LIVE if is_in_play else MarketStatus.PREMATCH

                # 1. H2H / Match Winner (1X2 or 2-way Moneyline)
                if "h2h" in configured_markets:
                    h2h_market = next((m for m in bm_markets if m.get("key") == "h2h"), None)
                    if h2h_market:
                        lay_market = next((m for m in bm_markets if m.get("key") == "h2h_lay"), None)
                        lay_by_name: dict[str, float] = {}
                        if lay_market:
                            for outcome in lay_market.get("outcomes", []):
                                lname, lprice = outcome.get("name"), outcome.get("price")
                                if lname and lprice:
                                    try:
                                        lay_by_name[lname] = float(lprice)
                                    except (TypeError, ValueError):
                                        continue

                        quotes: list[Quote] = []
                        for outcome in h2h_market.get("outcomes", []):
                            name = outcome.get("name")
                            price = outcome.get("price")
                            if not name or not price:
                                continue
                            back = float(price)
                            lay = lay_by_name.get(name)
                            if lay is not None and (lay <= 1.0 or lay < back):
                                lay = None
                            quotes.append(
                                Quote(
                                    source=self.name,
                                    selection=name,
                                    decimal_odds=back,
                                    lay_odds=lay,
                                    back_liquidity=None,
                                    lay_liquidity=None,
                                    captured_at=now,
                                )
                            )
                        if len(quotes) >= 2:
                            snapshots.append(
                                MarketSnapshot(
                                    event_id=event_id,
                                    market_id=f"oddsapi-{event_id}-h2h",
                                    market_type="MATCH_ODDS",
                                    sport=sport,
                                    status=mkt_status,
                                    start_time=start_time,
                                    total_matched=None,
                                    quotes=quotes,
                                    is_suspended=False,
                                    settlement_rule=SettlementRule.REGULATION_TIME,
                                    event_name=event_name,
                                )
                            )

                # 2. Totals (Over / Under goals or points)
                if "totals" in configured_markets:
                    totals_market = next((m for m in bm_markets if m.get("key") == "totals"), None)
                    if totals_market:
                        by_point: dict[float, list[Quote]] = {}
                        for outcome in totals_market.get("outcomes", []):
                            oname = outcome.get("name")
                            oprice = outcome.get("price")
                            opoint = outcome.get("point")
                            if not oname or oprice is None or opoint is None:
                                continue
                            try:
                                pt = float(opoint)
                                back = float(oprice)
                            except (TypeError, ValueError):
                                continue
                            if back <= 1.0:
                                continue
                            sel_label = f"{oname.strip()} {pt:g}"
                            by_point.setdefault(pt, []).append(
                                Quote(
                                    source=self.name,
                                    selection=sel_label,
                                    decimal_odds=back,
                                    captured_at=now,
                                )
                            )
                        for pt, t_quotes in by_point.items():
                            if len(t_quotes) >= 2:
                                snapshots.append(
                                    MarketSnapshot(
                                        event_id=event_id,
                                        market_id=f"oddsapi-{event_id}-totals-{pt:g}",
                                        market_type=f"TOTALS_{pt:g}",
                                        sport=sport,
                                        status=mkt_status,
                                        start_time=start_time,
                                        total_matched=None,
                                        quotes=t_quotes,
                                        is_suspended=False,
                                        settlement_rule=SettlementRule.REGULATION_TIME,
                                        event_name=event_name,
                                    )
                                )

                # 3. Spreads / Handicap
                if "spreads" in configured_markets:
                    spreads_market = next((m for m in bm_markets if m.get("key") == "spreads"), None)
                    if spreads_market:
                        by_abs_pt: dict[float, list[tuple[str, float, float]]] = {}
                        for outcome in spreads_market.get("outcomes", []):
                            oname = outcome.get("name")
                            oprice = outcome.get("price")
                            opoint = outcome.get("point")
                            if not oname or oprice is None or opoint is None:
                                continue
                            try:
                                pt = float(opoint)
                                back = float(oprice)
                            except (TypeError, ValueError):
                                continue
                            if back <= 1.0:
                                continue
                            by_abs_pt.setdefault(round(abs(pt), 2), []).append((oname.strip(), back, pt))

                        for abs_pt, items in by_abs_pt.items():
                            if len(items) < 2:
                                continue
                            home_entry = next(
                                (it for it in items if it[0].lower() == home_team.lower()),
                                items[0],
                            )
                            home_line_str = _fmt_signed_line(home_entry[2])
                            s_quotes = [
                                Quote(
                                    source=self.name,
                                    selection=f"{t_name} ({_fmt_signed_line(t_pt)})",
                                    decimal_odds=t_price,
                                    captured_at=now,
                                )
                                for t_name, t_price, t_pt in items
                            ]
                            snapshots.append(
                                MarketSnapshot(
                                    event_id=event_id,
                                    market_id=f"oddsapi-{event_id}-spreads-{home_line_str}",
                                    market_type=f"HANDICAP_{home_line_str}",
                                    sport=sport,
                                    status=mkt_status,
                                    start_time=start_time,
                                    total_matched=None,
                                    quotes=s_quotes,
                                    is_suspended=False,
                                    settlement_rule=SettlementRule.REGULATION_TIME,
                                    event_name=event_name,
                                )
                            )

        log.info(
            "the_odds_api_fetch",
            source=self.name,
            bookmaker_key=self.target_bookmaker,
            sport=sport.value,
            live=live,
            events_seen=events_seen,
            markets=len(snapshots),
            events_without_target_bookmaker=events_without_target,
        )
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
