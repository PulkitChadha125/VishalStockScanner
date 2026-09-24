"""Wrapper around project-root FyresIntegration.py for Flask strategy use."""

from __future__ import annotations

import math
import sys
import threading
import time
from datetime import datetime, timedelta
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
_book_rotate_idx = 0
_last_depth_api_at = 0.0
_rate_limited_until = 0.0

_book_refresh_stop = threading.Event()
_book_refresh_thread: threading.Thread | None = None

LTP_WS_MAX_AGE_SEC = 15.0
LTP_QUOTES_MIN_INTERVAL_SEC = 2.0
LTP_QUOTES_MAX_AGE_SEC = 30.0

_ltp_quotes_cache: dict[str, tuple[float, float]] = {}
_last_ltp_quotes_at = 0.0
_ltp_quotes_lock = threading.Lock()


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
    _start_book_refresh_thread()
    return True, "", bal


def logout() -> None:
    global _connected, _last_balance, _last_balance_detail
    _stop_book_refresh_thread()
    _stop_market_websocket()
    clear_depth_cache()
    _connected = False
    _last_balance = None
    _last_balance_detail = None
    fyi.fyers = None
    fyi.access_token = None


def reset_session_state() -> None:
    """Stop feeds, clear caches, and disconnect Fyers (used on Stop button)."""
    logout()


def _all_market_symbol_names() -> list[str]:
    """Watchlist + scanner symbols for websocket LTP/depth."""
    watch = [s["symbol_name"] for s in repository.list_symbols()]
    scan = [s["symbol_name"] for s in repository.list_scanner_symbols()]
    seen: set[str] = set()
    merged: list[str] = []
    for name in watch + scan:
        key = name.upper()
        if key not in seen:
            seen.add(key)
            merged.append(name)
    return merged


def _start_market_websocket() -> None:
    if not is_connected():
        return
    names = _all_market_symbol_names()
    if names:
        fyers_market_ws.start(names)


def _stop_market_websocket() -> None:
    fyers_market_ws.stop()


def sync_market_websocket() -> None:
    if not is_connected():
        return
    names = _all_market_symbol_names()
    if names:
        fyers_market_ws.sync_symbols(names)
    else:
        fyers_market_ws.stop()


def is_market_ws_active() -> bool:
    return fyers_market_ws.is_active()


def _start_book_refresh_thread() -> None:
    global _book_refresh_thread
    _book_refresh_stop.clear()
    if _book_refresh_thread is not None and _book_refresh_thread.is_alive():
        return
    _book_refresh_thread = threading.Thread(
        target=_book_refresh_loop,
        name="book-totals-refresh",
        daemon=True,
    )
    _book_refresh_thread.start()


def _stop_book_refresh_thread() -> None:
    _book_refresh_stop.set()


