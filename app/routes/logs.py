from flask import Blueprint, jsonify, request

from app import market_tz, repository, trade_detail

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/orders", methods=["GET"])
def list_order_logs():
    """
    Trade history with optional filters.
    Query: symbol, from (YYYY-MM-DD), to (YYYY-MM-DD), today=1
    """
    symbol, date_from, date_to, today_only = _parse_log_filters()

    trades = repository.list_trades(
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        today_only=today_only,
    )
    summary = repository.get_trades_summary(
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        today_only=today_only,
    )
    return jsonify(
        {
            "trades": trades,
            "summary": summary,
            "today_ist": market_tz.today_key_ist(),
        }
    )


def _parse_log_filters():
    symbol = request.args.get("symbol") or None
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    today_only = request.args.get("today", "").lower() in ("1", "true", "yes")
    return symbol, date_from, date_to, today_only


@logs_bp.route("/orders/<int:trade_id>", methods=["GET"])
def get_order_trade(trade_id: int):
    trade = repository.get_trade(trade_id)
    if not trade:
        return jsonify({"error": "Trade not found."}), 404
    return jsonify(trade_detail.enrich_trade_for_display(trade))


@logs_bp.route("/orders/<int:trade_id>", methods=["DELETE"])
def delete_order_trade(trade_id: int):
    if not repository.delete_trade(trade_id):
        return jsonify({"error": "Trade not found."}), 404
    return jsonify({"ok": True, "id": trade_id})


@logs_bp.route("/orders", methods=["DELETE"])
def delete_order_trades_bulk():
    """Delete trades (and matching raw order logs) for current filter query."""
    symbol, date_from, date_to, today_only = _parse_log_filters()
    trades_deleted = repository.delete_trades(
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        today_only=today_only,
    )
    orders_deleted = repository.delete_order_logs(
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        today_only=today_only,
    )
    return jsonify(
        {
            "trades_deleted": trades_deleted,
            "order_logs_deleted": orders_deleted,
        }
    )


@logs_bp.route("/orders", methods=["POST"])
def create_order_log():
    """Record an order placed by the strategy or application."""
    if not repository.can_take_more_trades():
        settings = repository.get_strategy_settings()
        taken = repository.count_trades_today()
        return jsonify(
            {
                "error": (
                    f"Daily trade limit reached ({taken}/{settings['max_trades']}). "
                    "No further trades allowed today across all symbols."
                )
            }
        ), 403

    data = request.get_json(silent=True) or {}
    required = ("symbol_name", "side", "order_type", "quantity", "status")
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        quantity = float(data["quantity"])
        price = float(data["price"]) if data.get("price") is not None else None
        stop_loss = (
            float(data["stop_loss"]) if data.get("stop_loss") is not None else None
        )
        target = float(data["target"]) if data.get("target") is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid numeric field."}), 400

    order = repository.create_order_log(
        symbol_name=str(data["symbol_name"]).strip(),
        side=str(data["side"]).strip(),
        order_type=str(data["order_type"]).strip(),
        quantity=quantity,
        status=str(data["status"]).strip(),
        price=price,
        stop_loss=stop_loss,
        target=target,
    )
    repository.create_app_log(
        "order",
        f"Order placed: {order['side']} {order['symbol_name']}",
        page_path="/api/logs/orders",
        details={"order_id": order["id"]},
    )
    return jsonify(order), 201


@logs_bp.route("/app", methods=["GET"])
def list_app_logs():
    return jsonify(repository.list_app_logs())


@logs_bp.route("/app/<int:log_id>", methods=["DELETE"])
def delete_app_log_entry(log_id: int):
    if not repository.delete_app_log(log_id):
        return jsonify({"error": "Log not found."}), 404
    return jsonify({"ok": True, "id": log_id})


@logs_bp.route("/app", methods=["DELETE"])
def delete_all_app_logs():
    deleted = repository.delete_all_app_logs()
    return jsonify({"deleted": deleted})


@logs_bp.route("/app", methods=["POST"])
def create_app_log():
    data = request.get_json(silent=True) or {}
    if not data.get("description"):
        return jsonify({"error": "description is required."}), 400

    log = repository.create_app_log(
        activity_type=str(data.get("activity_type", "activity")),
        description=str(data["description"]),
        page_path=data.get("page_path"),
        element=data.get("element"),
        details=data.get("details"),
    )
    return jsonify(log), 201
