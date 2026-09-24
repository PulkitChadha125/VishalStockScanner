"""
Background strategy loop: market depth scan, entries, SL/target exits.
One open position at a time; max trades per day across all symbols.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time

from app import fyers_service, market_tz, repository, scanner_service

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_open_position: OpenPosition | None = None
_last_tick_at: str | None = None
_last_signal: str | None = None
_last_scanner_bias: str | None = None
# Exit legs whose cancellation the broker has not confirmed yet.
_pending_leg_cancels: list[dict] = []

FALLBACK_ENTRY_QTY = 1

# Exits are parked in the market right after entry: target = plain limit order (type 1),
# stop loss = SL-L (type 4, trigger + limit). Fyers rejects a sell trigger above LTP,
# which is why the target cannot be an SL order.
# The SL-L limit price sits this far beyond the trigger so a fast move still fills.
SL_LIMIT_BUFFER_PCT = 0.10
# Consecutive orderbook poll failures tolerated before falling back to local monitoring.
LEG_POLL_MAX_ERRORS = 10
ENTRY_FILL_POLL_ATTEMPTS = 4
ENTRY_FILL_POLL_DELAY_SEC = 0.4
# A cancel is only trusted once the orderbook confirms the leg is gone.
LEG_CANCEL_ATTEMPTS = 3
LEG_CANCEL_RETRY_DELAY_SEC = 0.4


def _leverage_multiplier() -> float:
    settings = repository.get_strategy_settings()
    return float(settings.get("leverage_multiplier") or 1)


def calc_exposure_quantity(
    balance: float,
    leverage_multiplier: float,
    share_price: float,
) -> tuple[int | None, dict]:
    """
    Exposure = balance × leverage; qty = floor(exposure / share_price).
    Example: ₹1L × 5 = ₹5L exposure; share ₹5000 → 100 shares.
    """
    exposure = float(balance) * float(leverage_multiplier)
    meta: dict = {
        "available_balance": round(float(balance), 2),
        "leverage_multiplier": float(leverage_multiplier),
        "exposure": round(exposure, 2),
        "share_value": round(float(share_price), 2),
    }
    if share_price <= 0:
        meta["order_quantity"] = None
        meta["order_value"] = None
        return None, meta

    qty = int(math.floor(exposure / share_price))
    order_value = qty * share_price
    meta["order_quantity"] = qty
    meta["order_value"] = round(order_value, 2)
    if qty < 1:
        return None, meta
    return qty, meta


def resolve_entry_quantity(
    balance: float | None,
    leverage_multiplier: float,
    share_price: float,
) -> tuple[int, dict]:
    """
    Use exposure-based qty when balance supports at least 1 share.
    Otherwise fall back to 1 share and still send the order to Fyers.
    """
    if (
        balance is not None
        and balance > 0
        and share_price > 0
    ):
        qty, sizing = calc_exposure_quantity(balance, leverage_multiplier, share_price)
        if qty is not None and qty >= 1:
            sizing["sizing_mode"] = "exposure"
            return qty, sizing

        exposure = float(balance) * float(leverage_multiplier)
        return FALLBACK_ENTRY_QTY, {
            "available_balance": round(float(balance), 2),
            "leverage_multiplier": float(leverage_multiplier),
            "exposure": round(exposure, 2),
            "share_value": round(float(share_price), 2),
            "order_quantity": FALLBACK_ENTRY_QTY,
            "order_value": round(FALLBACK_ENTRY_QTY * share_price, 2),
            "sizing_mode": "fallback_insufficient_exposure",
        }

    return FALLBACK_ENTRY_QTY, {
        "available_balance": round(float(balance), 2) if balance is not None else None,
        "leverage_multiplier": float(leverage_multiplier),
        "exposure": None,
        "share_value": round(float(share_price), 2) if share_price > 0 else None,
        "order_quantity": FALLBACK_ENTRY_QTY,
        "order_value": round(FALLBACK_ENTRY_QTY * share_price, 2)
        if share_price > 0
        else None,
        "sizing_mode": "fallback_no_balance",
    }


ENTRY_WORKING_STATUSES = {"PENDING", "PLACED"}


def _place_entry_order(
    symbol_name: str, side: int, qty: int, limit_price: float | None = None
) -> dict:
    """Always return request + response dict for order logs."""
    sym = fyers_service.to_fyers_symbol(symbol_name)
    order_type = fyers_service.ORDER_TYPE_LIMIT if limit_price else fyers_service.ORDER_TYPE_MARKET
    price = float(limit_price or 0)
    try:
        import FyresIntegration as fyi

        payload = fyi.build_order_payload(sym, qty, order_type, side, price)
    except Exception as exc:
        return {
            "request": {
                "symbol": sym,
                "qty": qty,
                "type": order_type,
                "side": side,
                "limitPrice": price,
            },
            "response": {"s": "error", "message": str(exc)},
        }

    if not fyers_service.is_connected():
        return {
            "request": payload,
            "response": {"s": "error", "message": "Fyers not connected"},
        }

    try:
        if limit_price:
            return fyers_service.place_limit_order(
                symbol_name, side, qty, limit_price, order_tag="entry"
            )
        return fyers_service.place_market_order(symbol_name, side, qty)
    except Exception as exc:
        return {
            "request": payload,
            "response": {"s": "error", "message": str(exc)},
        }


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
    # Broker-side exit legs (OCO emulated: a fill on one cancels the other)
    target_order_id: str | None = None
    sl_order_id: str | None = None
    sl_trigger_price: float | None = None
    sl_limit_price: float | None = None
    exit_mode: str = "local"  # local | broker | hybrid
    leg_poll_errors: int = 0
    entry_order_id: str | None = None
    entry_limit_price: float | None = None
    vwap_value: float | None = None
    vwap_band_low: float | None = None
    vwap_band_high: float | None = None
    entry_buffer_pct: float | None = None


def _now_market() -> datetime:
    return market_tz.now()


def _in_trading_window(start_hhmm: str, stop_hhmm: str) -> bool:
    now = _now_market().time()
    sh, sm = map(int, start_hhmm.split(":"))
    eh, em = map(int, stop_hhmm.split(":"))
    start = dt_time(sh, sm)
    end = dt_time(eh, em)
    return start <= now < end


def _at_or_past_stop_time(stop_hhmm: str) -> bool:
    now = _now_market().time()
    eh, em = map(int, stop_hhmm.split(":"))
    return now >= dt_time(eh, em)


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


def _position_from_trade(trade: dict) -> OpenPosition:
    sym = repository.get_symbol_by_name(trade["symbol_name"])
    side = 1 if trade["side"] == "BUY" else -1
    sl_pct = float(
        trade.get("stop_loss_pct")
        if trade.get("stop_loss_pct") is not None
        else (sym["stop_loss_pct"] if sym else 0.0)
    )
    tgt_pct = float(
        trade.get("target_pct")
        if trade.get("target_pct") is not None
        else (sym["target_pct"] if sym else 0.0)
    )
    return OpenPosition(
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
        target_order_id=trade.get("target_order_id"),
        sl_order_id=trade.get("sl_order_id"),
        sl_trigger_price=trade.get("sl_trigger_price"),
        sl_limit_price=trade.get("sl_limit_price"),
        exit_mode=trade.get("exit_mode") or "local",
        entry_order_id=trade.get("entry_order_id"),
        entry_limit_price=trade.get("entry_limit_price"),
        vwap_value=trade.get("vwap"),
        vwap_band_low=trade.get("vwap_band_low"),
        vwap_band_high=trade.get("vwap_band_high"),
        entry_buffer_pct=trade.get("entry_buffer_pct"),
    )


def _restore_open_position_from_trade(trade: dict) -> None:
    global _open_position

    with _lock:
        _open_position = _position_from_trade(trade)


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
    engine_alive = _thread is not None and _thread.is_alive() and not _stop_event.is_set()

    # On app restart/crash recovery, DB may say running but no engine thread exists.
    if settings.get("is_running") and not engine_alive:
        repository.set_strategy_running(False)
        settings = repository.get_strategy_settings()
    # Keep DB flag true when engine is live (scheduler start must show Stop in UI).
    elif engine_alive and not settings.get("is_running"):
        repository.set_strategy_running(True)
        settings = repository.get_strategy_settings()

    # Reflect live API connectivity so UI buttons are correct.
    if settings.get("api_connected") != fyers_service.is_connected():
        repository.set_api_connected(fyers_service.is_connected())
        settings = repository.get_strategy_settings()

    bal = fyers_service.get_cached_balance()
    if bal is None and fyers_service.is_connected():
        bal, _ = fyers_service.fetch_balance()
    # Effective running for UI: DB flag OR live engine thread
    is_running = bool(settings.get("is_running")) or engine_alive
    return {
        **settings,
        "is_running": is_running,
        "trades_taken_today": repository.count_trades_today(),
        "can_take_more_trades": repository.can_take_more_trades(),
        "available_balance": bal,
        "open_position": get_open_position(),
        "last_tick_at": _last_tick_at,
        "last_signal": _last_signal,
        "engine_alive": engine_alive,
        "pending_leg_cancels": list(_pending_leg_cancels),
    }


def is_engine_running() -> bool:
    return _thread is not None and _thread.is_alive() and not _stop_event.is_set()


def _evaluate_signal(
    total_bid_qty: float, total_ask_qty: float, volume_diff: float
) -> str | None:
    """Watchlist depth uses the same BUY/SELL rule as the scanner."""
    return scanner_service.evaluate_depth_signal(
        total_bid_qty, total_ask_qty, volume_diff
    )


def _vwap_enabled() -> bool:
    return bool(repository.get_strategy_settings().get("vwap_enabled", True))


def _vwap_allows_entry(sym: dict, signal: str) -> tuple[bool, dict]:
    if not _vwap_enabled():
        return True, {}
    time_frame = sym["time_frame"]
    ltp = fyers_service.get_ltp(sym["symbol_name"])
    vwap_meta = fyers_service.get_vwap_with_meta(sym["symbol_name"], time_frame)
    vwap = vwap_meta.get("vwap") if vwap_meta else None
    buffer_pct = float(sym.get("entry_buffer_pct") or 0)
    ok, _, details = fyers_service.passes_vwap_band_filter(
        signal, vwap, ltp, buffer_pct
    )
    if vwap_meta:
        details.setdefault("vwap_api_request", vwap_meta.get("request"))
        details.setdefault("vwap_api_response", vwap_meta.get("response"))
        details.setdefault("vwap_candle_count", vwap_meta.get("candle_count"))
    return ok, details


def _entry_candidate(sym: dict, depth: dict) -> dict | None:
    """Volume signal + VWAP confluence for one symbol."""
    if not depth or depth.get("error"):
        return None
    if not fyers_service.has_book_totals(depth):
        return None

    bid_qty = float(depth["bid_qty"])
    ask_qty = float(depth["ask_qty"])
    volume_diff = float(sym["volume_difference"])
    signal = _evaluate_signal(bid_qty, ask_qty, volume_diff)
    if not signal:
        return None

    vwap_ok, crossover = _vwap_allows_entry(sym, signal)
    if not vwap_ok:
        return None

    margin = (ask_qty - bid_qty) if signal == "SELL" else (bid_qty - ask_qty)
    return {
        "sym": sym,
        "signal": signal,
        "depth": depth,
        "margin": margin,
        "crossover": crossover,
    }


def _enter_trade(symbol: dict, signal: str, depth: dict):
    global _open_position, _last_signal

    if has_pending_leg_cancels():
        _log_app(
            f"Entry blocked — an exit leg from the previous trade is still live "
            f"at the broker (ignoring {signal} on {symbol['symbol_name']})"
        )
        _last_signal = "PENDING_LEG_CANCEL"
        return

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

    time_frame = symbol["time_frame"]
    vwap_meta = None
    vwap = None
    crossover: dict = {}
    if _vwap_enabled():
        vwap_meta = fyers_service.get_vwap_with_meta(symbol["symbol_name"], time_frame)
        vwap = vwap_meta.get("vwap") if vwap_meta else None
        ltp = fyers_service.get_ltp(symbol["symbol_name"])
        buffer_pct = float(symbol.get("entry_buffer_pct") or 0)
        vwap_ok, vwap_reason, crossover = fyers_service.passes_vwap_band_filter(
            signal, vwap, ltp, buffer_pct
        )
        if vwap is None:
            _log_app(
                f"Entry skipped — VWAP unavailable for {symbol['symbol_name']} ({time_frame})",
                {
                    "entry_price": entry_price,
                    "time_frame": time_frame,
                    "entry_ltp": ltp,
                    "entry_buffer_pct": buffer_pct,
                    "vwap_api_request": vwap_meta.get("request") if vwap_meta else None,
                    "vwap_api_response": vwap_meta.get("response") if vwap_meta else None,
                },
            )
            print(
                f"[VWAP] {symbol['symbol_name']} ({time_frame}): unavailable, entry skipped",
                flush=True,
            )
            return

        print(
            f"[VWAP] {symbol['symbol_name']} tf={time_frame} "
            f"vwap={vwap:.2f} ltp={crossover.get('ltp')} "
            f"band={crossover.get('vwap_band_low')}-{crossover.get('vwap_band_high')} "
            f"buffer={buffer_pct}% signal={signal} allowed={vwap_ok}",
            flush=True,
        )
        if not vwap_ok:
            _log_app(
                f"{vwap_reason} on {symbol['symbol_name']}",
                {"signal": signal, **crossover},
            )
            return

    sl_pct = float(symbol["stop_loss_pct"])
    tgt_pct = float(symbol["target_pct"])

    live_ltp = fyers_service.get_ltp(symbol["symbol_name"])
    if live_ltp is None and crossover:
        live_ltp = crossover.get("ltp")
    limit_price = fyers_service.live_entry_limit_price(
        signal,
        depth.get("bid_price"),
        depth.get("ask_price"),
        live_ltp,
    )
    if not limit_price:
        _log_app(f"Entry skipped — no live ask/bid for {symbol['symbol_name']}")
        return
    entry_price = limit_price

    sl_price, tgt_price = _calc_sl_target(entry_price, side, sl_pct, tgt_pct)

    book_buy = float(depth.get("bid_qty") or 0)
    book_sell = float(depth.get("ask_qty") or 0)
    volume_threshold = float(symbol["volume_difference"])
    buy_diff = book_buy - book_sell
    sell_diff = book_sell - book_buy
    volume_trigger = buy_diff if signal == "BUY" else sell_diff

    if repository.has_open_trade():
        open_trade = repository.get_open_trade()
        sym_name = open_trade["symbol_name"] if open_trade else "?"
        _log_app(
            f"Entry blocked — open trade in {sym_name} must exit "
            "(stop loss, target, or time exit) before a new entry",
            {"blocked_signal": signal, "symbol": symbol["symbol_name"]},
        )
        _last_signal = f"BLOCKED_{sym_name}"
        return

    balance, _bal_detail = fyers_service.fetch_balance()
    leverage = _leverage_multiplier()
    qty, sizing = resolve_entry_quantity(balance, leverage, entry_price)
    sizing["balance_detail"] = _bal_detail

    if sizing.get("sizing_mode") != "exposure":
        print(
            f"[ENTRY] {symbol['symbol_name']}: using fallback qty={qty} "
            f"({sizing.get('sizing_mode')}, balance={balance})",
            flush=True,
        )

    entry_time = _now_market().strftime("%Y-%m-%d %H:%M:%S")
    order_result = _place_entry_order(symbol["symbol_name"], side, qty, limit_price)
    resp = order_result.get("response")
    accepted = fyers_service.is_order_successful(resp)
    entry_order_id = fyers_service.extract_order_id(resp) if accepted else None
    if limit_price:
        entry_status = "PENDING" if entry_order_id else "REJECTED"
        order_type_label = "LIMIT"
    else:
        entry_status = "FILLED" if accepted else "REJECTED"
        order_type_label = "MARKET"
    exposure_note = (
        f"exposure ₹{sizing['exposure']:,.0f}"
        if sizing.get("exposure") is not None
        else sizing.get("sizing_mode", "fallback")
    )
    _log_app(
        f"ENTRY {signal} {symbol['symbol_name']} qty={qty} @ {entry_price:.2f} "
        f"({order_type_label}, {exposure_note}, {entry_status})",
        {
            "request": order_result.get("request"),
            "response": resp,
            "depth": depth,
            **sizing,
        },
    )
    _log_order(
        symbol["symbol_name"],
        signal,
        f"ENTRY_{entry_status}",
        entry_price,
        qty,
        stop_loss=sl_price,
        target=tgt_price,
        order_type=order_type_label,
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
            "prev_prev_close": crossover.get("prev_prev_close"),
            "prev_close": crossover.get("prev_close"),
            "vwap_crossover": crossover.get("crossover"),
            "entry_ltp": crossover.get("ltp"),
            "entry_buffer_pct": crossover.get("entry_buffer_pct"),
            "vwap_band_low": crossover.get("vwap_band_low"),
            "vwap_band_high": crossover.get("vwap_band_high"),
            "vwap_band_passed": crossover.get("vwap_band_passed"),
            "entry_limit_price": limit_price,
            "entry_order_type": order_type_label,
            "entry_order_id": entry_order_id,
            "stop_loss_pct": sl_pct,
            "target_pct": tgt_pct,
            "initial_stop_loss": sl_price,
            "volume_difference": volume_threshold,
            "book_buy_qty": book_buy,
            "book_sell_qty": book_sell,
            "volume_trigger": volume_trigger,
            "vwap_candle_count": vwap_meta.get("candle_count") if vwap_meta else None,
            "vwap_api_request": vwap_meta.get("request") if vwap_meta else None,
            "vwap_api_response": vwap_meta.get("response") if vwap_meta else None,
            "vwap_filter_passed": _vwap_enabled(),
            "entry_api_request": order_result.get("request"),
            "entry_api_response": resp,
            **sizing,
        },
    )
    if not trade:
        if entry_order_id and limit_price:
            fyers_service.cancel_order(entry_order_id)
        _log_app(
            f"Entry not recorded — open trade already exists "
            f"(blocked {signal} on {symbol['symbol_name']})",
            {"entry_status": entry_status, "entry_price": entry_price},
        )
        _last_signal = "BLOCKED_OPEN_TRADE"
        return

    if entry_status == "REJECTED":
        print(
            f"[ENTRY] {symbol['symbol_name']} {signal} rejected by Fyers — "
            f"not holding a position",
            flush=True,
        )
        repository.close_trade(
            trade_id=trade["id"],
            exit_price=entry_price,
            exit_reason="REJECTED",
            exit_status="REJECTED",
            pnl=0.0,
            details_update={"exit_via": "entry_rejected"},
        )
        return

    with _lock:
        _open_position = OpenPosition(
            trade_id=trade["id"],
            symbol_name=symbol["symbol_name"],
            fyers_symbol=depth.get("symbol")
            or fyers_service.to_fyers_symbol(symbol["symbol_name"]),
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
            entry_order_id=entry_order_id,
            entry_limit_price=limit_price,
            vwap_value=vwap,
            vwap_band_low=crossover.get("vwap_band_low"),
            vwap_band_high=crossover.get("vwap_band_high"),
            entry_buffer_pct=crossover.get("entry_buffer_pct"),
        )
        _last_signal = f"{signal} {symbol['symbol_name']}"
        pos = _open_position

    if entry_status == "FILLED":
        _place_exit_legs(pos, entry_order_id)
    else:
        print(
            f"[ENTRY] {symbol['symbol_name']} {signal} LIMIT parked @ {entry_price:.2f} "
            f"waiting for fill (id={entry_order_id})",
            flush=True,
        )


def _sl_limit_price(trigger: float, exit_side: int) -> float:
    """Sell SL-L needs limit <= trigger; buy SL-L needs limit >= trigger."""
    buffer = max(trigger * SL_LIMIT_BUFFER_PCT / 100, fyers_service.TICK_SIZE)
    if exit_side == -1:
        return fyers_service.round_to_tick(trigger - buffer, "down")
    return fyers_service.round_to_tick(trigger + buffer, "up")


def _resolve_entry_fill_price(
    order_id: str | None, fallback: float
) -> tuple[float, dict | None]:
    """Read the entry order's traded price so SL/target hang off the real fill."""
    if not order_id:
        return fallback, None

    state = None
    for attempt in range(ENTRY_FILL_POLL_ATTEMPTS):
        states = fyers_service.fetch_order_states([order_id])
        state = states.get("orders", {}).get(str(order_id))
        if state:
            if (
                state["status"] == fyers_service.ORDER_STATUS_FILLED
                and state["traded_price"]
            ):
                return float(state["traded_price"]), state
            if state["status"] in fyers_service.ORDER_STATUS_DEAD:
                return fallback, state
        if attempt < ENTRY_FILL_POLL_ATTEMPTS - 1:
            time.sleep(ENTRY_FILL_POLL_DELAY_SEC)

    return fallback, state


