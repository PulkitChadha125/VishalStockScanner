import re

from flask import Blueprint, jsonify, request

from app import repository

strategy_bp = Blueprint("strategy", __name__)

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _log(description: str, details: dict | None = None):
    repository.create_app_log(
        "strategy",
        description,
        page_path=request.path,
        details=details,
    )


def _validate_times(start_time: str, stop_time: str):
    if not TIME_PATTERN.match(start_time) or not TIME_PATTERN.match(stop_time):
        return "Times must be in HH:MM format (24-hour)."
    if start_time >= stop_time:
        return "Start time must be before stop time."
    return None


@strategy_bp.route("", methods=["GET"])
def get_strategy():
    settings = repository.get_strategy_settings()
    return jsonify(
        {
            **settings,
            "trades_taken_today": repository.count_trades_today(),
            "can_take_more_trades": repository.can_take_more_trades(),
        }
    )


@strategy_bp.route("/times", methods=["PUT"])
def update_times():
    data = request.get_json(silent=True) or {}
    start_time = str(data.get("start_time", "")).strip()
    stop_time = str(data.get("stop_time", "")).strip()

    error = _validate_times(start_time, stop_time)
    if error:
        return jsonify({"error": error}), 400

    try:
        max_trades = int(data.get("max_trades", 2))
    except (TypeError, ValueError):
        return jsonify({"error": "Max trades must be a whole number."}), 400

    if max_trades < 1:
        return jsonify({"error": "Max trades must be at least 1."}), 400

    settings = repository.update_strategy_config(
        start_time, stop_time, max_trades
    )
    trades_today = repository.count_trades_today()
    _log(
        f"Strategy settings updated: {start_time}–{stop_time}, max {max_trades} trades/day",
        {
            "start_time": start_time,
            "stop_time": stop_time,
            "max_trades": max_trades,
            "trades_taken_today": trades_today,
        },
    )
    return jsonify({**settings, "trades_taken_today": trades_today})


@strategy_bp.route("/login", methods=["POST"])
def login_api():
    """Stub: broker API login will be wired here later."""
    settings = repository.set_api_connected(True)
    _log("Broker API login requested (stub — not connected to broker yet)")
    return jsonify(
        {
            **settings,
            "message": "Login UI ready. Broker API integration pending.",
        }
    )


@strategy_bp.route("/logout", methods=["POST"])
def logout_api():
    """Stub: disconnect from broker API."""
    if repository.get_strategy_settings()["is_running"]:
        return jsonify(
            {"error": "Stop the strategy before logging out."}
        ), 400
    settings = repository.set_api_connected(False)
    _log("Broker API logout (stub)")
    return jsonify({**settings, "message": "Logged out (stub)."})


@strategy_bp.route("/start", methods=["POST"])
def start_strategy():
    settings = repository.get_strategy_settings()
    if not settings["api_connected"]:
        return jsonify(
            {"error": "Login to the broker API before starting the strategy."}
        ), 400
    if settings["is_running"]:
        return jsonify({"error": "Strategy is already running."}), 400

    settings = repository.set_strategy_running(True)
    _log(
        f"Strategy started (scheduled window {settings['start_time']} – {settings['stop_time']})",
        settings,
    )
    return jsonify(
        {
            **settings,
            "message": "Strategy started (stub — engine not connected yet).",
        }
    )


@strategy_bp.route("/stop", methods=["POST"])
def stop_strategy():
    settings = repository.get_strategy_settings()
    if not settings["is_running"]:
        return jsonify({"error": "Strategy is not running."}), 400

    settings = repository.set_strategy_running(False)
    _log("Strategy stopped manually")
    return jsonify(
        {**settings, "message": "Strategy stopped (stub)."}
    )
