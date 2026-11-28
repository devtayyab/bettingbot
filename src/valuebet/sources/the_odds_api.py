"""The Odds API integration (https://the-odds-api.com/).

Provides live and prematch market odds for Betfair, Pinnacle, and other sharp bookmakers
across Match Winner (h2h), Over/Under Totals (totals), and Handicap Spreads (spreads).
"""

from __future__ import annotations

from difflib import SequenceMatcher
import time
from datetime import UTC, datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import active_odds_provider, get_settings
from ..core.models import MarketSnapshot, MarketStatus, Quote, SettlementRule, Sport
from ..logging import get_logger

log = get_logger("source.the_odds_api")

_THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_PROPLINE_BASE = "https://api.prop-line.com/v1"


def _to_decimal_odds(raw_price: Any) -> float | None:
    """Normalize decimal OR American odds (+135 / -150) into decimal odds > 1.0."""
    if raw_price is None:
        return None
    try:
        val = float(raw_price)
    except (TypeError, ValueError):
        return None
    if val <= -100.0:
        dec = 1.0 + (100.0 / abs(val))
    elif val >= 100.0:
        dec = 1.0 + (val / 100.0)
    elif 1.0 < val < 100.0:
        dec = val
    else:
        return None
    return round(dec, 4) if dec > 1.0 else None


def _canonical_team_name(name: str, home_team: str, away_team: str) -> str:
    """Resolve bookmaker-specific team abbreviations (e.g. 'Man Utd' vs 'Manchester United')."""
    raw = name.strip()
    low = raw.lower()
    if low in ("draw", "tie", "x"):
        return "Draw"
    if not home_team or not away_team:
        return raw
    if low == home_team.lower():
        return home_team
    if low == away_team.lower():
        return away_team

    def _score(cand: str, ref: str) -> float:
        r_ratio = SequenceMatcher(None, cand.lower(), ref.lower()).ratio()
        c_toks = [t for t in cand.lower().replace("-", " ").split() if len(t) >= 2]
        r_toks = [t for t in ref.lower().replace("-", " ").split() if len(t) >= 2]
        prefix_hits = sum(
            1 for ct in c_toks if any(rt.startswith(ct[:3]) or ct.startswith(rt[:3]) for rt in r_toks)
        )
        tok_bonus = (prefix_hits / max(1, len(c_toks))) * 0.45
        return r_ratio + tok_bonus

    h_score = _score(raw, home_team)
    a_score = _score(raw, away_team)
    best = max(h_score, a_score)
    if best >= 0.72 and abs(h_score - a_score) >= 0.12:
        return home_team if h_score > a_score else away_team
    return raw


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

_PROPLINE_SPORT_KEYS = {
    Sport.SOCCER: [
        "soccer_epl",
        "soccer_la_liga",
        "soccer_bundesliga",
        "soccer_serie_a",
        "soccer_ligue_1",
        "soccer_uefa_champions_league",
        "soccer_uefa_europa_league",
        "soccer_greek_super_league",
        "soccer_eredivisie",
        "soccer_primeira_liga",
    ],
    Sport.TENNIS: ["tennis"],
    Sport.BASKETBALL: [
        "basketball_nba",
        "basketball_euroleague",
        "basketball_greece_basket_league",
    ],
    Sport.AMERICAN_FOOTBALL: ["football_nfl", "football_ncaaf"],
    Sport.BASEBALL: ["baseball_mlb"],
    Sport.ICE_HOCKEY: ["hockey_nhl"],
    Sport.CRICKET: ["cricket"],
    Sport.RUGBY_LEAGUE: ["rugby_league"],
    Sport.RUGBY_UNION: ["rugby_union"],
    Sport.GOLF: ["golf"],
    Sport.MMA: ["mma_ufc"],
    Sport.BOXING: ["boxing"],
    Sport.VOLLEYBALL: ["volleyball"],
    Sport.HANDBALL: [],
    Sport.DARTS: ["darts"],
    Sport.ESPORTS: ["esports"],
    Sport.TABLE_TENNIS: ["table_tennis"],
}