def _place_exit_legs(pos: OpenPosition, entry_order_id: str | None) -> None:
    """
    Park both exits in the market immediately after a filled entry:
      long  -> target = SELL LIMIT above entry, SL = SELL SL-L below entry
      short -> target = BUY LIMIT below entry, SL = BUY SL-L above entry
    Whichever fills first cancels the other (see _monitor_exit_legs).
    """
    if not fyers_service.is_connected():
        _log_app(
            "Exit legs not placed — Fyers not connected; "
            "SL/target will be monitored locally",
            {"trade_id": pos.trade_id},
        )
        return

    exit_side = -pos.side
    exit_label = "SELL" if exit_side == -1 else "BUY"

    fill_price, entry_state = _resolve_entry_fill_price(entry_order_id, pos.entry_price)
    sl_price, tgt_price = _calc_sl_target(
        fill_price, pos.side, pos.stop_loss_pct, pos.target_pct
    )
    sl_price = fyers_service.round_to_tick(sl_price)
    tgt_price = fyers_service.round_to_tick(tgt_price)
    sl_limit = _sl_limit_price(sl_price, exit_side)

    pos.entry_price = fill_price
    pos.stop_loss_price = sl_price
    pos.target_price = tgt_price
    pos.sl_trigger_price = sl_price
    pos.sl_limit_price = sl_limit
    repository.update_trade_levels(pos.trade_id, sl_price, tgt_price, fill_price)

    target_meta = fyers_service.place_limit_order(
        pos.symbol_name, exit_side, pos.quantity, tgt_price
    )
    target_resp = target_meta.get("response")
    pos.target_order_id = fyers_service.extract_order_id(target_resp)
    target_status = "PLACED" if pos.target_order_id else "REJECTED"
    _log_order(
        pos.symbol_name,
        exit_label,
        f"TARGET_{target_status}",
        tgt_price,
        pos.quantity,
        stop_loss=sl_price,
        target=tgt_price,
        order_type="LIMIT",
    )

    sl_meta = fyers_service.place_sl_limit_order(
        pos.symbol_name, exit_side, pos.quantity, sl_price, sl_limit
    )
    sl_resp = sl_meta.get("response")
    pos.sl_order_id = fyers_service.extract_order_id(sl_resp)
    sl_status = "PLACED" if pos.sl_order_id else "REJECTED"
    _log_order(
        pos.symbol_name,
        exit_label,
        f"SL_{sl_status}",
        sl_limit,
        pos.quantity,
        stop_loss=sl_price,
        target=tgt_price,
        order_type="SL-L",
    )

    if pos.target_order_id and pos.sl_order_id:
        pos.exit_mode = "broker"
    elif pos.target_order_id or pos.sl_order_id:
        pos.exit_mode = "hybrid"
    else:
        pos.exit_mode = "local"

    repository.merge_trade_details(
        pos.trade_id,
        {
            "entry_fill_price": fill_price,
            "entry_order_id": entry_order_id,
            "entry_fill_state": entry_state,
            "exit_mode": pos.exit_mode,
            "target_order_id": pos.target_order_id,
            "sl_order_id": pos.sl_order_id,
            "sl_trigger_price": sl_price,
            "sl_limit_price": sl_limit,
            "target_leg_request": target_meta.get("request"),
            "target_leg_response": target_resp,
            "sl_leg_request": sl_meta.get("request"),
            "sl_leg_response": sl_resp,
        },
    )

    _log_app(
        f"Exit legs {pos.exit_mode} for {pos.symbol_name}: "
        f"target {exit_label} LIMIT @ {tgt_price:.2f} ({target_status}), "
        f"SL {exit_label} SL-L trigger {sl_price:.2f} limit {sl_limit:.2f} ({sl_status})",
        {
            "trade_id": pos.trade_id,
            "entry_fill_price": fill_price,
            "target_order_id": pos.target_order_id,
            "sl_order_id": pos.sl_order_id,
            "target_leg_response": target_resp,
            "sl_leg_response": sl_resp,
        },
    )
    print(
        f"[EXIT LEGS] {pos.symbol_name} entry={fill_price:.2f} "
        f"target={tgt_price:.2f}({target_status}) "
        f"sl_trigger={sl_price:.2f} sl_limit={sl_limit:.2f}({sl_status}) "
        f"mode={pos.exit_mode}",
        flush=True,
    )

    if pos.exit_mode != "broker":
        _log_app(
            "One or both exit legs were not accepted — "
            "falling back to local SL/target monitoring for this trade",
            {
                "trade_id": pos.trade_id,
                "target_leg_response": target_resp,
                "sl_leg_response": sl_resp,
            },
        )