def _book_refresh_loop() -> None:
    while not _book_refresh_stop.is_set():
        try:
            if is_connected() and market_tz.session_status().get("market_open"):
                names = _all_market_symbol_names()
                if names:
                    refresh_ltps_via_quotes(names)
                    tick_book_totals_refresh(names)
        except Exception as e:
            print(f"[Book REST] refresh error: {e}", flush=True)
        _book_refresh_stop.wait(DEPTH_MIN_INTERVAL_SEC)


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

    bid_q = float(
        book.get("totalbuyqty")
        or book.get("totalBuyQty")
        or book.get("total_buy_qty")
        or book.get("tbq")
        or 0
    )
    ask_q = float(
        book.get("totalsellqty")
        or book.get("totalSellQty")
        or book.get("total_sell_qty")
        or book.get("tsq")
        or 0
    )
    qty_source = "full_book"
    if bid_q <= 0 and ask_q <= 0:
        bid_q = _sum_qty(bids)
        ask_q = _sum_qty(asks)
        qty_source = "top_levels_fallback"

    return {
        "symbol": sym,
        "bid_price": bid_p,
        "bid_qty": bid_q,
        "ask_price": ask_p,
        "ask_qty": ask_q,
        "qty_source": qty_source,
        "book_source": "rest",
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


def _has_book_totals(data: dict[str, Any] | None) -> bool:
    if not data or data.get("error"):
        return False
    src = data.get("qty_source")
    if src not in ("full_book", "top_levels_fallback"):
        return False
    return float(data.get("bid_qty") or 0) > 0 or float(data.get("ask_qty") or 0) > 0


def _merge_depth_snapshots(
    ws: dict[str, Any] | None, rest: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not ws and not rest:
        return None
    merged: dict[str, Any] = dict(rest or ws or {})
    if ws and rest:
        merged.update(rest)
        for field in ("bid_price", "ask_price", "ltp"):
            ws_val = ws.get(field)
            if ws_val and float(ws_val) > 0:
                merged[field] = ws_val
        if _has_book_totals(rest):
            merged["bid_qty"] = rest["bid_qty"]
            merged["ask_qty"] = rest["ask_qty"]
            merged["qty_source"] = rest.get("qty_source", "full_book")
            merged["book_source"] = rest.get("book_source", "rest")
        elif _has_book_totals(ws):
            merged["bid_qty"] = ws["bid_qty"]
            merged["ask_qty"] = ws["ask_qty"]
            merged["qty_source"] = ws.get("qty_source", "full_book")
            merged["book_source"] = ws.get("book_source", "websocket")
        if ws.get("updated_at"):
            merged["updated_at"] = ws["updated_at"]
    elif ws:
        merged = dict(ws)
    return merged


def has_book_totals(data: dict[str, Any] | None) -> bool:
    return _has_book_totals(data)


def tick_book_totals_refresh(symbol_names: list[str]) -> str | None:
    """
    REST depth() rotation: 1 symbol per second for full book buy/sell totals.
    Updates REST cache and live display cache (used when WS totals are missing).
    """
    global _book_rotate_idx

    if not symbol_names or not is_connected():
        return None

    name = symbol_names[_book_rotate_idx % len(symbol_names)]
    data = _call_depth_api(name)
    err = data.get("error")
    if err in ("throttled", "rate_limited"):
        return None

    _book_rotate_idx += 1
    if not err and _has_book_totals(data):
        _store_depth_cache(name, data)
        fyers_market_ws.merge_rest_book(name, data)
        print(
            f"[Book REST] {name} buy={data.get('bid_qty')} sell={data.get('ask_qty')}",
            flush=True,
        )
    return name if not err else None


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
    """Merge WebSocket prices/LTP with REST book totals (1 REST call/sec rotation)."""
    ws_depth = fyers_market_ws.get_depth(symbol_name)
    if ws_depth:
        age = time.time() - float(ws_depth.get("updated_at") or 0)
        ws_depth = {**ws_depth, "cache_age_sec": round(age, 1)}

    key = _depth_cache_key(symbol_name)
    rest_depth = None
    entry = _depth_cache.get(key)
    if entry:
        data, ts = entry
        age = time.time() - ts
        if age <= max_age_sec:
            rest_depth = {**data, "cache_age_sec": round(age, 1)}

    merged = _merge_depth_snapshots(ws_depth, rest_depth)
    if merged and _has_book_totals(merged):
        return merged
    if merged:
        return merged
    return None


def fetch_market_depth_immediate(symbol_name: str) -> dict[str, Any]:
    """Direct depth fetch (respects rate limit). Used for one-off probes."""
    data = _call_depth_api(symbol_name)
    if not data.get("error"):
        _store_depth_cache(symbol_name, data)
    return data


def clear_depth_cache() -> None:
    global _depth_rotate_idx, _book_rotate_idx, _last_depth_api_at, _rate_limited_until
    _depth_cache.clear()
    _depth_rotate_idx = 0
    _book_rotate_idx = 0
    _last_depth_api_at = 0.0
    _rate_limited_until = 0.0


def _short_symbol_name(symbol_name: str) -> str:
    sym = (symbol_name or "").strip().upper()
    if ":" in sym:
        sym = sym.split(":", 1)[1]
    return sym.replace("-EQ", "").replace("-eq", "")


def refresh_ltps_via_quotes(
    symbol_names: list[str], *, force: bool = False
) -> dict[str, float]:
    """
    Batch-fetch live LTP (lp) from Fyers quotes API.
    Updates REST cache and WebSocket LTP cache.
    """
    global _last_ltp_quotes_at

    if not is_connected() or not symbol_names:
        return {}

    names = sorted(
        {(n or "").strip().upper() for n in symbol_names if (n or "").strip()}
    )
    if not names:
        return {}

    now = time.time()
    with _ltp_quotes_lock:
        if (
            not force
            and now - _last_ltp_quotes_at < LTP_QUOTES_MIN_INTERVAL_SEC
        ):
            return {
                n: _ltp_quotes_cache[n][0]
                for n in names
                if n in _ltp_quotes_cache
            }

    symbols_str = ",".join(to_fyers_symbol(n) for n in names)
    try:
        res = fyi.fyers.quotes(data={"symbols": symbols_str})
    except Exception as exc:
        print(f"[LTP quotes] batch error: {exc}", flush=True)
        return {}

    if not isinstance(res, dict) or res.get("s") != "ok":
        print(f"[LTP quotes] rejected: {res}", flush=True)
        return {}

    out: dict[str, float] = {}
    fetched_at = time.time()
    for item in res.get("d") or []:
        if not isinstance(item, dict):
            continue
        raw_sym = item.get("n") or item.get("symbol") or ""
        key = _short_symbol_name(str(raw_sym))
        values = item.get("v") or {}
        lp = values.get("lp")
        if lp is None:
            continue
        try:
            ltp = float(lp)
        except (TypeError, ValueError):
            continue
        if ltp <= 0:
            continue
        out[key] = ltp
        fyers_market_ws.set_ltp(key, ltp)

    with _ltp_quotes_lock:
        for key, ltp in out.items():
            _ltp_quotes_cache[key] = (ltp, fetched_at)
        if out:
            _last_ltp_quotes_at = fetched_at

    return out


def get_ltp_updated_at(symbol_name: str) -> str | None:
    """IST timestamp when LTP was last refreshed (WS or REST quotes)."""
    key = (symbol_name or "").strip().upper()
    ws_ltp, ws_age = fyers_market_ws.get_ltp_with_age(key)
    if ws_ltp is not None and ws_age is not None and ws_age <= LTP_QUOTES_MAX_AGE_SEC:
        ts = market_tz.now_ist() - timedelta(seconds=ws_age)
        return ts.strftime("%Y-%m-%d %H:%M:%S")

    with _ltp_quotes_lock:
        cached = _ltp_quotes_cache.get(key)
    if cached and time.time() - cached[1] <= LTP_QUOTES_MAX_AGE_SEC:
        ts = datetime.fromtimestamp(
            cached[1], tz=market_tz.get_market_timezone()
        )
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return None


def get_ltp(symbol_name: str) -> float | None:
    if not is_connected():
        return None

    key = (symbol_name or "").strip().upper()
    ws_ltp, ws_age = fyers_market_ws.get_ltp_with_age(key)
    if (
        ws_ltp is not None
        and ws_age is not None
        and ws_age <= LTP_WS_MAX_AGE_SEC
    ):
        return ws_ltp

    with _ltp_quotes_lock:
        cached = _ltp_quotes_cache.get(key)
        if cached and time.time() - cached[1] <= LTP_QUOTES_MAX_AGE_SEC:
            return cached[0]

    sym = to_fyers_symbol(key)
    try:
        lp = fyi.get_ltp(sym)
        if lp is None:
            return None
        ltp = float(lp)
        if ltp > 0:
            fyers_market_ws.set_ltp(key, ltp)
            with _ltp_quotes_lock:
                _ltp_quotes_cache[key] = (ltp, time.time())
        return ltp if ltp > 0 else None
    except Exception:
        return None


# Fyers v3 order types
ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 2
ORDER_TYPE_SL_MARKET = 3  # SL-M: trigger only
ORDER_TYPE_SL_LIMIT = 4  # SL-L: trigger + limit

# Fyers v3 orderbook status codes
ORDER_STATUS_CANCELLED = 1
ORDER_STATUS_FILLED = 2
ORDER_STATUS_TRANSIT = 4
ORDER_STATUS_REJECTED = 5
ORDER_STATUS_PENDING = 6
ORDER_STATUS_EXPIRED = 7

ORDER_STATUS_LABELS = {
    ORDER_STATUS_CANCELLED: "CANCELLED",
    ORDER_STATUS_FILLED: "FILLED",
    ORDER_STATUS_TRANSIT: "TRANSIT",
    ORDER_STATUS_REJECTED: "REJECTED",
    ORDER_STATUS_PENDING: "PENDING",
    ORDER_STATUS_EXPIRED: "EXPIRED",
}

ORDER_STATUS_DEAD = {
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_REJECTED,
    ORDER_STATUS_EXPIRED,
}

TICK_SIZE = 0.05


def round_to_tick(price: float, mode: str = "nearest") -> float:
    """Fyers rejects prices that are not a multiple of the ₹0.05 tick."""
    value = float(price) / TICK_SIZE
    if mode == "down":
        ticks = math.floor(value)
    elif mode == "up":
        ticks = math.ceil(value)
    else:
        ticks = math.floor(value + 0.5)
    return round(max(ticks, 1) * TICK_SIZE, 2)


def place_market_order(symbol_name: str, side: int, quantity: int = 1) -> dict:
    """side: 1 buy, -1 sell. order type 2 = market. Returns request + response."""
    sym = to_fyers_symbol(symbol_name)
    return fyi.place_order_with_meta(sym, quantity, ORDER_TYPE_MARKET, side, 0)


def place_limit_order(
    symbol_name: str,
    side: int,
    quantity: int,
    limit_price: float,
    order_tag: str = "target",
) -> dict:
    """Plain limit order (type 1) — VWAP entries and the target leg."""
    sym = to_fyers_symbol(symbol_name)
    return fyi.place_order_with_meta(
        sym,
        quantity,
        ORDER_TYPE_LIMIT,
        side,
        round_to_tick(limit_price),
        0,
        order_tag,
    )


def place_sl_limit_order(
    symbol_name: str,
    side: int,
    quantity: int,
    trigger_price: float,
    limit_price: float,
    order_tag: str = "stoploss",
) -> dict:
    """
    SL-L order (type 4) — used for the stop loss leg.
    Fyers rule: sell needs limitPrice <= stopPrice, buy needs limitPrice >= stopPrice.
    """
    sym = to_fyers_symbol(symbol_name)
    return fyi.place_order_with_meta(
        sym,
        quantity,
        ORDER_TYPE_SL_LIMIT,
        side,
        round_to_tick(limit_price),
        round_to_tick(trigger_price),
        order_tag,
    )


def cancel_order(order_id: str) -> dict:
    """Cancel a pending order. Returns request + response for order logs."""
    if not order_id:
        return {
            "request": None,
            "response": {"s": "error", "message": "No order id"},
        }
    if not is_connected():
        return {
            "request": {"id": str(order_id)},
            "response": {"s": "error", "message": "Fyers not connected"},
        }
    try:
        return fyi.cancel_order_with_meta(order_id)
    except Exception as e:
        return {
            "request": {"id": str(order_id)},
            "response": {"s": "error", "message": str(e)},
        }


def extract_order_id(response: dict | None) -> str | None:
    if not isinstance(response, dict):
        return None
    order_id = response.get("id") or response.get("orderNumber")
    return str(order_id) if order_id else None


def _normalize_order_row(row: dict) -> dict:
    status = row.get("status")
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0

    traded_price = row.get("tradedPrice")
    try:
        traded_price = float(traded_price) if traded_price else None
    except (TypeError, ValueError):
        traded_price = None

    def _num(key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "id": str(row.get("id") or ""),
        "status": status,
        "status_label": ORDER_STATUS_LABELS.get(status, f"UNKNOWN_{status}"),
        "filled_qty": _num("filledQty"),
        "remaining_qty": _num("remainingQuantity"),
        "qty": _num("qty"),
        "traded_price": traded_price if traded_price else None,
        "limit_price": _num("limitPrice"),
        "stop_price": _num("stopPrice"),
        "order_type": row.get("type"),
        "message": row.get("message"),
    }


def fetch_order_states(order_ids: list[str]) -> dict[str, Any]:
    """
    Look up specific orders in the Fyers orderbook.
    Returns {"error": ...} or {"orders": {order_id: normalized_row}}.
    """
    ids = [str(o) for o in order_ids if o]
    if not ids:
        return {"orders": {}}
    if not is_connected():
        return {"error": "not_connected", "orders": {}}

    try:
        meta = fyi.orders_by_ids(ids)
    except Exception as e:
        return {"error": str(e), "orders": {}}

    response = meta.get("response")
    if not isinstance(response, dict) or response.get("s") != "ok":
        return {"error": response, "orders": {}, "request": meta.get("request")}

    book = response.get("orderBook") or response.get("orderbook") or []
    orders: dict[str, dict] = {}
    if isinstance(book, list):
        for row in book:
            if isinstance(row, dict):
                normalized = _normalize_order_row(row)
                if normalized["id"]:
                    orders[normalized["id"]] = normalized

    return {"orders": orders, "request": meta.get("request"), "response": response}


def is_order_successful(response: dict | None) -> bool:
    return isinstance(response, dict) and response.get("s") == "ok"


def order_status_label(response: dict | None) -> str:
    return "FILLED" if is_order_successful(response) else "REJECTED"


def timeframe_to_resolution(time_frame: str) -> str | None:
    return TIMEFRAME_TO_RESOLUTION.get((time_frame or "").strip().lower())


def _today_market_date():
    return market_tz.now().date()


def _summarize_history_response(response: dict | None) -> dict | None:
    if not isinstance(response, dict):
        return response
    summary = {
        "s": response.get("s"),
        "code": response.get("code"),
        "message": response.get("message"),
    }
    candles = response.get("candles")
    if isinstance(candles, list):
        summary["candle_count"] = len(candles)
    return summary


def _session_candles_df(df) -> pd.DataFrame:
    """Today's intraday candles used for session VWAP."""
    if df is None or df.empty:
        return pd.DataFrame()

    today = _today_market_date()
    work = df.copy()
    work["day"] = work["date"].apply(
        lambda x: x.date()
        if hasattr(x, "date")
        else pd.Timestamp(x, tz=market_tz.get_market_timezone()).date()
    )
    session = work[work["day"] == today]
    if session.empty:
        session = work
    return session.sort_values("date").reset_index(drop=True)


def _resolution_minutes(time_frame: str) -> int:
    resolution = timeframe_to_resolution(time_frame)
    return int(resolution) if resolution else 5


def _completed_session_candles(df, time_frame: str) -> pd.DataFrame:
    """Drop the still-forming candle so only closed bars are used."""
    session = _session_candles_df(df)
    if session.empty:
        return session

    resolution_mins = _resolution_minutes(time_frame)
    last_start = pd.Timestamp(session.iloc[-1]["date"])
    if last_start.tzinfo is None:
        last_start = last_start.tz_localize(market_tz.get_market_timezone())
    period_end = last_start + pd.Timedelta(minutes=resolution_mins)
    now = pd.Timestamp(market_tz.now())
    if now < period_end:
        session = session.iloc[:-1].reset_index(drop=True)
    return session


def get_last_two_completed_closes(
    df, time_frame: str
) -> tuple[float, float] | None:
    """
    Previous-to-previous and previous completed candle closes (oldest first).
    """
    completed = _completed_session_candles(df, time_frame)
    if len(completed) < 2:
        return None
    prev_prev = float(completed.iloc[-2]["close"])
    prev = float(completed.iloc[-1]["close"])
    return prev_prev, prev


def calculate_vwap_from_candles(df) -> float | None:
    """Session VWAP from today's candles: sum(tp * vol) / sum(vol)."""
    session = _session_candles_df(df)
    if session.empty:
        return None

    vol = session["volume"].astype(float)
    if vol.sum() <= 0:
        return None

    typical = (
        session["high"].astype(float)
        + session["low"].astype(float)
        + session["close"].astype(float)
    ) / 3.0
    return float((typical * vol).sum() / vol.sum())


def fetch_history_for_vwap(
    symbol_name: str, time_frame: str, days_back: int = 17
) -> dict | None:
    """
    Fetch Fyers history candles for the symbol's configured timeframe (e.g. 1h → 60).
    Returns request, summarized response, dataframe, and session VWAP.
    """
    if not is_connected() or fyi.fyers is None:
        return None

    tf = (time_frame or "").strip().lower()
    resolution = timeframe_to_resolution(tf)
    if not resolution:
        print(f"[VWAP] Unsupported timeframe: {time_frame}", flush=True)
        return None

    sym = to_fyers_symbol(symbol_name)
    today = market_tz.now().date()
    request = {
        "symbol": sym,
        "resolution": str(resolution),
        "date_format": "1",
        "range_from": str(today - timedelta(days=days_back)),
        "range_to": str(today + timedelta(days=1)),
        "cont_flag": "1",
    }

    try:
        response = fyi.fyers.history(data=request)
    except Exception as exc:
        print(f"[VWAP] History API error for {symbol_name}: {exc}", flush=True)
        return {
            "request": request,
            "response": {"s": "error", "message": str(exc)},
            "df": None,
            "vwap": None,
            "time_frame": tf,
        }

    if not isinstance(response, dict) or response.get("s") != "ok":
        print(
            f"[VWAP] History rejected for {symbol_name} ({tf}): {response}",
            flush=True,
        )
        return {
            "request": request,
            "response": _summarize_history_response(response),
            "df": None,
            "vwap": None,
            "time_frame": tf,
        }

    candles = response.get("candles") or []
    if not candles:
        print(f"[VWAP] No candles for {symbol_name} ({tf})", flush=True)
        return {
            "request": request,
            "response": _summarize_history_response(response),
            "df": None,
            "vwap": None,
            "time_frame": tf,
        }

    df = pd.DataFrame(
        candles, columns=["date", "open", "high", "low", "close", "volume"]
    )
    df["date"] = pd.to_datetime(df["date"], unit="s", utc=True).dt.tz_convert(
        market_tz.get_market_timezone()
    )
    vwap = calculate_vwap_from_candles(df)
    if vwap is None:
        print(
            f"[VWAP] Could not compute VWAP for {symbol_name} ({tf}) "
            f"from {len(candles)} candles",
            flush=True,
        )

    return {
        "request": request,
        "response": _summarize_history_response(response),
        "df": df,
        "vwap": vwap,
        "time_frame": tf,
        "candle_count": len(candles),
    }


def fetch_previous_day_close(symbol_name: str, time_frame: str) -> float | None:
    """
    Previous completed session close for the symbol's timeframe.
    For 1d: yesterday's daily close. For intraday: last candle before today.
    """
    if not is_connected() or fyi.fyers is None:
        return None

    tf = (time_frame or "").strip().lower()
    resolution = timeframe_to_resolution(tf)
    if not resolution:
        print(f"[Scanner] Unsupported timeframe: {time_frame}", flush=True)
        return None

    today = market_tz.now().date()
    days_back = 45 if tf == "1d" else 17
    sym = to_fyers_symbol(symbol_name)
    request = {
        "symbol": sym,
        "resolution": str(resolution),
        "date_format": "1",
        "range_from": str(today - timedelta(days=days_back)),
        "range_to": str(today + timedelta(days=1)),
        "cont_flag": "1",
    }

    try:
        response = fyi.fyers.history(data=request)
    except Exception as exc:
        print(
            f"[Scanner] History API error for {symbol_name}: {exc}",
            flush=True,
        )
        return None

    if not isinstance(response, dict) or response.get("s") != "ok":
        print(
            f"[Scanner] History rejected for {symbol_name} ({tf}): {response}",
            flush=True,
        )
        return None

    candles = response.get("candles") or []
    if not candles:
        return None

    df = pd.DataFrame(
        candles, columns=["date", "open", "high", "low", "close", "volume"]
    )
    df["date"] = pd.to_datetime(df["date"], unit="s", utc=True).dt.tz_convert(
        market_tz.get_market_timezone()
    )
    df["day"] = df["date"].apply(
        lambda x: x.date()
        if hasattr(x, "date")
        else pd.Timestamp(x, tz=market_tz.get_market_timezone()).date()
    )
    prior = df[df["day"] < today]
    if prior.empty:
        return None
    return float(prior.iloc[-1]["close"])


def get_vwap_with_meta(symbol_name: str, time_frame: str) -> dict | None:
    """VWAP plus Fyers history request/response metadata."""
    tf = (time_frame or "").strip().lower()
    cache_key = (symbol_name.upper(), tf)
    now = time.time()
    cached = _vwap_cache.get(cache_key)
    if cached and (now - cached[1]) < VWAP_CACHE_TTL_SEC:
        return {
            "vwap": cached[0],
            "time_frame": tf,
            "request": cached[2],
            "response": cached[3],
            "candle_count": cached[4],
            "prev_prev_close": cached[5] if len(cached) > 5 else None,
            "prev_close": cached[6] if len(cached) > 6 else None,
        }

    result = fetch_history_for_vwap(symbol_name, time_frame)
    if not result:
        return None

    vwap = result.get("vwap")
    closes = (
        get_last_two_completed_closes(result.get("df"), tf)
        if result.get("df") is not None
        else None
    )
    prev_prev_close = closes[0] if closes else None
    prev_close = closes[1] if closes else None

    if vwap is not None:
        _vwap_cache[cache_key] = (
            vwap,
            now,
            result.get("request"),
            result.get("response"),
            result.get("candle_count"),
            prev_prev_close,
            prev_close,
        )

    return {
        "vwap": vwap,
        "time_frame": result.get("time_frame"),
        "request": result.get("request"),
        "response": result.get("response"),
        "candle_count": result.get("candle_count"),
        "df": result.get("df"),
        "prev_prev_close": prev_prev_close,
        "prev_close": prev_close,
    }


def get_vwap(symbol_name: str, time_frame: str) -> float | None:
    """VWAP for symbol on configured UI timeframe (intraday session candles)."""
    meta = get_vwap_with_meta(symbol_name, time_frame)
    return meta.get("vwap") if meta else None


def vwap_band_prices(vwap: float, buffer_pct: float) -> tuple[float, float]:
    """Lower/upper prices for the entry buffer around session VWAP."""
    value = float(vwap)
    buffer = max(float(buffer_pct or 0), 0.0)
    low = value * (1 - buffer / 100.0)
    high = value * (1 + buffer / 100.0)
    return low, high


def passes_vwap_band_filter(
    signal: str,
    vwap: float | None,
    ltp: float | None,
    buffer_pct: float,
) -> tuple[bool, str, dict]:
    """
    Live LTP vs session VWAP band. Checked every engine tick.

    BUY:  VWAP * (1 - buffer%) <= LTP <= VWAP
    SELL: VWAP <= LTP <= VWAP * (1 + buffer%)

    Example: VWAP 100, buffer 2% -> BUY 98–100, SELL 100–102.
    """
    if vwap is None:
        return False, "VWAP unavailable", {}
    try:
        vwap_f = float(vwap)
    except (TypeError, ValueError):
        return False, "VWAP unavailable", {}
    if vwap_f <= 0:
        return False, "VWAP unavailable", {}

    if ltp is None:
        return False, "LTP unavailable", {"vwap": vwap_f}
    try:
        ltp_f = float(ltp)
    except (TypeError, ValueError):
        return False, "LTP unavailable", {"vwap": vwap_f}
    if ltp_f <= 0:
        return False, "LTP unavailable", {"vwap": vwap_f}

    low, high = vwap_band_prices(vwap_f, buffer_pct)
    details = {
        "vwap": vwap_f,
        "ltp": ltp_f,
        "entry_buffer_pct": float(buffer_pct or 0),
        "vwap_band_low": low,
        "vwap_band_high": high,
    }
    if signal == "BUY":
        details["band_side"] = "buy"
        if low <= ltp_f <= vwap_f:
            details["vwap_band_passed"] = True
            return True, "", details
        return (
            False,
            (
                f"BUY blocked: LTP {ltp_f:.2f} outside VWAP band "
                f"{low:.2f}–{vwap_f:.2f} (vwap={vwap_f:.2f})"
            ),
            details,
        )
    if signal == "SELL":
        details["band_side"] = "sell"
        if vwap_f <= ltp_f <= high:
            details["vwap_band_passed"] = True
            return True, "", details
        return (
            False,
            (
                f"SELL blocked: LTP {ltp_f:.2f} outside VWAP band "
                f"{vwap_f:.2f}–{high:.2f} (vwap={vwap_f:.2f})"
            ),
            details,
        )
    return False, "Unknown signal", details


def live_entry_limit_price(
    ltp: float | None,
    bid_price: float | None = None,
    ask_price: float | None = None,
) -> float | None:
    """
    Limit at the price currently trading — not a hardcoded band edge.
    Prefers live LTP, then ask, then bid. Rounded to the ₹0.05 tick.
    """
    for value in (ltp, ask_price, bid_price):
        if value is None:
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return round_to_tick(price)
    return None
