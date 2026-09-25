import json

from app import market_tz
from app.database import (
    app_log_row_to_dict,
    get_connection,
    order_row_to_dict,
    scanner_row_to_dict,
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
    tsl: float = 0,
    entry_range_down_pct: float = 2,
    entry_range_up_pct: float = 5,
    entry_buffer_pct: float | None = None,
) -> dict:
    down = float(
        entry_range_down_pct if entry_buffer_pct is None else entry_buffer_pct
    )
    up = float(entry_range_up_pct)
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO symbol_settings
                (symbol_name, time_frame, volume_difference, stop_loss_pct,
                 target_pct, tsl, entry_buffer_pct, entry_range_down_pct,
                 entry_range_up_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol_name,
                time_frame,
                volume_difference,
                stop_loss_pct,
                target_pct,
                tsl,
                down,
                down,
                up,
            ),
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
    tsl: float = 0,
    entry_range_down_pct: float = 2,
    entry_range_up_pct: float = 5,
    entry_buffer_pct: float | None = None,
) -> dict | None:
    down = float(
        entry_range_down_pct if entry_buffer_pct is None else entry_buffer_pct
    )
    up = float(entry_range_up_pct)
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE symbol_settings
            SET symbol_name = ?, time_frame = ?, volume_difference = ?,
                stop_loss_pct = ?, target_pct = ?, tsl = ?,
                entry_buffer_pct = ?, entry_range_down_pct = ?,
                entry_range_up_pct = ?
            WHERE id = ?
            """,
            (
                symbol_name,
                time_frame,
                volume_difference,
                stop_loss_pct,
                target_pct,
                tsl,
                down,
                down,
                up,
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
                    (symbol_name, time_frame, volume_difference, stop_loss_pct,
                     target_pct, tsl, entry_buffer_pct, entry_range_down_pct,
                     entry_range_up_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["symbol_name"],
                    row["time_frame"],
                    row["volume_difference"],
                    row["stop_loss_pct"],
                    row["target_pct"],
                    row.get("tsl", 0),
                    float(row.get("entry_range_down_pct", row.get("entry_buffer_pct", 2)) or 2),
                    float(row.get("entry_range_down_pct", row.get("entry_buffer_pct", 2)) or 2),
                    float(row.get("entry_range_up_pct", 5) or 5),
                ),
            )
        conn.commit()
    return list_symbols()


def list_scanner_symbols() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM scanner_settings ORDER BY id ASC"
        ).fetchall()
    return [scanner_row_to_dict(r) for r in rows]


def get_scanner_symbol(scanner_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM scanner_settings WHERE id = ?",
            (scanner_id,),
        ).fetchone()
    return scanner_row_to_dict(row) if row else None


def create_scanner_symbol(
    symbol_name: str, time_frame: str, volume_difference: float = 0
) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO scanner_settings (symbol_name, time_frame, volume_difference)
            VALUES (?, ?, ?)
            """,
            (symbol_name, time_frame, volume_difference),
        )
        conn.commit()
        scanner_id = cur.lastrowid
    return get_scanner_symbol(scanner_id)