def _leg_is_filled(state: dict | None, quantity: int) -> bool:
    if not state:
        return False
    if state["status"] == fyers_service.ORDER_STATUS_FILLED:
        return True
    return quantity > 0 and state["filled_qty"] >= float(quantity)


def _leg_book_status(order_id: str) -> tuple[str, dict | None]:
    """gone | dead | filled | live | unknown — read straight from the orderbook."""
    states = fyers_service.fetch_order_states([order_id])
    if states.get("error"):
        return "unknown", None

    state = states.get("orders", {}).get(str(order_id))
    if state is None:
        return "gone", None
    if state["status"] == fyers_service.ORDER_STATUS_FILLED or state["filled_qty"] > 0:
        return "filled", state
    if state["status"] in fyers_service.ORDER_STATUS_DEAD:
        return "dead", state
    return "live", state


def _cancel_leg(pos: OpenPosition, label: str, order_id: str | None) -> dict | None:
    """
    Cancel the surviving leg once its sibling has executed, then confirm from the
    orderbook that it is really gone. A leg left live would fire later and open an
    unwanted opposite position, so an unconfirmed cancel blocks further entries.
    """
    if not order_id:
        return None

    result: dict = {
        "order_id": order_id,
        "label": label,
        "confirmed": False,
        "book_status": "unknown",
        "filled_state": None,
        "attempts": 0,
        "responses": [],
    }

    for attempt in range(1, LEG_CANCEL_ATTEMPTS + 1):
        result["attempts"] = attempt
        meta = fyers_service.cancel_order(order_id)
        result["responses"].append(meta.get("response"))

        status, state = _leg_book_status(order_id)
        result["book_status"] = status
        if status in ("gone", "dead"):
            result["confirmed"] = True
            break
        if status == "filled":
            result["confirmed"] = True
            result["filled_state"] = state
            break
        if attempt < LEG_CANCEL_ATTEMPTS:
            time.sleep(LEG_CANCEL_RETRY_DELAY_SEC)

    if result["filled_state"]:
        outcome = "ALREADY_FILLED"
    elif result["confirmed"]:
        outcome = "OK"
    else:
        outcome = "UNCONFIRMED"

    _log_order(
        pos.symbol_name,
        "SELL" if pos.side == 1 else "BUY",
        f"{label}_CANCEL_{outcome}",
        None,
        pos.quantity,
        stop_loss=pos.stop_loss_price,
        target=pos.target_price,
        order_type="CANCEL",
    )
    print(
        f"[EXIT LEGS] {pos.symbol_name} cancel {label} leg {order_id}: "
        f"{outcome.lower()} after {result['attempts']} attempt(s)",
        flush=True,
    )

    if not result["confirmed"]:
        _register_orphan_leg(pos, label, order_id, result)

    return result


