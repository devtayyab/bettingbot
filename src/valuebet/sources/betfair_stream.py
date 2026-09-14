"""Betfair Stream API Integration.

Maintains a persistent WebSocket connection to Betfair for live market data.
Updates a local thread-safe cache of MarketSnapshots, so the ValueEngine can
poll the cache instantaneously without network latency.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

try:
    from betfairlightweight.streaming.cache import MarketBookCache  # type: ignore[import-untyped]
except ImportError:
    MarketBookCache = Any  # type: ignore

from ..config import get_settings
from ..core.models import MarketSnapshot, MarketStatus, Quote, Sport
from ..logging import get_logger
from .base import OddsSource

log = get_logger("source.betfair_stream")

_EVENT_TYPE: dict[Sport, str] = {
    Sport.SOCCER: "1",
    Sport.TENNIS: "2",
    Sport.GOLF: "3",
    Sport.CRICKET: "4",
    Sport.RUGBY_UNION: "5",
    Sport.BOXING: "6",
    Sport.AMERICAN_FOOTBALL: "6423",
    Sport.BASEBALL: "7511",
    Sport.BASKETBALL: "7522",
    Sport.ICE_HOCKEY: "7524",
    Sport.RUGBY_LEAGUE: "1477",
    Sport.MMA: "26420387",
    Sport.VOLLEYBALL: "998917",
    Sport.HANDBALL: "468328",
    Sport.DARTS: "3503",
    Sport.ESPORTS: "27454571",
    Sport.TABLE_TENNIS: "2593174",
}


class BetfairStreamSource:
    name: str = "betfair_stream"

    def __init__(self, fallback_source: OddsSource) -> None:
        self.fallback = fallback_source
        self._client: Any = None
        self._stream: Any = None
        self._listener: Any = None
        self._cache_lock = threading.Lock()
        self._market_caches: dict[str, MarketBookCache] = {}
        self._is_running = False
        self._sync_thread: threading.Thread | None = None
        self._stream_thread: threading.Thread | None = None

    def _ensure_client(self) -> Any:
        """Obtain an authenticated betfairlightweight APIClient."""
        if self._client is not None:
            return self._client

        if hasattr(self.fallback, "_ensure_login"):
            try:
                self._client = self.fallback._ensure_login()
                return self._client
            except Exception as e:
                log.warning("betfair_stream_fallback_login_failed", error=str(e))

        try:
            import betfairlightweight  # type: ignore[import-untyped]
        except ImportError:
            log.warning("betfairlightweight_not_installed")
            return None

        s = get_settings()
        if not (s.betfair_username and s.betfair_app_key):
            log.debug("betfair_credentials_not_configured_for_stream")
            return None

        certs = (s.betfair_cert_path, s.betfair_key_path) if s.betfair_cert_path else None
        client = betfairlightweight.APIClient(
            username=s.betfair_username,
            password=s.betfair_password,
            app_key=s.betfair_app_key,
            certs=certs,
        )

        try:
            if not client.session_token:
                if s.betfair_cert_path:
                    client.login()
                else:
                    client.login_interactive()
            else:
                try:
                    client.keep_alive()
                except Exception:
                    client.login_interactive()
            self._client = client
            return self._client
        except Exception as exc:
            log.warning("betfair_stream_login_failed", error=str(exc))
            return None

    def start(self, sport: Sport) -> None:
        """Start the background streaming thread for live markets."""
        if self._is_running:
            return

        client = self._ensure_client()
        if client is None:
            log.info("betfair_stream_skipped_no_client")
            return

        try:
            import betfairlightweight  # type: ignore[import-untyped]
            from betfairlightweight import filters  # type: ignore[import-untyped]

            self._listener = betfairlightweight.StreamListener(max_latency=3.0)
            self._stream = client.streaming.create_stream(listener=self._listener)

            # Start socket read loop on a background thread so it doesn't block
            def _stream_worker() -> None:
                try:
                    self._stream.start()
                except Exception as stream_err:
                    log.error("betfair_stream_socket_error", error=str(stream_err))
                    self._is_running = False

            self._stream_thread = threading.Thread(target=_stream_worker, daemon=True)
            self._stream_thread.start()

            # Wait briefly for socket connection to establish
            for _ in range(30):
                if getattr(self._stream, "running", False):
                    break
                time.sleep(0.1)

            event_type_id = _EVENT_TYPE.get(sport, "1")
            market_filter = filters.streaming_market_filter(
                event_type_ids=[event_type_id],
                market_types=["MATCH_ODDS"],
            )
            market_data_filter = filters.streaming_market_data_filter(
                fields=["EX_BEST_OFFERS", "EX_MARKET_DEF"], ladder_levels=1
            )

            log.info("betfair_stream_subscribing", sport=sport.value)
            self._stream.subscribe_to_markets(
                market_filter=market_filter,
                market_data_filter=market_data_filter,
                initial_clk=getattr(self._listener, "initial_clk", None),
                clk=getattr(self._listener, "clk", None),
            )
            self._is_running = True

            # Start thread to periodically sync stream caches to local cache
            self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
            self._sync_thread.start()
        except Exception as e:
            log.error("betfair_stream_start_failed", error=str(e))
            self._is_running = False

    def _sync_loop(self) -> None:
        while self._is_running:
            try:
                if self._listener and getattr(self._listener, "stream", None):
                    caches = getattr(self._listener.stream, "_caches", None)
                    if caches is not None:
                        with self._cache_lock:
                            self._market_caches = dict(caches)
            except Exception as e:
                log.error("stream_sync_error", error=str(e))
            time.sleep(1.0)

    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        """Return the latest snapshots from the in-memory stream cache."""
        if not self._is_running or not self._market_caches:
            log.debug("stream_inactive_falling_back_to_rest")
            return self.fallback.fetch_markets(sport, live)

        snapshots: list[MarketSnapshot] = []
        now = datetime.now(UTC)

        with self._cache_lock:
            caches = list(self._market_caches.values())

        for cache in caches:
            market_def = getattr(cache, "_market_definition_resource", None) or getattr(
                cache, "market_definition", None
            )
            if not market_def:
                continue

            # Suspension Detection
            status = getattr(market_def, "status", None) or (
                market_def.get("status") if isinstance(market_def, dict) else None
            )
            if status == "SUSPENDED":
                log.debug("market_suspended_skipping", market_id=getattr(cache, "market_id", ""))
                continue

            # Filter by in-play status if requested
            is_in_play = (
                getattr(market_def, "in_play", None)
                if hasattr(market_def, "in_play")
                else (market_def.get("inPlay", False) if isinstance(market_def, dict) else False)
            )
            if is_in_play is None:
                is_in_play = bool(getattr(cache, "_definition_in_play", False))

            if live and not is_in_play:
                continue
            if not live and is_in_play:
                continue

            quotes: list[Quote] = []
            runners = getattr(cache, "runners", [])
            for runner in runners:
                runner_status = getattr(runner, "_definition_status", None) or (
                    runner.definition.get("status")
                    if hasattr(runner, "definition") and isinstance(runner.definition, dict)
                    else getattr(runner, "status", None)
                )
                if runner_status != "ACTIVE":
                    continue

                atb = (
                    runner.available_to_back.serialised
                    if hasattr(runner, "available_to_back")
                    and hasattr(runner.available_to_back, "serialised")
                    else []
                )
                atl = (
                    runner.available_to_lay.serialised
                    if hasattr(runner, "available_to_lay")
                    and hasattr(runner.available_to_lay, "serialised")
                    else []
                )

                best_back = atb[0] if atb else None
                best_lay = atl[0] if atl else None

                if not best_back or not best_lay:
                    continue

                runner_name = str(runner.selection_id)
                runners_def = (
                    getattr(market_def, "runners", [])
                    if hasattr(market_def, "runners")
                    else (market_def.get("runners", []) if isinstance(market_def, dict) else [])
                )
                for r in runners_def:
                    r_id = getattr(r, "selection_id", None) or (
                        r.get("id") if isinstance(r, dict) else None
                    )
                    if r_id == runner.selection_id:
                        r_name = getattr(r, "name", None) or (
                            r.get("name") if isinstance(r, dict) else None
                        )
                        if r_name:
                            runner_name = r_name
                        break

                captured_at = now
                pub_time = getattr(cache, "publish_time", None)
                if isinstance(pub_time, (int, float)) and pub_time > 0:
                    try:
                        captured_at = datetime.fromtimestamp(pub_time / 1000.0, tz=UTC)
                    except Exception:
                        captured_at = now
                elif isinstance(pub_time, datetime):
                    captured_at = pub_time

                quotes.append(
                    Quote(
                        source=self.name,
                        selection=runner_name,
                        decimal_odds=float(best_back["price"]),
                        lay_odds=float(best_lay["price"]),
                        back_liquidity=float(best_back["size"]),
                        lay_liquidity=float(best_lay["size"]),
                        captured_at=captured_at,
                    )
                )

            if not quotes:
                continue

            event_id = getattr(market_def, "event_id", None) or (
                market_def.get("eventId", "") if isinstance(market_def, dict) else ""
            )
            raw_market_time = getattr(market_def, "market_time", None) or (
                market_def.get("marketTime") if isinstance(market_def, dict) else None
            )
            if isinstance(raw_market_time, datetime):
                market_time = raw_market_time
            elif isinstance(raw_market_time, str):
                try:
                    market_time = datetime.fromisoformat(raw_market_time.replace("Z", "+00:00"))
                except Exception:
                    market_time = now
            else:
                market_time = now

            total_matched = float(getattr(cache, "total_matched", 0.0) or 0.0)

            snapshots.append(
                MarketSnapshot(
                    event_id=str(event_id),
                    market_id=getattr(cache, "market_id", ""),
                    market_type="MATCH_ODDS",
                    sport=sport,
                    status=MarketStatus.LIVE if is_in_play else MarketStatus.PREMATCH,
                    start_time=market_time,
                    total_matched=total_matched,
                    quotes=quotes,
                )
            )

        return snapshots

    def stop(self) -> None:
        self._is_running = False
        if self._stream:
            try:
                self._stream.stop()
            except Exception as e:
                log.warning("betfair_stream_stop_error", error=str(e))
