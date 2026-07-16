"""End-to-end engine test using deterministic mock sources (no credentials)."""

from valuebet.config import Settings
from valuebet.core.models import Sport
from valuebet.engine.value_engine import ValueEngine
from valuebet.sources.mock import MockSource, demo_sources


def _engine(**overrides):
    betfair, pinnacle, stoiximan = demo_sources()
    settings = Settings(
        edge_threshold=overrides.get("edge_threshold", 0.049),
        confirmation_tolerance=overrides.get("confirmation_tolerance", 0.03),
        favorite_min_prob=overrides.get("favorite_min_prob", 0.55),
        kelly_fraction=overrides.get("kelly_fraction", 0.25),
        max_stake=overrides.get("max_stake", 10.0),
        bankroll=overrides.get("bankroll", 500.0),
        sport_overrides={
            "soccer": {
                "edge_threshold": overrides.get("edge_threshold", 0.049),
            }
        }
    )
    return ValueEngine(betfair, pinnacle, [stoiximan], settings=settings)


def test_detects_value_on_overpriced_favorite():
    result = _engine().scan(Sport.SOCCER)
    # Stoiximan offers 1.80 on Team A; Betfair fair prob ~0.586 -> edge ~0.055.
    team_a = [s for s in result.signals if s.selection == "Team A"]
    assert team_a, "expected a value signal on the overpriced favorite"
    sig = team_a[0]
    assert sig.edge >= 0.049
    assert sig.fair_prob >= 0.55
    assert 0 < sig.recommended_stake <= 10.0
    assert sig.confirm_prob is not None  # Pinnacle confirmation present


def test_scan_returns_snapshots_for_persistence():
    result = _engine().scan(Sport.SOCCER)
    # 3 sources x 2 markets x 3 selections = 18 quote rows to store.
    assert len(result.snapshots) == 6
    total_quotes = sum(len(s.quotes) for s in result.snapshots)
    assert total_quotes == 18


def test_favorite_filter_excludes_non_favorites():
    result = _engine().scan(Sport.SOCCER)
    assert all(s.fair_prob >= 0.55 for s in result.signals)
    assert all(s.selection not in {"Draw", "Team B", "Team D"} for s in result.signals)


def test_high_threshold_suppresses_signals():
    assert _engine(edge_threshold=0.50).scan(Sport.SOCCER).signals == []


def test_confirmation_tolerance_blocks_disagreement():
    assert _engine(confirmation_tolerance=0.0001).scan(Sport.SOCCER).signals == []


def _engine_without_pinnacle_match(require_confirmation: bool):
    betfair, _, stoiximan = demo_sources()
    # Pinnacle has totally different events -> no market will match.
    pinnacle = MockSource("pinnacle", {"9.999": [("Other X", 1.5), ("Other Y", 2.6)]})
    settings = Settings(
        require_confirmation=require_confirmation,
        sport_overrides={
            "soccer": {
                "edge_threshold": 0.049,
            }
        }
    )
    return ValueEngine(betfair, pinnacle, [stoiximan], settings=settings)


def test_require_confirmation_skips_when_no_pinnacle_price():
    # Default behaviour: without a Pinnacle confirmation, fire nothing.
    assert _engine_without_pinnacle_match(require_confirmation=True).scan(Sport.SOCCER).signals == []


def test_confirmation_optional_allows_betfair_only():
    # When explicitly disabled, the Betfair-only edge is allowed through.
    sigs = _engine_without_pinnacle_match(require_confirmation=False).scan(Sport.SOCCER).signals
    assert any(s.selection == "Team A" for s in sigs)
    assert all(s.confirm_prob is None for s in sigs)
