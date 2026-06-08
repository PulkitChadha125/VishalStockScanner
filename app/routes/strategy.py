import re

from flask import Blueprint, jsonify, request

from app import fyers_service, market_tz, repository, strategy_engine, strategy_scheduler

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
    status = strategy_engine.get_engine_status()
    return jsonify({**status, "timezones": list(market_tz.COMMON_TIMEZONES)})


def _schedule_error_if_running() -> str | None:
    if repository.get_strategy_settings().get("is_running"):
        return "Stop the strategy before changing schedule or timezone."
    return None


@strategy_bp.route("/times", methods=["PUT"])
def update_times():
    blocked = _schedule_error_if_running()
    if blocked:
        return jsonify({"error": blocked}), 400

    data = request.get_json(silent=True) or {}
    start_time = str(data.get("start_time", "")).strip()
    stop_time = str(data.get("stop_time", "")).strip()
    timezone = str(data.get("timezone", market_tz.DEFAULT_TIMEZONE)).strip()

    error = _validate_times(start_time, stop_time)
    if error:
        return jsonify({"error": error}), 400

    tz_err = market_tz.validate_timezone(timezone)
    if tz_err:
        return jsonify({"error": tz_err}), 400

    try:
        max_trades = int(data.get("max_trades", 2))
    except (TypeError, ValueError):
        return jsonify({"error": "Max trades must be a whole number."}), 400

    if max_trades < 1:
        return jsonify({"error": "Max trades must be at least 1."}), 400

    settings = repository.update_strategy_config(
        start_time, stop_time, max_trades, timezone
    )
    trades_today = repository.count_trades_today()
    _log(
        f"Schedule updated: {start_time}–{stop_time} ({timezone}), max {max_trades}/day",
        {
            "start_time": start_time,
            "stop_time": stop_time,
            "timezone": timezone,
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
    data = request.get_json(silent=True) or {}

    # Optional: save times from UI in same request as Start
    start_time = str(data.get("start_time", "")).strip()
    stop_time = str(data.get("stop_time", "")).strip()
    timezone = str(data.get("timezone", "")).strip()
    if start_time and stop_time:
        error = _validate_times(start_time, stop_time)
        if error:
            return jsonify({"error": error}), 400
        if timezone:
            tz_err = market_tz.validate_timezone(timezone)
            if tz_err:
                return jsonify({"error": tz_err}), 400
        try:
            max_trades = int(data.get("max_trades", 2))
        except (TypeError, ValueError):
            return jsonify({"error": "Max trades must be a whole number."}), 400
        if max_trades < 1:
            return jsonify({"error": "Max trades must be at least 1."}), 400
        settings = repository.get_strategy_settings()
        repository.update_strategy_config(
            start_time,
            stop_time,
            max_trades,
            timezone or settings.get("timezone", market_tz.DEFAULT_TIMEZONE),
        )

    # Clear stale DB "running" flag if engine thread is not alive
    strategy_engine.get_engine_status()

    if strategy_engine.is_engine_running():
        return jsonify(
            {"error": "Strategy is already running. Click Stop first."}
        ), 400

    # Always re-login when Start is clicked (fresh Fyers session)
    ok, err, balance = fyers_service.login_from_csv()
    if not ok:
        repository.set_api_connected(False)
        _console_error("start-auto-login", err or "Unknown auto-login error")
        _log("Auto-login failed during strategy start", {"error": err})
        return jsonify({"error": err or "Fyers auto-login failed"}), 400
    repository.set_api_connected(True)
    _log("Re-login on Start successful", {"available_balance": balance})

    settings = repository.get_strategy_settings()
    start_time = start_time or settings["start_time"]
    stop_time = stop_time or settings["stop_time"]

    if not strategy_engine.is_in_trading_window(start_time, stop_time):
        symbols = repository.list_symbols()
        if symbols:
            strategy_engine.probe_symbol_market_data()
        tz_label = market_tz.timezone_label()
        now_local = market_tz.now_hhmm()
        msg = (
            f"API logged in successfully. Cannot trade now — outside trading window "
            f"({start_time}–{stop_time} {tz_label}, now {now_local}). "
            "Market data was refreshed once; click Start again during the window."
        )
        _log(msg, {"start_time": start_time, "stop_time": stop_time})
        status = strategy_engine.get_engine_status()
        return jsonify(
            {
                **status,
                "api_connected": True,
                "is_running": False,
                "outside_trading_window": True,
                "available_balance": balance,
                "message": msg,
            }
        )

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
            "outside_trading_window": False,
            "message": "Logged in and strategy started. Fetching market depth every second.",
        }
    )


@strategy_bp.route("/stop", methods=["POST"])
def stop_strategy():
    strategy_engine.get_engine_status()
    was_running = strategy_engine.is_engine_running()

    if was_running:
        strategy_engine.stop(square_off=True)
    else:
        repository.set_strategy_running(False)
        strategy_engine.reset_session_state()

    fyers_service.reset_session_state()
    repository.set_api_connected(False)
    strategy_scheduler.on_manual_stop()

    status = strategy_engine.get_engine_status()
    _log("Strategy stopped — session reset (API disconnected, feeds cleared)")
    return jsonify(
        {
            **status,
            "api_connected": False,
            "is_running": False,
            "available_balance": None,
            "message": (
                "Strategy stopped and session reset. "
                "Click Login or Start to connect again."
            ),
        }
    )


@strategy_bp.route("/balance", methods=["GET"])
def get_balance():
    bal, detail = fyers_service.fetch_balance()
    if bal is None and detail and detail.get("error"):
        return jsonify({"error": detail["error"]}), 400
    return jsonify({"available_balance": bal, "raw": detail})
