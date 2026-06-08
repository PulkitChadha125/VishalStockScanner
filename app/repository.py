import json

from app import market_tz
from app.database import (
    app_log_row_to_dict,
    get_connection,
    order_row_to_dict,
    symbol_row_to_dict,
    trade_row_to_dict,
)


def list_symbols() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM symbol_settings ORDER BY id ASC"
        ).fetchall()
    return [symbol_row_to_dict(r) for r in rows]


def get_symbol_by_name(symbol_name: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM symbol_settings WHERE symbol_name = ? COLLATE NOCASE",
            (symbol_name.strip(),),
        ).fetchone()
    return symbol_row_to_dict(row) if row else None


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


def replace_all_symbols(rows: list[dict]) -> list[dict]:
    """Replace all symbol settings with rows from CSV import."""
    with get_connection() as conn:
        conn.execute("DELETE FROM symbol_settings")
        for row in rows:
            conn.execute(
                """
                INSERT INTO symbol_settings
                    (symbol_name, time_frame, volume_difference, stop_loss_pct, target_pct)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["symbol_name"],
                    row["time_frame"],
                    row["volume_difference"],
                    row["stop_loss_pct"],
                    row["target_pct"],
                ),
            )
        conn.commit()
    return list_symbols()


def list_order_logs() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM order_logs ORDER BY id DESC"
        ).fetchall()
    return [order_row_to_dict(r) for r in rows]


def _trade_filter_clauses(
    symbol: str | None,
    date_from: str | None,
    date_to: str | None,
    today_only: bool,
    *,
    time_column: str = "entry_time",
) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

    if symbol:
        clauses.append("UPPER(symbol_name) = UPPER(?)")
        params.append(symbol.strip())
    if today_only:
        clauses.append(f"date({time_column}) = date(?)")
        params.append(market_tz.today_key_ist())
    else:
        if date_from:
            clauses.append(f"date({time_column}) >= date(?)")
            params.append(date_from)
        if date_to:
            clauses.append(f"date({time_column}) <= date(?)")
            params.append(date_to)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_trades(
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    today_only: bool = False,
) -> list[dict]:
    where, params = _trade_filter_clauses(symbol, date_from, date_to, today_only)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM trades{where} ORDER BY id DESC",
            params,
        ).fetchall()
    return [trade_row_to_dict(r) for r in rows]


def get_trades_summary(
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    today_only: bool = False,
) -> dict:
    where, params = _trade_filter_clauses(symbol, date_from, date_to, today_only)
    closed_where = where + (" AND " if where else " WHERE ") + "exit_time IS NOT NULL"
    if not where:
        closed_where = " WHERE exit_time IS NOT NULL"

    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS closed_count,
                COALESCE(SUM(pnl), 0) AS total_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN pnl = 0 OR pnl IS NULL THEN 1 ELSE 0 END) AS breakeven
            FROM trades
            {closed_where}
            """,
            params,
        ).fetchone()
        open_row = conn.execute(
            f"""
            SELECT COUNT(*) AS open_count FROM trades
            {where}{' AND ' if where else ' WHERE '}exit_time IS NULL
            """,
            params,
        ).fetchone()

    closed = int(row["closed_count"]) if row else 0
    return {
        "closed_trades": closed,
        "open_trades": int(open_row["open_count"]) if open_row else 0,
        "total_pnl": float(row["total_pnl"]) if row else 0.0,
        "wins": int(row["wins"] or 0) if row else 0,
        "losses": int(row["losses"] or 0) if row else 0,
        "breakeven": int(row["breakeven"] or 0) if row else 0,
    }


def create_trade(
    symbol_name: str,
    side: str,
    quantity: float,
    entry_price: float,
    entry_status: str,
    stop_loss: float | None,
    target: float | None,
    entry_time: str | None = None,
    details: dict | None = None,
) -> dict:
    if entry_time is None:
        with get_connection() as conn:
            entry_time = conn.execute(
                "SELECT datetime('now', 'localtime')"
            ).fetchone()[0]

    details_json = json.dumps(details) if details else None

    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO trades
                (symbol_name, side, quantity, entry_time, entry_price,
                 entry_status, stop_loss, target, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol_name.strip(),
                side.upper(),
                quantity,
                entry_time,
                entry_price,
                entry_status.upper(),
                stop_loss,
                target,
                details_json,
            ),
        )
        conn.commit()
        trade_id = cur.lastrowid
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return trade_row_to_dict(row)


