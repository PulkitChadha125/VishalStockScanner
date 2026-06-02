import re

from flask import Blueprint, jsonify, request

from app import fyers_service, repository, strategy_engine

strategy_bp = Blueprint("strategy", __name__)

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _console_error(context: str, err: str):
    print(f"[Strategy:{context}] {err}", flush=True)


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
    return jsonify(strategy_engine.get_engine_status())


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
    ok, err, balance = fyers_service.login_from_csv()
    if not ok:
        repository.set_api_connected(False)
        _console_error("login", err or "Unknown login error")
        _log("Broker API login failed", {"error": err})
        return jsonify({"error": err or "Fyers login failed"}), 400

    settings = repository.set_api_connected(True)
    _log("Broker API login successful", {"available_balance": balance})
    return jsonify(
        {
            **settings,
            "available_balance": balance,
            "message": "Login successful.",
        }
    )


@strategy_bp.route("/logout", methods=["POST"])
def logout_api():
    if repository.get_strategy_settings()["is_running"]:
        return jsonify(
            {"error": "Stop the strategy before logging out."}
        ), 400
    fyers_service.logout()
    settings = repository.set_api_connected(False)
    _log("Broker API logout")
    return jsonify({**settings, "available_balance": None, "message": "Logged out."})


@strategy_bp.route("/start", methods=["POST"])
def start_strategy():
    settings = repository.get_strategy_settings()
    if not settings["api_connected"] or not fyers_service.is_connected():
        # Auto-login when Start is clicked.
        ok, err, balance = fyers_service.login_from_csv()
        if not ok:
            repository.set_api_connected(False)
            _console_error("start-auto-login", err or "Unknown auto-login error")
            _log("Auto-login failed during strategy start", {"error": err})
            return jsonify({"error": err or "Fyers auto-login failed"}), 400
        repository.set_api_connected(True)
        settings = repository.get_strategy_settings()
        _log("Auto-login successful on Start", {"available_balance": balance})
    if settings["is_running"] and strategy_engine.is_engine_running():
        return jsonify({"error": "Strategy is already running."}), 400

    ok, err = strategy_engine.start()
    if not ok:
        _console_error("start-engine", err or "Unknown engine start error")
        return jsonify({"error": err}), 400

    status = strategy_engine.get_engine_status()
    _log(
        f"Strategy started ({status['start_time']} – {status['stop_time']})",
        {
            "max_trades": status["max_trades"],
            "available_balance": status.get("available_balance"),
        },
    )
    return jsonify(
        {
            **status,
            "message": "Strategy started.",
        }
    )


@strategy_bp.route("/stop", methods=["POST"])
def stop_strategy():
    settings = repository.get_strategy_settings()
    if not settings["is_running"]:
        return jsonify({"error": "Strategy is not running."}), 400

    strategy_engine.stop(square_off=True)
    status = strategy_engine.get_engine_status()
    _log("Strategy stopped manually")
    return jsonify(
        {**status, "message": "Strategy stopped."}
    )


@strategy_bp.route("/balance", methods=["GET"])
def get_balance():
    bal, detail = fyers_service.fetch_balance()
    if bal is None and detail and detail.get("error"):
        return jsonify({"error": detail["error"]}), 400
    return jsonify({"available_balance": bal, "raw": detail})
