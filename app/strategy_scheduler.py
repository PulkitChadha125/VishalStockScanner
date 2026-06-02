"""
Clock-based scheduler:
- auto-login at/after 09:00 IST once per day
- auto-start at configured start_time once per day
- auto-stop at configured stop_time once per day
"""

from __future__ import annotations

import threading
from datetime import datetime, time as dt_time

import pytz

from app import fyers_service, repository, strategy_engine

IST = pytz.timezone("Asia/Kolkata")

_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()
_logged_in_date: str | None = None
_started_date: str | None = None
_stopped_date: str | None = None


def _now() -> datetime:
    return datetime.now(IST)


def _parse_hhmm(value: str) -> dt_time:
    hh, mm = map(int, value.split(":"))
    return dt_time(hh, mm)


def _today_key() -> str:
    return _now().strftime("%Y-%m-%d")


def _log(msg: str, details: dict | None = None):
    repository.create_app_log(
        "scheduler",
        msg,
        page_path="/strategy/scheduler",
        details=details,
    )
    print(f"[Scheduler] {msg}", flush=True)


def _tick():
    global _logged_in_date, _started_date, _stopped_date

    now = _now()
    tkey = _today_key()
    settings = repository.get_strategy_settings()
    now_t = now.time()

    login_t = dt_time(9, 0)
    start_t = _parse_hhmm(settings["start_time"])
    stop_t = _parse_hhmm(settings["stop_time"])

    # Auto login once/day at or after 09:00
    if now_t >= login_t and _logged_in_date != tkey:
        ok, err, bal = fyers_service.login_from_csv()
        if ok:
            repository.set_api_connected(True)
            _logged_in_date = tkey
            _log("Auto-login completed at 09:00 schedule", {"available_balance": bal})
        else:
            repository.set_api_connected(False)
            _log("Auto-login failed", {"error": err})

    # Auto start once/day at or after configured start time
    if now_t >= start_t and now_t < stop_t and _started_date != tkey:
        if not fyers_service.is_connected():
            ok, err, bal = fyers_service.login_from_csv()
            if ok:
                repository.set_api_connected(True)
                _logged_in_date = tkey
                _log("Auto-login completed before auto-start", {"available_balance": bal})
            else:
                repository.set_api_connected(False)
                _log("Auto-start skipped: login failed", {"error": err})
                return

        ok, err = strategy_engine.start()
        if ok:
            _started_date = tkey
            _stopped_date = None
            _log(
                "Auto-started strategy at configured start time",
                {"start_time": settings["start_time"]},
            )
        else:
            _log("Auto-start skipped", {"reason": err})

    # Auto stop once/day at or after configured stop time
    if now_t >= stop_t and _stopped_date != tkey:
        if strategy_engine.is_engine_running() or repository.get_strategy_settings().get("is_running"):
            strategy_engine.stop(square_off=True)
            _log(
                "Auto-stopped strategy at configured stop time",
                {"stop_time": settings["stop_time"]},
            )
        _stopped_date = tkey


def _run():
    _log("Scheduler thread started")
    last_day = _today_key()

    while not _stop.is_set():
        try:
            # reset one-day flags when date changes
            today = _today_key()
            if today != last_day:
                last_day = today
                # keep references; new day will refresh naturally
            _tick()
        except Exception as e:
            _log("Scheduler tick error", {"error": str(e)})
        _stop.wait(20.0)

    _log("Scheduler thread stopped")


def start_scheduler():
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(
            target=_run,
            name="strategy-scheduler",
            daemon=True,
        )
        _thread.start()