_PROPLINE_LEAGUE_ALIASES = {
    "soccer_spain_la_liga": "soccer_la_liga",
    "soccer_germany_bundesliga": "soccer_bundesliga",
    "soccer_italy_serie_a": "soccer_serie_a",
    "soccer_france_ligue_one": "soccer_ligue_1",
    "soccer_uefa_champs_league": "soccer_uefa_champions_league",
    "soccer_portugal_primeira_liga": "soccer_primeira_liga",
    "soccer_netherlands_eredivisie": "soccer_eredivisie",
    "soccer_greece_super_league": "soccer_greek_super_league",
    "soccer_turkey_super_league": "soccer_turkey_super_lig",
}

_ODDS_CACHE: dict[str, tuple[float, list[dict], str | None, str | None]] = {}
_ODDS_CACHE_TTL = 180.0  # 3 minutes cache for prematch across sources
_LIVE_CACHE_TTL = 45.0   # keep live quotes strictly under max_live_latency_seconds (60s)

_ACTIVE_SPORTS_CACHE: tuple[str, float, set[str]] | None = None
_ACTIVE_KEYS_CACHE: dict = {}  # Compatibility alias
_ACTIVE_SPORTS_TTL = 1800.0        # 30 minutes (The Odds API /sports/ is free)
_PROPLINE_SPORTS_TTL = 21600.0     # 6 hours (PropLine counts /v1/sports against daily quota)

# Short circuit when quota hits 0 / daily reserve so we don't spam requests
_QUOTA_EXHAUSTED_UNTIL: tuple[str, float] | None = None
# Daily quota state for 24h pacing on PropLine: (api_key, remaining, limit, reset_ts)
_DAILY_QUOTA_STATE: tuple[str, int, int, float] | None = None
# Track last network fetch time per cache_key to pace PropLine across 24 hours
_LAST_NETWORK_FETCH: dict[str, float] = {}


def _extract_quota_headers(headers: Any) -> tuple[str | None, str | None, float | None]:
    rem = headers.get("x-daily-remaining") or headers.get("x-requests-remaining")
    used = headers.get("x-daily-used") or headers.get("x-requests-used")
    reset_raw = headers.get("x-daily-reset")
    reset_ts: float | None = None
    if reset_raw is not None:
        try:
            reset_ts = float(reset_raw)
        except (TypeError, ValueError):
            reset_ts = None
    return rem, used, reset_ts


def _update_daily_pacing(
    api_key: str,
    headers: Any,
    *,
    is_propline: bool,
    reserve: int = 25,
) -> tuple[str | None, str | None]:
    global _QUOTA_EXHAUSTED_UNTIL, _DAILY_QUOTA_STATE
    rem, used, reset_ts = _extract_quota_headers(headers)
    if rem is None:
        return rem, used
    try:
        rem_int = int(rem)
    except (TypeError, ValueError):
        return rem, used

    limit_raw = headers.get("x-daily-limit")
    try:
        daily_limit = int(limit_raw) if limit_raw is not None else 1000
    except (TypeError, ValueError):
        daily_limit = 1000

    now = time.time()
    eff_reset = reset_ts if reset_ts and reset_ts > now else (now + 3600.0)
    if is_propline:
        _DAILY_QUOTA_STATE = (api_key, rem_int, daily_limit, eff_reset)
        if rem_int <= reserve:
            pause_until = min(eff_reset, now + 1800.0)
            _QUOTA_EXHAUSTED_UNTIL = (api_key, pause_until)
            log.warning(
                "propline_daily_reserve_reached",
                remaining=rem_int,
                reserve_for_scores=reserve,
                resets_at=eff_reset,
            )
    else:
        if rem_int <= 0:
            _QUOTA_EXHAUSTED_UNTIL = (api_key, now + 300.0)
    return rem, used


