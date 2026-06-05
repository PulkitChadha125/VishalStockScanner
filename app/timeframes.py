"""Supported chart time frames (UI + CSV + Fyers history resolution)."""

from __future__ import annotations

# Fyers history API minutes: 1,2,3,5,10,15,20,30,60,... (no native 4m → use 5m candles)
TIMEFRAME_TO_RESOLUTION: dict[str, str] = {
    "1m": "1",
    "2m": "2",
    "3m": "3",
    "4m": "5",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "20m": "20",
    "30m": "30",
    "1h": "60",
}

VALID_TIMEFRAMES = frozenset(TIMEFRAME_TO_RESOLUTION.keys())

TIMEFRAME_LABELS: dict[str, str] = {
    "1m": "1 minute",
    "2m": "2 minutes",
    "3m": "3 minutes",
    "4m": "4 minutes (5m candles on Fyers)",
    "5m": "5 minutes",
    "10m": "10 minutes",
    "15m": "15 minutes",
    "20m": "20 minutes",
    "30m": "30 minutes",
    "1h": "1 hour",
}


def allowed_timeframes_display() -> str:
    return ", ".join(sorted(VALID_TIMEFRAMES, key=_sort_key))


def _sort_key(tf: str) -> tuple:
    if tf.endswith("m"):
        return (0, int(tf[:-1]))
    if tf.endswith("h"):
        return (1, int(tf[:-1]))
    return (2, 0)
