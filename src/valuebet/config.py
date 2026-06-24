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

    # Pinnacle
    pinnacle_username: str = ""
    pinnacle_password: str = ""

    # Value engine
    edge_threshold: float = Field(0.049, ge=0, le=1)
    confirmation_tolerance: float = Field(0.03, ge=0, le=1)
    # When true, a signal requires a matching Pinnacle price; never bet on Betfair alone.
    require_confirmation: bool = True
    favorite_min_prob: float = Field(0.55, ge=0, le=1)
    kelly_fraction: float = Field(0.25, ge=0, le=1)
    max_stake: float = Field(10.0, gt=0)
    bankroll: float = Field(500.0, gt=0)

    # Cadence
    poll_interval_live: int = 120
    poll_interval_prematch: int = 900

    # Placement (single account)
    stoiximan_username: str = ""
    stoiximan_password: str = ""
    placement_dry_run: bool = True
    placement_require_approval: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