def close_trade(
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    exit_status: str,
    pnl: float,
    exit_time: str | None = None,
    details_update: dict | None = None,
) -> dict | None:
    if exit_time is None:
        with get_connection() as conn:
            exit_time = conn.execute(
                "SELECT datetime('now', 'localtime')"
            ).fetchone()[0]

    with get_connection() as conn:
        details_json = None
        if details_update:
            row = conn.execute(
                "SELECT details FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            existing: dict = {}
            if row and row["details"]:
                try:
                    parsed = json.loads(row["details"])
                    if isinstance(parsed, dict):
                        existing = parsed
                except (json.JSONDecodeError, TypeError):
                    existing = {}
            existing.update(details_update)
            details_json = json.dumps(existing)

        if details_json is not None:
            cur = conn.execute(
                """
                UPDATE trades
                SET exit_time = ?, exit_price = ?, exit_reason = ?,
                    exit_status = ?, pnl = ?, details = ?
                WHERE id = ? AND exit_time IS NULL
                """,
                (
                    exit_time,
                    exit_price,
                    exit_reason.upper(),
                    exit_status.upper(),
                    pnl,
                    details_json,
                    trade_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                UPDATE trades
                SET exit_time = ?, exit_price = ?, exit_reason = ?,
                    exit_status = ?, pnl = ?
                WHERE id = ? AND exit_time IS NULL
                """,
                (
                    exit_time,
                    exit_price,
                    exit_reason.upper(),
                    exit_status.upper(),
                    pnl,
                    trade_id,
                ),
            )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return trade_row_to_dict(row) if row else None


def get_trade(trade_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return trade_row_to_dict(row) if row else None


def merge_trade_details(trade_id: int, updates: dict) -> dict | None:
    if not updates:
        return get_trade(trade_id)

    with get_connection() as conn:
        row = conn.execute("SELECT details FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return None

        existing: dict = {}
        if row["details"]:
            try:
                parsed = json.loads(row["details"])
                if isinstance(parsed, dict):
                    existing = parsed
            except (json.JSONDecodeError, TypeError):
                existing = {}

        existing.update(updates)
        conn.execute(
            "UPDATE trades SET details = ? WHERE id = ?",
            (json.dumps(existing), trade_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()

    return trade_row_to_dict(updated) if updated else None


def find_entry_app_log_for_trade(trade: dict) -> dict | None:
    symbol = trade.get("symbol_name")
    entry_time = trade.get("entry_time")
    if not symbol or not entry_time:
        return None

    pattern = f"ENTRY %{symbol}%"
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM app_logs
            WHERE activity_type = 'strategy'
              AND description LIKE ?
              AND datetime(created_at) BETWEEN datetime(?, '-2 minutes')
                                         AND datetime(?, '+2 minutes')
            ORDER BY ABS(
                strftime('%s', created_at) - strftime('%s', ?)
            ) ASC
            LIMIT 1
            """,
            (pattern, entry_time, entry_time, entry_time),
        ).fetchone()

    return app_log_row_to_dict(row) if row else None


def calc_trade_pnl(side: int, entry_price: float, exit_price: float, quantity: float) -> float:
    if side == 1:
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity


def delete_trade(trade_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        conn.commit()
        return cur.rowcount > 0


def delete_trades(
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    today_only: bool = False,
) -> int:
    where, params = _trade_filter_clauses(symbol, date_from, date_to, today_only)
    with get_connection() as conn:
        cur = conn.execute(f"DELETE FROM trades{where}", params)
        conn.commit()
        return cur.rowcount


def delete_order_logs(
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    today_only: bool = False,
) -> int:
    where, params = _trade_filter_clauses(
        symbol, date_from, date_to, today_only, time_column="placed_at"
    )
    with get_connection() as conn:
        cur = conn.execute(f"DELETE FROM order_logs{where}", params)
        conn.commit()
        return cur.rowcount


def delete_app_log(log_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM app_logs WHERE id = ?", (log_id,))
        conn.commit()
        return cur.rowcount > 0


def delete_all_app_logs() -> int:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM app_logs")
        conn.commit()
        return cur.rowcount


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
    timezone = row["timezone"] if "timezone" in keys else "Asia/Kolkata"
    return {
        "start_time": row["start_time"],
        "stop_time": row["stop_time"],
        "max_trades": int(max_trades),
        "timezone": timezone,
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
            "timezone": "Asia/Kolkata",
            "is_running": False,
            "api_connected": False,
        }
    return _strategy_row_to_dict(row)


def count_trades_today() -> int:
    """Strategy entries opened today (includes broker-rejected paper trades)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM trades
            WHERE date(entry_time) = date(?)
            """,
            (market_tz.today_key_ist(),),
        ).fetchone()
    return int(row["cnt"]) if row else 0


def get_open_trade() -> dict | None:
    """Single open trade (exit_time IS NULL), oldest first."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM trades
            WHERE exit_time IS NULL
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    return trade_row_to_dict(row) if row else None


def has_open_trade() -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM trades WHERE exit_time IS NULL LIMIT 1"
        ).fetchone()
    return row is not None


def can_take_more_trades() -> bool:
    if has_open_trade():
        return False
    settings = get_strategy_settings()
    return count_trades_today() < settings["max_trades"]


def update_strategy_config(
    start_time: str,
    stop_time: str,
    max_trades: int,
    timezone: str = "Asia/Kolkata",
) -> dict:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE strategy_settings
            SET start_time = ?, stop_time = ?, max_trades = ?, timezone = ?
            WHERE id = 1
            """,
            (start_time, stop_time, max_trades, timezone.strip()),
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
