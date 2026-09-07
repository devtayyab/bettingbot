"""Unit tests for Stoiximan cookie storage, sanitization, and session persistence."""

import typing
from pathlib import Path
from unittest.mock import MagicMock

from valuebet.placement.session_store import (
    delete_cookie_file,
    get_cookie_status,
    load_cookies_into_context,
    read_cookie_json,
    sanitize_cookie,
    save_cookies_from_context,
    save_raw_cookie_list,
)


def test_sanitize_cookie_basic():
    raw = {
        "name": "session_id",
        "value": "xyz123abc",
        "domain": ".stoiximan.com.cy",
        "path": "/",
        "httpOnly": True,
        "secure": True,
    }
    cleaned = sanitize_cookie(raw)
    assert cleaned is not None
    assert cleaned["name"] == "session_id"
    assert cleaned["value"] == "xyz123abc"
    assert cleaned["domain"] == ".stoiximan.com.cy"
    assert cleaned["httpOnly"] is True
    assert cleaned["secure"] is True


def test_sanitize_cookie_converts_expiration_date():
    raw = {
        "name": "token",
        "value": "abc",
        "expirationDate": 1799999999.5,
        "extraField": "removeMe",
    }
    cleaned = sanitize_cookie(raw)
    assert cleaned is not None
    assert cleaned["expires"] == 1799999999
    assert "extraField" not in cleaned


def test_sanitize_cookie_samesite_normalization():
    # sameSite: "none" requires secure: True
    raw = {
        "name": "pref",
        "value": "gr",
        "sameSite": "none",
        "secure": False,
    }
    cleaned = sanitize_cookie(raw)
    assert cleaned is not None
    assert cleaned["sameSite"] == "None"
    assert cleaned["secure"] is True

    # sameSite: "lax"
    raw_lax = {"name": "c2", "value": "v2", "sameSite": "lax"}
    cleaned_lax = sanitize_cookie(raw_lax)
    assert cleaned_lax is not None
    assert cleaned_lax["sameSite"] == "Lax"


def test_sanitize_invalid_cookie():
    assert sanitize_cookie(typing.cast(dict[str, typing.Any], "not a dict")) is None
    assert sanitize_cookie({}) is None
    assert sanitize_cookie({"name": "foo"}) is None


def test_save_and_read_raw_cookie_list(tmp_path: Path):
    target = tmp_path / "test_cookies.json"
    raw_cookies = typing.cast(list[dict[str, typing.Any]], [
        {"name": "c1", "value": "v1", "domain": ".stoiximan.com.cy"},
        {"name": "c2", "value": "v2", "expirationDate": 1800000000},
    ])

    count = save_raw_cookie_list(raw_cookies, path=target)
    assert count == 2
    assert target.exists()

    loaded = read_cookie_json(path=target)
    assert len(loaded) == 2
    assert loaded[0]["name"] == "c1"
    assert loaded[1]["name"] == "c2"
    assert loaded[1]["expires"] == 1800000000


def test_cookie_status_flow(tmp_path: Path):
    target = tmp_path / "cookies_status.json"

    # Status before creation
    status_empty = get_cookie_status(path=target)
    assert status_empty["has_cookies"] is False
    assert status_empty["cookie_count"] == 0

    # Save cookies
    save_raw_cookie_list(typing.cast(list[dict[str, typing.Any]], [{"name": "s1", "value": "val"}]), path=target)

    # Status after creation
    status_populated = get_cookie_status(path=target)
    assert status_populated["has_cookies"] is True
    assert status_populated["cookie_count"] == 1
    assert "1 cookies saved" in status_populated["message"]

    # Delete
    deleted = delete_cookie_file(path=target)
    assert deleted is True
    assert not target.exists()


def test_load_and_save_with_mock_context(tmp_path: Path):
    target = tmp_path / "mock_context.json"
    mock_ctx = MagicMock()
    mock_ctx.cookies.return_value = [
        {"name": "ctx_cookie", "value": "secret", "domain": ".stoiximan.com.cy", "path": "/"}
    ]

    # Save from context
    saved_count = save_cookies_from_context(mock_ctx, path=target)
    assert saved_count == 1
    assert target.exists()

    # Load into context
    success, loaded_count = load_cookies_into_context(mock_ctx, path=target)
    assert success is True
    assert loaded_count == 1
    mock_ctx.add_cookies.assert_called_once()