def update_scanner_symbol(
    scanner_id: int,
    symbol_name: str,
    time_frame: str,
    volume_difference: float = 0,
) -> dict | None:
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE scanner_settings
            SET symbol_name = ?, time_frame = ?, volume_difference = ?
            WHERE id = ?
            """,
            (symbol_name, time_frame, volume_difference, scanner_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_scanner_symbol(scanner_id)


def delete_scanner_symbol(scanner_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM scanner_settings WHERE id = ?",
            (scanner_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def replace_all_scanner_symbols(rows: list[dict]) -> list[dict]:
    with get_connection() as conn:
        conn.execute("DELETE FROM scanner_settings")
        for row in rows:
            conn.execute(
                """
                INSERT INTO scanner_settings
                    (symbol_name, time_frame, volume_difference)
                VALUES (?, ?, ?)
                """,
                (
                    row["symbol_name"],
                    row["time_frame"],
                    float(row.get("volume_difference") or 0),
                ),
            )
        conn.commit()
    return list_scanner_symbols()


def bootstrap_scanner_from_csv(csv_path) -> int:
    """Import scanner.csv when the table is empty. Returns rows imported."""
    if list_scanner_symbols():
        return 0
    path = csv_path
    if not path.exists():
        return 0
    from app.scanner_csv import parse_scanner_csv

    rows, errors = parse_scanner_csv(path.read_bytes())
    if errors or not rows:
        return 0
    replace_all_scanner_symbols(rows)
    return len(rows)


def repair_zero_scanner_volume_from_csv(csv_path) -> int:
    """
    If every scanner row has volume_difference 0, copy thresholds from scanner.csv.
    An empty/zero threshold makes majority fire on any 1-share imbalance.
    """
    symbols = list_scanner_symbols()
    if not symbols:
        return 0
    if any(float(s.get("volume_difference") or 0) > 0 for s in symbols):
        return 0
    path = csv_path
    if not path.exists():
        return 0
    from app.scanner_csv import parse_scanner_csv

    rows, errors = parse_scanner_csv(path.read_bytes())
    if errors or not rows:
        return 0
    by_name = {
        str(r["symbol_name"]).strip().upper(): float(r.get("volume_difference") or 0)
        for r in rows
    }
    updated = 0
    for sym in symbols:
        new_diff = by_name.get(str(sym["symbol_name"]).strip().upper())
        if new_diff is None or new_diff <= 0:
            continue
        update_scanner_symbol(
            sym["id"],
            sym["symbol_name"],
            sym["time_frame"],
            new_diff,
        )
        updated += 1
    return updated


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


def trades_to_log_events(trades: list[dict]) -> list[dict]:
    """One row per entry and one row per exit (chronological, newest first)."""
    events: list[dict] = []

    def _sizing_fields(trade: dict) -> dict:
        share_value = trade.get("share_value")
        if share_value is None:
            share_value = trade.get("entry_price")
        qty = trade.get("quantity")
        order_value = trade.get("order_value")
        if order_value is None and share_value is not None and qty is not None:
            order_value = float(share_value) * float(qty)
        return {
            "share_value": share_value,
            "exposure": trade.get("exposure"),
            "leverage_multiplier": trade.get("leverage_multiplier"),
            "available_balance": trade.get("available_balance"),
            "order_value": order_value,
        }

    for trade in trades:
        trade_id = trade["id"]
        entry_side = trade["side"]
        exit_side = "SELL" if entry_side == "BUY" else "BUY"
        sizing = _sizing_fields(trade)

        events.append(
            {
                "id": f"{trade_id}-entry",
                "trade_id": trade_id,
                "event_type": "ENTRY",
                "time": trade["entry_time"],
                "symbol_name": trade["symbol_name"],
                "side": entry_side,
                "quantity": trade["quantity"],
                "price": trade["entry_price"],
                "status": trade["entry_status"],
                "order_type": trade.get("entry_order_type") or "MARKET",
                "exit_reason": None,
                "stop_loss": trade["stop_loss"],
                "target": trade["target"],
                "pnl": None,
                "is_open": trade["is_open"],
                **sizing,
            }
        )

        if trade.get("exit_time"):
            events.append(
                {
                    "id": f"{trade_id}-exit",
                    "trade_id": trade_id,
                    "event_type": "EXIT",
                    "time": trade["exit_time"],
                    "symbol_name": trade["symbol_name"],
                    "side": exit_side,
                    "quantity": trade["quantity"],
                    "price": trade["exit_price"],
                    "status": trade["exit_status"],
                    "exit_reason": trade["exit_reason"],
                    "stop_loss": trade["stop_loss"],
                    "target": trade["target"],
                    "pnl": trade["pnl"],
                    "is_open": False,
                    **sizing,
                }
            )

    events.sort(key=lambda row: row["time"] or "", reverse=True)
    return events


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
) -> dict | None:
    if entry_time is None:
        with get_connection() as conn:
            entry_time = conn.execute(
                "SELECT datetime('now', 'localtime')"
            ).fetchone()[0]

    details_json = json.dumps(details) if details else None

    with get_connection() as conn:
        open_row = conn.execute(
            """
            SELECT id FROM trades
            WHERE exit_time IS NULL
              AND date(entry_time) = date(?)
            LIMIT 1
            """,
            (market_tz.today_key_ist(),),
        ).fetchone()
        if open_row:
            return None

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


def update_trade_levels(
    trade_id: int,
    stop_loss: float,
    target: float,
    entry_price: float | None = None,
    entry_status: str | None = None,
) -> dict | None:
    """Re-align SL/target (and entry) once the broker reports the real fill price."""
    with get_connection() as conn:
        assignments = ["stop_loss = ?", "target = ?"]
        params: list = [stop_loss, target]
        if entry_price is not None:
            assignments.append("entry_price = ?")
            params.append(entry_price)
        if entry_status is not None:
            assignments.append("entry_status = ?")
            params.append(entry_status.upper())
        params.extend([trade_id])
        conn.execute(
            f"""
            UPDATE trades
            SET {", ".join(assignments)}
            WHERE id = ? AND exit_time IS NULL
            """,
            params,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return trade_row_to_dict(row) if row else None


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


def find_signal_app_log_for_trade(trade: dict) -> dict | None:
    symbol = trade.get("symbol_name")
    entry_time = trade.get("entry_time")
    side = trade.get("side")
    if not symbol or not entry_time or not side:
        return None

    pattern = f"Signal {side} on {symbol}%"
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM app_logs
            WHERE activity_type = 'strategy'
              AND description LIKE ?
              AND datetime(created_at) BETWEEN datetime(?, '-3 minutes')
                                         AND datetime(?, '+1 minute')
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
    vwap_enabled = row["vwap_enabled"] if "vwap_enabled" in keys else 1
    leverage = row["leverage_multiplier"] if "leverage_multiplier" in keys else 5
    return {
        "start_time": row["start_time"],
        "stop_time": row["stop_time"],
        "max_trades": int(max_trades),
        "timezone": timezone,
        "vwap_enabled": bool(vwap_enabled),
        "leverage_multiplier": float(leverage),
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
            "vwap_enabled": True,
            "leverage_multiplier": 5.0,
            "is_running": False,
            "api_connected": False,
        }
    return _strategy_row_to_dict(row)


def count_trades_today() -> int:
    """Strategy entries opened today (excludes unfilled/rejected limits)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM trades
            WHERE date(entry_time) = date(?)
              AND COALESCE(exit_reason, '') NOT IN ('UNFILLED', 'REJECTED')
            """,
            (market_tz.today_key_ist(),),
        ).fetchone()
    return int(row["cnt"]) if row else 0


def symbols_traded_today() -> set[str]:
    """Symbol names that already had an entry today (open or closed)."""
    today = market_tz.today_key_ist()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT symbol_name FROM trades
            WHERE date(entry_time) = date(?)
            """,
            (today,),
        ).fetchall()
    return {str(r["symbol_name"]).upper() for r in rows}


