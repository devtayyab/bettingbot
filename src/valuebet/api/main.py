"""FastAPI service: signals feed, approval workflow, manual placement, P&L, dashboard,
accounts management, reports, live config editing, and real-time SSE feed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import get_settings
from ..core.models import Sport
from ..db.models import Account, AccountNote, Bet, Signal
from ..db.repository import (
    add_account_note,
    all_bookmaker_limit_summaries,
    bookmaker_limit_summary,
    cancel_signal,
    create_account,
    delete_account_note,
    export_csv,
    export_xlsx,
    generate_report,
    get_account,
    get_account_activity,
    list_account_notes,
    list_accounts,
    open_signals,
    pnl_summary,
    record_bet,
    save_limit_event,
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
app = FastAPI(title="ValueBet Pilot", version="0.2.0")


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


class PlaceIn(BaseModel):
    # Reject if live odds drop more than this fraction below detected odds.
    slippage: float = 0.02


class SignalUpdateIn(BaseModel):
    recommended_stake: float | None = None
    max_bet: float | None = None
    is_live: bool | None = None
    variables_complete: bool | None = None
    note: str | None = None


class SettleIn(BaseModel):
    outcome: str  # won | lost | void


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
    date_from: str | None = None   # ISO date string
    date_to: str | None = None
    sport: str | None = None
    status: str | None = None
    bookmaker: str | None = None
    include_dry_run: bool = True


class ConfigUpdateIn(BaseModel):
    edge_threshold: float | None = None
    live_edge_threshold: float | None = None
    kelly_fraction: float | None = None
    max_stake: float | None = None
    bankroll: float | None = None
    max_event_exposure: float | None = None
    placement_dry_run: bool | None = None
    placement_require_approval: bool | None = None
    require_confirmation: bool | None = None
    poll_interval_live: int | None = None
    poll_interval_prematch: int | None = None
    allow_demo_fallback: bool | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_out(s: Signal) -> SignalOut:
    return SignalOut(
        id=s.id,
        event_id=s.event_id,
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
    )


def _db_backend() -> str:
    """Report the database actually in use, so a dev SQLite fallback is visible."""
    try:
        return get_engine().url.get_backend_name()
    except Exception as exc:  # noqa: BLE001
        return f"unavailable: {exc}"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
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
    """Close the shared browser sessions so uvicorn reloads don't leak Chromium."""
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
    s = get_settings()
    return {
        "edge_threshold": s.edge_threshold,
        "live_edge_threshold": s.live_edge_threshold,
        "confirmation_tolerance": s.confirmation_tolerance,
        "max_live_latency_seconds": s.max_live_latency_seconds,
        "max_prematch_latency_seconds": s.max_prematch_latency_seconds,
        "min_total_matched": s.min_total_matched,
        "min_liquidity": s.min_liquidity,
        "max_spread": s.max_spread,
        "require_confirmation": s.require_confirmation,
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
def clear_cookies() -> dict:
    """Delete saved cookies."""
    from ..placement.session_store import delete_cookie_file
    delete_cookie_file()
    return {"message": "Cookies deleted."}

@app.patch("/config")
def patch_config(body: ConfigUpdateIn) -> dict:
    """Update editable config variables (writes to .env file)."""
    env_path = Path(".env")
    if not env_path.exists():
        raise HTTPException(404, ".env file not found — create it first")

    lines = env_path.read_text().splitlines()
    updates: dict[str, Any] = {
        k: v for k, v in body.model_dump().items() if v is not None
    }
    env_map = {k.upper(): str(v).lower() if isinstance(v, bool) else str(v)
               for k, v in updates.items()}

    new_lines = []
    updated_keys = set()
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in env_map:
                new_lines.append(f"{key}={env_map[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys that weren't already in .env
    for key, val in env_map.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n")
    # Also apply to running process environment immediately
    for key, val in env_map.items():
        os.environ[key] = val
    log.info("config_updated", updates=list(updates.keys()))
    return {"updated": list(updates.keys()), "message": "Config saved and applied"}


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
    """Server-Sent Events endpoint — pushes new signals in real time.

    Clients connect once and receive a continuous stream. Each event is a JSON
    payload of the latest signal list. The stream never ends; failed bets are
    filtered out on the client side (status=failed/cancelled).
    """
    async def _generator() -> AsyncIterator[str]:
        last_ids: set[int] = set()
        while True:
            try:
                with session_scope() as session:
                    sigs = open_signals(session)
                    # Send full list on first connection, then only deltas
                    current_ids = {s.id for s in sigs}
                    new_sigs = [_to_out(s) for s in sigs if s.id not in last_ids]
                    if new_sigs or not last_ids:
                        payload = json.dumps([s.model_dump() for s in [_to_out(s) for s in sigs]])
                        yield f"data: {payload}\n\n"
                        last_ids = current_ids
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
# Bets (settlement)
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
        log.info("bet_settled", bet_id=bet_id, selection=bet.selection,
                 previous_outcome=previous, outcome=body.outcome,
                 stake=bet.stake, placed_odds=bet.placed_odds,
                 profit=bet.profit, dry_run=bet.dry_run)
        return {"bet_id": bet_id, "outcome": body.outcome, "profit": bet.profit}


@app.get("/pnl")
def pnl() -> dict:
    with session_scope() as session:
        return pnl_summary(session)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@app.get("/accounts")
def get_accounts() -> list[dict]:
    """List all customer accounts."""
    with session_scope() as session:
        accounts = list_accounts(session)
        result = []
        for acc in accounts:
            # Compute quick stats for this account
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
    """Create a new customer account."""
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
    """Full account detail including bets and activity."""
    with session_scope() as session:
        acc = get_account(session, account_id)
        if not acc:
            raise HTTPException(404, "account not found")
        return get_account_activity(session, account_id)


@app.patch("/accounts/{account_id}")
def update_account_endpoint(account_id: int, body: AccountUpdateIn) -> dict:
    """Update a customer account (including pause/unpause)."""
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
    """Generate a report with optional date/sport/status filters."""
    with session_scope() as session:
        return generate_report(
            session,
            date_from=_parse_dt(body.date_from),
            date_to=_parse_dt(body.date_to),
            sport=body.sport or None,
            status=body.status or None,
            bookmaker=body.bookmaker or None,
            include_dry_run=body.include_dry_run,
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
) -> Response:
    """Export report as CSV or Excel with human-friendly column names."""
    kwargs = dict(
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
        sport=sport,
        status=status,
        bookmaker=bookmaker,
        include_dry_run=include_dry_run,
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
# Feature 4: Bookmaker Limit / Account Health endpoints
# ---------------------------------------------------------------------------


@app.get("/limits")
def get_all_limits() -> list[dict]:
    """Return stake-acceptance summary for all bookmakers."""
    with session_scope() as session:
        return all_bookmaker_limit_summaries(session)


@app.get("/limits/{bookmaker}")
def get_bookmaker_limits(bookmaker: str, last_n: int = 50) -> dict:
    """Return stake-acceptance summary for one bookmaker."""
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
    from fastapi.responses import FileResponse
    for p in [Path("data/navigate_failed.png"), Path("data/stoiximan_blocked.png")]:
        if p.exists():
            return FileResponse(p, media_type="image/png")
    raise HTTPException(404, "No screenshot available yet")


# ---------------------------------------------------------------------------
# Frontend static files & Root dashboard
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
if not _FRONTEND_DIST.exists():
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
