from datetime import datetime

from flask import Blueprint, Response, jsonify, request

from app import fyers_service, market_tz, repository, scanner_service, strategy_engine
from app.symbols_csv import parse_symbols_csv, symbols_to_csv

symbols_bp = Blueprint("symbols", __name__)

REQUIRED_FIELDS = (
    "symbol_name",
    "time_frame",
    "volume_difference",
    "stop_loss_pct",
    "target_pct",
)


def _log_server_activity(description: str, details: dict | None = None):
    repository.create_app_log(
        activity_type="api",
        description=description,
        page_path=request.path,
        details=details,
    )


def _parse_payload():
    data = request.get_json(silent=True) or {}
    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        return None, jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    symbol_name = str(data["symbol_name"]).strip()
    time_frame = str(data["time_frame"]).strip()

    if not symbol_name or not time_frame:
        return None, jsonify({"error": "Symbol name and time frame are required."}), 400

    try:
        volume_difference = float(data["volume_difference"])
        stop_loss_pct = float(data["stop_loss_pct"])
        target_pct = float(data["target_pct"])
        down_raw = data.get("entry_range_down_pct", data.get("entry_buffer_pct", 2))
        up_raw = data.get("entry_range_up_pct", 5)
        entry_range_down_pct = float(down_raw)
        entry_range_up_pct = float(up_raw)
    except (TypeError, ValueError):
        return (
            None,
            jsonify(
                {
                    "error": (
                        "Volume difference, stop loss, target and entry ranges "
                        "must be numbers."
                    )
                }
            ),
            400,
        )

    if volume_difference < 0:
        return None, jsonify({"error": "Volume difference cannot be negative."}), 400
    if stop_loss_pct <= 0 or target_pct <= 0:
        return None, jsonify({"error": "Stop loss and target must be positive."}), 400
    if entry_range_down_pct < 0 or entry_range_up_pct < 0:
        return None, jsonify({"error": "Entry ranges cannot be negative."}), 400
    if entry_range_up_pct <= entry_range_down_pct:
        return (
            None,
            jsonify(
                {
                    "error": (
                        "Entry range up must be greater than entry range down."
                    )
                }
            ),
            400,
        )

    return (
        symbol_name,
        time_frame,
        volume_difference,
        stop_loss_pct,
        target_pct,
        entry_range_down_pct,
        entry_range_up_pct,
    ), None, None


@symbols_bp.route("", methods=["GET"])
def list_symbols():
    return jsonify(repository.list_symbols())