def _register_orphan_leg(
    pos: OpenPosition, label: str, order_id: str, result: dict
) -> None:
    """Leg we could not confirm as cancelled — no new entries until it is resolved."""
    global _pending_leg_cancels

    with _lock:
        if any(o["order_id"] == order_id for o in _pending_leg_cancels):
            return
        _pending_leg_cancels.append(
            {
                "order_id": order_id,
                "label": label,
                "symbol_name": pos.symbol_name,
                "trade_id": pos.trade_id,
                "entry_side": pos.side,
                "side_label": pos.side_label,
                "quantity": pos.quantity,
                "attempts": 0,
            }
        )

    _log_app(
        f"{label} leg {order_id} on {pos.symbol_name} could not be confirmed as "
        "cancelled — blocking new entries until it is cleared",
        {"trade_id": pos.trade_id, "cancel_result": result},
    )
    print(
        f"[EXIT LEGS] {pos.symbol_name} {label} leg {order_id} still live — "
        "entries blocked until cancelled",
        flush=True,
    )


def has_pending_leg_cancels() -> bool:
    with _lock:
        return bool(_pending_leg_cancels)


def _drop_pending_leg(order_id: str) -> None:
    global _pending_leg_cancels

    with _lock:
        _pending_leg_cancels = [
            o for o in _pending_leg_cancels if o["order_id"] != order_id
        ]


