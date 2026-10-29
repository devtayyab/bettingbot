"""Tests for live .env syncing and overriding stale Docker/Uvicorn os.environ keys."""

import os

from valuebet.config import (
    _build_settings,
    sync_dotenv_to_environ,
    write_dotenv_updates,
)


def test_dotenv_overrides_stale_docker_environ_key(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "THE_ODDS_API_KEY=fresh_key_abcdef123456\n"
        "DATABASE_URL=sqlite:///./local.db\n",
        encoding="utf-8",
    )

    # Simulate Docker Compose `env_file` + `environment:` locked in parent process
    monkeypatch.setenv("THE_ODDS_API_KEY", "eab7e3_stale_old_key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://valuebet:valuebet@db:5432/valuebet")

    sync_dotenv_to_environ(force=True, env_path=env_file)
    s = _build_settings()

    # App key from .env MUST override stale os.environ
    assert os.environ["THE_ODDS_API_KEY"] == "fresh_key_abcdef123456"
    assert s.the_odds_api_key == "fresh_key_abcdef123456"

    # Container DATABASE_URL in os.environ MUST be preserved
    assert os.environ["DATABASE_URL"] == "postgresql+psycopg://valuebet:valuebet@db:5432/valuebet"


def test_write_dotenv_updates_deduplicates_and_cleans_quotes(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment preserved\n"
        "export THE_ODDS_API_KEY = 'old_key_1'\n"
        "EDGE_THRESHOLD=0.05\n"
        "THE_ODDS_API_KEY=old_duplicate_key_2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("THE_ODDS_API_KEY", "old_key_1")

    write_dotenv_updates({"THE_ODDS_API_KEY": '"brand_new_key_7890"'}, env_path=env_file)

    text = env_file.read_text(encoding="utf-8")
    assert text.count("THE_ODDS_API_KEY=") == 1
    assert "THE_ODDS_API_KEY=brand_new_key_7890" in text
    assert "# Comment preserved" in text
    assert os.environ["THE_ODDS_API_KEY"] == "brand_new_key_7890"

