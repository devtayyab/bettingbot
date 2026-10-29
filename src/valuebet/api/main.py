"""FastAPI service: signals feed, approval workflow, manual placement, P&L, dashboard,
accounts management, reports, live config editing, and real-time SSE feed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import get_settings, reset_settings_cache, sync_dotenv_to_environ, write_dotenv_updates
from ..core.models import Sport
from ..db.init import ensure_schema_columns
from ..db.models import Bet, Signal
from ..db.repository import (
    add_account_note,
    all_bookmaker_limit_summaries,
    bookmaker_limit_summary,
    cancel_signal,
    classify_bet_type,
    create_account,
    delete_account_note,
    export_csv,
    export_xlsx,
    extract_final_score,
    extract_settled_at_iso,
    generate_report,
    get_account,
    get_account_activity,
    infer_event_name,
    list_account_notes,
    list_accounts,
    open_signals,
    pnl_summary,
    purge_demo_data,
    record_bet,
    save_limit_event,
    settle_pending_bets,
    update_account,
    update_signal,
)
from ..db.session import get_engine, session_scope
from ..logging import configure_logging, get_logger
from ..pipeline import run_scan
from ..placement.base import PlacementRequest
from .dashboard import DASHBOARD_HTML

configure_logging()
log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema_columns()
    s = get_settings()
    scheduler = None
    if s.enable_embedded_scheduler:
        import threading
        from apscheduler.schedulers.background import BackgroundScheduler
        from ..scheduler.run import _execute_bets, _scan_live, _scan_prematch, _settle_bets, _track_clv

        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(_scan_prematch, "interval", seconds=s.poll_interval_prematch, id="prematch")
        scheduler.add_job(_scan_live, "interval", seconds=s.poll_interval_live, id="live")
        scheduler.add_job(_execute_bets, "interval", seconds=30, id="executor")
        scheduler.add_job(_track_clv, "interval", minutes=5, id="clv_tracker")
        scheduler.add_job(_settle_bets, "interval", minutes=15, id="settlement")
        scheduler.start()
        app.state.scheduler = scheduler
        log.info(
            "background_scheduler_started",
            prematch_interval=s.poll_interval_prematch,
            live_interval=s.poll_interval_live,
        )

        timer = threading.Timer(2.0, _scan_prematch)
        timer.daemon = True
        timer.start()
    else:
        app.state.scheduler = None
        log.info("embedded_scheduler_disabled", msg="dedicated scheduler process handles polling")

    yield

    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
    try:
        from ..engine.executor import PlacementRouter
        PlacementRouter.shared().close_all()
    except Exception:
        pass


app = FastAPI(title="ValueBet Pilot", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def rewrite_api_prefix(request, call_next):
    if request.scope.get("path", "").startswith("/api/"):
        request.scope["path"] = request.scope["path"][4:]
    return await call_next(request)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SignalOut(BaseModel):
    id: int
    event_id: int
    event_name: str | None = None
    selection: str
    sport: str
    market_type: str
    fair_prob: float
    confirm_prob: float | None
    target_odds: float
    edge: float
    recommended_stake: float
    is_live: bool
    max_bet: float | None
    variables_complete: bool
    status: str
    detected_at: str | None
    event_start_time: str | None
    betfair_odds: float | None = None
    pinnacle_odds: float | None = None
    target_bookmaker: str = "stoiximan"
    bet_type: str | None = None
    home_odds: float | None = None
    draw_odds: float | None = None
    away_odds: float | None = None
    all_market_odds: dict[str, float] | None = None


class PlaceIn(BaseModel):
    slippage: float = 0.02


class SignalUpdateIn(BaseModel):
    recommended_stake: float | None = None
    max_bet: float | None = None
    is_live: bool | None = None
    variables_complete: bool | None = None
    note: str | None = None


class SettleIn(BaseModel):
    outcome: str  # won | lost | void
    final_score: str | None = None


class AccountIn(BaseModel):
    name: str
    bookmaker: str = "stoiximan"
    percentage_share: float = 1.0
    initial_deposit: float = 0.0


class AccountUpdateIn(BaseModel):
    name: str | None = None
    bookmaker: str | None = None
    percentage_share: float | None = None
    initial_deposit: float | None = None
    is_paused: bool | None = None


class AccountNoteIn(BaseModel):
    content: str


class ReportParams(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    sport: str | None = None
    status: str | None = None
    bookmaker: str | None = None
    include_dry_run: bool = True
    account_id: int | None = None


class ConfigUpdateIn(BaseModel):
    edge_threshold: float | None = None
    live_edge_threshold: float | None = None
    re_read_min_edge_delta: float | None = None
    favorite_min_prob: float | None = None
    odds_api_markets: str | None = None
    kelly_fraction: float | None = None
    max_stake: float | None = None
    bankroll: float | None = None
    max_event_exposure: float | None = None
    placement_dry_run: bool | None = None
    placement_require_approval: bool | None = None
    require_confirmation: bool | None = None
    enable_pinnacle_confirmation: bool | None = None
    require_score_sync: bool | None = None
    confirmation_tolerance: float | None = None
    min_odds: float | None = None
    max_odds: float | None = None
    min_total_matched: float | None = None
    min_liquidity: float | None = None
    max_spread: float | None = None
    strong_total_matched: float | None = None
    strong_liquidity: float | None = None
    good_spread: float | None = None
    depth_band: float | None = None
    betfair_ladder_levels: int | None = None
    min_market_quality: float | None = None
    enable_edge_buffer: bool | None = None
    slippage_buffer: float | None = None
    live_slippage_buffer: float | None = None
    quality_penalty_max: float | None = None
    unknown_spread_prob: float | None = None
    poll_interval_live: int | None = None
    poll_interval_prematch: int | None = None
    allow_demo_fallback: bool | None = None
    odds_api_fetch_lay: bool | None = None
    sport_overrides: dict[str, dict] | None = None


class OddsApiKeyIn(BaseModel):
    api_key: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_out(s: Signal) -> SignalOut:
    bf_odds = round(1.0 / s.fair_prob, 2) if s.fair_prob and s.fair_prob > 0 else None
    pin_odds = round(1.0 / s.confirm_prob, 2) if s.confirm_prob and s.confirm_prob > 0 else None
    btype = classify_bet_type(s.market_type, s.selection)
    ev_name = infer_event_name(s)

    all_market_odds: dict[str, float] | None = None
    try:
        if s.all_market_odds_json:
            all_market_odds = json.loads(s.all_market_odds_json)
    except Exception:
        pass

    h_odds = s.home_odds
    d_odds = s.draw_odds
    a_odds = s.away_odds

    if all_market_odds:
        draw_item = next(
            (item for item in all_market_odds.items() if item[0].strip().lower() in ("draw", "tie", "x")),
            None,
        )
        team_items = [
            item for item in all_market_odds.items() if item[0].strip().lower() not in ("draw", "tie", "x")
        ]
        if draw_item and len(team_items) >= 2:
            all_market_odds = {
                team_items[0][0]: team_items[0][1],
                draw_item[0]: draw_item[1],
                team_items[1][0]: team_items[1][1],
            }
            h_odds = team_items[0][1]
            d_odds = draw_item[1]
            a_odds = team_items[1][1]

    return SignalOut(
        id=s.id,
        event_id=s.event_id,
        event_name=ev_name,
        selection=s.selection,
        sport=s.sport,
        market_type=s.market_type,
        fair_prob=s.fair_prob,
        confirm_prob=s.confirm_prob,
        target_odds=s.target_odds,
        edge=s.edge,
        recommended_stake=s.recommended_stake,
        is_live=bool(s.is_live),
        max_bet=s.max_bet,
        variables_complete=bool(s.variables_complete),
        status=s.status,
        detected_at=s.detected_at.isoformat() if s.detected_at else None,
        event_start_time=s.event_start_time.isoformat() if s.event_start_time else None,
        betfair_odds=bf_odds,
        pinnacle_odds=pin_odds,
        target_bookmaker="stoiximan",
        bet_type=btype,
        home_odds=h_odds,
        draw_odds=d_odds,
        away_odds=a_odds,
        all_market_odds=all_market_odds,
    )


def _db_backend() -> str:
    try:
        return get_engine().url.get_backend_name()
    except Exception as exc:  # noqa: BLE001
        return f"unavailable: {exc}"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.on_event("shutdown")
def _close_placers() -> None:
    try:
        from ..engine.executor import PlacementRouter
        PlacementRouter.shared().close_all()
    except Exception as exc:  # noqa: BLE001
        log.warning("placer_shutdown_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    s = get_settings()
    from ..placement.session_store import get_cookie_status
    cookies = get_cookie_status()
    return {
        "status": "ok",
        "env": s.env,
        "dry_run": s.placement_dry_run,
        "require_approval": s.placement_require_approval,
        "demo_fallback": s.allow_demo_fallback,
        "has_session_cookies": cookies.get("has_cookies", False),
        "cookie_age_hours": cookies.get("age_hours"),
        "db_backend": _db_backend(),
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@app.get("/config")
def get_config() -> dict:
    """Return all editable configuration variables."""
    sync_dotenv_to_environ(force=True)
    s = get_settings()
    return {
        "edge_threshold": s.edge_threshold,
        "live_edge_threshold": s.live_edge_threshold,
        "re_read_min_edge_delta": s.re_read_min_edge_delta,
        "confirmation_tolerance": s.confirmation_tolerance,
        "max_live_latency_seconds": s.max_live_latency_seconds,
        "max_prematch_latency_seconds": s.max_prematch_latency_seconds,
        "min_total_matched": s.min_total_matched,
        "min_liquidity": s.min_liquidity,
        "max_spread": s.max_spread,
        "strong_total_matched": s.strong_total_matched,
        "strong_liquidity": s.strong_liquidity,
        "good_spread": s.good_spread,
        "depth_band": s.depth_band,
        "betfair_ladder_levels": s.betfair_ladder_levels,
        "min_market_quality": s.min_market_quality,
        "enable_edge_buffer": s.enable_edge_buffer,
        "slippage_buffer": s.slippage_buffer,
        "live_slippage_buffer": s.live_slippage_buffer,
        "quality_penalty_max": s.quality_penalty_max,
        "unknown_spread_prob": s.unknown_spread_prob,
        "require_confirmation": s.require_confirmation,
        "enable_pinnacle_confirmation": s.enable_pinnacle_confirmation,
        "require_score_sync": s.require_score_sync,
        "min_odds": s.min_odds,
        "max_odds": s.max_odds,
        "favorite_min_prob": s.favorite_min_prob,
        "kelly_fraction": s.kelly_fraction,
        "max_stake": s.max_stake,
        "max_event_exposure": s.max_event_exposure,
        "bankroll": s.bankroll,
        "poll_interval_live": s.poll_interval_live,
        "poll_interval_prematch": s.poll_interval_prematch,
        "placement_dry_run": s.placement_dry_run,
        "placement_require_approval": s.placement_require_approval,
        "allow_demo_fallback": s.allow_demo_fallback,
        "placement_bookmaker": s.placement_bookmaker,
        "sport_overrides": s.sport_overrides,
        "odds_api_fetch_lay": s.odds_api_fetch_lay,
        "odds_api_markets": s.odds_api_markets,
    }


@app.post("/cookies")
def upload_cookies(cookies: list[dict[str, Any]]) -> dict:
    """Upload raw JSON cookies from browser extension."""
    from ..placement.session_store import save_raw_cookie_list
    try:
        count = save_raw_cookie_list(cookies)
        return {"message": f"Successfully saved {count} cookies."}
    except Exception as exc:
        raise HTTPException(400, detail=str(exc))


@app.delete("/cookies")
def clear_cookies_legacy() -> dict:
    """Delete saved cookies (legacy endpoint)."""
    from ..placement.session_store import delete_cookie_file
    delete_cookie_file()
    return {"message": "Cookies deleted."}


@app.get("/odds-api/status")
async def get_odds_api_status() -> dict:
    """Check the current Odds API key status, remaining quota, and validity."""
    sync_dotenv_to_environ(force=True)
    s = get_settings()
    key = (s.the_odds_api_key or "").strip().strip('"').strip("'")
    if not key:
        return {
            "configured": False,
            "valid": False,
            "quota_exhausted": False,
            "masked_key": "",
            "message": "No Odds API key configured",
            "requests_remaining": None,
            "requests_used": None,
        }

    masked = (key[:6] + "..." + key[-4:]) if len(key) > 10 else "***"
    import httpx
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://api.the-odds-api.com/v4/sports/?apiKey={key}")
            rem = resp.headers.get("x-requests-remaining")
            used = resp.headers.get("x-requests-used")
            rem_int = int(rem) if rem and rem.isdigit() else None
            used_int = int(used) if used and used.isdigit() else used

            if resp.status_code == 200:
                if rem_int is not None and rem_int <= 0:
                    return {
                        "configured": True,
                        "valid": False,
                        "quota_exhausted": True,
                        "masked_key": masked,
                        "message": "Quota Exhausted (0 requests remaining) — Update key or upgrade plan to scan real games",
                        "requests_remaining": 0,
                        "requests_used": used_int,
                    }
                return {
                    "configured": True,
                    "valid": True,
                    "quota_exhausted": False,
                    "masked_key": masked,
                    "message": "API Key is Active & LIVE",
                    "requests_remaining": rem_int if rem_int is not None else rem,
                    "requests_used": used_int,
                }
            elif resp.status_code == 401:
                return {
                    "configured": True,
                    "valid": False,
                    "quota_exhausted": False,
                    "masked_key": masked,
                    "message": "Invalid or Expired API key (Unauthorized 401)",
                    "requests_remaining": 0,
                    "requests_used": None,
                }
            else:
                return {
                    "configured": True,
                    "valid": False,
                    "quota_exhausted": False,
                    "masked_key": masked,
                    "message": f"API returned status {resp.status_code}",
                    "requests_remaining": rem_int if rem_int is not None else rem,
                    "requests_used": used_int,
                }
    except Exception as exc:
        return {
            "configured": True,
            "valid": False,
            "quota_exhausted": False,
            "masked_key": masked,
            "message": f"Network check failed: {exc}",
            "requests_remaining": None,
            "requests_used": None,
        }


@app.post("/odds-api/update")
async def update_odds_api_key(body: OddsApiKeyIn) -> dict:
    """Validate and update the Odds API key, saving it to .env and runtime environment."""
    raw_key = body.api_key.strip()
    if "=" in raw_key and raw_key.upper().startswith("THE_ODDS_API_KEY"):
        raw_key = raw_key.split("=", 1)[1].strip()
    new_key = raw_key.strip('"').strip("'").strip()
    if not new_key:
        raise HTTPException(400, "API key cannot be empty")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://api.the-odds-api.com/v4/sports/?apiKey={new_key}")
            rem = resp.headers.get("x-requests-remaining")
            used = resp.headers.get("x-requests-used")
            if resp.status_code != 200:
                detail = "Invalid or expired key (Unauthorized 401)" if resp.status_code == 401 else f"Status {resp.status_code}"
                raise HTTPException(400, f"API key test failed: {detail}")
            if rem and rem.isdigit() and int(rem) <= 0:
                raise HTTPException(400, "This API key has 0 requests remaining (quota exhausted). Please enter a key with available quota.")
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"Failed to connect to The Odds API: {exc}")

    env_path = write_dotenv_updates({"THE_ODDS_API_KEY": new_key})

    # Clear any quota-exhausted circuit breakers and cached active-sport lists immediately
    from ..core import results as _results_mod
    from ..sources import the_odds_api as _odds_mod
    _odds_mod._QUOTA_EXHAUSTED_UNTIL = None
    _odds_mod._ACTIVE_KEYS_CACHE.clear()
    _results_mod._SCORES_QUOTA_EXHAUSTED_UNTIL = None

    # Trigger an immediate background pre-match Soccer scan so live matches appear right away
    import threading

    def _immediate_soccer_scan() -> None:
        try:
            run_scan(Sport.SOCCER, live=False)
        except Exception as scan_exc:
            log.warning("immediate_scan_after_key_update_failed", error=str(scan_exc))

    threading.Thread(target=_immediate_soccer_scan, daemon=True).start()

    log.info("odds_api_key_updated", remaining=rem, env_file=str(env_path))

    masked = (new_key[:6] + "..." + new_key[-4:]) if len(new_key) > 10 else "***"
    return {
        "success": True,
        "configured": True,
        "valid": True,
        "quota_exhausted": False,
        "masked_key": masked,
        "message": f"API key is valid and now LIVE! ({rem} requests remaining)",
        "requests_remaining": int(rem) if rem and rem.isdigit() else rem,
        "requests_used": int(used) if used and used.isdigit() else used,
    }


@app.patch("/config")
def patch_config(body: ConfigUpdateIn) -> dict:
    """Update editable config variables (writes to .env file and updates runtime)."""
    updates: dict[str, Any] = {
        k: v for k, v in body.model_dump().items() if v is not None
    }
    env_map: dict[str, str] = {}
    for k, v in updates.items():
        if isinstance(v, bool):
            env_map[k.upper()] = str(v).lower()
        elif isinstance(v, (dict, list)):
            env_map[k.upper()] = json.dumps(v)
        else:
            env_map[k.upper()] = str(v)

    if env_map:
        write_dotenv_updates(env_map)

    # Dynamically reschedule embedded APScheduler jobs if running
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        s = get_settings()
        if "poll_interval_prematch" in updates:
            try:
                sched.reschedule_job("prematch", trigger="interval", seconds=s.poll_interval_prematch)
            except Exception:
                pass
        if "poll_interval_live" in updates:
            try:
                sched.reschedule_job("live", trigger="interval", seconds=s.poll_interval_live)
            except Exception:
                pass

    log.info("config_updated", updates=list(updates.keys()))
    return {"updated": list(updates.keys()), "message": "Config saved and applied immediately"}


@app.delete("/demo-data")
def delete_demo_data() -> dict:
    """Purge fabricated mock/demo signals and bets (Team A, etc.) from the database."""
    with session_scope() as session:
        counts = purge_demo_data(session)
    return {
        "success": True,
        **counts,
        "message": (
            f"Removed {counts['signals_deleted']} demo signals and "
            f"{counts['bets_deleted']} demo bets."
        ),
    }


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


@app.post("/scan")
def trigger_scan(sport: str = "all", live: bool = False) -> dict:
    try:
        if sport == "all":
            total = 0
            for sp in Sport:
                try:
                    total += run_scan(sp, live=live)
                except Exception as e:
                    log.warning("scan_failed_for_sport", sport=sp.value, error=str(e))
            return {"new_signals": total}

        try:
            sport_enum = Sport(sport)
        except ValueError:
            total = 0
            for sp in Sport:
                try:
                    total += run_scan(sp, live=live)
                except Exception as e:
                    log.warning("scan_failed_for_sport", sport=sp.value, error=str(e))
            return {"new_signals": total}

        count = run_scan(sport_enum, live=live)
        return {"new_signals": count}
    except Exception as exc:
        log.error("scan_endpoint_failed", sport=sport, live=live, error=str(exc))
        return {"new_signals": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@app.get("/signals", response_model=list[SignalOut])
def list_signals(status: str | None = None) -> list[SignalOut]:
    with session_scope() as session:
        return [_to_out(s) for s in open_signals(session, status)]


@app.get("/signals/feed")
async def signals_feed() -> StreamingResponse:
    """Server-Sent Events endpoint — pushes new & updated signals in real time."""
    async def _generator() -> AsyncIterator[str]:
        last_fingerprint: set[tuple] | None = None
        while True:
            try:
                with session_scope() as session:
                    sigs = open_signals(session)
                    fingerprint = {
                        (s.id, round(s.edge, 4), round(s.target_odds, 3), s.status, round(s.recommended_stake, 2))
                        for s in sigs
                    }
                    if last_fingerprint is None or fingerprint != last_fingerprint:
                        payload = json.dumps([_to_out(s).model_dump() for s in sigs])
                        yield f"data: {payload}\n\n"
                        last_fingerprint = fingerprint
            except Exception as exc:
                log.warning("sse_error", error=str(exc))
                yield f"event: error\ndata: {str(exc)}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/signals/{signal_id}/approve")
def approve_signal(signal_id: int) -> dict:
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            log.warning("approve_signal_not_found", signal_id=signal_id)
            raise HTTPException(404, "signal not found")
        if sig.status == "approved":
            return {"id": signal_id, "status": "approved"}
        if sig.status != "detected":
            log.warning("approve_rejected_bad_state", signal_id=signal_id, status=sig.status)
            raise HTTPException(409, f"signal is '{sig.status}', cannot approve")
        sig.status = "approved"
        log.info("signal_approved", signal_id=signal_id, selection=sig.selection,
                 target_odds=sig.target_odds, recommended_stake=sig.recommended_stake,
                 edge=round(sig.edge, 4))
    return {"id": signal_id, "status": "approved"}


@app.post("/signals/{signal_id}/reject")
def reject_signal(signal_id: int) -> dict:
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            log.warning("reject_signal_not_found", signal_id=signal_id)
            raise HTTPException(404, "signal not found")
        previous = sig.status
        sig.status = "rejected"
        log.info("signal_rejected", signal_id=signal_id, selection=sig.selection,
                 previous_status=previous)
    return {"id": signal_id, "status": "rejected"}


@app.post("/signals/{signal_id}/cancel")
def cancel_signal_endpoint(signal_id: int) -> dict:
    """Manually abort/cancel a signal and void its associated bet."""
    with session_scope() as session:
        sig = cancel_signal(session, signal_id)
        if not sig:
            raise HTTPException(404, "signal not found")
    return {"id": signal_id, "status": "cancelled"}


@app.patch("/signals/{signal_id}")
def update_signal_endpoint(signal_id: int, body: SignalUpdateIn) -> dict:
    """Update mutable fields on a signal (stake, max_bet, live flag, variables, note)."""
    with session_scope() as session:
        sig = update_signal(
            session,
            signal_id,
            recommended_stake=body.recommended_stake,
            max_bet=body.max_bet,
            is_live=body.is_live,
            variables_complete=body.variables_complete,
            note=body.note,
        )
        if not sig:
            raise HTTPException(404, "signal not found")
        return _to_out(sig).model_dump()


@app.post("/signals/{signal_id}/place")
def place_bet(signal_id: int, body: PlaceIn | None = None) -> dict:
    """Place the bet for an (approved) signal on Stoiximan."""
    if body is None:
        body = PlaceIn()
    s = get_settings()
    with session_scope() as session:
        sig = session.get(Signal, signal_id)
        if not sig:
            raise HTTPException(404, "signal not found")
        if s.placement_require_approval and sig.status != "approved":
            raise HTTPException(409, "approval required before placement")
        if sig.status in ("placed", "unconfirmed"):
            raise HTTPException(409, f"signal is '{sig.status}', refusing to place again")

        min_odds = round(sig.target_odds * (1 - body.slippage), 2)
        log.info(
            "placement_requested",
            signal_id=signal_id,
            selection=sig.selection,
            target_odds=sig.target_odds,
            min_odds=min_odds,
            stake=sig.recommended_stake,
            dry_run=s.placement_dry_run,
        )
        request = PlacementRequest(
            event_id=str(sig.event_id), market_type=sig.market_type,
            selection=sig.selection, target_odds=sig.target_odds,
            stake=sig.recommended_stake, min_odds=min_odds,
        )
        from ..engine.executor import PlacementRouter
        from ..placement.base import PlacementStatus

        placer = PlacementRouter.shared().get_placer(s.placement_bookmaker)
        if placer is None:
            log.error("no_placer_configured", bookmaker=s.placement_bookmaker)
            raise HTTPException(501, f"no placer configured for '{s.placement_bookmaker}'")
        result = placer.place(request)
        log.info(
            "placement_completed",
            signal_id=signal_id,
            selection=sig.selection,
            status=result.status.value if result.status else "unknown",
            placed_on_platform=result.verified_on_platform,
            placed_odds=result.placed_odds,
            requested_stake=result.requested_stake,
            accepted_stake=result.accepted_stake,
            stake_reduced=result.was_stake_reduced,
            message=result.message,
        )

        def payload(bet_id: int | None) -> dict:
            return {
                "signal_id": signal_id,
                "bet_id": bet_id,
                "success": result.success,
                "status": result.status.value if result.status else "unknown",
                "placed_on_platform": result.verified_on_platform,
                "needs_manual_check": result.needs_manual_check,
                "dry_run": result.dry_run,
                "message": result.message,
                "placed_odds": result.placed_odds,
                "requested_stake": result.requested_stake,
                "accepted_stake": result.accepted_stake,
                "stake_reduced": result.was_stake_reduced,
            }

        if result.status == PlacementStatus.DRY_RUN:
            bet = record_bet(
                session, sig,
                placed_odds=result.placed_odds or sig.target_odds,
                stake=result.accepted_stake,
                requested_stake=result.requested_stake,
                dry_run=True, note=result.message,
                bookmaker=result.bookmaker, verified=False,
            )
            return payload(bet.id)

        if not result.is_real_bet:
            if result.status == PlacementStatus.REJECTED:
                save_limit_event(
                    session, bookmaker=result.bookmaker,
                    requested_stake=result.requested_stake,
                    accepted_stake=0.0, was_rejected=True, note=result.message,
                )
            return payload(None)

        bet = record_bet(
            session, sig,
            placed_odds=result.placed_odds or sig.target_odds,
            stake=result.accepted_stake,
            requested_stake=result.requested_stake,
            dry_run=False, note=result.message,
            bookmaker=result.bookmaker,
            verified=result.verified_on_platform,
        )
        save_limit_event(
            session,
            bookmaker=result.bookmaker,
            requested_stake=result.requested_stake,
            accepted_stake=result.accepted_stake,
            was_rejected=False,
            note=result.message,
        )
        if result.needs_manual_check:
            log.error("placement_unconfirmed", signal_id=signal_id,
                      selection=sig.selection, stake=result.accepted_stake,
                      msg=result.message)
        return payload(bet.id)


# ---------------------------------------------------------------------------
# Bets (settlement & results)
# ---------------------------------------------------------------------------


@app.post("/bets/{bet_id}/settle")
def settle_bet(bet_id: int, body: SettleIn) -> dict:
    if body.outcome not in {"won", "lost", "void"}:
        raise HTTPException(400, "outcome must be won|lost|void")
    with session_scope() as session:
        bet = session.get(Bet, bet_id)
        if not bet:
            raise HTTPException(404, "bet not found")
        previous = bet.outcome
        bet.outcome = body.outcome
        if body.outcome == "won":
            bet.profit = round(bet.stake * (bet.placed_odds - 1), 2)
        elif body.outcome == "lost":
            bet.profit = -bet.stake
        else:
            bet.profit = 0.0
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        bet.settled_at = now_dt
        if body.final_score:
            bet.final_score = body.final_score
        clean_note = bet.note or ""
        if "settled_at:" in clean_note:
            clean_note = re.sub(r"settled_at:\S+\s*", "", clean_note).strip()
        bet.note = f"settled_at:{now_iso} {clean_note}".strip()[:510]
        log.info("bet_settled", bet_id=bet_id, selection=bet.selection,
                 previous_outcome=previous, outcome=body.outcome,
                 stake=bet.stake, placed_odds=bet.placed_odds,
                 profit=bet.profit, dry_run=bet.dry_run)
        return {
            "bet_id": bet_id,
            "outcome": body.outcome,
            "profit": bet.profit,
            "final_score": bet.final_score,
            "settled_at": now_iso,
        }


@app.post("/bets/settle-all")
def trigger_settle_all() -> dict:
    """Trigger automatic settlement of pending bets against official The Odds API match scores."""
    with session_scope() as session:
        settled = settle_pending_bets(session, force_check_all=True)
    return {
        "settled": settled,
        "message": (
            f"Automatically settled {settled} completed match(es) from official scores."
            if settled > 0
            else "Checked official scores — no newly completed matches ready to settle yet."
        ),
    }


@app.get("/results")
def get_results(
    outcome: str | None = None,
    sport: str | None = None,
    include_pending: bool = False,
    limit: int = 200,
) -> list[dict]:
    """Return bets with full match name, actual bet, match played time, official score, and settlement time."""
    with session_scope() as session:
        from sqlalchemy import select
        stmt = (
            select(Bet, Signal)
            .join(Signal, Bet.signal_id == Signal.id)
            .order_by(Bet.placed_at.desc())
        )
        if outcome and outcome != "all":
            stmt = stmt.where(Bet.outcome == outcome)
        elif not include_pending:
            stmt = stmt.where(Bet.outcome.in_(["won", "lost", "void"]))
        if sport and sport != "all":
            stmt = stmt.where(Signal.sport == sport)
        stmt = stmt.limit(limit)

        rows = session.execute(stmt).all()
        results = []
        for bet, sig in rows:
            settled_time_str = extract_settled_at_iso(bet)
            score_str = extract_final_score(bet)
            ev_name = infer_event_name(sig)

            profit_val = bet.profit if bet.profit is not None else 0.0
            if bet.outcome == "won":
                profit_pct = round(((bet.placed_odds - 1) * 100), 1)
            elif bet.outcome == "lost":
                profit_pct = -100.0
            else:
                profit_pct = 0.0

            bf_odds = round(1.0 / sig.fair_prob, 2) if sig.fair_prob and sig.fair_prob > 0 else None
            pin_odds = round(1.0 / sig.confirm_prob, 2) if sig.confirm_prob and sig.confirm_prob > 0 else None
            btype = classify_bet_type(sig.market_type, bet.selection)

            results.append({
                "bet_id": bet.id,
                "signal_id": sig.id,
                "event_name": ev_name,
                "selection": bet.selection,
                "sport": sig.sport,
                "market_type": sig.market_type,
                "bet_type": btype,
                "is_live": bool(sig.is_live),
                "placed_odds": bet.placed_odds,
                "stake": bet.stake,
                "outcome": bet.outcome,
                "final_score": score_str,
                "profit": profit_val,
                "profit_pct": profit_pct,
                "edge": sig.edge,
                "fair_prob": sig.fair_prob,
                "betfair_odds": bf_odds,
                "pinnacle_odds": pin_odds,
                "accuracy": round(sig.fair_prob * 100, 1),
                "event_start_time": sig.event_start_time.isoformat() if sig.event_start_time else None,
                "signal_generated_at": sig.detected_at.isoformat() if sig.detected_at else None,
                "bet_placed_at": bet.placed_at.isoformat() if bet.placed_at else None,
                "end_time": settled_time_str,
                "bookmaker": bet.book or "stoiximan",
                "dry_run": bet.dry_run,
            })
        return results


@app.get("/pnl")
def pnl() -> dict:
    with session_scope() as session:
        return pnl_summary(session)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@app.get("/accounts")
def get_accounts() -> list[dict]:
    with session_scope() as session:
        accounts = list_accounts(session)
        result = []
        for acc in accounts:
            activity = get_account_activity(session, acc.id)
            result.append({
                "id": acc.id,
                "name": acc.name,
                "bookmaker": acc.bookmaker,
                "percentage_share": acc.percentage_share,
                "initial_deposit": acc.initial_deposit,
                "is_paused": acc.is_paused,
                "created_at": acc.created_at.isoformat() if acc.created_at else None,
                "total_bets": activity.get("total_bets", 0),
                "net_profit": activity.get("net_profit", 0.0),
                "roi": activity.get("roi", 0.0),
            })
        return result


@app.post("/accounts")
def create_account_endpoint(body: AccountIn) -> dict:
    with session_scope() as session:
        acc = create_account(
            session,
            name=body.name,
            bookmaker=body.bookmaker,
            percentage_share=body.percentage_share,
            initial_deposit=body.initial_deposit,
        )
        return {
            "id": acc.id,
            "name": acc.name,
            "bookmaker": acc.bookmaker,
            "percentage_share": acc.percentage_share,
            "initial_deposit": acc.initial_deposit,
            "is_paused": acc.is_paused,
        }


@app.get("/accounts/{account_id}")
def get_account_detail(account_id: int) -> dict:
    with session_scope() as session:
        acc = get_account(session, account_id)
        if not acc:
            raise HTTPException(404, "account not found")
        return get_account_activity(session, account_id)


@app.patch("/accounts/{account_id}")
def update_account_endpoint(account_id: int, body: AccountUpdateIn) -> dict:
    with session_scope() as session:
        acc = update_account(
            session,
            account_id,
            name=body.name,
            bookmaker=body.bookmaker,
            percentage_share=body.percentage_share,
            initial_deposit=body.initial_deposit,
            is_paused=body.is_paused,
        )
        if not acc:
            raise HTTPException(404, "account not found")
        return {
            "id": acc.id,
            "name": acc.name,
            "bookmaker": acc.bookmaker,
            "percentage_share": acc.percentage_share,
            "initial_deposit": acc.initial_deposit,
            "is_paused": acc.is_paused,
        }


@app.get("/accounts/{account_id}/notes")
def get_notes(account_id: int) -> list[dict]:
    with session_scope() as session:
        notes = list_account_notes(session, account_id)
        return [{"id": n.id, "content": n.content,
                 "created_at": n.created_at.isoformat()} for n in notes]


@app.post("/accounts/{account_id}/notes")
def add_note(account_id: int, body: AccountNoteIn) -> dict:
    with session_scope() as session:
        note = add_account_note(session, account_id, body.content)
        if not note:
            raise HTTPException(404, "account not found")
        return {"id": note.id, "content": note.content,
                "created_at": note.created_at.isoformat()}


@app.delete("/accounts/{account_id}/notes/{note_id}")
def delete_note(account_id: int, note_id: int) -> dict:
    with session_scope() as session:
        ok = delete_account_note(session, note_id)
        if not ok:
            raise HTTPException(404, "note not found")
        return {"deleted": note_id}


@app.get("/accounts/{account_id}/activity")
def account_activity(account_id: int) -> dict:
    with session_scope() as session:
        acc = get_account(session, account_id)
        if not acc:
            raise HTTPException(404, "account not found")
        return get_account_activity(session, account_id)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@app.post("/reports/generate")
def reports_generate(body: ReportParams) -> list[dict]:
    with session_scope() as session:
        return generate_report(
            session,
            date_from=_parse_dt(body.date_from),
            date_to=_parse_dt(body.date_to),
            sport=body.sport or None,
            status=body.status or None,
            bookmaker=body.bookmaker or None,
            include_dry_run=body.include_dry_run,
            account_id=body.account_id,
        )


@app.get("/reports/export")
def reports_export(
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    date_from: str | None = None,
    date_to: str | None = None,
    sport: str | None = None,
    status: str | None = None,
    bookmaker: str | None = None,
    include_dry_run: bool = True,
    account_id: int | None = None,
) -> Response:
    kwargs = dict(
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
        sport=sport,
        status=status,
        bookmaker=bookmaker,
        include_dry_run=include_dry_run,
        account_id=account_id,
    )
    with session_scope() as session:
        if format == "xlsx":
            data = export_xlsx(session, **kwargs)
            filename = f"betting_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return Response(
                content=data,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        else:
            data = export_csv(session, **kwargs)
            filename = f"betting_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            return Response(
                content=data,
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )


# ---------------------------------------------------------------------------
# Bookmaker Limit / Account Health endpoints
# ---------------------------------------------------------------------------


@app.get("/limits")
def get_all_limits() -> list[dict]:
    with session_scope() as session:
        return all_bookmaker_limit_summaries(session)


@app.get("/limits/{bookmaker}")
def get_bookmaker_limits(bookmaker: str, last_n: int = 50) -> dict:
    with session_scope() as session:
        return bookmaker_limit_summary(session, bookmaker, last_n)


# ---------------------------------------------------------------------------
# Cookie management
# ---------------------------------------------------------------------------


@app.get("/cookie-status")
def cookie_status() -> dict:
    from ..placement.session_store import get_cookie_status
    return get_cookie_status()


class CookieImport(BaseModel):
    cookies: list[dict]


@app.post("/import-cookies")
def import_cookies(body: CookieImport) -> dict:
    if not body.cookies:
        raise HTTPException(400, "cookies list is empty")
    from ..placement.session_store import save_raw_cookie_list
    try:
        count = save_raw_cookie_list(body.cookies)
        log.info("cookies_imported", count=count)
        return {
            "success": True,
            "imported": count,
            "message": f"✅ {count} cookies imported and saved to JSON! Bot will reuse this session.",
        }
    except Exception as exc:
        raise HTTPException(400, f"Failed to save cookies: {exc}")


@app.get("/export-cookies")
def export_cookies() -> list[dict]:
    from ..placement.session_store import read_cookie_json
    return read_cookie_json()


@app.delete("/import-cookies")
def clear_cookies() -> dict:
    from ..placement.session_store import delete_cookie_file
    deleted = delete_cookie_file()
    log.warning("cookies_cleared", deleted=deleted,
                msg="placement will re-login on the next attempt")
    if deleted:
        return {"success": True, "message": "Cookies cleared from JSON file."}
    return {"success": True, "message": "No cookie file found to delete."}


# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------


@app.get("/debug/screenshot")
def debug_screenshot():
    for p in [Path("data/navigate_failed.png"), Path("data/stoiximan_blocked.png")]:
        if p.exists():
            return FileResponse(p, media_type="image/png")
    raise HTTPException(404, "No screenshot available yet")


# ---------------------------------------------------------------------------
# Frontend static files & Root dashboard
# ---------------------------------------------------------------------------

# Prefer /opt/frontend-dist built inside the Docker image so a host `.:/app`
# volume mount cannot shadow the newly built React assets with an outdated copy.
_DOCKER_FRONTEND_DIST = Path("/opt/frontend-dist")
_LOCAL_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
if (_DOCKER_FRONTEND_DIST / "index.html").exists():
    _FRONTEND_DIST = _DOCKER_FRONTEND_DIST
elif (_LOCAL_FRONTEND_DIST / "index.html").exists():
    _FRONTEND_DIST = _LOCAL_FRONTEND_DIST
else:
    _FRONTEND_DIST = Path("frontend/dist")

if (_FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")


@app.get("/favicon.svg")
def favicon_svg():
    fav = _FRONTEND_DIST / "favicon.svg"
    if fav.exists():
        return FileResponse(fav)
    raise HTTPException(404)


@app.get("/icons.svg")
def icons():
    ic = _FRONTEND_DIST / "icons.svg"
    if ic.exists():
        return FileResponse(ic)
    raise HTTPException(404)


@app.get("/legacy", response_class=HTMLResponse)
def legacy_dashboard() -> str:
    return DASHBOARD_HTML


@app.get("/")
def dashboard():
    index_file = _FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse(DASHBOARD_HTML)
