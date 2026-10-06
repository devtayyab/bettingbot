"""Centralised, validated configuration. All tuning lives here, sourced from env."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infra
    database_url: str = "postgresql+psycopg://valuebet:valuebet@localhost:5432/valuebet"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    env: str = "dev"

    # Betfair
    betfair_app_key: str = ""
    betfair_username: str = ""
    betfair_password: str = ""
    betfair_cert_path: str = ""
    betfair_key_path: str = ""

    # The Odds API
    the_odds_api_key: str = ""

    # Pinnacle
    pinnacle_username: str = ""
    pinnacle_password: str = ""

    # Value engine
    edge_threshold: float = Field(default=0.01, ge=0, le=1)
    live_edge_threshold: float = Field(default=0.03, ge=0, le=1)
    confirmation_tolerance: float = Field(default=0.15, ge=0, le=1)
    max_live_latency_seconds: float = Field(default=60.0, gt=0)
    max_prematch_latency_seconds: float = Field(default=300.0, gt=0)
    # Market Health (Betfair Quality Filters)
    min_total_matched: float = Field(default=10000.0, ge=0)
    min_liquidity: float = Field(default=100.0, ge=0)
    max_spread: float = Field(default=0.02, ge=0, le=1)
    # When true, a signal requires a matching Pinnacle price; never bet on Betfair alone.
    require_confirmation: bool = False
    enable_pinnacle_confirmation: bool = False
    # When true, drop live markets if real-time Betfair/Stoiximan score feeds cannot be matched.
    # Set to false when using third-party odds APIs (e.g. The Odds API) without direct score scraping.
    require_score_sync: bool = False
    min_odds: float = Field(default=1.01, ge=1.0)
    max_odds: float = Field(default=6.0, gt=1.0)
    favorite_min_prob: float = Field(default=0.10, ge=0, le=1)
    kelly_fraction: float = Field(default=0.25, ge=0, le=1)
    max_stake: float = Field(default=10.0, gt=0)
    max_event_exposure: float = Field(default=25.0, gt=0)
    bankroll: float = Field(default=500.0, gt=0)

    # Dynamic Sport Overrides (JSON string mapped to dict)
    sport_overrides: dict[str, dict] = Field(
        default_factory=lambda: {
            "soccer": {"edge_threshold": 0.01, "min_total_matched": 10000.0, "max_spread": 0.02, "min_liquidity": 100.0},
            "tennis": {"edge_threshold": 0.01, "min_total_matched": 10000.0, "max_spread": 0.02, "min_liquidity": 100.0},
            "basketball": {"edge_threshold": 0.01, "min_total_matched": 10000.0, "max_spread": 0.02, "min_liquidity": 100.0},
            "american_football": {"edge_threshold": 0.01, "min_total_matched": 10000.0, "max_spread": 0.02, "min_liquidity": 100.0},
            "baseball": {"edge_threshold": 0.01, "min_total_matched": 10000.0, "max_spread": 0.02, "min_liquidity": 100.0},
            "ice_hockey": {"edge_threshold": 0.01, "min_total_matched": 10000.0, "max_spread": 0.02, "min_liquidity": 100.0},
            "cricket": {"edge_threshold": 0.01, "min_total_matched": 10000.0, "max_spread": 0.02, "min_liquidity": 100.0},
            "rugby_league": {"edge_threshold": 0.015, "min_total_matched": 5000.0, "max_spread": 0.025, "min_liquidity": 50.0},
            "rugby_union": {"edge_threshold": 0.015, "min_total_matched": 5000.0, "max_spread": 0.025, "min_liquidity": 50.0},
            "golf": {"edge_threshold": 0.015, "min_total_matched": 5000.0, "max_spread": 0.025, "min_liquidity": 50.0},
            "mma": {"edge_threshold": 0.015, "min_total_matched": 5000.0, "max_spread": 0.025, "min_liquidity": 50.0},
            "boxing": {"edge_threshold": 0.015, "min_total_matched": 5000.0, "max_spread": 0.025, "min_liquidity": 50.0},
            "volleyball": {"edge_threshold": 0.02, "min_total_matched": 2500.0, "max_spread": 0.03, "min_liquidity": 50.0},
            "handball": {"edge_threshold": 0.02, "min_total_matched": 2500.0, "max_spread": 0.03, "min_liquidity": 50.0},
            "darts": {"edge_threshold": 0.02, "min_total_matched": 2500.0, "max_spread": 0.03, "min_liquidity": 50.0},
            "esports": {"edge_threshold": 0.02, "min_total_matched": 2500.0, "max_spread": 0.03, "min_liquidity": 50.0},
            "table_tennis": {"edge_threshold": 0.02, "min_total_matched": 1000.0, "max_spread": 0.03, "min_liquidity": 25.0},
        }
    )

    def get_sport_config(self, sport: str) -> dict:
        """Returns the specific thresholds for a sport, falling back to globals."""
        overrides = self.sport_overrides.get(sport.lower(), {})
        return {
            "edge_threshold": overrides.get("edge_threshold", self.edge_threshold),
            "live_edge_threshold": overrides.get("live_edge_threshold", self.live_edge_threshold),
            "min_total_matched": overrides.get("min_total_matched", self.min_total_matched),
            "max_spread": overrides.get("max_spread", self.max_spread),
            "min_liquidity": overrides.get("min_liquidity", self.min_liquidity),
            "max_live_latency_seconds": overrides.get("max_live_latency_seconds", self.max_live_latency_seconds),
            "max_prematch_latency_seconds": overrides.get("max_prematch_latency_seconds", self.max_prematch_latency_seconds),
            "min_odds": overrides.get("min_odds", self.min_odds),
            "max_odds": overrides.get("max_odds", self.max_odds),
        }

    # Cadence
    poll_interval_live: int = 300
    poll_interval_prematch: int = 1800

    # Placement (single account)
    # Notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Stoiximan
    stoiximan_username: str = ""
    stoiximan_password: str = ""
    stoiximan_cookie_path: str = "data/stoiximan_cookies.json"
    stoiximan_proxy: str = ""
    stoiximan_proxy_username: str = ""
    stoiximan_proxy_password: str = ""
    placement_dry_run: bool = True
    placement_require_approval: bool = True

    # When true, a scan that finds no real value falls back to the deterministic
    # mock sources. Those produce fake events ("Team A", "Real Madrid" at invented
    # odds) that exist at no bookmaker, so a signal from them can never be placed.
    # Useful for demoing the UI; must stay false in any real deployment.
    allow_demo_fallback: bool = False

    # When true, an unreachable DATABASE_URL is a hard error instead of silently
    # falling back to a local SQLite file that nothing else reads.
    strict_database: bool = True

    # The Odds API bookmaker key whose prices we treat as the target book's.
    # This MUST be the book we actually place on. It defaulted to "betano_uk"
    # while placement hits stoiximan.com.cy: sister brands, but separate markets
    # with different prices, so the price a signal was detected at does not exist
    # on the site the bot then tries to bet it on.
    odds_api_target_bookmaker: str = "betano_uk"
    # The book placement is routed to. Must correspond to the key above.
    placement_bookmaker: str = "stoiximan"


from functools import lru_cache


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings instance.

    Settings is expensive to construct (reads .env + validates all fields).
    The cache is invalidated by ``reset_settings_cache()`` when config is
    patched at runtime via PATCH /config so the next call re-reads env vars.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Invalidate the settings cache so the next ``get_settings()`` re-parses env."""
    get_settings.cache_clear()
