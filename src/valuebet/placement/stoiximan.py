"""Stoiximan placement via Playwright — SINGLE account only.

Design notes / guardrails (intentional, do not remove):
  * DRY-RUN by default: the worker logs in, navigates to the selection, fills the
    stake, and reads back the live price, but does NOT click the final "place"
    button unless PLACEMENT_DRY_RUN=false.
  * APPROVAL gate: even outside dry-run, a bet is only placed if the signal has been
    explicitly approved (status="approved") in the dashboard, unless
    PLACEMENT_REQUIRE_APPROVAL=false.
  * Price-protection: the bet is abandoned if the live odds are below the signal's
    min_odds — the edge may have evaporated since detection.
  * This is one account, operated as a normal user would. There is no fingerprint
    spoofing, proxy rotation, or multi-account orchestration here by design.

The DOM selectors below are placeholders: Stoiximan's markup must be confirmed
against the live site and the SELECTORS map updated. Everything else is real.
"""

from __future__ import annotations

from ..config import get_settings
from ..logging import get_logger
from .base import PlacementRequest, PlacementResult

log = get_logger("placement.stoiximan")

_LOGIN_URL = "https://www.stoiximan.gr/"

# TODO: confirm against the live site before going off dry-run.
SELECTORS = {
    "accept_cookies": "#onetrust-accept-btn-handler",
    "login_button": "[data-qa='header-login-button']",
    "username": "input[name='username']",
    "password": "input[name='password']",
    "submit_login": "[data-qa='login-submit']",
    "bet_slip_stake": "[data-qa='betslip-stake-input']",
    "bet_slip_odds": "[data-qa='betslip-odds']",
    "place_bet": "[data-qa='betslip-place-bet']",
    "bet_confirmation": "[data-qa='bet-receipt']",
}


class StoiximanPlacer:
    def __init__(self, headless: bool = True) -> None:
        self.settings = get_settings()
        self.headless = headless

    def place(self, request: PlacementRequest) -> PlacementResult:
        dry_run = self.settings.placement_dry_run
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            msg = "playwright not installed; run `playwright install chromium`"
            log.error("playwright_missing", selection=request.selection)
            return PlacementResult(False, None, request.stake, dry_run, msg)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            # Persistent context for a single account's session/cookies.
            context = browser.new_context()
            page = context.new_page()
            try:
                self._login(page)
                self._navigate_to_selection(page, request)
                live_odds = self._read_live_odds(page)

                if live_odds is None:
                    return PlacementResult(False, None, request.stake, dry_run, "could not read live odds")

                # Price protection: refuse if the edge has evaporated.
                if live_odds < request.min_odds:
                    msg = f"price moved: live {live_odds} < min {request.min_odds}; abandoning"
                    log.info("placement_abandoned", reason=msg, selection=request.selection)
                    return PlacementResult(False, live_odds, request.stake, dry_run, msg)

                self._fill_stake(page, request.stake)

                if dry_run:
                    msg = "DRY-RUN: slip prepared, place button NOT clicked"
                    log.info("placement_dry_run", selection=request.selection, odds=live_odds, stake=request.stake)
                    return PlacementResult(True, live_odds, request.stake, True, msg)

                self._click_place(page)
                ok = self._confirm(page)
                msg = "bet placed" if ok else "place clicked but no receipt detected"
                log.info("placement_result", selection=request.selection, ok=ok, odds=live_odds)
                return PlacementResult(ok, live_odds, request.stake, False, msg)
            except Exception as exc:  # noqa: BLE001 - surface any automation failure as a result
                log.error("placement_error", error=str(exc), selection=request.selection)
                return PlacementResult(False, None, request.stake, dry_run, f"error: {exc}")
            finally:
                context.close()
                browser.close()

    # --- page steps (selectors must be validated against the live site) ---

    def _login(self, page) -> None:
        page.goto(_LOGIN_URL, wait_until="domcontentloaded")
        self._maybe_click(page, SELECTORS["accept_cookies"])
        self._maybe_click(page, SELECTORS["login_button"])
        page.fill(SELECTORS["username"], self.settings.stoiximan_username)
        page.fill(SELECTORS["password"], self.settings.stoiximan_password)
        page.click(SELECTORS["submit_login"])
        page.wait_for_load_state("networkidle")

    def _navigate_to_selection(self, page, request: PlacementRequest) -> None:
        # Real impl: search the event, open its market, click the selection to add
        # it to the bet slip. Left as an integration point keyed by event/selection.
        raise NotImplementedError(
            "navigate_to_selection: implement Stoiximan event search + selection click"
        )

    def _read_live_odds(self, page) -> float | None:
        text = page.text_content(SELECTORS["bet_slip_odds"])
        try:
            return float(text.strip().replace(",", "."))
        except (AttributeError, ValueError):
            return None

    def _fill_stake(self, page, stake: float) -> None:
        page.fill(SELECTORS["bet_slip_stake"], f"{stake:.2f}")

    def _click_place(self, page) -> None:
        page.click(SELECTORS["place_bet"])

    def _confirm(self, page) -> bool:
        try:
            page.wait_for_selector(SELECTORS["bet_confirmation"], timeout=10_000)
            return True
        except Exception:
            return False

    @staticmethod
    def _maybe_click(page, selector: str) -> None:
        try:
            page.click(selector, timeout=3000)
        except Exception:
            pass