def _process_pending_leg_cancels() -> None:
    """Retry leftover cancels every tick until the broker confirms them gone."""
    with _lock:
        pending = list(_pending_leg_cancels)
    if not pending:
        return

    for entry in pending:
        entry["attempts"] += 1
        order_id = entry["order_id"]
        fyers_service.cancel_order(order_id)
        status, state = _leg_book_status(order_id)

        if status in ("gone", "dead"):
            _drop_pending_leg(order_id)
            _log_app(
                f"Leftover {entry['label']} leg {order_id} on "
                f"{entry['symbol_name']} confirmed cancelled — entries resume",
                {"attempts": entry["attempts"]},
            )
        elif status == "filled":
            filled_qty = int(state["filled_qty"] or entry["quantity"])
            _flatten_unwanted_position(
                entry["symbol_name"],
                entry["entry_side"],
                entry["side_label"],
                filled_qty,
                f"leftover {entry['label']} leg {order_id} executed",
                {"trade_id": entry["trade_id"], "leg_state": state},
            )
            _drop_pending_leg(order_id)
        elif entry["attempts"] % 10 == 0:
            _log_app(
                f"Leftover {entry['label']} leg {order_id} on "
                f"{entry['symbol_name']} is still live at the broker after "
                f"{entry['attempts']} cancel attempts — entries stay blocked",
                {"book_status": status, "leg_state": state},
            )


def _cancel_live_exit_legs(
    pos: OpenPosition,
) -> tuple[tuple[str, dict] | None, dict]:
    """
    Cancel both pending legs, then re-read them: a leg can execute in the same
    instant we cancel, and that fill is the real exit.
    """
    results: dict = {}
    filled: tuple[str, dict] | None = None
    for label, order_id in (
        ("TARGET", pos.target_order_id),
        ("SL", pos.sl_order_id),
    ):
        if not order_id:
            continue
        result = _cancel_leg(pos, label, order_id)
        results[label] = result
        if result and result.get("filled_state"):
            filled = (label, result["filled_state"])

    pos.target_order_id = None
    pos.sl_order_id = None

    if len([r for r in results.values() if r and r.get("filled_state")]) == 2:
        _flatten_reverse_position(pos)

    return filled, results


def _flatten_unwanted_position(
    symbol_name: str,
    entry_side: int,
    side_label: str,
    quantity: int,
    reason: str,
    details: dict | None = None,
) -> dict:
    """An exit leg executed when it should not have — trade back out of it."""
    meta = fyers_service.place_market_order(symbol_name, entry_side, quantity)
    status = fyers_service.order_status_label(meta.get("response"))
    _log_order(
        symbol_name,
        side_label,
        f"RECONCILE_FLATTEN_{status}",
        None,
        quantity,
        order_type="MARKET",
    )
    _log_app(
        f"Flattened unwanted position in {symbol_name} ({reason}) — "
        f"{quantity} qty market order {status}",
        {**(details or {}), "response": meta.get("response")},
    )
    print(
        f"[RECONCILE] {symbol_name}: {reason} — flattened {quantity} qty ({status})",
        flush=True,
    )
    return meta


def _flatten_reverse_position(pos: OpenPosition) -> dict:
    """Both legs traded (price gapped through both) — buy/sell back the reverse."""
    return _flatten_unwanted_position(
        pos.symbol_name,
        pos.side,
        pos.side_label,
        pos.quantity,
        "both exit legs executed",
        {"trade_id": pos.trade_id},
    )


def _close_position_from_leg(
    pos: OpenPosition,
    reason: str,
    state: dict | None,
    leg_cancels: dict | None = None,
) -> dict | None:
    """Broker already executed the exit — record it without sending a new order."""
    global _open_position

    existing = repository.get_trade(pos.trade_id)
    if existing and existing.get("exit_time"):
        return existing

    exit_price = state.get("traded_price") if state else None
    if not exit_price:
        exit_price = pos.target_price if reason == "TARGET" else pos.stop_loss_price
    exit_price = float(exit_price or pos.entry_price)

    exit_time = _now_market().strftime("%Y-%m-%d %H:%M:%S")
    pnl = repository.calc_trade_pnl(
        pos.side, pos.entry_price, exit_price, pos.quantity
    )
    status = f"EXIT_{reason}_FILLED"

    with _lock:
        if _open_position and _open_position.trade_id == pos.trade_id:
            _open_position = None

    _log_app(
        f"{status} {pos.side_label} {pos.symbol_name} @ {exit_price:.2f} "
        f"pnl={pnl:.2f} (broker {reason.lower()} leg)",
        {
            "position": asdict(pos),
            "leg_state": state,
            "leg_cancels": leg_cancels,
            "pnl": pnl,
        },
    )
    _log_order(
        pos.symbol_name,
        "SELL" if pos.side == 1 else "BUY",
        status,
        exit_price,
        pos.quantity,
        stop_loss=pos.stop_loss_price,
        target=pos.target_price,
        order_type="LIMIT" if reason == "TARGET" else "SL-L",
    )

    trade = repository.close_trade(
        trade_id=pos.trade_id,
        exit_price=exit_price,
        exit_reason=reason,
        exit_status="FILLED",
        pnl=pnl,
        exit_time=exit_time,
        details_update={
            "exit_leg": reason,
            "exit_leg_state": state,
            "exit_leg_cancels": leg_cancels,
            "exit_via": "broker_leg",
        },
    )
    if trade:
        _print_trade_result(trade)
    return trade


