from flask import Blueprint, jsonify, request

from app import repository

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
    except (TypeError, ValueError):
        return (
            None,
            jsonify(
                {
                    "error": (
                        "Volume difference, stop loss and target must be numbers."
                    )
                }
            ),
            400,
        )

    if volume_difference < 0:
        return None, jsonify({"error": "Volume difference cannot be negative."}), 400
    if stop_loss_pct <= 0 or target_pct <= 0:
        return None, jsonify({"error": "Stop loss and target must be positive."}), 400

    return (
        symbol_name,
        time_frame,
        volume_difference,
        stop_loss_pct,
        target_pct,
    ), None, None


@symbols_bp.route("", methods=["GET"])
def list_symbols():
    return jsonify(repository.list_symbols())


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

    symbol_name, time_frame, volume_difference, stop_loss_pct, target_pct = parsed
    symbol = repository.create_symbol(
        symbol_name, time_frame, volume_difference, stop_loss_pct, target_pct
    )
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

    symbol_name, time_frame, volume_difference, stop_loss_pct, target_pct = parsed
    symbol = repository.update_symbol(
        symbol_id,
        symbol_name,
        time_frame,
        volume_difference,
        stop_loss_pct,
        target_pct,
    )
    if not symbol:
        return jsonify({"error": "Symbol not found."}), 404
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
    name = symbol["symbol_name"] if symbol else str(symbol_id)
    _log_server_activity(f"Symbol deleted: {name}", {"symbol_id": symbol_id})
    return jsonify({"message": "Deleted successfully."})
