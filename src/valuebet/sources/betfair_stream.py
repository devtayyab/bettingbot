"""Betfair Stream API Integration.

Maintains a persistent WebSocket connection to Betfair for live market data.
Updates a local thread-safe cache of MarketSnapshots, so the ValueEngine can
poll the cache instantaneously without network latency.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from betfairlightweight.resources.streamingresources import MarketBookCache

from ..core.models import MarketSnapshot, MarketStatus, Quote, Sport
from ..logging import get_logger
from .betfair import BetfairSource, _EVENT_TYPE

log = get_logger("source.betfair_stream")


class BetfairStreamSource:
    name = "betfair_stream"

    def __init__(self, fallback_source: BetfairSource) -> None:
        self.fallback = fallback_source
        self._client = None
        self._stream = None
        self._listener = None
        self._cache_lock = threading.Lock()
        self._market_caches: Dict[str, MarketBookCache] = {}
        self._is_running = False

    def start(self, sport: Sport) -> None:
        """Start the background streaming thread for live markets."""
        import betfairlightweight

        if self._is_running:
            return

        self._client = self.fallback._ensure_login()
        self._listener = betfairlightweight.StreamListener(max_latency=3.0)
        
        # Start stream on a background thread managed by betfairlightweight
        self._stream = self._client.streaming.create_stream(listener=self._listener)
        self._stream.start()

        from betfairlightweight import filters

        event_type_id = _EVENT_TYPE[sport]
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
            initial_clk=self._listener.initial_clk,
            clk=self._listener.clk,
        )
        self._is_running = True

        # Start a local thread to sync the listener's cache into our simplified model
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

    def _sync_loop(self) -> None:
        import time
        while self._is_running:
            try:
                # The listener updates its internal caches automatically.
                # We just hold onto references to them.
                with self._cache_lock:
                    self._market_caches = dict(self._listener.stream.market_caches)
            except Exception as e:
                log.error("stream_sync_error", error=str(e))
            time.sleep(1.0)

    def fetch_markets(self, sport: Sport, live: bool = True) -> list[MarketSnapshot]:
        """Return the latest snapshots from the in-memory stream cache."""
        if not self._is_running:
            log.warning("stream_not_running_falling_back_to_rest")
            return self.fallback.fetch_markets(sport, live)

        snapshots: list[MarketSnapshot] = []
        now = datetime.now(timezone.utc)

        with self._cache_lock:
            # We copy the dictionary items to avoid dict size changing during iteration
            caches = list(self._market_caches.values())

        for cache in caches:
            market_def = cache.market_definition
            if not market_def:
                continue

            # Suspension Detection: skip suspended markets completely
            if market_def.status == "SUSPENDED":
                log.debug("market_suspended_skipping", market_id=cache.market_id)
                continue

            # Filter by in-play status if requested
            is_in_play = market_def.in_play
            if live and not is_in_play:
                continue
            if not live and is_in_play:
                continue

            quotes: list[Quote] = []
            for runner in cache.runners:
                if runner.status != "ACTIVE":
                    continue
                
                best_back = runner.ex.available_to_back[0] if runner.ex.available_to_back else None
                best_lay = runner.ex.available_to_lay[0] if runner.ex.available_to_lay else None
                
                if not best_back or not best_lay:
                    continue

                # In streaming, we get selection_id. We map it using the market definition.
                runner_def = next((r for r in market_def.runners if r.selection_id == runner.selection_id), None)
                runner_name = runner_def.name if runner_def and hasattr(runner_def, "name") else str(runner.selection_id)

                quotes.append(
                    Quote(
                        source=self.name,
                        selection=runner_name,
                        decimal_odds=best_back["price"],
                        lay_odds=best_lay["price"],
                        back_liquidity=best_back["size"],
                        lay_liquidity=best_lay["size"],
                        captured_at=cache.publish_time or now,
                    )
                )

            if not quotes:
                continue
                
            snapshots.append(
                MarketSnapshot(
                    event_id=str(market_def.event_id),
                    market_id=cache.market_id,
                    market_type="MATCH_ODDS",
                    sport=sport,
                    status=MarketStatus.LIVE if is_in_play else MarketStatus.PREMATCH,
                    start_time=market_def.market_time or now,
                    total_matched=10000.0, # Approximation, stream doesn't give total matched easily per poll
                    quotes=quotes,
                )
            )

        return snapshots

    def stop(self) -> None:
        self._is_running = False
        if self._stream:
            self._stream.stop()
