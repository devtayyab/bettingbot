"""Deterministic mock source — lets the full pipeline run without live credentials.

Produces a small set of soccer match-odds markets where the target book (added by
the engine layer) can be made to show a value edge, so we can validate detection,
sizing, signal persistence and the dashboard before real API keys are configured.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..core.models import MarketSnapshot, MarketStatus, Quote, Sport


class MockSource:
    def __init__(self, name: str, odds_table: dict[str, list[tuple[str, float]]]):
        self.name = name
        # market_id -> list of (selection, decimal_odds)
        self._odds_table = odds_table

    def fetch_markets(self, sport: Sport, live: bool = False) -> list[MarketSnapshot]:
        now = datetime.now(timezone.utc)
        snapshots: list[MarketSnapshot] = []
        for i, (market_id, quotes) in enumerate(self._odds_table.items()):
            snapshots.append(
                MarketSnapshot(
                    event_id=f"evt-{i}",
                    market_id=market_id,
                    market_type="MATCH_ODDS",
                    sport=sport,
                    status=MarketStatus.LIVE if live else MarketStatus.PREMATCH,
                    start_time=now + timedelta(hours=2),
                    total_matched=10000.0,
                    quotes=[
                        Quote(
                            source=self.name,
                            selection=sel,
                            decimal_odds=odds,
                            captured_at=now,
                            back_liquidity=5000.0
                        )
                        for sel, odds in quotes
                    ],
                )
            )
        return snapshots


def demo_sources() -> tuple[MockSource, MockSource, MockSource]:
    """Betfair (sharp ref), Pinnacle (confirmation), Stoiximan (target with an edge)."""
    betfair = MockSource(
        "betfair",
        {
            "1.111": [("Team A", 1.66), ("Draw", 4.2), ("Team B", 6.0)],
            "1.222": [("Team C", 2.10), ("Draw", 3.4), ("Team D", 3.6)],
            "1.333": [("Real Madrid", 1.50), ("Draw", 4.5), ("Getafe", 8.0)],
            "1.444": [("Bayern", 1.30), ("Draw", 5.0), ("Bochum", 12.0)],
            "1.555": [("PSG", 1.80), ("Draw", 3.8), ("Dortmund", 4.2)],
        },
    )
    pinnacle = MockSource(
        "pinnacle",
        {
            "1.111": [("Team A", 1.68), ("Draw", 4.1), ("Team B", 5.9)],
            "1.222": [("Team C", 2.12), ("Draw", 3.4), ("Team D", 3.55)],
            "1.333": [("Real Madrid", 1.52), ("Draw", 4.4), ("Getafe", 7.8)],
            "1.444": [("Bayern", 1.32), ("Draw", 4.9), ("Bochum", 11.5)],
            "1.555": [("PSG", 1.82), ("Draw", 3.7), ("Dortmund", 4.1)],
        },
    )
    # Stoiximan offers a generous price on the favorites -> positive edge.
    stoiximan = MockSource(
        "stoiximan",
        {
            "1.111": [("Team A", 1.80), ("Draw", 4.0), ("Team B", 5.5)],
            "1.222": [("Team C", 2.05), ("Draw", 3.3), ("Team D", 3.5)],
            "1.333": [("Real Madrid", 1.70), ("Draw", 4.2), ("Getafe", 7.5)],  # Big edge
            "1.444": [("Bayern", 1.45), ("Draw", 4.8), ("Bochum", 10.0)],      # Edge
            "1.555": [("PSG", 2.00), ("Draw", 3.6), ("Dortmund", 4.0)],        # Edge
        },
    )
    return betfair, pinnacle, stoiximan
