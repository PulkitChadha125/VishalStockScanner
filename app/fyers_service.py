"""Wrapper around project-root FyresIntegration.py for Flask strategy use."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import BASE_DIR

# Import FyresIntegration from project root
_root = str(BASE_DIR)
if _root not in sys.path:
    sys.path.insert(0, _root)

import FyresIntegration as fyi  # noqa: E402

from app.fyers_credentials import load_credentials
from app import fyers_market_ws, market_tz, repository

_connected = False
_last_balance: float | None = None
_last_balance_detail: dict[str, Any] | None = None

VWAP_CACHE_TTL_SEC = 45

from app.timeframes import TIMEFRAME_TO_RESOLUTION

_vwap_cache: dict[tuple[str, str], tuple[float, float]] = {}

# Fyers depth() allows ONE symbol per request (no batch). Rate-limit to 1 call/sec.
DEPTH_MIN_INTERVAL_SEC = 1.0
DEPTH_CACHE_TTL_SEC = 300
DEPTH_RATE_LIMIT_BACKOFF_SEC = 3.0

_depth_cache: dict[str, tuple[dict[str, Any], float]] = {}
_depth_rotate_idx = 0
_last_depth_api_at = 0.0
_rate_limited_until = 0.0


def is_connected() -> bool:
    return _connected and fyi.fyers is not None


def to_fyers_symbol(symbol_name: str) -> str:
    name = (symbol_name or "").strip().upper()
    if not name:
        raise ValueError("Empty symbol name")
    if ":" in name:
        return name
    return f"NSE:{name}-EQ"


def login_from_csv() -> tuple[bool, str, float | None]:
    """Login via automated_login using FyersCredentials.csv."""
    global _connected, _last_balance, _last_balance_detail

    try:
        store = load_credentials()
    except FileNotFoundError as e:
        return False, str(e), None

    token, err = fyi.run_automated_login_from_store(store)
    if err or not token:
        _connected = False
        fyi.fyers = None
        fyi.access_token = None
        return False, err or "Login failed", None

    verr = ""
    ok = False
    for attempt in range(3):
        ok, verr = fyi.verify_profile_ok()
        if ok:
            break
        if attempt < 2:
            time.sleep(1.5)

    if not ok:
        _connected = False
        fyi.fyers = None
        fyi.access_token = None
        return False, verr or "Profile verification failed", None

    _connected = True
    bal, detail = fetch_balance()
    _last_balance = bal
    _last_balance_detail = detail
    _start_market_websocket()
    return True, "", bal


def logout() -> None:
    global _connected, _last_balance, _last_balance_detail
    _stop_market_websocket()
    clear_depth_cache()
    _connected = False
    _last_balance = None
    _last_balance_detail = None
    fyi.fyers = None
    fyi.access_token = None


def _start_market_websocket() -> None:
    if not is_connected():
        return
    names = [s["symbol_name"] for s in repository.list_symbols()]
    if names:
        fyers_market_ws.start(names)


def _stop_market_websocket() -> None:
    fyers_market_ws.stop()


def sync_market_websocket() -> None:
    if not is_connected():
        return
    names = [s["symbol_name"] for s in repository.list_symbols()]
    if names:
        fyers_market_ws.sync_symbols(names)
    else:
        fyers_market_ws.stop()


def is_market_ws_active() -> bool:
    return fyers_market_ws.is_active()


def fetch_balance() -> tuple[float | None, dict[str, Any] | None]:
    """Return available fund limit from Fyers funds API."""
    global _last_balance, _last_balance_detail

    if not is_connected():
        return None, None

    try:
        res = fyi.fyers.funds()
    except Exception as e:
        return None, {"error": str(e)}

    if not isinstance(res, dict) or res.get("s") != "ok":
        return None, res if isinstance(res, dict) else {"raw": str(res)}

    fund = res.get("fund_limit") or res.get("data") or res
    available = None
    if isinstance(fund, list) and fund:
        row = fund[0]
        if isinstance(row, dict):
            for key in (
                "equityAmount",
                "availableBalance",
                "available_balance",
                "balance",
                "limit",
            ):
                if key in row and row[key] is not None:
                    available = float(row[key])
                    break
    elif isinstance(fund, dict):
        for key in (
            "equityAmount",
            "availableBalance",
            "available_balance",
            "balance",
            "limit",
        ):
            if key in fund and fund[key] is not None:
                available = float(fund[key])
                break

    _last_balance = available
    _last_balance_detail = res
    return available, res


def get_cached_balance() -> float | None:
    return _last_balance


def _depth_cache_key(symbol_name: str) -> str:
    return (symbol_name or "").strip().upper()


def _is_rate_limit_response(res: Any) -> bool:
    if not isinstance(res, dict):
        return False
    if res.get("code") == 429:
        return True
    msg = str(res.get("message") or "").lower()
    return "limit" in msg or "too many" in msg


def _parse_depth_response(res: dict, sym: str) -> dict[str, Any]:
    book = res.get("d") or res.get("data")
    if isinstance(book, dict) and "bids" not in book and "ask" not in book:
        if len(book) == 1:
            book = next(iter(book.values()))

    if not isinstance(book, dict):
        return {"error": "Unexpected depth format", "symbol": sym, "raw": res}

    bids = book.get("bids") or book.get("bid") or []
    asks = book.get("ask") or book.get("asks") or []

    def _level(arr, idx=0):
        if not arr or idx >= len(arr):
            return 0.0, 0.0
        lvl = arr[idx]
        if not isinstance(lvl, dict):
            return 0.0, 0.0
        price = float(lvl.get("price") or lvl.get("p") or 0)
        qty = float(lvl.get("qty") or lvl.get("volume") or lvl.get("v") or 0)
        return price, qty

    def _sum_qty(arr) -> float:
        total = 0.0
        if not arr:
            return total
        for lvl in arr:
            if isinstance(lvl, dict):
                total += float(lvl.get("qty") or lvl.get("volume") or lvl.get("v") or 0)
        return total

    bid_p, _ = _level(bids, 0)
    ask_p, _ = _level(asks, 0)
    bid_q = _sum_qty(bids)
    ask_q = _sum_qty(asks)

    return {
        "symbol": sym,
        "bid_price": bid_p,
        "bid_qty": bid_q,
        "ask_price": ask_p,
        "ask_qty": ask_q,
        "raw": book,
    }


def _call_depth_api(symbol_name: str) -> dict[str, Any]:
    """Single-symbol Fyers depth() call (only one symbol allowed per request)."""
    global _last_depth_api_at, _rate_limited_until

    if not is_connected():
        return {"error": "not_connected"}

    sym = to_fyers_symbol(symbol_name)
    now = time.time()
    if now < _rate_limited_until:
        return {
            "error": "rate_limited",
            "symbol": sym,
            "retry_after_sec": round(_rate_limited_until - now, 1),
        }
    if now - _last_depth_api_at < DEPTH_MIN_INTERVAL_SEC:
        return {"error": "throttled", "symbol": sym}

    _last_depth_api_at = now
    try:
        res = fyi.fyers.depth(data={"symbol": sym, "ohlcv_flag": "0"})
    except Exception as e:
        return {"error": str(e), "symbol": sym}

    if not isinstance(res, dict) or res.get("s") != "ok":
        if _is_rate_limit_response(res):
            _rate_limited_until = now + DEPTH_RATE_LIMIT_BACKOFF_SEC
        return {"error": res, "symbol": sym}

    return _parse_depth_response(res, sym)


def _store_depth_cache(symbol_name: str, data: dict[str, Any]) -> None:
    _depth_cache[_depth_cache_key(symbol_name)] = (data, time.time())


def tick_depth_refresh(symbol_names: list[str]) -> str | None:
    """
    Refresh depth for one symbol per engine tick (max 1 depth API call/sec).
    Rotates through symbol_names. Returns the symbol refreshed this tick.
    Skipped when the market WebSocket is active.
    """
    global _depth_rotate_idx

    if fyers_market_ws.is_active():
        return None

    if not symbol_names:
        return None

    name = symbol_names[_depth_rotate_idx % len(symbol_names)]
    data = _call_depth_api(name)
    err = data.get("error")
    if err in ("throttled", "rate_limited"):
        return None
    _depth_rotate_idx += 1
    if not err:
        _store_depth_cache(name, data)
    elif isinstance(err, dict) or err:
        _store_depth_cache(name, data)
    if err:
        return None
    return name


def get_market_depth(
    symbol_name: str, max_age_sec: float = DEPTH_CACHE_TTL_SEC
) -> dict[str, Any] | None:
    """Return WebSocket depth when connected, else REST cache."""
    ws_depth = fyers_market_ws.get_depth(symbol_name)
    if ws_depth:
        return ws_depth

    key = _depth_cache_key(symbol_name)
    entry = _depth_cache.get(key)
    if not entry:
        return None

    data, ts = entry
    age = time.time() - ts
    if age > max_age_sec:
        return None

    return {**data, "cache_age_sec": round(age, 1)}


def fetch_market_depth_immediate(symbol_name: str) -> dict[str, Any]:
    """Direct depth fetch (respects rate limit). Used for one-off probes."""
    data = _call_depth_api(symbol_name)
    if not data.get("error"):
        _store_depth_cache(symbol_name, data)
    return data


def clear_depth_cache() -> None:
    global _depth_rotate_idx, _last_depth_api_at, _rate_limited_until
    _depth_cache.clear()
    _depth_rotate_idx = 0
    _last_depth_api_at = 0.0
    _rate_limited_until = 0.0


def get_ltp(symbol_name: str) -> float | None:
    if not is_connected():
        return None

    ws_ltp = fyers_market_ws.get_ltp(symbol_name)
    if ws_ltp is not None:
        return ws_ltp

    sym = to_fyers_symbol(symbol_name)
    try:
        lp = fyi.get_ltp(sym)
        return float(lp) if lp is not None else None
    except Exception:
        return None


def place_market_order(symbol_name: str, side: int, quantity: int = 1) -> dict:
    """side: 1 buy, -1 sell. order type 2 = market."""
    sym = to_fyers_symbol(symbol_name)
    return fyi.place_order(sym, quantity, 2, side, 0)


def is_order_successful(response: dict | None) -> bool:
    return isinstance(response, dict) and response.get("s") == "ok"


def order_status_label(response: dict | None) -> str:
    return "FILLED" if is_order_successful(response) else "REJECTED"


def timeframe_to_resolution(time_frame: str) -> str | None:
    return TIMEFRAME_TO_RESOLUTION.get((time_frame or "").strip().lower())


def _today_market_date():
    return market_tz.now().date()


def calculate_vwap_from_candles(df) -> float | None:
    """Session VWAP from today's candles: sum(tp * vol) / sum(vol)."""
    if df is None or df.empty:
        return None

    today = _today_market_date()
    df = df.copy()
    df["day"] = df["date"].apply(
        lambda x: x.date()
        if hasattr(x, "date")
        else pd.Timestamp(x, tz=market_tz.get_market_timezone()).date()
    )
    session = df[df["day"] == today]
    if session.empty:
        session = df

    vol = session["volume"].astype(float)
    if vol.sum() <= 0:
        return None

    typical = (
        session["high"].astype(float)
        + session["low"].astype(float)
        + session["close"].astype(float)
    ) / 3.0
    return float((typical * vol).sum() / vol.sum())


