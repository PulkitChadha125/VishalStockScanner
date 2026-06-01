from flask import Blueprint, jsonify, request

from app import repository

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/orders", methods=["GET"])
def list_order_logs():
    return jsonify(repository.list_order_logs())


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
