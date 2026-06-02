import json

from app.database import (
    app_log_row_to_dict,
    get_connection,
    order_row_to_dict,
    symbol_row_to_dict,
)


def list_symbols() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM symbol_settings ORDER BY id ASC"
        ).fetchall()
    return [symbol_row_to_dict(r) for r in rows]


def get_symbol(symbol_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM symbol_settings WHERE id = ?",
            (symbol_id,),
        ).fetchone()
    return symbol_row_to_dict(row) if row else None


def create_symbol(
    symbol_name: str,
    time_frame: str,
    volume_difference: float,
    stop_loss_pct: float,
    target_pct: float,
) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO symbol_settings
                (symbol_name, time_frame, volume_difference, stop_loss_pct, target_pct)
            VALUES (?, ?, ?, ?, ?)
            """,
            (symbol_name, time_frame, volume_difference, stop_loss_pct, target_pct),
        )
        conn.commit()
        symbol_id = cur.lastrowid
    return get_symbol(symbol_id)


def update_symbol(
    symbol_id: int,
    symbol_name: str,
    time_frame: str,
    volume_difference: float,
    stop_loss_pct: float,
    target_pct: float,
) -> dict | None:
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE symbol_settings
            SET symbol_name = ?, time_frame = ?, volume_difference = ?, stop_loss_pct = ?, target_pct = ?
            WHERE id = ?
            """,
            (
                symbol_name,
                time_frame,
                volume_difference,
                stop_loss_pct,
                target_pct,
                symbol_id,
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_symbol(symbol_id)


def delete_symbol(symbol_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM symbol_settings WHERE id = ?",
            (symbol_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def list_order_logs() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM order_logs ORDER BY id DESC"
        ).fetchall()
    return [order_row_to_dict(r) for r in rows]


def create_order_log(
    symbol_name: str,
    side: str,
    order_type: str,
    quantity: float,
    status: str,
    price: float | None = None,
    stop_loss: float | None = None,
    target: float | None = None,
) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO order_logs
                (symbol_name, side, order_type, quantity, price, status, stop_loss, target)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol_name,
                side.upper(),
                order_type.upper(),
                quantity,
                price,
                status.upper(),
                stop_loss,
                target,
            ),
        )
        conn.commit()
        order_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM order_logs WHERE id = ?", (order_id,)
        ).fetchone()
    return order_row_to_dict(row)


def list_app_logs() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM app_logs ORDER BY id DESC"
        ).fetchall()
    return [app_log_row_to_dict(r) for r in rows]


def create_app_log(
    activity_type: str,
    description: str,
    page_path: str | None = None,
    element: str | None = None,
    details: dict | str | None = None,
) -> dict:
    if isinstance(details, dict):
        details = json.dumps(details)
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO app_logs
                (activity_type, description, page_path, element, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (activity_type, description, page_path, element, details),
        )
        conn.commit()
        log_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM app_logs WHERE id = ?", (log_id,)
        ).fetchone()
    return app_log_row_to_dict(row)


def _strategy_row_to_dict(row) -> dict:
    keys = row.keys()
    max_trades = row["max_trades"] if "max_trades" in keys else 2
    return {
        "start_time": row["start_time"],
        "stop_time": row["stop_time"],
        "max_trades": int(max_trades),
        "is_running": bool(row["is_running"]),
        "api_connected": bool(row["api_connected"]),
    }


def get_strategy_settings() -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_settings WHERE id = 1"
        ).fetchone()
    if not row:
        return {
            "start_time": "09:30",
            "stop_time": "15:00",
            "max_trades": 2,
            "is_running": False,
            "api_connected": False,
        }
    return _strategy_row_to_dict(row)


def count_trades_today() -> int:
    """Filled/placed orders today across all symbols (universe-wide cap)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM order_logs
            WHERE date(placed_at) = date('now', 'localtime')
              AND status IN ('PLACED', 'FILLED', 'PARTIAL')
            """
        ).fetchone()
    return int(row["cnt"]) if row else 0


def can_take_more_trades() -> bool:
    settings = get_strategy_settings()
    return count_trades_today() < settings["max_trades"]


def update_strategy_config(
    start_time: str, stop_time: str, max_trades: int
) -> dict:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE strategy_settings
            SET start_time = ?, stop_time = ?, max_trades = ?
            WHERE id = 1
            """,
            (start_time, stop_time, max_trades),
        )
        conn.commit()
    return get_strategy_settings()


def set_strategy_running(is_running: bool) -> dict:
    with get_connection() as conn:
        conn.execute(
            "UPDATE strategy_settings SET is_running = ? WHERE id = 1",
            (1 if is_running else 0,),
        )
        conn.commit()
    return get_strategy_settings()


def set_api_connected(connected: bool) -> dict:
    with get_connection() as conn:
        conn.execute(
            "UPDATE strategy_settings SET api_connected = ? WHERE id = 1",
            (1 if connected else 0,),
        )
        conn.commit()
    return get_strategy_settings()
