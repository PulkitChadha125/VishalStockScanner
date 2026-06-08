"""
Background strategy loop: market depth scan, entries, SL/target exits.
One open position at a time; max trades per day across all symbols.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time

from app import fyers_service, market_tz, repository

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_open_position: OpenPosition | None = None
_last_tick_at: str | None = None
_last_signal: str | None = None

DEFAULT_QTY = int(os.environ.get("STRATEGY_DEFAULT_QTY", "1"))


@dataclass
class OpenPosition:
    trade_id: int
    symbol_name: str
    fyers_symbol: str
    side: int  # 1=BUY, -1=SELL
    side_label: str
    entry_price: float
    quantity: int
    stop_loss_pct: float
    target_pct: float
    stop_loss_price: float
    target_price: float
    opened_at: str
    entry_status: str


def _now_market() -> datetime:
    return market_tz.now()


def _in_trading_window(start_hhmm: str, stop_hhmm: str) -> bool:
    now = _now_market().time()
    sh, sm = map(int, start_hhmm.split(":"))
    eh, em = map(int, stop_hhmm.split(":"))
    start = dt_time(sh, sm)
    end = dt_time(eh, em)
    return start <= now < end


def is_in_trading_window(
    start_hhmm: str | None = None, stop_hhmm: str | None = None
) -> bool:
    settings = repository.get_strategy_settings()
    return _in_trading_window(
        start_hhmm or settings["start_time"],
        stop_hhmm or settings["stop_time"],
    )


def probe_symbol_market_data() -> list[dict]:
    """Depth probe via WebSocket cache or one REST call per symbol."""
    rows: list[dict] = []
    symbols = repository.list_symbols()
    ws_active = fyers_service.is_market_ws_active()
    for i, sym in enumerate(symbols):
        name = sym["symbol_name"]
        if not ws_active and i > 0:
            time.sleep(fyers_service.DEPTH_MIN_INTERVAL_SEC)
        depth = fyers_service.get_market_depth(name, max_age_sec=30)
        if not depth and not ws_active:
            depth = fyers_service.fetch_market_depth_immediate(name)
        rows.append({"symbol_name": name, "depth": depth})
        if depth and not depth.get("error"):
            print(
                f"[PROBE] {name} bid={depth.get('bid_price')} ask={depth.get('ask_price')} "
                f"book_buy={depth.get('bid_qty')} book_sell={depth.get('ask_qty')}",
                flush=True,
            )
        else:
            print(f"[PROBE] {name}: {depth.get('error') if depth else 'no data'}", flush=True)
    return rows


def _log_app(description: str, details: dict | None = None):
    repository.create_app_log(
        "strategy",
        description,
        page_path="/strategy/engine",
        details=details,
    )


def _log_order(
    symbol_name: str,
    side: str,
    status: str,
    price: float | None,
    quantity: float,
    stop_loss: float | None = None,
    target: float | None = None,
    order_type: str = "MARKET",
):
    repository.create_order_log(
        symbol_name=symbol_name,
        side=side,
        order_type=order_type,
        quantity=quantity,
        status=status,
        price=price,
        stop_loss=stop_loss,
        target=target,
    )


def _calc_sl_target(entry: float, side: int, sl_pct: float, tgt_pct: float):
    if side == 1:
        sl = entry * (1 - sl_pct / 100)
        tgt = entry * (1 + tgt_pct / 100)
    else:
        sl = entry * (1 + sl_pct / 100)
        tgt = entry * (1 - tgt_pct / 100)
    return sl, tgt


def _print_trade_result(trade: dict):
    print(
        (
            f"[TRADE] {trade['symbol_name']} {trade['side']} "
            f"entry={trade['entry_time']} @ {trade['entry_price']:.2f} "
            f"exit={trade['exit_time']} @ {trade['exit_price']:.2f} "
            f"reason={trade['exit_reason']} pnl={trade['pnl']:.2f} "
            f"(entry={trade['entry_status']}, exit={trade['exit_status']})"
        ),
        flush=True,
    )


def get_open_position() -> dict | None:
    _sync_open_position_from_db()
    with _lock:
        return asdict(_open_position) if _open_position else None


def _restore_open_position_from_trade(trade: dict) -> None:
    global _open_position

    sym = repository.get_symbol_by_name(trade["symbol_name"])
    side = 1 if trade["side"] == "BUY" else -1
    sl_pct = float(sym["stop_loss_pct"]) if sym else 0.0
    tgt_pct = float(sym["target_pct"]) if sym else 0.0

    with _lock:
        _open_position = OpenPosition(
            trade_id=trade["id"],
            symbol_name=trade["symbol_name"],
            fyers_symbol=fyers_service.to_fyers_symbol(trade["symbol_name"]),
            side=side,
            side_label=trade["side"],
            entry_price=float(trade["entry_price"]),
            quantity=int(trade["quantity"]),
            stop_loss_pct=sl_pct,
            target_pct=tgt_pct,
            stop_loss_price=float(trade["stop_loss"] or 0),
            target_price=float(trade["target"] or 0),
            opened_at=trade["entry_time"],
            entry_status=trade["entry_status"],
        )


def _sync_open_position_from_db() -> bool:
    """Load the open trade from DB into memory when the engine restarted."""
    global _open_position

    with _lock:
        if _open_position is not None:
            return True

    trade = repository.get_open_trade()
    if not trade:
        return False

    _restore_open_position_from_trade(trade)
    _log_app(
        f"Restored open position in {trade['symbol_name']} — "
        "new entries blocked until SL/target",
        {"trade_id": trade["id"]},
    )
    print(
        f"[POSITION] Restored open trade in {trade['symbol_name']} "
        f"(id={trade['id']}) — blocking other symbols",
        flush=True,
    )
    return True


def _has_open_position() -> bool:
    if _sync_open_position_from_db():
        return True
    return repository.has_open_trade()


def _open_position_symbol() -> str | None:
    with _lock:
        if _open_position is not None:
            return _open_position.symbol_name
    trade = repository.get_open_trade()
    return trade["symbol_name"] if trade else None


def get_engine_status() -> dict:
    settings = repository.get_strategy_settings()
    engine_alive = _thread is not None and _thread.is_alive()

    # On app restart/crash recovery, DB may say running but no engine thread exists.
    if settings.get("is_running") and not engine_alive:
        repository.set_strategy_running(False)
        settings = repository.get_strategy_settings()

    # Reflect live API connectivity so UI buttons are correct.
    if settings.get("api_connected") != fyers_service.is_connected():
        repository.set_api_connected(fyers_service.is_connected())
        settings = repository.get_strategy_settings()

    bal = fyers_service.get_cached_balance()
    if bal is None and fyers_service.is_connected():
        bal, _ = fyers_service.fetch_balance()
    return {
        **settings,
        "trades_taken_today": repository.count_trades_today(),
        "can_take_more_trades": repository.can_take_more_trades(),
        "available_balance": bal,
        "open_position": get_open_position(),
        "last_tick_at": _last_tick_at,
        "last_signal": _last_signal,
        "engine_alive": engine_alive,
    }


def is_engine_running() -> bool:
    return _thread is not None and _thread.is_alive() and not _stop_event.is_set()


def _evaluate_signal(
    total_bid_qty: float, total_ask_qty: float, volume_diff: float
) -> str | None:
    """Compare summed bid-book qty vs summed ask-book qty (all depth levels)."""
    sell_diff = total_ask_qty - total_bid_qty
    buy_diff = total_bid_qty - total_ask_qty
    if sell_diff >= volume_diff:
        return "SELL"
    if buy_diff >= volume_diff:
        return "BUY"
    return None


def _enter_trade(symbol: dict, signal: str, depth: dict):
    global _open_position, _last_signal

    if _has_open_position():
        open_sym = _open_position_symbol()
        print(
            f"[ENTRY BLOCKED] Open trade in {open_sym} — "
            f"ignoring {signal} on {symbol['symbol_name']}",
            flush=True,
        )
        _last_signal = f"BLOCKED_{open_sym}"
        return

    side = 1 if signal == "BUY" else -1
    entry_price = depth["ask_price"] if side == 1 else depth["bid_price"]
    if not entry_price or entry_price <= 0:
        entry_price = fyers_service.get_ltp(symbol["symbol_name"]) or 0
    if entry_price <= 0:
        _log_app(f"Entry skipped — no price for {symbol['symbol_name']}")
        return

    time_frame = symbol.get("time_frame", "5m")
    vwap_meta = fyers_service.get_vwap_with_meta(symbol["symbol_name"], time_frame)
    vwap = vwap_meta.get("vwap") if vwap_meta else None
    if vwap is None:
        _log_app(
            f"Entry skipped — VWAP unavailable for {symbol['symbol_name']} ({time_frame})",
            {
                "entry_price": entry_price,
                "time_frame": time_frame,
                "vwap_api_request": vwap_meta.get("request") if vwap_meta else None,
                "vwap_api_response": vwap_meta.get("response") if vwap_meta else None,
            },
        )
        print(
            f"[VWAP] {symbol['symbol_name']} ({time_frame}): unavailable, entry skipped",
            flush=True,
        )
        return

    vwap_ok, vwap_reason = fyers_service.passes_vwap_filter(signal, entry_price, vwap)
    print(
        f"[VWAP] {symbol['symbol_name']} tf={time_frame} entry={entry_price:.2f} "
        f"vwap={vwap:.2f} signal={signal} allowed={vwap_ok}",
        flush=True,
    )
    if not vwap_ok:
        _log_app(
            f"{vwap_reason} on {symbol['symbol_name']}",
            {"entry_price": entry_price, "vwap": vwap, "signal": signal},
        )
        return

    sl_pct = float(symbol["stop_loss_pct"])
    tgt_pct = float(symbol["target_pct"])
    sl_price, tgt_price = _calc_sl_target(entry_price, side, sl_pct, tgt_pct)

    qty = DEFAULT_QTY
    entry_time = _now_market().strftime("%Y-%m-%d %H:%M:%S")
    order_result = fyers_service.place_market_order(symbol["symbol_name"], side, qty)
    resp = order_result.get("response")
    entry_status = fyers_service.order_status_label(resp)
    _log_app(
        f"ENTRY {signal} {symbol['symbol_name']} @ {entry_price:.2f} ({entry_status})",
        {"request": order_result.get("request"), "response": resp, "depth": depth},
    )
    _log_order(
        symbol["symbol_name"],
        signal,
        f"ENTRY_{entry_status}",
        entry_price,
        qty,
        stop_loss=sl_price,
        target=tgt_price,
    )

    trade = repository.create_trade(
        symbol_name=symbol["symbol_name"],
        side=signal,
        quantity=qty,
        entry_price=entry_price,
        entry_status=entry_status,
        stop_loss=sl_price,
        target=tgt_price,
        entry_time=entry_time,
        details={
            "vwap": vwap,
            "time_frame": time_frame,
            "vwap_candle_count": vwap_meta.get("candle_count"),
            "vwap_api_request": vwap_meta.get("request"),
            "vwap_api_response": vwap_meta.get("response"),
            "vwap_filter_passed": True,
            "entry_api_request": order_result.get("request"),
            "entry_api_response": resp,
        },
    )

    # Track position in backend even when Fyers rejects the order (paper SL/target).
    with _lock:
        _open_position = OpenPosition(
            trade_id=trade["id"],
            symbol_name=symbol["symbol_name"],
            fyers_symbol=depth["symbol"],
            side=side,
            side_label=signal,
            entry_price=entry_price,
            quantity=qty,
            stop_loss_pct=sl_pct,
            target_pct=tgt_pct,
            stop_loss_price=sl_price,
            target_price=tgt_price,
            opened_at=entry_time,
            entry_status=entry_status,
        )
        _last_signal = f"{signal} {symbol['symbol_name']}"


def _exit_trade(reason: str, exit_price: float | None = None):
    global _open_position

    with _lock:
        pos = _open_position
        if not pos:
            return
        _open_position = None

    exit_side = -pos.side
    ltp = exit_price if exit_price is not None else (
        fyers_service.get_ltp(pos.symbol_name) or pos.entry_price
    )
    exit_time = _now_market().strftime("%Y-%m-%d %H:%M:%S")
    order_result = fyers_service.place_market_order(
        pos.symbol_name, exit_side, pos.quantity
    )
    resp = order_result.get("response")
    exit_status = fyers_service.order_status_label(resp)
    pnl = repository.calc_trade_pnl(
        pos.side, pos.entry_price, ltp, pos.quantity
    )
    status = f"EXIT_{reason}_{exit_status}"
    _log_app(
        f"{status} {pos.side_label} {pos.symbol_name} @ {ltp:.2f} pnl={pnl:.2f}",
        {
            "request": order_result.get("request"),
            "response": resp,
            "position": asdict(pos),
            "pnl": pnl,
        },
    )
    _log_order(
        pos.symbol_name,
        "SELL" if pos.side == 1 else "BUY",
        status,
        ltp,
        pos.quantity,
        stop_loss=pos.stop_loss_price,
        target=pos.target_price,
    )

    trade = repository.close_trade(
        trade_id=pos.trade_id,
        exit_price=ltp,
        exit_reason=reason,
        exit_status=exit_status,
        pnl=pnl,
        exit_time=exit_time,
        details_update={
            "exit_api_request": order_result.get("request"),
            "exit_api_response": resp,
        },
    )
    if trade:
        _print_trade_result(trade)


def _monitor_open_position():
    with _lock:
        pos = _open_position
    if not pos:
        return

    ltp = fyers_service.get_ltp(pos.symbol_name)
    if ltp is None:
        return

    if pos.side == 1:
        if ltp <= pos.stop_loss_price:
            _exit_trade("SL", exit_price=ltp)
        elif ltp >= pos.target_price:
            _exit_trade("TARGET", exit_price=ltp)
    else:
        if ltp >= pos.stop_loss_price:
            _exit_trade("SL", exit_price=ltp)
        elif ltp <= pos.target_price:
            _exit_trade("TARGET", exit_price=ltp)


def _scan_for_entry():
    global _last_signal

    if not repository.can_take_more_trades():
        if _last_signal != "MAX_TRADES_REACHED":
            _last_signal = "MAX_TRADES_REACHED"
            settings = repository.get_strategy_settings()
            taken = repository.count_trades_today()
            _log_app(
                f"Max trades reached for day ({taken}/{settings['max_trades']}).",
                {"trades_taken_today": taken, "max_trades": settings["max_trades"]},
            )
        return

    if _has_open_position():
        open_sym = _open_position_symbol()
        blocked_key = f"WAITING_{open_sym}"
        if _last_signal != blocked_key:
            _last_signal = blocked_key
            print(
                f"[ENTRY BLOCKED] Open trade in {open_sym} — "
                "no new entries until SL or target",
                flush=True,
            )
        return

    symbols = repository.list_symbols()
    if not symbols:
        return

    tick_ts = market_tz.now_ist().strftime("%H:%M:%S")
    symbol_names = [s["symbol_name"] for s in symbols]
    ws_active = fyers_service.is_market_ws_active()
    refreshed = fyers_service.tick_depth_refresh(symbol_names)

    for sym in symbols:
        name = sym["symbol_name"]
        depth = fyers_service.get_market_depth(name)
        if not depth:
            wait_msg = (
                "waiting for websocket feed"
                if ws_active
                else "waiting for cache (1 symbol/sec REST refresh)"
            )
            print(f"[DEPTH {tick_ts}] {name}: {wait_msg}", flush=True)
            continue

        if depth.get("error"):
            print(
                f"[DEPTH {tick_ts}] {name}: error={depth.get('error')}",
                flush=True,
            )
            continue

        if not fyers_service.has_book_totals(depth):
            print(
                f"[DEPTH {tick_ts}] {name}: waiting for book totals (REST 1/sec)",
                flush=True,
            )
            continue

        if depth.get("source") == "websocket":
            cache_note = f" ws age={depth.get('cache_age_sec', '?')}s"
        elif name == refreshed:
            cache_note = " fresh"
        else:
            cache_note = f" cached={depth.get('cache_age_sec', '?')}s"

        bid_qty = float(depth["bid_qty"])
        ask_qty = float(depth["ask_qty"])
        bid_price = float(depth["bid_price"])
        ask_price = float(depth["ask_price"])
        volume_diff = float(sym["volume_difference"])
        sell_diff = ask_qty - bid_qty
        buy_diff = bid_qty - ask_qty

        signal = _evaluate_signal(
            bid_qty,
            ask_qty,
            volume_diff,
        )
        vwap_note = ""
        if signal:
            entry_preview = ask_price if signal == "BUY" else bid_price
            vwap_val = fyers_service.get_vwap(sym["symbol_name"], sym["time_frame"])
            if vwap_val is not None:
                ok, _ = fyers_service.passes_vwap_filter(
                    signal, entry_preview, vwap_val
                )
                vwap_note = f" vwap={vwap_val:.2f} vwap_ok={ok}"
            else:
                vwap_note = " vwap=NA"

        print(
            (
                f"[DEPTH {tick_ts}] {name}{cache_note} "
                f"bid_p={bid_price:.2f} ask_p={ask_price:.2f} "
                f"book_buy={bid_qty:.0f} book_sell={ask_qty:.0f} "
                f"sell_diff={sell_diff:.0f} buy_diff={buy_diff:.0f} "
                f"threshold={volume_diff:.0f} tf={sym['time_frame']} "
                f"signal={signal or 'NONE'}{vwap_note}"
            ),
            flush=True,
        )
        if not signal:
            continue

        if _has_open_position():
            return

        _log_app(
            f"Signal {signal} on {sym['symbol_name']}: "
            f"book_buy={depth['bid_qty']} book_sell={depth['ask_qty']} "
            f"need>={sym['volume_difference']}",
            {"depth": depth},
        )
        _enter_trade(sym, signal, depth)
        return


def _tick():
    global _last_tick_at

    settings = repository.get_strategy_settings()
    if not settings.get("is_running"):
        return

    _last_tick_at = _now_market().strftime("%Y-%m-%d %H:%M:%S")

    if not fyers_service.is_connected():
        _log_app("Engine tick skipped — Fyers not connected")
        return

    if not _in_trading_window(settings["start_time"], settings["stop_time"]):
        return

    fyers_service.fetch_balance()

    if _has_open_position():
        _monitor_open_position()
    else:
        _scan_for_entry()


def _run_loop():
    global _thread
    _log_app("Strategy engine thread started")
    tick_count = 0
    while not _stop_event.is_set():
        try:
            _tick()
        except Exception as e:
            _log_app(f"Engine tick error: {e}")
        tick_count += 1
        if tick_count % 30 == 0:
            fyers_service.fetch_balance()
        _stop_event.wait(1.0)
    _log_app("Strategy engine thread stopped")
    with _lock:
        _thread = None


def start() -> tuple[bool, str]:
    global _thread

    if not fyers_service.is_connected():
        return False, "Login to Fyers API first."

    symbols = repository.list_symbols()
    if not symbols:
        return False, "Add at least one symbol before starting."

    fyers_service.sync_market_websocket()

    if is_engine_running():
        return False, "Strategy engine is already running."

    _sync_open_position_from_db()

    _stop_event.clear()
    repository.set_strategy_running(True)
    _thread = threading.Thread(target=_run_loop, name="strategy-engine", daemon=True)
    _thread.start()
    _log_app("Strategy started via engine")
    return True, ""


def stop(square_off: bool = True) -> tuple[bool, str]:
    _stop_event.set()
    repository.set_strategy_running(False)

    if square_off:
        with _lock:
            has_open = _open_position is not None
        if has_open:
            _exit_trade("STOP")

    if _thread and _thread.is_alive():
        _thread.join(timeout=3.0)

    reset_session_state()
    _log_app("Strategy stopped via engine")
    return True, ""


def reset_session_state() -> None:
    """Clear in-memory strategy flags after Stop."""
    global _open_position, _last_signal, _last_tick_at, _thread

    with _lock:
        _open_position = None
        _last_signal = None
        _last_tick_at = None
        _thread = None
    fyers_service.clear_depth_cache()
