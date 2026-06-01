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

            INSERT OR IGNORE INTO strategy_settings (id, start_time, stop_time)
            VALUES (1, '09:30', '15:00');
            """
        )
        try:
            conn.execute(
                "ALTER TABLE strategy_settings ADD COLUMN max_trades INTEGER NOT NULL DEFAULT 2"
            )
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "UPDATE strategy_settings SET max_trades = 2 WHERE id = 1 AND max_trades IS NULL"
        )
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