def _monitor_exit_legs(pos: OpenPosition) -> str:
    """
    Poll the live legs once per tick.

    Returns:
      "closed"  — a leg executed and the trade is now closed
      "covered" — both exits sit at the broker, nothing more to do this tick
      "local"   — caller must also run LTP-based SL/target checks
    """
    ids = [i for i in (pos.target_order_id, pos.sl_order_id) if i]
    if not ids:
        return "local"

    # Only a full broker bracket removes the need for local price checks.
    idle = "covered" if pos.exit_mode == "broker" else "local"

    states = fyers_service.fetch_order_states(ids)
    if states.get("error"):
        pos.leg_poll_errors += 1
        if pos.leg_poll_errors >= LEG_POLL_MAX_ERRORS:
            pos.exit_mode = "local"
            _log_app(
                "Exit leg status polling keeps failing — "
                "switching this trade to local SL/target monitoring",
                {"trade_id": pos.trade_id, "error": states.get("error")},
            )
            return "local"
        # Legs are still live at the broker; never exit twice on a poll hiccup.
        return idle

    pos.leg_poll_errors = 0
    orders = states.get("orders", {})
    target_state = orders.get(pos.target_order_id or "")
    sl_state = orders.get(pos.sl_order_id or "")

    target_filled = _leg_is_filled(target_state, pos.quantity)
    sl_filled = _leg_is_filled(sl_state, pos.quantity)

    if target_filled and sl_filled:
        pos.target_order_id = None
        pos.sl_order_id = None
        _flatten_reverse_position(pos)
        _close_position_from_leg(pos, "TARGET", target_state)
        return "closed"

    if target_filled or sl_filled:
        reason = "TARGET" if target_filled else "SL"
        other_label = "SL" if target_filled else "TARGET"
        filled_state = target_state if target_filled else sl_state
        other_id = pos.sl_order_id if target_filled else pos.target_order_id
        pos.target_order_id = None
        pos.sl_order_id = None

        cancel_result = _cancel_leg(pos, other_label, other_id)
        if cancel_result and cancel_result.get("filled_state"):
            other_state = cancel_result["filled_state"]
            _flatten_unwanted_position(
                pos.symbol_name,
                pos.side,
                pos.side_label,
                int(other_state["filled_qty"] or pos.quantity),
                f"{other_label} leg executed while being cancelled",
                {"trade_id": pos.trade_id, "leg_state": other_state},
            )

        _close_position_from_leg(
            pos, reason, filled_state, {other_label: cancel_result}
        )
        return "closed"

    dead: list[str] = []
    for label, order_id, state in (
        ("TARGET", pos.target_order_id, target_state),
        ("SL", pos.sl_order_id, sl_state),
    ):
        if not order_id:
            continue
        if state is None or state["status"] in fyers_service.ORDER_STATUS_DEAD:
            dead.append(label)
            if label == "TARGET":
                pos.target_order_id = None
            else:
                pos.sl_order_id = None
        elif 0 < state["filled_qty"] < float(pos.quantity):
            _log_app(
                f"{label} leg partially filled on {pos.symbol_name} "
                f"({state['filled_qty']:.0f}/{pos.quantity})",
                {"trade_id": pos.trade_id, "leg_state": state},
            )

    if dead:
        pos.exit_mode = "hybrid" if (pos.target_order_id or pos.sl_order_id) else "local"
        _log_app(
            f"Exit leg(s) {', '.join(dead)} no longer live at broker on "
            f"{pos.symbol_name} — monitoring SL/target locally",
            {
                "trade_id": pos.trade_id,
                "target_state": target_state,
                "sl_state": sl_state,
                "exit_mode": pos.exit_mode,
            },
        )
        return "local"

    return idle


def _resolve_exit_price(
    symbol_name: str, side: int, entry_price: float
) -> float | None:
    ltp = fyers_service.get_ltp(symbol_name)
    if ltp is not None:
        return ltp

    depth = fyers_service.get_market_depth(symbol_name)
    if depth and not depth.get("error"):
        price_key = "bid_price" if side == 1 else "ask_price"
        price = depth.get(price_key)
        if price is not None and float(price) > 0:
            return float(price)

    return None


def _close_position_with_logs(
    pos: OpenPosition,
    reason: str,
    exit_price: float | None = None,
) -> dict | None:
    existing = repository.get_trade(pos.trade_id)
    if existing and existing.get("exit_time"):
        return existing

    # Unfilled VWAP limits are cancelled, not flattened — there is no position yet.
    if pos.entry_status in ENTRY_WORKING_STATUSES:
        cancel_meta, filled_state = _cancel_entry_order(pos)
        if not filled_state:
            return _close_unfilled_entry(pos, reason, cancel_meta)
        fill_price = float(filled_state.get("traded_price") or pos.entry_price)
        pos.entry_status = "FILLED"
        pos.entry_price = fill_price
        repository.update_trade_levels(
            pos.trade_id,
            pos.stop_loss_price,
            pos.target_price,
            fill_price,
            entry_status="FILLED",
        )
        repository.merge_trade_details(
            pos.trade_id,
            {"entry_fill_price": fill_price, "entry_fill_state": filled_state},
        )

    # Pending legs must go before any market exit, or a stale leg could re-open
    # a position after square-off.
    leg_cancels: dict = {}
    if pos.target_order_id or pos.sl_order_id:
        filled_leg, leg_cancels = _cancel_live_exit_legs(pos)
        if filled_leg:
            label, state = filled_leg
            _log_app(
                f"{label} leg executed at broker while closing {pos.symbol_name} "
                f"({reason}) — recording that fill as the exit",
                {"trade_id": pos.trade_id, "leg_state": state},
            )
            return _close_position_from_leg(pos, label, state, leg_cancels)

    exit_side = -pos.side
    ltp = exit_price if exit_price is not None else _resolve_exit_price(
        pos.symbol_name, pos.side, pos.entry_price
    )
    if ltp is None:
        ltp = pos.entry_price

    exit_time = _now_market().strftime("%Y-%m-%d %H:%M:%S")
    is_paper = pos.entry_status != "FILLED"
    if is_paper:
        order_result = {"request": None, "response": None}
        resp = None
        exit_status = "PAPER"
    else:
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
            "exit_leg_cancels": leg_cancels or None,
            "exit_via": "market_order",
        },
    )
    if trade:
        _print_trade_result(trade)
    return trade


