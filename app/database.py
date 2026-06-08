import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_db_path: Path | None = None


def init_db(database_path: Path) -> None:
    global _db_path
    _db_path = database_path
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS symbol_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_name TEXT NOT NULL,
                time_frame TEXT NOT NULL,
                volume_difference REAL NOT NULL DEFAULT 0,
                stop_loss_pct REAL NOT NULL,
                target_pct REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_name TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL,
                status TEXT NOT NULL,
                stop_loss REAL,
                target REAL,
                placed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_name TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_status TEXT NOT NULL,
                exit_time TEXT,
                exit_price REAL,
                exit_reason TEXT,
                exit_status TEXT,
                stop_loss REAL,
                target REAL,
                pnl REAL
            );

            CREATE TABLE IF NOT EXISTS app_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_type TEXT NOT NULL,
                description TEXT NOT NULL,
                page_path TEXT,
                element TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS strategy_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                start_time TEXT NOT NULL DEFAULT '09:30',
                stop_time TEXT NOT NULL DEFAULT '15:00',
                is_running INTEGER NOT NULL DEFAULT 0,
                api_connected INTEGER NOT NULL DEFAULT 0
            );

            INSERT OR IGNORE INTO strategy_settings (id, start_time, stop_time, timezone)
            VALUES (1, '09:30', '15:00', 'Asia/Kolkata');
            """
        )
        try:
            conn.execute(
                "ALTER TABLE strategy_settings ADD COLUMN max_trades INTEGER NOT NULL DEFAULT 2"
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE strategy_settings ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata'"
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE symbol_settings ADD COLUMN volume_difference REAL NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "UPDATE strategy_settings SET max_trades = 2 WHERE id = 1 AND max_trades IS NULL"
        )
        conn.execute(
            "UPDATE symbol_settings SET volume_difference = 0 WHERE volume_difference IS NULL"
        )
        conn.execute(
            """
            UPDATE strategy_settings
            SET timezone = 'Asia/Kolkata'
            WHERE id = 1 AND (timezone IS NULL OR timezone = '')
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        migrated = conn.execute(
            "SELECT 1 FROM app_meta WHERE key = 'default_timezone_ist'"
        ).fetchone()
        if not migrated:
            conn.execute(
                """
                UPDATE strategy_settings
                SET timezone = 'Asia/Kolkata'
                WHERE id = 1 AND timezone = 'Asia/Dubai'
                """
            )
            conn.execute(
                "INSERT INTO app_meta (key, value) VALUES ('default_timezone_ist', '1')"
            )
        try:
            conn.execute("ALTER TABLE trades ADD COLUMN details TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()


@contextmanager
def get_connection():
    if _db_path is None:
        raise RuntimeError("Database not initialized. Call init_db first.")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def symbol_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "symbol_name": row["symbol_name"],
        "time_frame": row["time_frame"],
        "volume_difference": row["volume_difference"],
        "stop_loss_pct": row["stop_loss_pct"],
        "target_pct": row["target_pct"],
    }


def order_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "symbol_name": row["symbol_name"],
        "side": row["side"],
        "order_type": row["order_type"],
        "quantity": row["quantity"],
        "price": row["price"],
        "status": row["status"],
        "stop_loss": row["stop_loss"],
        "target": row["target"],
        "placed_at": row["placed_at"],
    }


def _parse_trade_details(row: sqlite3.Row) -> dict:
    raw = row["details"] if "details" in row.keys() else None
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def trade_row_to_dict(row: sqlite3.Row) -> dict:
    details = _parse_trade_details(row)
    return {
        "id": row["id"],
        "symbol_name": row["symbol_name"],
        "side": row["side"],
        "quantity": row["quantity"],
        "entry_time": row["entry_time"],
        "entry_price": row["entry_price"],
        "entry_status": row["entry_status"],
        "exit_time": row["exit_time"],
        "exit_price": row["exit_price"],
        "exit_reason": row["exit_reason"],
        "exit_status": row["exit_status"],
        "stop_loss": row["stop_loss"],
        "target": row["target"],
        "pnl": row["pnl"],
        "is_open": row["exit_time"] is None,
        "vwap": details.get("vwap"),
        "time_frame": details.get("time_frame"),
        "vwap_candle_count": details.get("vwap_candle_count"),
        "vwap_api_request": details.get("vwap_api_request"),
        "vwap_api_response": details.get("vwap_api_response"),
        "vwap_filter_passed": details.get("vwap_filter_passed"),
        "entry_api_request": details.get("entry_api_request"),
        "entry_api_response": details.get("entry_api_response"),
        "exit_api_request": details.get("exit_api_request"),
        "exit_api_response": details.get("exit_api_response"),
    }


def app_log_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "activity_type": row["activity_type"],
        "description": row["description"],
        "page_path": row["page_path"],
        "element": row["element"],
        "details": row["details"],
        "created_at": row["created_at"],
    }