def get_vwap(symbol_name: str, time_frame: str) -> float | None:
    """
    VWAP for symbol on configured UI timeframe (intraday session candles).
    Cached briefly to limit history API calls.
    """
    if not is_connected():
        return None

    tf = (time_frame or "").strip().lower()
    resolution = timeframe_to_resolution(tf)
    if not resolution:
        return None

    cache_key = (symbol_name.upper(), tf)
    now = time.time()
    cached = _vwap_cache.get(cache_key)
    if cached and (now - cached[1]) < VWAP_CACHE_TTL_SEC:
        return cached[0]

    sym = to_fyers_symbol(symbol_name)
    try:
        df = fyi.fetchOHLC(sym, resolution)
        if df is None or getattr(df, "empty", True):
            return None
        vwap = calculate_vwap_from_candles(df)
        if vwap is not None:
            _vwap_cache[cache_key] = (vwap, now)
        return vwap
    except Exception:
        return None


def passes_vwap_filter(signal: str, entry_price: float, vwap: float) -> tuple[bool, str]:
    """
    BUY only if entry > VWAP.
    SELL only if entry < VWAP.
    """
    if signal == "BUY":
        if entry_price > vwap:
            return True, ""
        return (
            False,
            f"BUY blocked: entry {entry_price:.2f} <= VWAP {vwap:.2f}",
        )
    if signal == "SELL":
        if entry_price < vwap:
            return True, ""
        return (
            False,
            f"SELL blocked: entry {entry_price:.2f} >= VWAP {vwap:.2f}",
        )
    return False, "Unknown signal"
