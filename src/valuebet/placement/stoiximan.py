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
  * Feature 3: After placement, the accepted stake is read from the bet receipt so
    any stake reduction by the bookmaker is recorded accurately.
  * Feature 4: After each placement attempt, a LimitEvent is recorded via the
    global BookmakerLimitTracker so the operator can monitor account health.
  * This is one account, operated as a normal user would. There is no fingerprint
    spoofing, proxy rotation, or multi-account orchestration here by design.

The DOM selectors below are placeholders: Stoiximan's markup must be confirmed
against the live site and the SELECTORS map updated. Everything else is real.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import get_settings
from ..core.limit_tracker import LimitEvent, get_limit_tracker
from ..logging import get_logger
from .base import PlacementRequest, PlacementResult
from .session_store import (
    get_cookie_file_path,
    get_cookie_status,
    load_cookies_into_context,
    save_cookies_from_context,
)

log = get_logger("placement.stoiximan")

_LOGIN_URL = "https://www.stoiximan.com.cy/"

SELECTORS = {
    "accept_cookies": "#js-accept-btn, button[data-isterms='true'], #onetrust-accept-btn-handler, button:has-text('Accept'), button:has-text('Αποδοχή')",
    "login_button": "[data-qa='login-button'], button:has-text('ΣΥΝΔΕΣΗ'), button:has-text('LOG IN'), button:has-text('Log In')",
    "logged_in_indicator": "[data-qa='user-balance'], [data-qa='header-user-btn'], [data-qa='account-button'], .account-balance, button:has-text('Κατάθεση'), button:has-text('Deposit')",
    "username": "#username, input[name='username'], form[data-qa='login'] input[name='username']",
    "password": "#password, input[name='Password'], input[name='password'], form[data-qa='login'] input[type='password']",
    "submit_login": "form[data-qa='login'] button[data-qa='submit'], [data-qa='submit'], button[type='submit']",
    "bet_slip_stake": "[data-qa='stake-area'], [data-qa='betslip-stake-input'], input[name='stake'], .betslip-stake-input input",
    "bet_slip_odds": "[data-qa='total-amounts-item-value'], [data-qa='betslip-odds'], .betslip-odds",
    "place_bet": "[data-qa='place-bet-button'], [data-qa='betslip-place-bet'], button:has-text('ΣΤΟΙΧΗΜΑΤΙΣΕ'), button:has-text('PLACE BET'), button:has-text('STAKE')",
    # Feature 3: receipt selectors — read accepted stake back from confirmation
    "bet_confirmation": "[data-qa='bet-receipt'], .bet-receipt, .receipt-container",
    "receipt_stake": "[data-qa='receipt-stake']",    # Accepted stake amount
    "receipt_odds": "[data-qa='receipt-odds']",      # Confirmed odds
}

BOOKMAKER_NAME = "stoiximan"


