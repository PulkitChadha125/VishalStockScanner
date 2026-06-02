"""Wrapper around project-root FyresIntegration.py for Flask strategy use."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

# Import FyresIntegration from project root
_root = str(BASE_DIR)
if _root not in sys.path:
    sys.path.insert(0, _root)

import FyresIntegration as fyi  # noqa: E402

from app.fyers_credentials import load_credentials

_connected = False
_last_balance: float | None = None
_last_balance_detail: dict[str, Any] | None = None


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
        return False, err or "Login failed", None

    ok, verr = fyi.verify_profile_ok()
    if not ok:
        _connected = False
        return False, verr or "Profile verification failed", None

    _connected = True
    bal, detail = fetch_balance()
    _last_balance = bal
    _last_balance_detail = detail
    return True, "", bal


def logout() -> None:
    global _connected, _last_balance, _last_balance_detail
    _connected = False
    _last_balance = None
    _last_balance_detail = None
    fyi.fyers = None
    fyi.access_token = None


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


def get_market_depth(symbol_name: str) -> dict[str, Any] | None:
    """
    Fetch market depth for symbol. Returns best bid/ask price and quantity.
    """
    if not is_connected():
        return None

    sym = to_fyers_symbol(symbol_name)
    try:
        res = fyi.fyers.depth(data={"symbol": sym, "ohlcv_flag": "0"})
    except Exception as e:
        return {"error": str(e), "symbol": sym}

    if not isinstance(res, dict) or res.get("s") != "ok":
        return {"error": res, "symbol": sym}

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

    bid_p, bid_q = _level(bids, 0)
    ask_p, ask_q = _level(asks, 0)

    return {
        "symbol": sym,
        "bid_price": bid_p,
        "bid_qty": bid_q,
        "ask_price": ask_p,
        "ask_qty": ask_q,
        "raw": book,
    }


def get_ltp(symbol_name: str) -> float | None:
    if not is_connected():
        return None
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
