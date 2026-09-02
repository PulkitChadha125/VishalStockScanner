"""Scanner depth-based majority bias (Gate 1)."""

from __future__ import annotations

from app import fyers_service, repository


def evaluate_depth_signal(
    total_bid_qty: float, total_ask_qty: float, volume_diff: float
) -> str | None:
    """Same depth rule as watchlist: BUY / SELL / none."""
    sell_diff = total_ask_qty - total_bid_qty
    buy_diff = total_bid_qty - total_ask_qty
    if sell_diff >= volume_diff:
        return "SELL"
    if buy_diff >= volume_diff:
        return "BUY"
    return None


def majority_threshold(total: int) -> int:
    """floor(n/2)+1 — e.g. 11 of 20, 6 of 10, 51 of 100."""
    if total <= 0:
        return 0
    return total // 2 + 1


def _count_scanner_bias() -> dict:
    """
    Live depth signal per scanner symbol; majority side gates watchlist direction.
    Majority = floor(n/2)+1 of all scanner symbols (any n).
    """
    symbols = repository.list_scanner_symbols()
    buy_count = 0
    sell_count = 0
    none_count = 0
    missing = 0
    rows: list[dict] = []
    n = len(symbols)
    majority = majority_threshold(n)

    for sym in symbols:
        name = sym["symbol_name"]
        volume_diff = float(sym.get("volume_difference") or 0)
        depth = None
        if fyers_service.is_connected():
            depth = fyers_service.get_market_depth(name)

        if (
            not depth
            or depth.get("error")
            or not fyers_service.has_book_totals(depth)
        ):
            missing += 1
            rows.append(
                {
                    "id": sym.get("id"),
                    "symbol_name": name,
                    "time_frame": sym.get("time_frame"),
                    "volume_difference": volume_diff,
                    "bid_qty": None,
                    "ask_qty": None,
                    "buy_diff": None,
                    "sell_diff": None,
                    "signal": None,
                    "bias": "missing_depth",
                }
            )
            continue

        bid_qty = float(depth.get("bid_qty") or 0)
        ask_qty = float(depth.get("ask_qty") or 0)
        buy_diff = bid_qty - ask_qty
        sell_diff = ask_qty - bid_qty
        signal = evaluate_depth_signal(bid_qty, ask_qty, volume_diff)

        if signal == "BUY":
            buy_count += 1
            bias = "buy"
        elif signal == "SELL":
            sell_count += 1
            bias = "sell"
        else:
            none_count += 1
            bias = "none"

        rows.append(
            {
                "id": sym.get("id"),
                "symbol_name": name,
                "time_frame": sym.get("time_frame"),
                "volume_difference": volume_diff,
                "bid_qty": bid_qty,
                "ask_qty": ask_qty,
                "buy_diff": buy_diff,
                "sell_diff": sell_diff,
                "signal": signal,
                "bias": bias,
            }
        )

    if n > 0 and buy_count >= majority:
        allowed_signal = "BUY"
    elif n > 0 and sell_count >= majority:
        allowed_signal = "SELL"
    else:
        allowed_signal = None

    return {
        "total": n,
        "majority": majority,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "none_count": none_count,
        "flat_count": none_count,
        "missing": missing,
        "compared": buy_count + sell_count + none_count,
        "allowed_signal": allowed_signal,
        "symbols": rows,
    }


def get_live_bias() -> tuple[str | None, dict]:
    """
    Continuous live bias from scanner depth signals.
    BUY count >= majority -> BUY only.
    SELL count >= majority -> SELL only.
    Else -> None (no entries).
    """
    details = _count_scanner_bias()
    if not details["total"]:
        return None, {**details, "skipped": True, "reason": "no_scanner_symbols"}
    return details.get("allowed_signal"), details


def evaluate_scanner_gate(signal: str) -> tuple[bool, dict]:
    """True when the requested signal matches the current live scanner majority side."""
    allowed, details = get_live_bias()
    if details.get("skipped"):
        return True, {**details, "signal": signal, "passed": True}

    passed = allowed is not None and signal == allowed
    return passed, {
        **details,
        "signal": signal,
        "passed": passed,
    }


def scanner_status_summary() -> dict:
    """Live scanner bias for the UI (polled every few seconds)."""
    allowed, details = get_live_bias()
    return {
        "total": details.get("total", 0),
        "majority": details.get("majority", 0),
        "buy_count": details.get("buy_count", 0),
        "sell_count": details.get("sell_count", 0),
        "none_count": details.get("none_count", 0),
        "flat_count": details.get("none_count", 0),
        "missing": details.get("missing", 0),
        "compared": details.get("compared", 0),
        "allowed_signal": allowed,
        "is_neutral": allowed is None and not details.get("skipped"),
        "symbols": details.get("symbols", []),
    }