@symbols_bp.route("/market-book", methods=["GET"])
def market_book_snapshot():
    """Lightweight read of WebSocket cache — full book buy/sell totals per symbol."""
    symbols = repository.list_symbols()
    session = market_tz.session_status()

    def _closed_payload(**extra):
        return jsonify(
            {
                "connected": fyers_service.is_connected(),
                "ws_active": fyers_service.is_market_ws_active(),
                "market_open": False,
                "market_message": session["message"],
                "start_time": session["start_time"],
                "stop_time": session["stop_time"],
                "timezone": session["timezone"],
                "now": session["now"],
                "updated_at": market_tz.now_ist().strftime("%H:%M:%S"),
                "symbols": [
                    {"symbol_name": s["symbol_name"], "status": "market_closed"}
                    for s in symbols
                ],
                **extra,
            }
        )

    if not session["market_open"]:
        return _closed_payload()

    if not fyers_service.is_connected():
        return jsonify(
            {
                "connected": False,
                "ws_active": False,
                "market_open": True,
                "market_message": "",
                "start_time": session["start_time"],
                "stop_time": session["stop_time"],
                "timezone": session["timezone"],
                "now": session["now"],
                "updated_at": market_tz.now_ist().strftime("%H:%M:%S"),
                "symbols": [
                    {"symbol_name": s["symbol_name"], "status": "login_required"}
                    for s in symbols
                ],
            }
        )

    rows: list[dict] = []
    engine_status = strategy_engine.get_engine_status()
    strategy_running = bool(engine_status.get("is_running"))
    can_trade = bool(engine_status.get("can_take_more_trades"))
    vwap_enabled = bool(engine_status.get("vwap_enabled", True))
    open_position = engine_status.get("open_position")
    open_symbol = open_position["symbol_name"] if open_position else None
    scanner_bias, scanner_info = scanner_service.get_live_bias()
    scanner_skipped = bool(scanner_info.get("skipped"))
    scanner_summary = {
        "allowed_signal": scanner_bias,
        "buy_count": scanner_info.get("buy_count", 0),
        "sell_count": scanner_info.get("sell_count", 0),
        "majority": scanner_info.get("majority", 0),
        "total": scanner_info.get("total", 0),
        "missing": scanner_info.get("missing", 0),
        "skipped": scanner_skipped,
        "is_neutral": scanner_bias is None and not scanner_skipped,
    }

    if not strategy_running:
        trade_block_reason = "Strategy is stopped — click Start to take trades"
    elif open_symbol:
        trade_block_reason = f"Open trade in {open_symbol} — waiting for SL, target, or time exit"
    elif not can_trade:
        taken = engine_status.get("trades_taken_today", 0)
        max_trades = engine_status.get("max_trades", 0)
        trade_block_reason = f"Daily trade limit reached ({taken}/{max_trades})"
    else:
        trade_block_reason = None

    for sym in symbols:
        name = sym["symbol_name"]
        depth = fyers_service.get_market_depth(name)
        threshold = float(sym["volume_difference"])

        if not depth or depth.get("error"):
            rows.append({"symbol_name": name, "status": "waiting"})
            continue

        if not fyers_service.has_book_totals(depth):
            rows.append({"symbol_name": name, "status": "waiting_totals"})
            continue

        book_buy = float(depth.get("bid_qty") or 0)
        book_sell = float(depth.get("ask_qty") or 0)
        sell_diff = book_sell - book_buy
        buy_diff = book_buy - book_sell
        signal = None
        if sell_diff >= threshold:
            signal = "SELL"
        elif buy_diff >= threshold:
            signal = "BUY"
        vwap_signal = None
        vwap_ok = None
        vwap_reason = None
        ltp = fyers_service.get_ltp(name)
        if signal and vwap_enabled:
            vwap_meta = fyers_service.get_vwap_with_meta(name, sym["time_frame"])
            vwap = vwap_meta.get("vwap") if vwap_meta else None
            range_down, range_up = fyers_service.symbol_entry_ranges(sym)
            vwap_ok, vwap_reason, _ = fyers_service.passes_vwap_band_filter(
                signal,
                vwap,
                ltp,
                range_down,
                range_up,
                vwap_meta.get("prev_close") if vwap_meta else None,
            )
            if vwap_ok:
                vwap_signal = signal
        elif signal and not vwap_enabled:
            vwap_ok = True
            vwap_signal = signal
            vwap_reason = "VWAP filter off"

        scanner_ok = bool(
            signal
            and (scanner_skipped or (scanner_bias is not None and signal == scanner_bias))
        )
        if signal and not scanner_ok:
            if scanner_summary["is_neutral"]:
                vwap_reason = (
                    f"Scanner neutral — need {scanner_summary['majority']} of "
                    f"{scanner_summary['total']} ({signal} blocked)"
                )
            else:
                vwap_reason = (
                    f"Scanner majority is {scanner_bias or '—'} — {signal} blocked"
                )

        trade_ready = bool(
            signal
            and vwap_signal
            and scanner_ok
            and can_trade
            and strategy_running
        )

        total = book_buy + book_sell
        bid_pct = round((book_buy / total) * 100, 2) if total > 0 else 0

        rows.append(
            {
                "symbol_name": name,
                "status": "live",
                "book_buy_qty": book_buy,
                "book_sell_qty": book_sell,
                "bid_pct": bid_pct,
                "bid_price": depth.get("bid_price"),
                "ask_price": depth.get("ask_price"),
                "ltp": ltp,
                "buy_diff": buy_diff,
                "sell_diff": sell_diff,
                "volume_diff": threshold,
                "signal": signal,
                "vwap_signal": vwap_signal,
                "vwap_ok": vwap_ok,
                "vwap_reason": vwap_reason,
                "scanner_ok": scanner_ok,
                "trade_ready": trade_ready,
                "cache_age_sec": depth.get("cache_age_sec"),
                "book_source": depth.get("book_source", "rest"),
            }
        )

    return jsonify(
        {
            "connected": True,
            "ws_active": fyers_service.is_market_ws_active(),
            "market_open": True,
            "market_message": "",
            "start_time": session["start_time"],
            "stop_time": session["stop_time"],
            "timezone": session["timezone"],
            "now": session["now"],
            "updated_at": market_tz.now_ist().strftime("%H:%M:%S"),
            "strategy_running": strategy_running,
            "can_take_trades": can_trade and strategy_running and not open_symbol,
            "trade_block_reason": trade_block_reason,
            "open_position_symbol": open_symbol,
            "scanner": scanner_summary,
            "symbols": rows,
        }
    )


