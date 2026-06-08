"""
Clock-based scheduler:
- auto-login at/after 09:00 IST once per day
- auto-start at configured start_time once per day
- auto-stop at configured stop_time once per day
"""

from __future__ import annotations

import threading
from datetime import datetime, time as dt_time

from app import fyers_service, market_tz, repository, strategy_engine

_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()
_logged_in_date: str | None = None
_started_date: str | None = None
_stopped_date: str | None = None


def _now() -> datetime:
    return market_tz.now()


def _parse_hhmm(value: str) -> dt_time:
    hh, mm = map(int, value.split(":"))
    return dt_time(hh, mm)


def _today_key() -> str:
    return market_tz.today_key()


# Auto-start only shortly after configured start time (not all day when app is opened later).
AUTO_START_GRACE_MINUTES = 30


def _in_auto_start_window(now_t: dt_time, start_t: dt_time, stop_t: dt_time) -> bool:
    if now_t < start_t or now_t >= stop_t:
        return False
    start_mins = start_t.hour * 60 + start_t.minute
    now_mins = now_t.hour * 60 + now_t.minute
    return (now_mins - start_mins) <= AUTO_START_GRACE_MINUTES


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

    login_just_succeeded = False

    # Auto login once/day at or after 09:00
    if now_t >= login_t and _logged_in_date != tkey:
        ok, err, bal = fyers_service.login_from_csv()
        if ok:
            repository.set_api_connected(True)
            _logged_in_date = tkey
            login_just_succeeded = True
            _log("Auto-login completed at 09:00 schedule", {"available_balance": bal})
        else:
            repository.set_api_connected(False)
            _log("Auto-login failed", {"error": err})

    # If you start the app mid-day: after login, run strategy so depth/VWAP scans begin.
    if (
        login_just_succeeded
        and start_t <= now_t < stop_t
        and not strategy_engine.is_engine_running()
    ):
        ok, err = strategy_engine.start()
        if ok:
            _started_date = tkey
            _stopped_date = None
            _log(
                "Strategy started after login (trading window active — fetching symbol depth)",
                {"start_time": settings["start_time"], "stop_time": settings["stop_time"]},
            )
        else:
            _log("Could not start strategy after login", {"reason": err})

    # Auto start once/day only within grace window after start_time (e.g. 09:30–10:00)
    if _in_auto_start_window(now_t, start_t, stop_t) and _started_date != tkey:
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


def on_manual_stop() -> None:
    """Record manual stop so scheduler state stays consistent for the day."""
    global _stopped_date
    _stopped_date = _today_key()


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

