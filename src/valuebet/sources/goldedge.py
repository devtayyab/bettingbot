"""GoldEdge integration (https://www.goldedge.net/).

Signals
  GoldEdge's odds lists render one `a.ev-row` per match. Each price cell carries
  the bookmaker and Pinnacle's no-vig fair price for that outcome in its title
  ("Betano · fair 1.33"). A cell is value when the Betano/Stoiximan price is
  above that fair price: edge = offered / fair - 1. Nothing is de-vigged here —
  the row shows the best price per outcome from DIFFERENT bookmakers, so
  de-vigging those three numbers together does not give a fair probability.
  Because a row only names the best-priced bookmaker, Betano is usually hidden
  behind someone paying more. So for every row whose best price beats fair by
  the minimum edge, the match page is opened and Betano's own price is read
  from its bookmaker table.
  With saved session cookies the members' value-bet dashboard is read as well.

Results
  A finished match's page shows status FINISHED and the final score. Signals keep
  the page URL (`source_url`) so their bets can be settled from it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..core.models import SettlementRule, Sport, ValueSignal
from ..core.odds_math import kelly_stake
from ..db.repository import save_signal
from ..db.session import session_scope
from ..logging import get_logger
from ..placement.session_store import (
    load_cookies_into_context,
    save_cookies_from_context,
)

log = get_logger("sources.goldedge")

_BASE_URL = "https://www.goldedge.net"
_DASHBOARD_URL = f"{_BASE_URL}/valuebets/dashboard"
_LIST_PATHS = ("/", "/odds/football", "/odds/tennis", "/odds/basketball")
# The sport pages only list the day's top ~20 matches. Each also links its main
# competitions; this many of those are scanned per sync for wider coverage.
_MAX_COMPETITION_PAGES = 10
# Runs in the page: competition links such as /odds/football/england/premier-league.
_COMPETITIONS_JS = """() => [...new Set([...document.querySelectorAll('a[href^="/odds/"]')]
  .map(a => a.getAttribute('href'))
  .filter(h => h.split('/').filter(Boolean).length === 4))]"""
# Match pages opened per sync to read Betano's own price (best edges first).
_MAX_EVENT_PAGES = 25
# A bookmaker price older than this on the match page is not trusted.
_MAX_PRICE_AGE_MINUTES = 30.0
# What the most recent scan saw, so "0 value bets" can be told apart from
# "the site served this machine nothing". Returned by POST /goldedge/sync.
LAST_SCAN: dict[str, Any] = {}
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def get_goldedge_cookie_path(custom_path: str | Path | None = None) -> Path:
    if custom_path is not None:
        target = Path(custom_path)
    else:
        settings = get_settings()
        target = Path(getattr(settings, "goldedge_cookie_path", "data/goldedge_cookies.json"))
    if not target.is_absolute():
        target = Path.cwd() / target
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def interactive_login(
    url: str = _BASE_URL,
    cookie_path: str | Path | None = None,
    timeout_seconds: int = 180,
) -> tuple[Path, int]:
    """Launch an interactive (visible) browser session for the operator to log in to GoldEdge."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright is required for GoldEdge login. Install with `pip install playwright`.")

    path = get_goldedge_cookie_path(cookie_path)
    log.info("goldedge_interactive_login_started", url=url, path=str(path))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )

        load_cookies_into_context(context, path)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        print("\n" + "=" * 70)
        print("          GOLDEDGE INTERACTIVE LOGIN SESSION")
        print("=" * 70)
        print(f"1. A browser window has opened to {url}")
        print("2. Please sign in to your GoldEdge account.")
        print(f"3. Press ENTER in this console to capture and save session cookies.")
        print("=" * 70 + "\n")

        try:
            input("Press [ENTER] after completing login in the browser...")
        except (EOFError, KeyboardInterrupt):
            pass

        count = save_cookies_from_context(context, path)
        browser.close()

    log.info("goldedge_interactive_login_complete", path=str(path), cookies_saved=count)
    return path, count