@symbols_bp.route("/export.csv", methods=["GET"])
def export_symbols_csv():
    symbols = repository.list_symbols()
    csv_text = symbols_to_csv(symbols)
    filename = f"symbol_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@symbols_bp.route("/import.csv", methods=["POST"])
def import_symbols_csv():
    if repository.get_strategy_settings().get("is_running"):
        return jsonify(
            {"error": "Stop the strategy before loading a CSV file."}
        ), 400

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "No CSV file selected."}), 400

    if not upload.filename.lower().endswith(".csv"):
        return jsonify({"error": "Please upload a .csv file."}), 400

    rows, errors = parse_symbols_csv(upload.read())
    if errors:
        return jsonify(
            {
                "error": "; ".join(errors),
                "errors": errors,
            }
        ), 400
    if not rows:
        return jsonify({"error": "No valid symbol rows in CSV."}), 400

    symbols = repository.replace_all_symbols(rows)
    fyers_service.sync_market_websocket()
    _log_server_activity(
        f"Loaded {len(symbols)} symbol(s) from CSV",
        {"filename": upload.filename, "count": len(symbols)},
    )
    return jsonify(
        {
            "message": f"Loaded {len(symbols)} symbol(s) from CSV.",
            "symbols": symbols,
            "count": len(symbols),
        }
    )


@symbols_bp.route("/<int:symbol_id>", methods=["GET"])
def get_symbol(symbol_id):
    symbol = repository.get_symbol(symbol_id)
    if not symbol:
        return jsonify({"error": "Symbol not found."}), 404
    return jsonify(symbol)


@symbols_bp.route("", methods=["POST"])
def create_symbol():
    parsed, err_response, status = _parse_payload()
    if err_response is not None:
        return err_response, status

    (
        symbol_name,
        time_frame,
        volume_difference,
        stop_loss_pct,
        target_pct,
        entry_range_down_pct,
        entry_range_up_pct,
    ) = parsed
    symbol = repository.create_symbol(
        symbol_name,
        time_frame,
        volume_difference,
        stop_loss_pct,
        target_pct,
        entry_range_down_pct=entry_range_down_pct,
        entry_range_up_pct=entry_range_up_pct,
    )
    fyers_service.sync_market_websocket()
    _log_server_activity(
        f"Symbol created: {symbol_name}",
        {"symbol_id": symbol["id"], "time_frame": time_frame},
    )
    return jsonify(symbol), 201


@symbols_bp.route("/<int:symbol_id>", methods=["PUT"])
def update_symbol(symbol_id):
    parsed, err_response, status = _parse_payload()
    if err_response is not None:
        return err_response, status

    (
        symbol_name,
        time_frame,
        volume_difference,
        stop_loss_pct,
        target_pct,
        entry_range_down_pct,
        entry_range_up_pct,
    ) = parsed
    symbol = repository.update_symbol(
        symbol_id,
        symbol_name,
        time_frame,
        volume_difference,
        stop_loss_pct,
        target_pct,
        entry_range_down_pct=entry_range_down_pct,
        entry_range_up_pct=entry_range_up_pct,
    )
    if not symbol:
        return jsonify({"error": "Symbol not found."}), 404
    fyers_service.sync_market_websocket()
    _log_server_activity(
        f"Symbol updated: {symbol_name}",
        {"symbol_id": symbol_id},
    )
    return jsonify(symbol)


@symbols_bp.route("/<int:symbol_id>", methods=["DELETE"])
def delete_symbol(symbol_id):
    symbol = repository.get_symbol(symbol_id)
    if not repository.delete_symbol(symbol_id):
        return jsonify({"error": "Symbol not found."}), 404
    fyers_service.sync_market_websocket()
    name = symbol["symbol_name"] if symbol else str(symbol_id)
    _log_server_activity(f"Symbol deleted: {name}", {"symbol_id": symbol_id})
    return jsonify({"message": "Deleted successfully."})
