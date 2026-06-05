"""Trading schedule timezone (configurable in Symbol Settings)."""

from __future__ import annotations

from datetime import datetime

import pytz

DEFAULT_TIMEZONE = "Asia/Kolkata"

# Common IANA zones for the UI dropdown
COMMON_TIMEZONES = (
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Europe/London",
    "Europe/Berlin",
    "America/New_York",
    "America/Chicago",
    "UTC",
)


def validate_timezone(name: str) -> str | None:
    tz = (name or "").strip()
    if not tz:
        return "Timezone is required."
    try:
        pytz.timezone(tz)
        return None
    except pytz.UnknownTimeZoneError:
        return f"Unknown timezone: {tz}"


def resolve_timezone(name: str | None) -> pytz.BaseTzInfo:
    tz_name = (name or "").strip() or DEFAULT_TIMEZONE
    try:
        return pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone(DEFAULT_TIMEZONE)


def get_market_timezone() -> pytz.BaseTzInfo:
    from app import repository

    settings = repository.get_strategy_settings()
    return resolve_timezone(settings.get("timezone"))


def now() -> datetime:
    return datetime.now(get_market_timezone())


def now_ist() -> datetime:
    """India Standard Time — used for console [DEPTH] / [WS] log timestamps."""
    return datetime.now(pytz.timezone("Asia/Kolkata"))


def today_key() -> str:
    return now().strftime("%Y-%m-%d")


def today_key_ist() -> str:
    """Calendar date in India (IST) — used for order-log 'today' filters."""
    return now_ist().strftime("%Y-%m-%d")


def now_hhmm() -> str:
    return now().strftime("%H:%M")


def timezone_label() -> str:
    return get_market_timezone().zone