_SPORT_BY_PATH = {
    "football": Sport.SOCCER,
    "tennis": Sport.TENNIS,
    "basketball": Sport.BASKETBALL,
    "ice-hockey": Sport.ICE_HOCKEY,
    "american-football": Sport.AMERICAN_FOOTBALL,
    "baseball": Sport.BASEBALL,
    "esports": Sport.ESPORTS,
    "rugby-league": Sport.RUGBY_LEAGUE,
}

_TARGET_BOOK_RE = re.compile(r"betano|stoiximan", re.IGNORECASE)
_FAIR_TITLE_RE = re.compile(r"fair\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
_SCORE_RE = re.compile(r"(\d+)\D+?(\d+)")

# Runs in the page: one plain dict per match row.
_ROWS_JS = """() => [...document.querySelectorAll('a.ev-row')].map(r => ({
  href: r.getAttribute('href') || '',
  start: r.querySelector('time') ? r.querySelector('time').getAttribute('datetime') : null,
  live: !!r.querySelector('.ev-live, .ev-sc'),
  teams: [...r.querySelectorAll('.ev-tn')].map(e => e.textContent.trim()),
  cells: [...r.querySelectorAll('.ev-cell')].map(c => ({
    title: c.getAttribute('title') || '',
    odds: c.querySelector('.n') ? c.querySelector('.n').textContent.trim() : '',
    book: c.querySelector('.ev-bk') ? c.querySelector('.ev-bk').textContent.trim() : '',
  })),
}))"""

# Runs in a match page: the per-bookmaker 1X2 / moneyline table.
_BOOK_TABLE_JS = """() => {
  const t = document.querySelector('table.ot');
  if (!t) return [];
  return [...t.querySelectorAll('tr')].filter(r => r.querySelector('td.ot-p')).map(r => ({
    book: r.querySelector('.ot-bkn') ? r.querySelector('.ot-bkn').textContent.trim() : '',
    age: r.querySelector('.ot-age') ? r.querySelector('.ot-age').textContent.trim() : '',
    odds: [...r.querySelectorAll('td.ot-p')].map(
      c => c.querySelector('.n') ? c.querySelector('.n').textContent.trim() : ''),
  }));
}"""

_RESULT_JS = """() => {
  const st = document.querySelector('.evh-status');
  const sc = document.querySelector('.evh-score');
  return {
    status_class: st ? st.className : '',
    status_text: st ? st.textContent.trim() : '',
    score_text: sc ? sc.innerText : '',
  };
}"""


def _to_float(text: str | None) -> float | None:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 1.0 else None


def _age_minutes(text: str | None) -> float | None:
    """'45s' -> 0.75, '4m' -> 4, '2h' -> 120, '1d' -> 1440. None when unreadable."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", (text or "").lower())
    if not m:
        return None
    return int(m.group(1)) * {"s": 1 / 60, "m": 1.0, "h": 60.0, "d": 1440.0}[m.group(2)]


def best_price_edge(row: dict[str, Any]) -> float:
    """Largest (best price / fair - 1) in a pre-match row; 0 for live or unpriced rows.

    Betano never pays more than the row's best price, so this bounds Betano's edge.
    """
    if row.get("live"):
        return 0.0
    best = 0.0
    for cell in row.get("cells") or []:
        offered = _to_float(cell.get("odds"))
        fair_match = _FAIR_TITLE_RE.search(cell.get("title") or "")
        fair_odds = _to_float(fair_match.group(1)) if fair_match else None
        if offered is not None and fair_odds is not None:
            best = max(best, offered / fair_odds - 1.0)
    return best


def row_with_book_prices(
    row: dict[str, Any], table: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The list row re-priced with Betano/Stoiximan's own odds from the match page.

    The fair price stays the one GoldEdge published, unless Pinnacle's line on the
    match page de-vigs to a higher fair price (the line moved) - then that is used,
    so a stale fair price cannot invent an edge. None when Betano is not listed,
    its price is stale, or the table does not line up with the row's outcomes.
    """
    cells = row.get("cells") or []
    target = next((r for r in table if _TARGET_BOOK_RE.search(r.get("book") or "")), None)
    if target is None or len(target.get("odds") or []) != len(cells):
        return None
    age = _age_minutes(target.get("age"))
    if age is None or age > _MAX_PRICE_AGE_MINUTES:
        return None

    pin_fair: list[float | None] = [None] * len(cells)
    pin = next((r for r in table if "pinnacle" in (r.get("book") or "").lower()), None)
    if pin is not None and len(pin.get("odds") or []) == len(cells):
        pin_odds = [_to_float(o) for o in pin["odds"]]
        if all(o is not None for o in pin_odds):
            overround = sum(1.0 / o for o in pin_odds)  # type: ignore[operator]
            pin_fair = [round(o * overround, 2) for o in pin_odds]  # type: ignore[operator]

    new_cells = []
    for cell, price, pf in zip(cells, target["odds"], pin_fair, strict=True):
        fair_match = _FAIR_TITLE_RE.search(cell.get("title") or "")
        fair_odds = _to_float(fair_match.group(1)) if fair_match else None
        if fair_odds is not None and pf is not None:
            fair_odds = max(fair_odds, pf)
        title = f"{target['book']} · fair {fair_odds}" if fair_odds is not None else ""
        new_cells.append({"title": title, "odds": price, "book": target["book"]})
    return {**row, "cells": new_cells}


def event_id_from_href(href: str) -> str | None:
    """'/odds/football/europe/champions-league/como-aek-athens-38czyxt' -> 'ge_como-aek-athens-38czyxt'."""
    slug = href.rstrip("/").rsplit("/", 1)[-1]
    return f"ge_{slug}" if slug else None


def parse_event_row(
    row: dict[str, Any],
    *,
    now: datetime,
    min_edge: float,
    stake_fn: Any,
    target_bookmaker: str = "stoiximan",
) -> list[ValueSignal]:
    """Turn one GoldEdge match row into value signals for Betano/Stoiximan prices.

    Only pre-match rows are used: in-play rows carry no fair price, so there is
    nothing sound to measure an edge against.
    """
    href = row.get("href") or ""
    parts = [seg for seg in href.split("/") if seg]
    if len(parts) < 3 or parts[0] != "odds":
        return []
    sport = _SPORT_BY_PATH.get(parts[1])
    teams = [t for t in (row.get("teams") or []) if t]
    cells = row.get("cells") or []
    if sport is None or len(teams) != 2 or row.get("live"):
        return []
    event_id = event_id_from_href(href)
    if event_id is None:
        return []

    home, away = teams
    if len(cells) == 3:
        labels = [home, "Draw", away]
    elif len(cells) == 2:
        labels = [home, away]
    else:
        return []

    start_time: datetime | None = None
    if row.get("start"):
        try:
            start_time = datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00"))
        except ValueError:
            start_time = None
    if start_time is not None and start_time <= now:
        return []  # already kicked off: the listed price is no longer pre-match

    market_odds = {
        label: odds
        for label, cell in zip(labels, cells, strict=True)
        if (odds := _to_float(cell.get("odds"))) is not None
    }

    signals: list[ValueSignal] = []
    for label, cell in zip(labels, cells, strict=True):
        title = cell.get("title") or ""
        book = cell.get("book") or title
        if not _TARGET_BOOK_RE.search(book):
            continue
        offered = _to_float(cell.get("odds"))
        fair_match = _FAIR_TITLE_RE.search(title)
        fair_odds = _to_float(fair_match.group(1)) if fair_match else None
        if offered is None or fair_odds is None:
            continue
        edge = offered / fair_odds - 1.0
        if edge <= 0.0 or edge < min_edge:
            continue
        fair_prob = 1.0 / fair_odds
        stake = stake_fn(fair_prob, offered)
        if stake <= 0:
            continue
        signals.append(
            ValueSignal(
                event_id=event_id,
                market_id=f"{event_id}-h2h",
                market_type="MATCH_ODDS",
                selection=label,
                sport=sport,
                fair_prob=round(fair_prob, 4),
                confirm_prob=round(fair_prob, 4),
                target_bookmaker=target_bookmaker,
                target_odds=offered,
                edge=round(edge, 4),
                recommended_stake=stake,
                detected_at=now,
                is_live=False,
                event_start_time=start_time,
                all_market_odds=market_odds or None,
                event_name=f"{home} vs {away}",
                pinnacle_odds=fair_odds,
                betfair_odds=None,
                source_url=f"{_BASE_URL}{href}",
            )
        )
    return signals


def parse_event_result(raw: dict[str, Any], home: str, away: str) -> dict[str, Any] | None:
    """Final score from a GoldEdge event page, or None unless the match is FINISHED.

    Returned in the same shape the other score resolvers use.
    """
    status = f"{raw.get('status_class', '')} {raw.get('status_text', '')}".lower()
    if "finished" not in status:
        return None
    m = _SCORE_RE.search(raw.get("score_text") or "")
    if not m or not home or not away:
        return None
    return {
        "home_team": home,
        "away_team": away,
        "completed": True,
        "scores": [
            {"name": home, "score": m.group(1)},
            {"name": away, "score": m.group(2)},
        ],
    }


def fetch_goldedge_results(
    events: dict[str, tuple[str, str]], *, headless: bool = True, limit: int = 30
) -> dict[str, dict[str, Any]]:
    """Read final scores for {event_url: (home, away)}. Unfinished matches are omitted."""
    results: dict[str, dict[str, Any]] = {}
    targets = [
        (url, teams) for url, teams in events.items() if url.startswith(f"{_BASE_URL}/odds/")
    ][:limit]
    if not targets:
        return results
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("playwright_missing", source="goldedge_results")
        return results

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_context(
                viewport={"width": 1366, "height": 900}, user_agent=_USER_AGENT
            ).new_page()
            for url, (home, away) in targets:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(1500)
                    parsed = parse_event_result(page.evaluate(_RESULT_JS), home, away)
                except Exception as exc:  # noqa: BLE001
                    log.warning("goldedge_result_fetch_error", url=url, error=str(exc))
                    continue
                if parsed is not None:
                    parsed["id"] = url
                    results[url] = parsed
            browser.close()
    except Exception as exc:  # noqa: BLE001
        log.error("goldedge_results_error", error=str(exc))
    log.info("goldedge_results_fetched", requested=len(targets), finished=len(results))
    return results


class GoldEdgeSource:
    """Scrapes value betting signals from GoldEdge and normalizes them into ValueSignals."""

    name = "goldedge"

    def __init__(self, headless: bool = True, cookie_path: str | Path | None = None) -> None:
        self.headless = headless
        self.cookie_path = get_goldedge_cookie_path(cookie_path)
        self._settings = get_settings()

    def _stake(self, fair_prob: float, odds: float) -> float:
        """Fractional Kelly on the configured bankroll, capped at MAX_STAKE.

        There used to be a 10-unit floor here, which staked 10 even when Kelly
        asked for far less and ignored a MAX_STAKE below 10.
        """
        s = get_settings()
        return kelly_stake(
            fair_prob, odds, bankroll=s.bankroll, fraction=s.kelly_fraction, max_stake=s.max_stake
        )

    def fetch_value_bets(self, min_edge: float | None = None) -> list[ValueSignal]:
        """Scrape current pre-match value bets from GoldEdge."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("playwright_missing", source="goldedge")
            return []

        self._settings = get_settings()
        effective_min_edge = max(
            0.001,
            min_edge if min_edge is not None else self._settings.goldedge_min_edge,
        )
        signals: list[ValueSignal] = []
        seen: set[tuple[str, str, str]] = set()
        now = datetime.now(UTC)
        stats: dict[str, Any] = {
            "scanned_at": now.isoformat(),
            "min_edge": effective_min_edge,
            "dashboard": "no cookies",
            "pages_scanned": 0,
            "matches_seen": 0,
            "candidates": 0,
            "match_pages_opened": 0,
            "betano_listed": 0,
            "signals": 0,
            "empty_pages": [],
            "errors": [],
        }
        LAST_SCAN.clear()
        LAST_SCAN.update(stats)
        stats = LAST_SCAN

        def add(sig: ValueSignal) -> None:
            key = (sig.event_name or sig.event_id, sig.market_type, sig.selection)
            if key not in seen:
                seen.add(key)
                signals.append(sig)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    viewport={"width": 1366, "height": 900}, user_agent=_USER_AGENT
                )
                has_cookies = self.cookie_path.exists()
                if has_cookies:
                    load_cookies_into_context(context, self.cookie_path)
                page = context.new_page()

                # Members' value-bet dashboard (needs a saved session).
                if has_cookies:
                    try:
                        page.goto(_DASHBOARD_URL, wait_until="domcontentloaded", timeout=20000)
                        page.wait_for_timeout(2500)
                        if "sign-in" not in page.url and "/valuebets" in page.url:
                            stats["dashboard"] = "open"
                            cards = page.locator(
                                '[class*="card"], article, div:has-text("FAIR ODDS")'
                            ).all()
                            for idx, card in enumerate(cards):
                                try:
                                    text = card.inner_text().strip()
                                except Exception:  # noqa: BLE001
                                    continue
                                if "FAIR ODDS" not in text:
                                    continue
                                sig = self._parse_valuebet_card(
                                    text, idx=idx, now=now, min_edge=effective_min_edge
                                )
                                if sig is not None:
                                    add(sig)
                        else:
                            stats["dashboard"] = f"session rejected (landed on {page.url})"
                            log.warning(
                                "goldedge_session_invalid",
                                msg="saved GoldEdge cookies did not open the dashboard; "
                                    "re-run `valuebet login-goldedge`",
                            )
                    except Exception as exc:  # noqa: BLE001
                        stats["dashboard"] = f"error: {str(exc)[:120]}"
                        log.warning("goldedge_dashboard_error", error=str(exc))

                # Public odds lists: every row carries Pinnacle's fair price.
                queue = list(_LIST_PATHS)
                candidates: dict[str, tuple[float, dict[str, Any]]] = {}
                competitions: list[str] = []
                while queue:
                    path = queue.pop(0)
                    try:
                        page.goto(_BASE_URL + path, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(2500)
                        rows = page.evaluate(_ROWS_JS)
                        if path == "/odds/football":
                            competitions = page.evaluate(_COMPETITIONS_JS)[:_MAX_COMPETITION_PAGES]
                            queue.extend(c for c in competitions if c not in _LIST_PATHS)
                    except Exception as exc:  # noqa: BLE001
                        stats["errors"].append(f"{path}: {str(exc)[:120]}")
                        log.warning("goldedge_page_scan_error", path=path, error=str(exc))
                        continue
                    stats["pages_scanned"] += 1
                    stats["matches_seen"] += len(rows)
                    if not rows:
                        try:
                            stats["empty_pages"].append(
                                f"{path} -> {page.url} [{page.title()[:60]}]"
                            )
                        except Exception:  # noqa: BLE001
                            stats["empty_pages"].append(path)
                    found = 0
                    for row in rows:
                        href = row.get("href") or ""
                        edge_cap = best_price_edge(row)
                        if href and edge_cap >= effective_min_edge:
                            candidates[href] = (edge_cap, row)
                        for sig in parse_event_row(
                            row,
                            now=now,
                            min_edge=effective_min_edge,
                            stake_fn=self._stake,
                        ):
                            add(sig)
                            found += 1
                    log.info("goldedge_list_scanned", path=path, rows=len(rows), value_bets=found)

                # Match pages: Betano's own price where the best price shows value.
                ranked = sorted(candidates.items(), key=lambda kv: -kv[1][0])
                stats["candidates"] = len(candidates)
                opened = from_pages = 0
                for href, (_cap, row) in ranked[:_MAX_EVENT_PAGES]:
                    try:
                        page.goto(_BASE_URL + href, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(1500)
                        table = page.evaluate(_BOOK_TABLE_JS)
                    except Exception as exc:  # noqa: BLE001
                        stats["errors"].append(f"{href}: {str(exc)[:120]}")
                        log.warning("goldedge_event_page_error", href=href, error=str(exc))
                        continue
                    opened += 1
                    stats["match_pages_opened"] = opened
                    if any(_TARGET_BOOK_RE.search(r.get("book") or "") for r in table):
                        stats["betano_listed"] += 1
                    repriced = row_with_book_prices(row, table)
                    if repriced is None:
                        continue
                    for sig in parse_event_row(
                        repriced,
                        now=now,
                        min_edge=effective_min_edge,
                        stake_fn=self._stake,
                    ):
                        add(sig)
                        from_pages += 1
                log.info(
                    "goldedge_event_pages_scanned",
                    candidates=len(candidates),
                    opened=opened,
                    value_bets=from_pages,
                )

                browser.close()
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"scrape: {str(exc)[:200]}")
            log.error("goldedge_scrape_error", error=str(exc))

        stats["signals"] = len(signals)
        log.info("goldedge_signals_extracted", count=len(signals))
        return signals

    def _parse_valuebet_card(
        self,
        text: str,
        idx: int,
        now: datetime,
        min_edge: float,
        is_live: bool = False,
    ) -> ValueSignal | None:
        """Parse a GoldEdge Open Value Bets card (Betano/Stoiximan vs Pinnacle Fair Odds)."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 5:
            return None

        # Only accept Betano or Stoiximan cards (Betano is Stoiximan)
        text_lower = text.lower()
        if "betano" not in text_lower and "stoiximan" not in text_lower:
            return None

        # 1. Edge % (first percentage token, e.g. '+7.09%')
        edge_val: float | None = None
        for line in lines[:5]:
            m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", line)
            if m:
                try:
                    edge_val = float(m.group(1)) / 100.0
                    break
                except ValueError:
                    continue

        if edge_val is None or edge_val <= 0.0 or edge_val < min_edge:
            return None

        # 2. Sport detection
        if "🎾" in text or "ATP" in text or "WTA" in text or "Set " in text:
            sport = Sport.TENNIS
        elif "🏀" in text or "NBA" in text or "Basketball" in text:
            sport = Sport.BASKETBALL
        else:
            sport = Sport.SOCCER

        # 3. Extract Betano/Stoiximan offered odds and Pinnacle/Fair odds
        offered_odds: float | None = None
        sharp_odds: float | None = None

        for i, line in enumerate(lines):
            ll = line.lower()
            if ("betano" in ll or "stoiximan" in ll) and offered_odds is None:
                # Odds may be on same line or next 1-2 lines
                for cand in lines[i : i + 3]:
                    m = re.search(r"\b(\d+\.\d{2})\b", cand)
                    if m:
                        val = float(m.group(1))
                        if 1.02 <= val <= 100.0:
                            offered_odds = val
                            break
            if ("fair odds" in ll or ll.startswith("pin ")) and sharp_odds is None:
                for cand in lines[i : i + 3]:
                    m = re.search(r"\b(\d+\.\d{2})\b", cand)
                    if m:
                        val = float(m.group(1))
                        if 1.02 <= val <= 100.0:
                            sharp_odds = val
                            break

        if offered_odds is None or sharp_odds is None:
            return None
        # Must be positive value against Pinnacle fair odds
        if offered_odds <= sharp_odds:
            return None

        fair_prob = 1.0 / sharp_odds
        computed_edge = (fair_prob * offered_odds) - 1.0
        if computed_edge <= 0.0 or computed_edge < min_edge:
            return None

        # 4. Extract match teams & selection/market
        # Filter out metadata lines to find team names and selection
        teams: list[str] = []
        selection = "Selection"
        market_type = "MATCH_ODDS"

        betano_idx = next(
            (i for i, l in enumerate(lines) if "betano" in l.lower() or "stoiximan" in l.lower()),
            len(lines),
        )
        header_lines = lines[:betano_idx]
        for l in header_lines:
            ll = l.lower()
            if (
                re.search(r"^\+?-?\d+(\.\d+)?%$", l)
                or ll in ("ev", "strong", "best", "live")
                or "ago" in ll
                or "· in " in ll
                or "·" in l and any(k in ll for k in ("divisie", "league", "cup", "serie", "liga", "bundesliga", "premier"))
                or re.match(r"^\d{2}/\d{2}\s+\d{2}:\d{2}", l)
            ):
                continue
            teams.append(l.replace("↗", "").strip())

        if len(teams) >= 2:
            event_name = f"{teams[0]} vs {teams[1]}"
        else:
            event_name = teams[0] if teams else f"GoldEdge Match {idx}"

        if len(teams) >= 3:
            selection = teams[2]
        if len(teams) >= 4:
            mt_raw = teams[3].split("·")[0].strip()
            if mt_raw:
                market_type = mt_raw.upper().replace(" ", "_")

        date_str = now.strftime("%Y%m%d")
        ev_hash = hashlib.md5(f"{event_name}_{date_str}".encode()).hexdigest()[:10]
        stake = self._stake(fair_prob, offered_odds)

        return ValueSignal(
            event_id=f"ge_{ev_hash}",
            market_id=f"ge_m_{ev_hash}",
            market_type=market_type,
            selection=selection,
            sport=sport,
            fair_prob=round(fair_prob, 4),
            confirm_prob=round(fair_prob, 4),
            target_bookmaker="stoiximan",
            target_odds=offered_odds,
            edge=round(edge_val, 4),
            recommended_stake=stake,
            detected_at=now,
            is_live=is_live,
            event_name=event_name,
            pinnacle_odds=sharp_odds,
            betfair_odds=None,
        )

    def _parse_row_text(self, text: str, idx: int, now: datetime, min_edge: float) -> ValueSignal | None:
        """Parse text of a GoldEdge valuebet row into a ValueSignal (supports legacy table format & test mocks)."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 3:
            return None

        edge_val = None
        for line in lines:
            m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", line)
            if m:
                try:
                    edge_val = float(m.group(1)) / 100.0
                    break
                except ValueError:
                    continue

        if edge_val is None or edge_val <= 0.0 or edge_val < min_edge:
            return None

        odds_candidates = []
        for line in lines:
            m = re.findall(r"\b(\d+\.\d{2})\b", line)
            for val in m:
                try:
                    f = float(val)
                    if 1.05 <= f <= 20.0:
                        odds_candidates.append(f)
                except ValueError:
                    continue

        if len(odds_candidates) < 2:
            return None

        offered_odds = odds_candidates[0]
        sharp_odds = odds_candidates[1]
        if offered_odds <= sharp_odds:
            return None

        fair_prob = 1.0 / sharp_odds if sharp_odds > 1.0 else 0.5
        event_name = lines[0] if len(lines) > 0 else f"GoldEdge Match {idx}"
        selection = lines[1] if len(lines) > 1 else "Selection"

        stake = self._stake(fair_prob, offered_odds)
        target_book = self._settings.placement_bookmaker or "stoiximan"

        date_str = now.strftime("%Y%m%d")
        ev_hash = hashlib.md5(f"{event_name}_{date_str}".encode()).hexdigest()[:10]

        return ValueSignal(
            event_id=f"ge_{ev_hash}",
            market_id=f"ge_m_{ev_hash}",
            market_type="MATCH_ODDS",
            selection=selection,
            sport=Sport.SOCCER,
            fair_prob=round(fair_prob, 4),
            confirm_prob=round(fair_prob, 4),
            target_bookmaker=target_book,
            target_odds=offered_odds,
            edge=round(edge_val, 4),
            recommended_stake=stake,
            detected_at=now,
            is_live=False,
            event_name=event_name,
            pinnacle_odds=sharp_odds,
            betfair_odds=None,
        )


def sync_goldedge_to_db(min_edge: float | None = None) -> int:
    """Convenience function to fetch value bets from GoldEdge and save to DB."""
    source = GoldEdgeSource(headless=True)
    signals = source.fetch_value_bets(min_edge=min_edge)
    # The configured odds range applies to GoldEdge signals like any other source.
    settings = get_settings()
    in_range = [s for s in signals if settings.min_odds <= s.target_odds <= settings.max_odds]
    if len(in_range) != len(signals):
        log.info("goldedge_odds_range_filtered", dropped=len(signals) - len(in_range))
    signals = in_range
    persisted = 0
    with session_scope() as session:
        for sig in signals:
            saved = save_signal(session, sig)
            if saved:
                persisted += 1
    log.info("goldedge_sync_complete", extracted=len(signals), persisted=persisted)
    return persisted