def _propline_pacing_wait(api_key: str, cache_key: str, num_leagues: int) -> bool:
    """Return True if we should defer fetching `cache_key` to spread daily quota across 24h."""
    if not _DAILY_QUOTA_STATE or _DAILY_QUOTA_STATE[0] != api_key:
        return False
    _, rem_int, daily_limit, reset_ts = _DAILY_QUOTA_STATE
    now = time.time()
    seconds_left = max(300.0, reset_ts - now)
    reserve = 25
    avail = max(0, rem_int - reserve)
    if avail <= 0:
        return True
    last_ts = _LAST_NETWORK_FETCH.get(cache_key)
    if last_ts is None:
        return False

    # Target minimum interval between full refreshes of each league so quota lasts until reset.
    leagues = max(1, num_leagues)
    cycles_possible = max(1.0, avail / leagues)
    desired_interval = seconds_left / cycles_possible
    # Never throttle slower than 1 hour if we still have healthy headroom, and never
    # throttle below 10 minutes when remaining quota is getting tight (< 50% left).
    if desired_interval > 1100.0:
        min_gap = min(3600.0, desired_interval)
        if (now - last_ts) < min_gap:
            log.debug(
                "propline_pacing_deferred",
                cache_key=cache_key,
                remaining=rem_int,
                min_gap_sec=round(min_gap, 0),
                elapsed_sec=round(now - last_ts, 0),
            )
            return True
    return False


def _get_active_sport_keys(
    client: httpx.Client,
    api_key: str,
    *,
    is_propline: bool = False,
) -> set[str]:
    """Query /sports/ to get the list of currently active leagues."""
    global _ACTIVE_SPORTS_CACHE, _QUOTA_EXHAUSTED_UNTIL
    now = time.time()
    provider_tag = "propline" if is_propline else "the_odds_api"
    ttl = _PROPLINE_SPORTS_TTL if is_propline else _ACTIVE_SPORTS_TTL
    if (
        _ACTIVE_SPORTS_CACHE
        and _ACTIVE_SPORTS_CACHE[0] == provider_tag
        and (now - _ACTIVE_SPORTS_CACHE[1]) < ttl
    ):
        return _ACTIVE_SPORTS_CACHE[2]

    try:
        path = "/sports" if is_propline else "/sports/"
        res = client.get(path, params={"apiKey": api_key})
        rem, _used = _update_daily_pacing(api_key, res.headers, is_propline=is_propline)
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
            _ACTIVE_SPORTS_CACHE = (provider_tag, now, active_keys)
            log.info("the_odds_api_active_sports_updated", provider=provider_tag, count=len(active_keys))
            return active_keys
        else:
            log.warning("the_odds_api_sports_check_failed", status=res.status_code)
    except Exception as exc:
        log.warning("the_odds_api_sports_check_error", error=str(exc))

    if _ACTIVE_SPORTS_CACHE and _ACTIVE_SPORTS_CACHE[0] == provider_tag:
        return _ACTIVE_SPORTS_CACHE[2]
    return set()