def _exit_trade(reason: str, exit_price: float | None = None):
    global _open_position

    with _lock:
        pos = _open_position
        if not pos:
            return
        _open_position = None

    _close_position_with_logs(pos, reason, exit_price)


def close_trade_record(
    trade: dict, reason: str, exit_price: float | None = None
) -> dict | None:
    """Close an open DB trade with full order log + PnL (engine thread not required)."""
    if trade.get("exit_time"):
        return None
    result = _close_position_with_logs(
        _position_from_trade(trade), reason, exit_price
    )
    if result:
        global _open_position
        with _lock:
            if _open_position and _open_position.trade_id == result["id"]:
                _open_position = None
    return result


def square_off_all_open_trades(exit_reason: str = "EOD") -> list[int]:
    """Close every open trade today — SL/target/EOD/stop all use the same logging path."""
    _sync_open_position_from_db()
    closed_ids: list[int] = []
    for _ in range(16):
        with _lock:
            has_mem = _open_position is not None
        if has_mem:
            with _lock:
                trade_id = _open_position.trade_id if _open_position else None
            _exit_trade(exit_reason)
            if trade_id is not None:
                closed_ids.append(trade_id)
            continue

        trade = repository.get_open_trade()
        if not trade:
            break
        result = close_trade_record(trade, exit_reason)
        if result:
            closed_ids.append(result["id"])
    return closed_ids


def _is_pending_entry(pos: OpenPosition) -> bool:
    return pos.entry_status in ENTRY_WORKING_STATUSES


def _pending_entry_still_in_band(pos: OpenPosition, ltp: float | None) -> bool:
    """Keep the resting limit only while live LTP is still inside the VWAP band."""
    if ltp is None:
        return True
    buffer = float(pos.entry_buffer_pct or 0)
    vwap = pos.vwap_value
    if _vwap_enabled():
        sym = repository.get_symbol_by_name(pos.symbol_name)
        time_frame = sym["time_frame"] if sym else "5m"
        meta = fyers_service.get_vwap_with_meta(pos.symbol_name, time_frame)
        if meta and meta.get("vwap"):
            vwap = meta["vwap"]
    if vwap is None:
        low, high = pos.vwap_band_low, pos.vwap_band_high
        if low is None or high is None:
            return True
        vwap = (float(low) + float(high)) / 2
    ok, _, _ = fyers_service.passes_vwap_band_filter(
        pos.side_label, vwap, ltp, buffer
    )
    return ok


def _cancel_entry_order(pos: OpenPosition) -> tuple[dict | None, dict | None]:
    """Cancel the resting entry. Returns (cancel meta, filled state if it raced)."""
    if not pos.entry_order_id:
        return None, None
    meta = fyers_service.cancel_order(pos.entry_order_id)
    states = fyers_service.fetch_order_states([pos.entry_order_id])
    state = states.get("orders", {}).get(str(pos.entry_order_id))
    if state and state.get("status") == fyers_service.ORDER_STATUS_FILLED:
        return meta, state
    return meta, None


def _activate_filled_entry(
    pos: OpenPosition, fill_price: float, state: dict | None = None
) -> None:
    """Limit got filled — realign levels and park the exit legs."""
    sl_price, tgt_price = _calc_sl_target(
        fill_price, pos.side, pos.stop_loss_pct, pos.target_pct
    )
    sl_price = fyers_service.round_to_tick(sl_price)
    tgt_price = fyers_service.round_to_tick(tgt_price)
    with _lock:
        pos.entry_status = "FILLED"
        pos.entry_price = fill_price
        pos.stop_loss_price = sl_price
        pos.target_price = tgt_price
    repository.update_trade_levels(
        pos.trade_id, sl_price, tgt_price, fill_price, entry_status="FILLED"
    )
    repository.merge_trade_details(
        pos.trade_id,
        {"entry_fill_price": fill_price, "entry_fill_state": state},
    )
    _log_order(
        pos.symbol_name,
        pos.side_label,
        "ENTRY_FILLED",
        fill_price,
        pos.quantity,
        stop_loss=sl_price,
        target=tgt_price,
        order_type="LIMIT",
    )
    _log_app(
        f"ENTRY FILLED {pos.side_label} {pos.symbol_name} @ {fill_price:.2f}",
        {"trade_id": pos.trade_id, "entry_order_id": pos.entry_order_id, "state": state},
    )
    print(
        f"[ENTRY] {pos.symbol_name} LIMIT filled @ {fill_price:.2f} — placing exit legs",
        flush=True,
    )
    _place_exit_legs(pos, pos.entry_order_id)


def _close_unfilled_entry(
    pos: OpenPosition, reason: str, cancel_meta: dict | None = None
) -> dict | None:
    """Drop a resting limit with no position and no P&L."""
    existing = repository.get_trade(pos.trade_id)
    if existing and existing.get("exit_time"):
        return existing

    _log_order(
        pos.symbol_name,
        pos.side_label,
        "ENTRY_CANCELLED",
        pos.entry_limit_price or pos.entry_price,
        pos.quantity,
        stop_loss=pos.stop_loss_price,
        target=pos.target_price,
        order_type="LIMIT",
    )
    _log_app(
        f"ENTRY_CANCELLED {pos.side_label} {pos.symbol_name} ({reason})",
        {"trade_id": pos.trade_id, "cancel": cancel_meta},
    )
    print(
        f"[ENTRY] {pos.symbol_name} unfilled LIMIT cancelled ({reason})",
        flush=True,
    )
    return repository.close_trade(
        trade_id=pos.trade_id,
        exit_price=pos.entry_price,
        exit_reason=reason,
        exit_status="CANCELLED",
        pnl=0.0,
        details_update={
            "exit_via": "unfilled_limit",
            "entry_cancel": cancel_meta,
        },
    )


def _exit_unfilled_entry(pos: OpenPosition, reason: str) -> None:
    global _open_position

    cancel_meta, filled_state = _cancel_entry_order(pos)
    if filled_state:
        fill_price = float(filled_state.get("traded_price") or pos.entry_price)
        _activate_filled_entry(pos, fill_price, filled_state)
        return

    with _lock:
        if _open_position and _open_position.trade_id == pos.trade_id:
            _open_position = None
    _close_unfilled_entry(pos, reason, cancel_meta)


def _monitor_pending_entry(pos: OpenPosition) -> None:
    if pos.entry_order_id:
        states = fyers_service.fetch_order_states([pos.entry_order_id])
        state = states.get("orders", {}).get(str(pos.entry_order_id))
        if state:
            status = state.get("status")
            if status == fyers_service.ORDER_STATUS_FILLED:
                fill_price = float(state.get("traded_price") or pos.entry_price)
                _activate_filled_entry(pos, fill_price, state)
                return
            if status in fyers_service.ORDER_STATUS_DEAD:
                _exit_unfilled_entry(pos, "UNFILLED")
                return

    ltp = fyers_service.get_ltp(pos.symbol_name)
    if not _pending_entry_still_in_band(pos, ltp):
        _exit_unfilled_entry(pos, "UNFILLED")


