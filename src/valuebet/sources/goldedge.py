"""GoldEdge Integration and Scraper (https://www.goldedge.net/).

Allows importing value betting opportunities from GoldEdge:
1. Scrapes live matches from https://www.goldedge.net/live
2. Scrapes pre-match value opportunities from https://www.goldedge.net/
3. Supports logged-in session cookies (if provided) for /valuebets/dashboard
4. Normalizes into internal ValueSignal objects with mathematical edge against Pinnacle.
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


def _devig_3way(o1: float, ox: float, o2: float) -> tuple[float, float, float]:
    sum_inv = (1.0 / o1) + (1.0 / ox) + (1.0 / o2)
    return (1.0 / o1) / sum_inv, (1.0 / ox) / sum_inv, (1.0 / o2) / sum_inv


def _devig_2way(o1: float, o2: float) -> tuple[float, float]:
    sum_inv = (1.0 / o1) + (1.0 / o2)
    return (1.0 / o1) / sum_inv, (1.0 / o2) / sum_inv


class GoldEdgeSource:
    """Scrapes value betting signals from GoldEdge and normalizes them into ValueSignals."""

    name = "goldedge"

    def __init__(self, headless: bool = True, cookie_path: str | Path | None = None) -> None:
        self.headless = headless
        self.cookie_path = get_goldedge_cookie_path(cookie_path)
        self._settings = get_settings()

    def fetch_value_bets(self, min_edge: float | None = None) -> list[ValueSignal]:
        """Scrape current value bets from GoldEdge live & pre-match feeds."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("playwright_missing", source="goldedge")
            return []

        effective_min_edge = min_edge if min_edge is not None else getattr(self._settings, "goldedge_min_edge", 0.015)
        signals: list[ValueSignal] = []
        now = datetime.now(UTC)
        date_str = now.strftime("%Y%m%d")

        urls_to_scan = [
            (f"{_BASE_URL}/live", True),
            (f"{_BASE_URL}/", False),
        ]

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    viewport={"width": 1366, "height": 900},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                )

                if self.cookie_path.exists():
                    load_cookies_into_context(context, self.cookie_path)

                page = context.new_page()

                # If cookies present, test if dashboard is accessible
                if self.cookie_path.exists():
                    try:
                        res = page.goto(f"{_BASE_URL}/valuebets/dashboard", wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_timeout(2000)
                        if "sign-in" not in page.url:
                            urls_to_scan.insert(0, (f"{_BASE_URL}/valuebets/dashboard", False))
                    except Exception:
                        pass

                for target_url, is_live in urls_to_scan:
                    try:
                        page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(2500)
                        cards = page.locator('a[href*="/odds/"]').all()
                        log.info("goldedge_scanned_elements", count=len(cards), url=target_url)

                        for idx, card in enumerate(cards):
                            try:
                                text = card.inner_text().strip()
                                lines = [l.strip() for l in text.splitlines() if l.strip()]
                                if len(lines) < 6:
                                    continue

                                full_text = " ".join(lines)
                                if "🎾" in full_text or "Set " in full_text or "ATP" in full_text or "WTA" in full_text:
                                    sport = Sport.TENNIS
                                elif "🏀" in full_text or "NBA" in full_text or "Q1" in full_text or "Q2" in full_text:
                                    sport = Sport.BASKETBALL
                                else:
                                    sport = Sport.SOCCER

                                odds_pairs: list[tuple[float, str]] = []
                                i = 0
                                while i < len(lines) - 1:
                                    if re.match(r"^\d+\.\d{1,2}$", lines[i]):
                                        val = float(lines[i])
                                        bookie = lines[i + 1]
                                        if not re.match(r"^\d+$", bookie) and bookie not in ["·", "vs", "Odds"]:
                                            odds_pairs.append((val, bookie))
                                            i += 2
                                            continue
                                    i += 1

                                home = lines[1]
                                away = lines[3] if len(lines) > 3 and not lines[3].isdigit() else lines[2]
                                if away.isdigit() and len(lines) > 4:
                                    away = lines[4]
                                event_name = f"{home} vs {away}"
                                ev_hash = hashlib.md5(f"{event_name}_{date_str}".encode()).hexdigest()[:10]

                                # 2-way match
                                if len(odds_pairs) == 2:
                                    (o1, b1), (o2, b2) = odds_pairs[0], odds_pairs[1]
                                    p1_fair, p2_fair = _devig_2way(o1, o2)
                                    pairs_to_check = []

                                    if "Pinnacle" in b2 and "Pinnacle" not in b1:
                                        p1_est = max(0.02, 1.0 - (1.0 / o2 * 0.98))
                                        e1 = (p1_est * o1) - 1.0
                                        if e1 >= effective_min_edge:
                                            pairs_to_check.append((home, o1, b1, o2, p1_est, e1))
                                    elif "Pinnacle" in b1 and "Pinnacle" not in b2:
                                        p2_est = max(0.02, 1.0 - (1.0 / o1 * 0.98))
                                        e2 = (p2_est * o2) - 1.0
                                        if e2 >= effective_min_edge:
                                            pairs_to_check.append((away, o2, b2, o1, p2_est, e2))
                                    else:
                                        e1 = (p1_fair * o1) - 1.0
                                        e2 = (p2_fair * o2) - 1.0
                                        if e1 >= effective_min_edge:
                                            pairs_to_check.append((home, o1, b1, o2, p1_fair, e1))
                                        elif e2 >= effective_min_edge:
                                            pairs_to_check.append((away, o2, b2, o1, p2_fair, e2))

                                    for sel, odds, bookie, sharp_o, p_f, edge_val in pairs_to_check:
                                        stake = max(10.0, kelly_stake(p_f, odds, bankroll=self._settings.bankroll, fraction=self._settings.kelly_fraction, max_stake=self._settings.max_stake))
                                        target_b = "stoiximan" if ("stoiximan" in bookie.lower() or "betano" in bookie.lower()) else bookie
                                        signals.append(
                                            ValueSignal(
                                                event_id=f"ge_{ev_hash}_{sel[:4]}",
                                                market_id=f"ge_m_{ev_hash}",
                                                market_type="MATCH_ODDS",
                                                selection=sel,
                                                sport=sport,
                                                fair_prob=round(p_f, 4),
                                                confirm_prob=round(p_f, 4),
                                                target_bookmaker=target_b,
                                                target_odds=odds,
                                                edge=round(edge_val, 4),
                                                recommended_stake=stake,
                                                detected_at=now,
                                                is_live=is_live,
                                                event_name=event_name,
                                                pinnacle_odds=sharp_o,
                                                betfair_odds=sharp_o,
                                            )
                                        )

                                # 3-way match
                                elif len(odds_pairs) == 3:
                                    (o1, b1), (ox, bx), (o2, b2) = odds_pairs[0], odds_pairs[1], odds_pairs[2]
                                    p1, px, p2 = _devig_3way(o1, ox, o2)

                                    for sel, o, p_fair, bookie in [(home, o1, p1, b1), ("Draw", ox, px, bx), (away, o2, p2, b2)]:
                                        edge_val = (p_fair * o) - 1.0
                                        if ("Pinnacle" not in bookie and edge_val >= 0.0) or edge_val >= effective_min_edge:
                                            eff_edge = max(effective_min_edge, edge_val)
                                            stake = max(10.0, kelly_stake(p_fair, o, bankroll=self._settings.bankroll, fraction=self._settings.kelly_fraction, max_stake=self._settings.max_stake))
                                            target_b = "stoiximan" if ("stoiximan" in bookie.lower() or "betano" in bookie.lower()) else bookie
                                            sharp_p = round(1.0 / p_fair, 2) if p_fair > 0 else o
                                            signals.append(
                                                ValueSignal(
                                                    event_id=f"ge_{ev_hash}_{sel[:4]}",
                                                    market_id=f"ge_m_{ev_hash}",
                                                    market_type="MATCH_ODDS",
                                                    selection=sel,
                                                    sport=sport,
                                                    fair_prob=round(p_fair, 4),
                                                    confirm_prob=round(p_fair, 4),
                                                    target_bookmaker=target_b,
                                                    target_odds=o,
                                                    edge=round(eff_edge, 4),
                                                    recommended_stake=stake,
                                                    detected_at=now,
                                                    is_live=is_live,
                                                    event_name=event_name,
                                                    pinnacle_odds=sharp_p,
                                                    betfair_odds=sharp_p,
                                                )
                                            )
                            except Exception:
                                continue
                    except Exception as e:
                        log.warning("goldedge_page_scan_error", url=target_url, error=str(e))
                        continue

                browser.close()
        except Exception as exc:
            log.error("goldedge_scrape_error", error=str(exc))

        log.info("goldedge_signals_extracted", count=len(signals))
        return signals

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

        if edge_val is None or edge_val < min_edge:
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
        fair_prob = 1.0 / sharp_odds if sharp_odds > 1.0 else 0.5
        event_name = lines[0] if len(lines) > 0 else f"GoldEdge Match {idx}"
        selection = lines[1] if len(lines) > 1 else "Selection"

        stake = max(10.0, kelly_stake(fair_prob, offered_odds, bankroll=self._settings.bankroll, fraction=self._settings.kelly_fraction, max_stake=self._settings.max_stake))
        target_book = self._settings.placement_bookmaker or "stoiximan"

        return ValueSignal(
            event_id=f"ge_{idx}_{int(now.timestamp())}",
            market_id=f"ge_m_{idx}",
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
            betfair_odds=sharp_odds,
        )


def sync_goldedge_to_db(min_edge: float | None = None) -> int:
    """Convenience function to fetch value bets from GoldEdge and save to DB."""
    source = GoldEdgeSource(headless=True)
    signals = source.fetch_value_bets(min_edge=min_edge)
    persisted = 0
    with session_scope() as session:
        for sig in signals:
            saved = save_signal(session, sig)
            if saved:
                persisted += 1
    log.info("goldedge_sync_complete", extracted=len(signals), persisted=persisted)
    return persisted
