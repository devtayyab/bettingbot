"""Centralised, validated configuration. All tuning lives here, sourced from env."""

from __future__ import annotations

from functools import lru_cache

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
    edge_threshold: float = Field(0.01, ge=0, le=1)
    live_edge_threshold: float = Field(0.03, ge=0, le=1)
    confirmation_tolerance: float = Field(0.15, ge=0, le=1)
    max_live_latency_seconds: float = Field(3.0, gt=0)
    max_prematch_latency_seconds: float = Field(300.0, gt=0)
    # Market Health
    min_total_matched: float = Field(1000.0, ge=0)
    min_liquidity: float = Field(10.0, ge=0)
    max_spread: float = Field(0.10, ge=0, le=1)
    # When true, a signal requires a matching Pinnacle price; never bet on Betfair alone.
    require_confirmation: bool = False
    favorite_min_prob: float = Field(0.10, ge=0, le=1)
    kelly_fraction: float = Field(0.25, ge=0, le=1)
    max_stake: float = Field(10.0, gt=0)
    max_event_exposure: float = Field(25.0, gt=0)
    bankroll: float = Field(500.0, gt=0)

    # Dynamic Sport Overrides (JSON string mapped to dict)
    sport_overrides: dict[str, dict] = Field(
        default_factory=lambda: {
            "soccer": {"edge_threshold": 0.01, "min_total_matched": 1000.0, "max_spread": 0.10, "min_liquidity": 10.0},
            "tennis": {"edge_threshold": 0.01, "min_total_matched": 500.0, "max_spread": 0.12, "min_liquidity": 10.0},
            "basketball": {"edge_threshold": 0.01, "min_total_matched": 1000.0, "max_spread": 0.10, "min_liquidity": 10.0},
            "american_football": {"edge_threshold": 0.01, "min_total_matched": 1000.0, "max_spread": 0.10, "min_liquidity": 10.0},
            "baseball": {"edge_threshold": 0.01, "min_total_matched": 500.0, "max_spread": 0.12, "min_liquidity": 10.0},
            "ice_hockey": {"edge_threshold": 0.01, "min_total_matched": 500.0, "max_spread": 0.12, "min_liquidity": 10.0},
            "cricket": {"edge_threshold": 0.01, "min_total_matched": 500.0, "max_spread": 0.12, "min_liquidity": 10.0},
            "rugby_league": {"edge_threshold": 0.015, "min_total_matched": 250.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "rugby_union": {"edge_threshold": 0.015, "min_total_matched": 250.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "golf": {"edge_threshold": 0.015, "min_total_matched": 250.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "mma": {"edge_threshold": 0.015, "min_total_matched": 250.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "boxing": {"edge_threshold": 0.015, "min_total_matched": 250.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "volleyball": {"edge_threshold": 0.02, "min_total_matched": 100.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "handball": {"edge_threshold": 0.02, "min_total_matched": 100.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "darts": {"edge_threshold": 0.02, "min_total_matched": 100.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "esports": {"edge_threshold": 0.02, "min_total_matched": 100.0, "max_spread": 0.15, "min_liquidity": 5.0},
            "table_tennis": {"edge_threshold": 0.02, "min_total_matched": 50.0, "max_spread": 0.20, "min_liquidity": 5.0},
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
        }

    # Cadence
    poll_interval_live: int = 120
    poll_interval_prematch: int = 900

    # Placement (single account)
    # Notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Stoiximan
    stoiximan_username: str = ""
    stoiximan_password: str = ""
    placement_dry_run: bool = True
    placement_require_approval: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