class TheOddsAPISource:
    def __init__(self, target_bookmaker: str = "betfair_ex_uk", name: str | None = None) -> None:
        self._settings = get_settings()
        self.target_bookmaker = target_bookmaker
        self.name = name or ("betfair" if "betfair" in target_bookmaker else target_bookmaker)
        self._provider, _ = active_odds_provider(self._settings)
        base_url = _PROPLINE_BASE if self._provider == "propline" else _THE_ODDS_API_BASE
        self._client = httpx.Client(base_url=base_url, timeout=15.0)

    def _ensure_client(self, provider: str) -> httpx.Client:
        want_base = _PROPLINE_BASE if provider == "propline" else _THE_ODDS_API_BASE
        if str(self._client.base_url).rstrip("/") != want_base:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = httpx.Client(base_url=want_base, timeout=15.0)
        self._provider = provider
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        global _QUOTA_EXHAUSTED_UNTIL
        self._settings = get_settings()
        provider, api_key = active_odds_provider(self._settings)
        is_propline = provider == "propline"
        client = self._ensure_client(provider)

        if not api_key:
            log.warning("the_odds_api_key_missing", source=self.name)
            return []

        if _QUOTA_EXHAUSTED_UNTIL and _QUOTA_EXHAUSTED_UNTIL[0] == api_key and time.time() < _QUOTA_EXHAUSTED_UNTIL[1]:
            log.warning("the_odds_api_skipped_quota_exhausted", source=self.name, sport=sport.value, provider=provider)
            return []

        key_map = _PROPLINE_SPORT_KEYS if is_propline else _SPORT_KEYS
        sport_keys = list(key_map.get(sport, ["upcoming"]))
        if not sport_keys:
            return []

        # Optimization: filter sport_keys by explicit leagues if configured
        if getattr(self._settings, "odds_api_leagues", None):
            raw_allowed = [
                item.strip().lower()
                for item in self._settings.odds_api_leagues.split(",")
                if item.strip()
            ]
            if is_propline:
                allowed_leagues = {
                    _PROPLINE_LEAGUE_ALIASES.get(item, item) for item in raw_allowed
                }
            else:
                allowed_leagues = set(raw_allowed)
            if allowed_leagues:
                sport_keys = [k for k in sport_keys if k.lower() in allowed_leagues]
                if not sport_keys:
                    return []

        active_keys = _get_active_sport_keys(client, api_key, is_propline=is_propline)
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
        if (
            not is_propline
            and "h2h" in api_market_list
            and self._settings.odds_api_fetch_lay
            and "h2h_lay" not in api_market_list
        ):
            api_market_list.append("h2h_lay")
        markets_param = ",".join(api_market_list)
        regions_param = (getattr(self._settings, "odds_api_regions", None) or "eu").strip()
        propline_books = "pinnacle,stoiximan,betfair_ex"
        cache_ttl = _LIVE_CACHE_TTL if live else _ODDS_CACHE_TTL

        for sport_key in sport_keys:
            cache_key = (
                f"pl:{sport_key}:{markets_param}:{propline_books}"
                if is_propline
                else f"{sport_key}:{regions_param}:{markets_param}"
            )
            cached = _ODDS_CACHE.get(cache_key)
            fetched_ts = time.time()
            if cached and (time.time() - cached[0]) < cache_ttl:
                fetched_ts = cached[0]
                events = cached[1]
                remaining = cached[2]
                used = cached[3]
            elif is_propline and _propline_pacing_wait(api_key, cache_key, len(sport_keys) * max(1, len(self._settings.active_sports.split(",")))):
                # Quota pacing deferred a fresh network hit: reuse latest cached events if
                # not stale (< 4.5 min prematch) rather than burning daily budget in 1h.
                if cached and not live and (time.time() - cached[0]) < 270.0:
                    fetched_ts = cached[0]
                    events = cached[1]
                    remaining = cached[2]
                    used = cached[3]
                else:
                    continue
            else:
                try:
                    if is_propline:
                        res = client.get(
                            f"/sports/{sport_key}/odds",
                            params={
                                "apiKey": api_key,
                                "markets": markets_param,
                                "bookmakers": propline_books,
                            },
                        )
                    else:
                        res = client.get(
                            f"/sports/{sport_key}/odds/",
                            params={
                                "apiKey": api_key,
                                "regions": regions_param,
                                "markets": markets_param,
                                "oddsFormat": "decimal",
                            },
                        )
                    if is_propline:
                        _LAST_NETWORK_FETCH[cache_key] = time.time()
                    remaining, used = _update_daily_pacing(
                        api_key, res.headers, is_propline=is_propline
                    )
                    if not is_propline:
                        pause_thresh = getattr(self._settings, "odds_api_quota_pause_threshold", 50)
                        if remaining is not None and str(remaining).isdigit() and int(remaining) <= pause_thresh:
                            _QUOTA_EXHAUSTED_UNTIL = (api_key, time.time() + 600.0)
                            log.warning("the_odds_api_quota_low_pausing", remaining=int(remaining), threshold=pause_thresh)
                    if res.status_code in (401, 429):
                        _QUOTA_EXHAUSTED_UNTIL = (api_key, time.time() + 300.0)
                        log.error(
                            "the_odds_api_quota_or_auth_error",
                            provider=provider,
                            sport_key=sport_key,
                            status=res.status_code,
                            remaining=remaining,
                            body=res.text[:300],
                        )
                        break
                    if res.status_code != 200:
                        log.error(
                            "the_odds_api_error",
                            provider=provider,
                            sport_key=sport_key,
                            status=res.status_code,
                            body=res.text[:500],
                        )
                        continue

                    events = res.json()
                    _ODDS_CACHE[cache_key] = (time.time(), events, remaining, used)
                except Exception as exc:
                    log.error("the_odds_api_request_failed", provider=provider, sport_key=sport_key, error=str(exc))
                    continue

            if remaining is not None:
                try:
                    rem_int = int(remaining)
                    if rem_int <= 0:
                        _QUOTA_EXHAUSTED_UNTIL = (api_key, time.time() + 300.0)
                    elif rem_int < 100:
                        log.warning(
                            "the_odds_api_quota_low",
                            provider=provider,
                            requests_remaining=rem_int,
                            used=used,
                        )
                except ValueError:
                    pass

            # Stamp quotes with when this payload was actually fetched. Stamping a
            # 3-minute-old cached payload as "now" made the stale-odds check a no-op.
            quote_ts = datetime.fromtimestamp(fetched_ts, UTC)
            for ev in events:
                event_id = str(ev.get("id", ""))
                home_team = ev.get("home_team", "Home")
                away_team = ev.get("away_team", "Away")
                event_name = f"{home_team} vs {away_team}"
                start_time = _parse_commence_time(ev.get("commence_time")) or now

                if "live" in ev and ev.get("live") is not None:
                    is_in_play = bool(ev.get("live"))
                else:
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
                    (
                        bm
                        for bm in bookmakers
                        if bm.get("key")
                        in (
                            [self.target_bookmaker, "betfair_ex", "betfair_ex_uk", "betfair_ex_eu", "betfair"]
                            if "betfair" in self.target_bookmaker
                            else (
                                [self.target_bookmaker, "stoiximan", "betano_uk", "betano"]
                                if ("stoiximan" in self.target_bookmaker or "betano" in self.target_bookmaker)
                                else [self.target_bookmaker]
                            )
                        )
                    ),
                    None,
                )
                if not target_bm:
                    events_without_target += 1
                    continue

                bm_markets = target_bm.get("markets", [])
                mkt_status = MarketStatus.LIVE if is_in_play else MarketStatus.PREMATCH
                full_match_markets = [
                    m
                    for m in bm_markets
                    if not m.get("suspended_at") and not m.get("period") and not m.get("team")
                ]

                # 1. H2H / Match Winner (1X2 or 2-way Moneyline)
                if "h2h" in configured_markets:
                    h2h_market = next(
                        (m for m in full_match_markets if m.get("key") == "h2h"),
                        None,
                    )
                    if h2h_market:
                        lay_market = next(
                            (m for m in full_match_markets if m.get("key") == "h2h_lay"),
                            None,
                        )
                        lay_by_name: dict[str, float] = {}
                        if lay_market:
                            for outcome in lay_market.get("outcomes", []):
                                lname, lprice = outcome.get("name"), outcome.get("price")
                                dec_lay = _to_decimal_odds(lprice)
                                if lname and dec_lay is not None:
                                    lay_by_name[_canonical_team_name(lname, home_team, away_team)] = dec_lay

                        quotes: list[Quote] = []
                        min_ex_liq = getattr(self._settings, "min_liquidity", 0.0) if ("betfair" in self.target_bookmaker and is_propline) else 0.0
                        for outcome in h2h_market.get("outcomes", []):
                            raw_name = outcome.get("name")
                            price = outcome.get("price")
                            back = _to_decimal_odds(price)
                            if not raw_name or back is None:
                                continue
                            liq_raw = outcome.get("liquidity")
                            liq_val = float(liq_raw) if liq_raw is not None else None
                            if min_ex_liq > 0 and liq_val is not None and liq_val < min_ex_liq:
                                continue
                            name = _canonical_team_name(raw_name, home_team, away_team) if is_propline else raw_name
                            lay = lay_by_name.get(name)
                            if lay is not None and (lay <= 1.0 or lay < back):
                                lay = None
                            quotes.append(
                                Quote(
                                    source=self.name,
                                    selection=name,
                                    decimal_odds=back,
                                    lay_odds=lay,
                                    back_liquidity=liq_val if "betfair" in self.target_bookmaker else None,
                                    lay_liquidity=None,
                                    captured_at=quote_ts,
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

                # 2. Totals (Over / Under goals or points — full match only, main before alternate)
                if "totals" in configured_markets:
                    totals_markets = sorted(
                        [m for m in full_match_markets if m.get("key") == "totals"],
                        key=lambda m: 0 if m.get("line_type") == "main" else 1,
                    )
                    if totals_markets:
                        by_point: dict[float, list[Quote]] = {}
                        seen_tot_sel: set[tuple[float, str]] = set()
                        min_ex_liq = getattr(self._settings, "min_liquidity", 0.0) if ("betfair" in self.target_bookmaker and is_propline) else 0.0
                        for totals_market in totals_markets:
                            for outcome in totals_market.get("outcomes", []):
                                oname = outcome.get("name")
                                oprice = outcome.get("price")
                                opoint = outcome.get("point")
                                back = _to_decimal_odds(oprice)
                                if not oname or back is None or opoint is None:
                                    continue
                                liq_raw = outcome.get("liquidity")
                                liq_val = float(liq_raw) if liq_raw is not None else None
                                if min_ex_liq > 0 and liq_val is not None and liq_val < min_ex_liq:
                                    continue
                                try:
                                    pt = float(opoint)
                                except (TypeError, ValueError):
                                    continue
                                side_word = oname.strip()
                                if side_word.lower() not in ("over", "under"):
                                    continue
                                sel_label = f"{side_word.title()} {pt:g}"
                                if (pt, sel_label) in seen_tot_sel:
                                    continue
                                seen_tot_sel.add((pt, sel_label))
                                by_point.setdefault(pt, []).append(
                                    Quote(
                                        source=self.name,
                                        selection=sel_label,
                                        decimal_odds=back,
                                        back_liquidity=liq_val if "betfair" in self.target_bookmaker else None,
                                        captured_at=quote_ts,
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

                # 3. Spreads / Handicap (full match only, main before alternate)
                if "spreads" in configured_markets:
                    spreads_markets = sorted(
                        [m for m in full_match_markets if m.get("key") == "spreads"],
                        key=lambda m: 0 if m.get("line_type") == "main" else 1,
                    )
                    if spreads_markets:
                        by_abs_pt: dict[float, list[tuple[str, float, float, float | None]]] = {}
                        seen_spread_sel: set[tuple[float, str, float]] = set()
                        min_ex_liq = getattr(self._settings, "min_liquidity", 0.0) if ("betfair" in self.target_bookmaker and is_propline) else 0.0
                        for spreads_market in spreads_markets:
                            for outcome in spreads_market.get("outcomes", []):
                                oname = outcome.get("name")
                                oprice = outcome.get("price")
                                opoint = outcome.get("point")
                                back = _to_decimal_odds(oprice)
                                if not oname or back is None or opoint is None:
                                    continue
                                liq_raw = outcome.get("liquidity")
                                liq_val = float(liq_raw) if liq_raw is not None else None
                                if min_ex_liq > 0 and liq_val is not None and liq_val < min_ex_liq:
                                    continue
                                try:
                                    pt = float(opoint)
                                except (TypeError, ValueError):
                                    continue
                                team_label = (
                                    _canonical_team_name(oname, home_team, away_team)
                                    if is_propline
                                    else oname.strip()
                                )
                                key_sig = (round(abs(pt), 2), team_label, pt)
                                if key_sig in seen_spread_sel:
                                    continue
                                seen_spread_sel.add(key_sig)
                                by_abs_pt.setdefault(round(abs(pt), 2), []).append((team_label, back, pt, liq_val))

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
                                    back_liquidity=t_liq if "betfair" in self.target_bookmaker else None,
                                    captured_at=quote_ts,
                                )
                                for t_name, t_price, t_pt, t_liq in items
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
