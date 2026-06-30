"""Scanner prep (previous close) and live macro entry bias."""

from __future__ import annotations

import time

from app import fyers_service, market_tz, repository


def prepare_prev_closes() -> dict[str, float | None]:
    """
    Fetch previous trading day close for every scanner symbol.
    Returns {symbol_name: close or None}.
    """
    symbols = repository.list_scanner_symbols()
    if not symbols:
        return {}

    if not fyers_service.is_connected():
        print("[Scanner] Cannot fetch prev close — Fyers not connected", flush=True)
        return {}

    fetched_at = market_tz.now().strftime("%Y-%m-%d %H:%M:%S")
    results: dict[str, float | None] = {}

    for i, sym in enumerate(symbols):
        name = sym["symbol_name"]
        tf = sym["time_frame"]
        if i > 0:
            time.sleep(fyers_service.DEPTH_MIN_INTERVAL_SEC)
        close = fyers_service.fetch_previous_day_close(name, tf)
        results[name] = close
        if close is not None:
            repository.update_scanner_prev_close(sym["id"], close, fetched_at)
            print(f"[Scanner] {name} ({tf}) prev close = {close:.2f}", flush=True)
        else:
            print(f"[Scanner] {name} ({tf}) prev close unavailable", flush=True)

    ok_count = sum(1 for v in results.values() if v is not None)
    repository.create_app_log(
        "scanner",
        f"Scanner prev-close refresh: {ok_count}/{len(symbols)} symbols",
        page_path="/scanner",
        details={"results": results},
    )
    return results


def _refresh_scanner_ltps() -> None:
    symbols = repository.list_scanner_symbols()
    if not symbols or not fyers_service.is_connected():
        return
    names = [s["symbol_name"] for s in symbols]
    fyers_service.refresh_ltps_via_quotes(names)


def _count_scanner_bias() -> dict:
    """
    Live count: how many scanner symbols are above vs below prev close.
    Works for any list size (19, 20, 21, …). Prev close is static; LTP is live.
    """
    _refresh_scanner_ltps()
    symbols = repository.list_scanner_symbols()
    buy_count = 0
    sell_count = 0
    flat_count = 0
    missing = 0
    rows: list[dict] = []

    for sym in symbols:
        name = sym["symbol_name"]
        prev = sym.get("prev_close")
        if prev is None:
            missing += 1
            rows.append(
                {
                    "symbol_name": name,
                    "prev_close": None,
                    "ltp": None,
                    "bias": "missing_prev_close",
                }
            )
            continue

        ltp = fyers_service.get_ltp(name)
        if ltp is None or ltp <= 0:
            missing += 1
            rows.append(
                {
                    "symbol_name": name,
                    "prev_close": prev,
                    "ltp": ltp,
                    "bias": "missing_ltp",
                }
            )
            continue

        if ltp > prev:
            buy_count += 1
            bias = "buy"
        elif ltp < prev:
            sell_count += 1
            bias = "sell"
        else:
            flat_count += 1
            bias = "flat"

        rows.append(
            {
                "symbol_name": name,
                "prev_close": prev,
                "ltp": ltp,
                "bias": bias,
            }
        )

    if buy_count > sell_count:
        allowed_signal = "BUY"
    elif sell_count > buy_count:
        allowed_signal = "SELL"
    else:
        allowed_signal = None

    return {
        "total": len(symbols),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "flat_count": flat_count,
        "missing": missing,
        "compared": buy_count + sell_count + flat_count,
        "allowed_signal": allowed_signal,
        "symbols": rows,
    }


def get_live_bias() -> tuple[str | None, dict]:
    """
    Continuous live bias from scanner symbols.
    More stocks above prev close -> BUY only.
    More stocks below prev close -> SELL only.
    Equal counts -> None (no entries).
    """
    details = _count_scanner_bias()
    if not details["total"]:
        return None, {**details, "skipped": True, "reason": "no_scanner_symbols"}
    return details.get("allowed_signal"), details


def evaluate_scanner_gate(signal: str) -> tuple[bool, dict]:
    """True when the requested signal matches the current live scanner winner."""
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
        "buy_count": details.get("buy_count", 0),
        "sell_count": details.get("sell_count", 0),
        "flat_count": details.get("flat_count", 0),
        "missing": details.get("missing", 0),
        "compared": details.get("compared", 0),
        "allowed_signal": allowed,
        "is_neutral": allowed is None and not details.get("skipped"),
        "symbols": details.get("symbols", []),
    }
