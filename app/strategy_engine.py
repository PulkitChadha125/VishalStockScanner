"""
Background strategy loop: market depth scan, entries, SL/target exits.
One open position at a time; max trades per day across all symbols.
"""

from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time

import pytz

from app import fyers_service, repository

IST = pytz.timezone("Asia/Kolkata")

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_open_position: OpenPosition | None = None
_last_tick_at: str | None = None
_last_signal: str | None = None

DEFAULT_QTY = int(os.environ.get("STRATEGY_DEFAULT_QTY", "1"))


@dataclass
class OpenPosition:
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


def _now_ist() -> datetime:
    return datetime.now(IST)


def _in_trading_window(start_hhmm: str, stop_hhmm: str) -> bool:
    now = _now_ist().time()
    sh, sm = map(int, start_hhmm.split(":"))
    eh, em = map(int, stop_hhmm.split(":"))
    start = dt_time(sh, sm)
    end = dt_time(eh, em)
    return start <= now < end


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


def get_open_position() -> dict | None:
    with _lock:
        return asdict(_open_position) if _open_position else None


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


def _evaluate_signal(bid_qty: float, ask_qty: float, volume_diff: float) -> str | None:
    sell_diff = ask_qty - bid_qty
    buy_diff = bid_qty - ask_qty
    if sell_diff >= volume_diff:
        return "SELL"
    if buy_diff >= volume_diff:
        return "BUY"
    return None


def _enter_trade(symbol: dict, signal: str, depth: dict):
    global _open_position, _last_signal

    side = 1 if signal == "BUY" else -1
    entry_price = depth["ask_price"] if side == 1 else depth["bid_price"]
    if not entry_price or entry_price <= 0:
        entry_price = fyers_service.get_ltp(symbol["symbol_name"]) or 0
    if entry_price <= 0:
        _log_app(f"Entry skipped — no price for {symbol['symbol_name']}")
        return

    time_frame = symbol.get("time_frame", "5m")
    vwap = fyers_service.get_vwap(symbol["symbol_name"], time_frame)
    if vwap is None:
        _log_app(
            f"Entry skipped — VWAP unavailable for {symbol['symbol_name']} ({time_frame})",
            {"entry_price": entry_price, "time_frame": time_frame},
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
    resp = fyers_service.place_market_order(symbol["symbol_name"], side, qty)
    _log_app(
        f"ENTRY {signal} {symbol['symbol_name']} @ {entry_price:.2f}",
        {"response": resp, "depth": depth},
    )
    _log_order(
        symbol["symbol_name"],
        signal,
        "ENTRY",
        entry_price,
        qty,
        stop_loss=sl_price,
        target=tgt_price,
    )

    with _lock:
        _open_position = OpenPosition(
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
            opened_at=_now_ist().isoformat(),
        )
        _last_signal = f"{signal} {symbol['symbol_name']}"


def _exit_trade(reason: str):
    global _open_position

    with _lock:
        pos = _open_position
        if not pos:
            return
        _open_position = None

    exit_side = -pos.side
    ltp = fyers_service.get_ltp(pos.symbol_name) or pos.entry_price
    resp = fyers_service.place_market_order(
        pos.symbol_name, exit_side, pos.quantity
    )
    status = f"EXIT_{reason}"
    _log_app(
        f"{status} {pos.side_label} {pos.symbol_name} @ {ltp:.2f}",
        {"response": resp, "position": asdict(pos)},
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
            _exit_trade("SL")
        elif ltp >= pos.target_price:
            _exit_trade("TARGET")
    else:
        if ltp >= pos.stop_loss_price:
            _exit_trade("SL")
        elif ltp <= pos.target_price:
            _exit_trade("TARGET")


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

    with _lock:
        if _open_position is not None:
            return

    symbols = repository.list_symbols()
    if not symbols:
        return

    tick_ts = _now_ist().strftime("%H:%M:%S")
    for sym in symbols:
        depth = fyers_service.get_market_depth(sym["symbol_name"])
        if not depth:
            print(
                f"[DEPTH {tick_ts}] {sym['symbol_name']}: no response",
                flush=True,
            )
            continue

        if depth.get("error"):
            print(
                f"[DEPTH {tick_ts}] {sym['symbol_name']}: error={depth.get('error')}",
                flush=True,
            )
            continue

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
                f"[DEPTH {tick_ts}] {sym['symbol_name']} "
                f"bid_p={bid_price:.2f} ask_p={ask_price:.2f} "
                f"bid_q={bid_qty:.2f} ask_q={ask_qty:.2f} "
                f"sell_diff={sell_diff:.2f} buy_diff={buy_diff:.2f} "
                f"threshold={volume_diff:.2f} tf={sym['time_frame']} "
                f"signal={signal or 'NONE'}{vwap_note}"
            ),
            flush=True,
        )
        if not signal:
            continue

        with _lock:
            if _open_position is not None:
                return

        _log_app(
            f"Signal {signal} on {sym['symbol_name']}: "
            f"bid={depth['bid_qty']} ask={depth['ask_qty']} "
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

    _last_tick_at = _now_ist().strftime("%Y-%m-%d %H:%M:%S")

    if not fyers_service.is_connected():
        _log_app("Engine tick skipped — Fyers not connected")
        return

    if not _in_trading_window(settings["start_time"], settings["stop_time"]):
        return

    fyers_service.fetch_balance()

    with _lock:
        has_open = _open_position is not None

    if has_open:
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

    if is_engine_running():
        return False, "Strategy engine is already running."

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

    _log_app("Strategy stopped via engine")
    return True, ""