def _monitor_open_position():
    with _lock:
        pos = _open_position
    if not pos:
        return

    if _is_pending_entry(pos):
        _monitor_pending_entry(pos)
        return

    # Exits sitting at the broker: a fill on one leg cancels the other.
    # Anything less than a full bracket still needs the local price check below.
    if pos.target_order_id or pos.sl_order_id:
        if _monitor_exit_legs(pos) in ("closed", "covered"):
            return

    ltp = _resolve_exit_price(pos.symbol_name, pos.side, pos.entry_price)
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


def _log_scanner_state(tick_ts: str, allowed: str | None, info: dict) -> None:
    global _last_scanner_bias

    bias_label = allowed or "NEUTRAL"
    print(
        (
            f"[SCANNER {tick_ts}] buy={info.get('buy_count', 0)} "
            f"sell={info.get('sell_count', 0)} none={info.get('none_count', info.get('flat_count', 0))} "
            f"missing={info.get('missing', 0)} total={info.get('total', 0)} "
            f"majority_need={info.get('majority', 0)} bias={bias_label}"
            + (f" -> only {allowed} entries" if allowed else " -> no entries")
        ),
        flush=True,
    )

    if allowed != _last_scanner_bias:
        _last_scanner_bias = allowed
        if allowed:
            _log_app(
                f"Scanner depth majority -> {allowed} "
                f"({info.get('buy_count')} BUY / {info.get('sell_count')} SELL, "
                f"need {info.get('majority')} of {info.get('total')})",
                info,
            )
        elif info.get("total"):
            _log_app(
                "Scanner neutral — neither side reached majority, entries paused",
                info,
            )


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

    if has_pending_leg_cancels():
        if _last_signal != "PENDING_LEG_CANCEL":
            _last_signal = "PENDING_LEG_CANCEL"
            print(
                "[ENTRY BLOCKED] An exit leg is still live at the broker — "
                "no new entries until it is cancelled",
                flush=True,
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
    scanner_bias, scanner_info = scanner_service.get_live_bias()
    _log_scanner_state(tick_ts, scanner_bias, scanner_info)

    if scanner_info.get("total") and scanner_bias is None:
        if _last_signal != "SCANNER_NEUTRAL":
            _last_signal = "SCANNER_NEUTRAL"
        return

    symbol_names = [s["symbol_name"] for s in symbols]
    ws_active = fyers_service.is_market_ws_active()
    refreshed = fyers_service.tick_depth_refresh(symbol_names)

    candidates: list[dict] = []

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

        signal = _evaluate_signal(bid_qty, ask_qty, volume_diff)
        vwap_ok = False
        vwap_note = ""
        if signal:
            if _vwap_enabled():
                vwap_ok, crossover = _vwap_allows_entry(sym, signal)
                if crossover:
                    vwap_note = (
                        f" vwap={crossover.get('vwap')} ltp={crossover.get('ltp')} "
                        f"band={crossover.get('vwap_band_low')}-"
                        f"{crossover.get('vwap_band_high')} "
                        f"buffer={crossover.get('entry_buffer_pct')}% "
                        f"band_ok={vwap_ok}"
                    )
                else:
                    vwap_note = " vwap=NA"
            else:
                vwap_ok = True
                vwap_note = " vwap=off"

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

        candidate = _entry_candidate(sym, depth)
        if candidate and (
            not scanner_info.get("total")
            or candidate["signal"] == scanner_bias
        ):
            candidates.append(candidate)

    if not candidates:
        return

    if _has_open_position():
        return

    # Pick strongest volume imbalance among symbols that pass depth (+ optional VWAP).
    best = max(candidates, key=lambda c: c["margin"])
    sym = best["sym"]
    signal = best["signal"]
    depth = best["depth"]

    fresh = fyers_service.fetch_market_depth_immediate(sym["symbol_name"])
    if fresh and not fresh.get("error") and fyers_service.has_book_totals(fresh):
        recheck = _entry_candidate(sym, fresh)
        if recheck:
            depth = fresh
            signal = recheck["signal"]
        else:
            print(
                f"[ENTRY] {sym['symbol_name']}: fresh depth no longer passes — skipped",
                flush=True,
            )
            return

    scanner_ok, gate_info = scanner_service.evaluate_scanner_gate(signal)
    if not scanner_ok:
        gate_key = f"SCANNER_BLOCK_{signal}"
        if _last_signal != gate_key:
            _last_signal = gate_key
            print(
                f"[SCANNER BLOCK] {signal} blocked — live bias is "
                f"{gate_info.get('allowed_signal') or 'NEUTRAL'} "
                f"(buy={gate_info.get('buy_count')} sell={gate_info.get('sell_count')})",
                flush=True,
            )
        return

    _log_app(
        f"Signal {signal} on {sym['symbol_name']}: "
        f"book_buy={depth['bid_qty']} book_sell={depth['ask_qty']} "
        f"need>={sym['volume_difference']}",
        {"depth": depth, "candidates": [c["sym"]["symbol_name"] for c in candidates]},
    )
    _enter_trade(sym, signal, depth)


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
        if (
            _at_or_past_stop_time(settings["stop_time"])
            and _has_open_position()
        ):
            _square_off_all_open_positions("EOD")
        return

    fyers_service.fetch_balance()
    _process_pending_leg_cancels()

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


def _square_off_all_open_positions(reason: str) -> None:
    """Exit every open position for today — broker-filled or paper (rejected entry)."""
    square_off_all_open_trades(reason)


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

    stale = repository.finalize_stale_open_trades()
    if stale:
        _log_app(
            f"Cleared {len(stale)} stale open trade(s) before session start",
            {"trade_ids": stale},
        )

    _sync_open_position_from_db()

    _stop_event.clear()
    repository.set_strategy_running(True)
    _thread = threading.Thread(target=_run_loop, name="strategy-engine", daemon=True)
    _thread.start()
    _log_app("Strategy started via engine")
    return True, ""


def stop(square_off: bool = True, exit_reason: str = "SESSION") -> tuple[bool, str]:
    _stop_event.set()
    repository.set_strategy_running(False)

    if square_off:
        _square_off_all_open_positions(exit_reason)

    if _thread and _thread.is_alive():
        _thread.join(timeout=3.0)

    reset_session_state()
    _log_app("Strategy stopped via engine")
    return True, ""


def reset_session_state() -> None:
    """Clear in-memory strategy flags after Stop."""
    global _open_position, _last_signal, _last_tick_at, _thread, _last_scanner_bias

    with _lock:
        _open_position = None
        _last_signal = None
        _last_scanner_bias = None
        _last_tick_at = None
        _thread = None
    fyers_service.clear_depth_cache()