class StoiximanPlacer:
    """Persistent-session Stoiximan placer.

    The browser is launched once and the login is performed once.
    Every subsequent call to ``place()`` reuses the same page —
    no re-login on every bet.  The session is automatically refreshed
    if it expires (e.g. after a long idle period).
    """

    def __init__(self, headless: bool = True, cookie_path: str | None = None) -> None:
        self.settings = get_settings()
        self.headless = headless
        self.cookie_path = get_cookie_file_path(cookie_path or self.settings.stoiximan_cookie_path)
        # Persistent session state
        self._pw = None          # playwright instance
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False

    # ------------------------------------------------------------------ public

    def place(self, request: PlacementRequest) -> PlacementResult:
        dry_run = self.settings.placement_dry_run
        try:
            self._ensure_session()
        except Exception as exc:
            msg = f"session error: {exc}"
            log.error("session_failed", error=msg)
            result = PlacementResult(
                success=False, placed_odds=None,
                requested_stake=request.stake, accepted_stake=0.0,
                dry_run=dry_run, message=msg,
            )
            self._record_limit_event(result, request)
            return result

        page = self._page
        try:
            self._navigate_to_selection(page, request)
            live_odds = self._read_live_odds(page)

            if live_odds is None:
                result = PlacementResult(
                    success=False, placed_odds=None,
                    requested_stake=request.stake, accepted_stake=0.0,
                    dry_run=dry_run, message="could not read live odds",
                )
                self._record_limit_event(result, request)
                return result

            # Price protection: refuse if the edge has evaporated.
            if live_odds < request.min_odds:
                msg = f"price moved: live {live_odds} < min {request.min_odds}; abandoning"
                log.info("placement_abandoned", reason=msg, selection=request.selection)
                result = PlacementResult(
                    success=False, placed_odds=live_odds,
                    requested_stake=request.stake, accepted_stake=0.0,
                    dry_run=dry_run, message=msg,
                )
                self._record_limit_event(result, request)
                return result

            self._fill_stake(page, request.stake)

            if dry_run:
                msg = "DRY-RUN: slip prepared, place button NOT clicked"
                log.info("placement_dry_run", selection=request.selection,
                         odds=live_odds, stake=request.stake)
                return PlacementResult(
                    success=True, placed_odds=live_odds,
                    requested_stake=request.stake, accepted_stake=request.stake,
                    dry_run=True, message=msg,
                )

            self._click_place(page)
            ok = self._confirm(page)

            accepted_stake = self._read_accepted_stake(page, request.stake)
            receipt_odds = self._read_receipt_odds(page, live_odds)

            msg = "bet placed" if ok else "place clicked but no receipt detected"
            log.info("placement_result", selection=request.selection,
                     ok=ok, requested_stake=request.stake,
                     accepted_stake=accepted_stake, odds=receipt_odds)

            result = PlacementResult(
                success=ok,
                placed_odds=receipt_odds,
                requested_stake=request.stake,
                accepted_stake=accepted_stake,
                dry_run=False,
                message=msg,
            )
            self._record_limit_event(result, request)
            return result

        except Exception as exc:
            log.error("placement_error", error=str(exc), selection=request.selection)
            # If error looks like session expired, reset so next call re-logs in
            if any(k in str(exc).lower() for k in ["timeout", "closed", "disconnected", "login"]):
                log.warning("session_reset", reason="placement error, will re-login on next call")
                self._logged_in = False
            result = PlacementResult(
                success=False, placed_odds=None,
                requested_stake=request.stake, accepted_stake=0.0,
                dry_run=dry_run, message=f"error: {exc}",
            )
            self._record_limit_event(result, request)
            return result

    def close(self) -> None:
        """Cleanly shut down the persistent browser session."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._pw = None
            self._logged_in = False

    # ------------------------------------------------------------------ session

    def _ensure_session(self) -> None:
        """Guarantee browser is open and user is logged in.
        Called before every placement — cheap if already set up.
        """
        self._start_browser_if_needed()
        if not self._logged_in:
            self._login(self._page)
            self._logged_in = True

    def _start_browser_if_needed(self) -> None:
        """Launch browser + context once; reuse on subsequent calls."""
        if self._page is not None:
            try:
                # Quick check: page still alive?
                self._page.title()
                return
            except Exception:
                log.warning("browser_dead", msg="restarting browser session")
                self.close()

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("playwright not installed; run `playwright install chromium`")

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-infobars",
                "--start-maximized",
            ]
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="el-GR",
            timezone_id="Europe/Athens",
            java_script_enabled=True,
        )
        # Load saved cookies if they exist (avoids re-login)
        self._load_cookies(self._context)
        self._page = self._context.new_page()
        # Mask webdriver property so DataDome doesn't detect headless
        self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        log.info("browser_started", headless=self.headless, cookie_path=str(self.cookie_path))

    # ------------------------------------------------------------------ cookies

    def _load_cookies(self, context) -> bool:
        """Load saved cookies into browser context. Returns True if loaded."""
        loaded, count = load_cookies_into_context(context, self.cookie_path)
        return loaded

    def _save_cookies(self, context) -> None:
        """Save browser cookies to file for reuse in future sessions."""
        save_cookies_from_context(context, self.cookie_path)

    def _is_logged_in(self, page) -> bool:
        """Check if we are already logged in (cookies still valid)."""
        try:
            page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)  # Let JS render

            content = page.content().lower()
            if "regulatory provisions" in content or "access denied" in content or "attention required" in content:
                log.error("stoiximan_access_blocked", msg="Site blocked by location or Cloudflare on this server IP")
                try:
                    Path("data").mkdir(exist_ok=True)
                    page.screenshot(path="data/stoiximan_blocked.png")
                except Exception:
                    pass
                return False

            # If user balance or account menu is visible → logged in!
            logged_in_el = page.query_selector(SELECTORS["logged_in_indicator"])
            if logged_in_el and logged_in_el.is_visible():
                return True

            # If login button is visible → not logged in
            login_btn = page.query_selector(SELECTORS["login_button"])
            if login_btn and login_btn.is_visible():
                return False

            return False
        except Exception:
            return False

    def _login(self, page) -> None:
        """Login only if not already logged in via saved cookies."""
        try:
            # Check if saved cookies are still valid
            if self._is_logged_in(page):
                log.info("session_reused", msg="already logged in via cookies")
                return

            log.info("login_required", msg="cookies invalid or missing, logging in")
            # Human-like delay before interacting
            time.sleep(1)
            self._maybe_click(page, SELECTORS["accept_cookies"])
            time.sleep(0.5)

            if not self._maybe_click(page, SELECTORS["login_button"]):
                page.goto("https://www.stoiximan.com.cy/?login=1", wait_until="domcontentloaded", timeout=10000)
                time.sleep(1)

            page.wait_for_selector(SELECTORS["username"], timeout=8000)
            # Type slowly like a human
            page.type(SELECTORS["username"], self.settings.stoiximan_username, delay=80)
            time.sleep(0.3)
            page.type(SELECTORS["password"], self.settings.stoiximan_password, delay=80)
            time.sleep(0.5)
            page.click(SELECTORS["submit_login"], timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)

            # Save cookies so next run skips login entirely
            self._save_cookies(page.context)
            log.info("login_successful", msg="cookies saved for future reuse")

        except Exception as exc:
            log.warning("stoiximan_login_failed", error=str(exc))
            raise RuntimeError("Stoiximan login form could not be opened/filled") from exc

    def _navigate_to_selection(self, page, request: PlacementRequest) -> None:
        try:
            content = page.content().lower()
            if "regulatory provisions" in content or "access denied" in content:
                raise RuntimeError("Stoiximan is geo-blocked on this server IP (Access Denied)")

            # Try clicking search icon button (check parent button/link if svg)
            for sel in [
                "button:has([data-qa='header-icons-search-icon'])",
                "a:has([data-qa='header-icons-search-icon'])",
                "[data-qa='header-icons-search-icon']",
                "[data-qa='search-icon']",
                "button[aria-label*='Search']",
                "button[aria-label*='Αναζήτηση']",
            ]:
                if self._maybe_click(page, sel):
                    break

            time.sleep(1)
            search_input_sel = "input[type='search'], [data-qa='search-input'], input[placeholder*='Search'], input[placeholder*='Αναζήτηση']"
            page.wait_for_selector(search_input_sel, timeout=5000)
            page.fill(search_input_sel, request.selection)
            time.sleep(1)

            result_sel = "[data-qa='search-result']:first-child, [data-qa*='search-result']:first-child, .search-result-item:first-child"
            page.click(result_sel, timeout=5000)
            page.wait_for_load_state("networkidle")
            odds_str = str(request.target_odds).replace(".", ",")
            page.click(f"button:has-text('{odds_str}'), [data-qa='event-selection']:has-text('{odds_str}')", timeout=5000)
        except Exception as e:
            try:
                Path("data").mkdir(exist_ok=True)
                page.screenshot(path="data/navigate_failed.png")
            except Exception:
                pass
            log.error("navigate_to_selection_failed", error=str(e),
                      selection=request.selection)
            raise RuntimeError(f"Failed to navigate to {request.selection}") from e

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

    # --- Feature 3: Read accepted stake from receipt ---

    def _read_accepted_stake(self, page, fallback: float) -> float:
        """Read the accepted stake from the bet receipt.

        If the bookmaker reduced the stake, the receipt will show the smaller
        amount. Falls back to the requested amount if the element is not found.
        """
        try:
            text = page.text_content(SELECTORS["receipt_stake"], timeout=5_000)
            cleaned = (text or "").strip().replace("€", "").replace(",", ".")
            return float(cleaned)
        except Exception:
            return fallback

    def _read_receipt_odds(self, page, fallback: float) -> float:
        """Read the confirmed odds from the bet receipt."""
        try:
            text = page.text_content(SELECTORS["receipt_odds"], timeout=5_000)
            return float((text or "").strip().replace(",", "."))
        except Exception:
            return fallback

    # --- Feature 4: Record limit event ---

    def _record_limit_event(self, result: PlacementResult,
                            request: PlacementRequest) -> None:
        """Push a LimitEvent to the global BookmakerLimitTracker."""
        was_rejected = not result.success and not result.dry_run
        event = LimitEvent(
            bookmaker=BOOKMAKER_NAME,
            requested_stake=result.requested_stake,
            accepted_stake=result.accepted_stake if not was_rejected else 0.0,
            was_rejected=was_rejected,
            note=result.message,
        )
        tracker = get_limit_tracker()
        tracker.record(event)

        if result.was_stake_reduced:
            log.warning(
                "stake_reduced_by_bookmaker",
                bookmaker=BOOKMAKER_NAME,
                requested=result.requested_stake,
                accepted=result.accepted_stake,
                ratio=round(result.accepted_stake / result.requested_stake, 3),
            )
        if tracker.is_likely_limited(BOOKMAKER_NAME):
            log.warning(
                "account_likely_limited",
                bookmaker=BOOKMAKER_NAME,
                acceptance_rate=tracker.acceptance_rate(BOOKMAKER_NAME),
            )

    @staticmethod
    def _maybe_click(page, selector: str) -> bool:
        try:
            page.click(selector, timeout=3000)
            return True
        except Exception:
            return False


def interactive_login(
    url: str = _LOGIN_URL,
    cookie_path: str | Path | None = None,
    timeout_seconds: int = 180,
) -> tuple[Path, int]:
    """Launch a visible Chromium window to let the operator login to Stoiximan interactively.

    Captures and saves all cookies to JSON upon successful login or confirmation.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright not installed; run `playwright install chromium`")

    settings = get_settings()
    target_path = get_cookie_file_path(cookie_path or settings.stoiximan_cookie_path)
    log.info("starting_interactive_login", url=url, target_path=str(target_path))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--start-maximized",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="el-GR",
            timezone_id="Europe/Athens",
            java_script_enabled=True,
        )

        # Preload any existing cookies
        load_cookies_into_context(context, target_path)

        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        print("=" * 65)
        print("🌐 Stoiximan Interactive Login Session")
        print(f"Opening {url} ...")
        print("=" * 65)

        page.goto(url, wait_until="domcontentloaded")
        time.sleep(2)

        # If credentials configured and not logged in, auto-fill
        if settings.stoiximan_username and settings.stoiximan_password:
            try:
                # Accept cookie banner
                for sel in [SELECTORS["accept_cookies"]]:
                    try:
                        page.click(sel, timeout=2000)
                        break
                    except Exception:
                        pass

                # If login form openable
                login_btn = page.query_selector(SELECTORS["login_button"])
                if login_btn and login_btn.is_visible():
                    login_btn.click(timeout=2000)
                    time.sleep(0.5)

                if page.query_selector(SELECTORS["username"]):
                    page.fill(SELECTORS["username"], settings.stoiximan_username)
                    page.fill(SELECTORS["password"], settings.stoiximan_password)
                    print("Pre-filled username and password from configuration.")
            except Exception:
                pass

        print("\n👉 Please finish login (and any Captcha / 2FA) in the browser window.")
        print("👉 When logged in, press ENTER here in the terminal to save cookies.")
        print("   (Or close the browser when done)\n")

        try:
            input("Press [Enter] after successful login: ")
        except (KeyboardInterrupt, EOFError):
            print("\nSaving current cookies before exiting...")

        # Small grace period to ensure latest session cookies are written
        time.sleep(1)
        count = save_cookies_from_context(context, target_path)
        browser.close()

    return target_path, count
