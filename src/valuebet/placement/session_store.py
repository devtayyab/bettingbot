"""Session and cookie storage manager for Stoiximan (and other Playwright-based placers).

Handles:
- Cross-platform file persistence (JSON)
- Cookie sanitization / normalization for Playwright add_cookies
- Session metadata and status checks
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..logging import get_logger

log = get_logger("placement.session_store")


def get_cookie_file_path(custom_path: str | Path | None = None) -> Path:
    """Return an absolute Path to the cookie storage JSON, ensuring parent dir exists."""
    if custom_path is not None:
        target = Path(custom_path)
    else:
        settings = get_settings()
        target = Path(settings.stoiximan_cookie_path)

    if not target.is_absolute():
        # Place relative to current working directory / project root
        target = Path.cwd() / target

    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def sanitize_cookie(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize and clean a cookie dict so Playwright's add_cookies accepts it without error."""
    if not isinstance(raw, dict):
        return None

    name = raw.get("name")
    value = raw.get("value")
    if name is None or value is None:
        return None

    cookie: dict[str, Any] = {
        "name": str(name),
        "value": str(value),
    }

    # Domain / Path
    if "domain" in raw and raw["domain"]:
        cookie["domain"] = str(raw["domain"])
    if "path" in raw and raw["path"]:
        cookie["path"] = str(raw["path"])
    else:
        cookie["path"] = "/"

    # URL if domain is missing
    if "url" in raw and raw["url"]:
        cookie["url"] = str(raw["url"])

    # If neither domain nor url is present, set default stoiximan domain
    if "domain" not in cookie and "url" not in cookie:
        cookie["domain"] = ".stoiximan.com.cy"

    # Expiry normalization (Cookie-Editor uses 'expirationDate', Playwright uses 'expires')
    expires = raw.get("expires") if "expires" in raw else raw.get("expirationDate")
    if expires is not None:
        try:
            exp_val = float(expires)
            if exp_val > 0:
                cookie["expires"] = int(exp_val)
        except (ValueError, TypeError):
            pass

    # HttpOnly & Secure booleans
    if "httpOnly" in raw:
        cookie["httpOnly"] = bool(raw["httpOnly"])
    if "secure" in raw:
        cookie["secure"] = bool(raw["secure"])

    # sameSite normalization
    same_site = raw.get("sameSite")
    if isinstance(same_site, str):
        ss_lower = same_site.strip().lower()
        if ss_lower in {"strict", "lax", "none"}:
            cookie["sameSite"] = ss_lower.capitalize() if ss_lower != "none" else "None"
            if cookie["sameSite"] == "None":
                cookie["secure"] = True  # SameSite=None requires Secure

    return cookie


def load_cookies_into_context(context: Any, path: str | Path | None = None) -> tuple[bool, int]:
    """Load and sanitize cookies from JSON file into a Playwright BrowserContext.

    Returns (success: bool, count_loaded: int).
    """
    file_path = get_cookie_file_path(path)
    if not file_path.exists():
        return False, 0

    try:
        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            return False, 0

        raw_list = json.loads(content)
        if not isinstance(raw_list, list):
            log.warning("cookie_file_not_list", path=str(file_path))
            return False, 0

        sanitized: list[dict[str, Any]] = []
        for item in raw_list:
            cleaned = sanitize_cookie(item)
            if cleaned:
                sanitized.append(cleaned)

        if not sanitized:
            log.warning("no_valid_cookies_found", path=str(file_path))
            return False, 0

        context.add_cookies(sanitized)
        log.info("cookies_loaded", path=str(file_path), count=len(sanitized))
        return True, len(sanitized)

    except Exception as exc:
        log.warning("cookie_load_failed", path=str(file_path), error=str(exc))
        return False, 0


def save_cookies_from_context(context: Any, path: str | Path | None = None) -> int:
    """Extract cookies from a Playwright BrowserContext and save to JSON file."""
    file_path = get_cookie_file_path(path)
    try:
        cookies = context.cookies()
        if not cookies:
            log.warning("no_cookies_in_context_to_save")
            return 0

        file_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        log.info("cookies_saved", path=str(file_path), count=len(cookies))
        return len(cookies)
    except Exception as exc:
        log.error("cookie_save_failed", path=str(file_path), error=str(exc))
        return 0


def save_raw_cookie_list(cookies: list[dict[str, Any]], path: str | Path | None = None) -> int:
    """Save an arbitrary list of cookie dicts (e.g. from UI / Cookie-Editor import)."""
    file_path = get_cookie_file_path(path)
    if not isinstance(cookies, list):
        raise ValueError("Cookies must be a list of objects")

    sanitized = [c for c in (sanitize_cookie(x) for x in cookies) if c is not None]
    if not sanitized:
        raise ValueError("No valid cookies found in provided list")

    file_path.write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
    log.info("cookies_imported_to_file", path=str(file_path), count=len(sanitized))
    return len(sanitized)


def get_cookie_status(path: str | Path | None = None) -> dict[str, Any]:
    """Inspect cookie JSON file and return health/age metadata."""
    file_path = get_cookie_file_path(path)
    if not file_path.exists():
        return {
            "has_cookies": False,
            "cookie_count": 0,
            "age_hours": 0.0,
            "path": str(file_path),
            "message": "No cookies saved. Please login or import cookies.",
        }

    try:
        content = file_path.read_text(encoding="utf-8").strip()
        data = json.loads(content) if content else []
        count = len(data) if isinstance(data, list) else 0

        stat = file_path.stat()
        age_hours = round((datetime.now(timezone.utc).timestamp() - stat.st_mtime) / 3600, 1)

        return {
            "has_cookies": count > 0,
            "cookie_count": count,
            "age_hours": age_hours,
            "path": str(file_path),
            "message": f"{count} cookies saved, {age_hours}h old." if count > 0 else "Cookie file is empty.",
        }
    except Exception as exc:
        return {
            "has_cookies": False,
            "cookie_count": 0,
            "age_hours": 0.0,
            "path": str(file_path),
            "error": str(exc),
            "message": f"Corrupt cookie file: {exc}",
        }


def read_cookie_json(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Return raw list of cookies currently stored."""
    file_path = get_cookie_file_path(path)
    if not file_path.exists():
        return []
    try:
        content = file_path.read_text(encoding="utf-8").strip()
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def delete_cookie_file(path: str | Path | None = None) -> bool:
    """Delete the saved cookie JSON file."""
    file_path = get_cookie_file_path(path)
    if file_path.exists():
        try:
            file_path.unlink()
            log.info("cookie_file_deleted", path=str(file_path))
            return True
        except Exception as exc:
            log.error("cookie_file_delete_failed", path=str(file_path), error=str(exc))
            return False
    return False


# Convenient alias
clear_cookies = delete_cookie_file