def has_symbol_traded_today(symbol_name: str) -> bool:
    today = market_tz.today_key_ist()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM trades
            WHERE date(entry_time) = date(?)
              AND symbol_name = ? COLLATE NOCASE
            LIMIT 1
            """,
            (today, symbol_name.strip()),
        ).fetchone()
    return row is not None


def get_open_trade() -> dict | None:
    """Today's single open trade (filled or paper), oldest first."""
    today = market_tz.today_key_ist()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM trades
            WHERE exit_time IS NULL
              AND date(entry_time) = date(?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (today,),
        ).fetchone()
    return trade_row_to_dict(row) if row else None


def has_open_trade() -> bool:
    today = market_tz.today_key_ist()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM trades
            WHERE exit_time IS NULL
              AND date(entry_time) = date(?)
            LIMIT 1
            """,
            (today,),
        ).fetchone()
    return row is not None


def list_open_trades_today() -> list[dict]:
    today = market_tz.today_key_ist()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM trades
            WHERE exit_time IS NULL
              AND date(entry_time) = date(?)
            ORDER BY id ASC
            """,
            (today,),
        ).fetchall()
    return [trade_row_to_dict(r) for r in rows]


def finalize_stale_open_trades() -> list[int]:
    """Close open trades from prior days — new session starts with a clean slate."""
    today = market_tz.today_key_ist()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM trades
            WHERE exit_time IS NULL
              AND date(entry_time) < date(?)
            ORDER BY id ASC
            """,
            (today,),
        ).fetchall()

    closed_ids: list[int] = []
    for row in rows:
        trade = trade_row_to_dict(row)
        closed = close_trade(
            trade_id=trade["id"],
            exit_price=trade["entry_price"],
            exit_reason="EOD",
            exit_status=(
                "PAPER" if trade["entry_status"] != "FILLED" else "SKIPPED"
            ),
            pnl=0.0,
        )
        if closed:
            closed_ids.append(trade["id"])
    return closed_ids


def square_off_todays_open_trades(exit_reason: str = "EOD") -> list[int]:
    """Time-based exit for all still-open trades today (full order log + PnL)."""
    from app import strategy_engine

    return strategy_engine.square_off_all_open_trades(exit_reason)


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
    vwap_enabled: bool | None = None,
    leverage_multiplier: float | None = None,
) -> dict:
    with get_connection() as conn:
        if vwap_enabled is None and leverage_multiplier is None:
            conn.execute(
                """
                UPDATE strategy_settings
                SET start_time = ?, stop_time = ?, max_trades = ?, timezone = ?
                WHERE id = 1
                """,
                (start_time, stop_time, max_trades, timezone.strip()),
            )
        elif vwap_enabled is not None and leverage_multiplier is not None:
            conn.execute(
                """
                UPDATE strategy_settings
                SET start_time = ?, stop_time = ?, max_trades = ?, timezone = ?,
                    vwap_enabled = ?, leverage_multiplier = ?
                WHERE id = 1
                """,
                (
                    start_time,
                    stop_time,
                    max_trades,
                    timezone.strip(),
                    1 if vwap_enabled else 0,
                    float(leverage_multiplier),
                ),
            )
        elif vwap_enabled is not None:
            conn.execute(
                """
                UPDATE strategy_settings
                SET start_time = ?, stop_time = ?, max_trades = ?, timezone = ?,
                    vwap_enabled = ?
                WHERE id = 1
                """,
                (
                    start_time,
                    stop_time,
                    max_trades,
                    timezone.strip(),
                    1 if vwap_enabled else 0,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE strategy_settings
                SET start_time = ?, stop_time = ?, max_trades = ?, timezone = ?,
                    leverage_multiplier = ?
                WHERE id = 1
                """,
                (
                    start_time,
                    stop_time,
                    max_trades,
                    timezone.strip(),
                    float(leverage_multiplier),
                ),
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
