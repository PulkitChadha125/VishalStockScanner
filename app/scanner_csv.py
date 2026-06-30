"""CSV export/import for scanner symbols (Name, Timeframe)."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from app.timeframes import SCANNER_VALID_TIMEFRAMES, allowed_timeframes_display

CSV_HEADERS = ("Name", "Timeframe")

_HEADER_ALIASES: dict[str, str] = {
    "name": "symbol_name",
    "symbol": "symbol_name",
    "symbol name": "symbol_name",
    "symbol_name": "symbol_name",
    "timeframe": "time_frame",
    "time frame": "time_frame",
    "time_frame": "time_frame",
}


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def scanner_to_csv(symbols: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    for s in symbols:
        writer.writerow([s["symbol_name"], s["time_frame"]])
    return buf.getvalue()


def parse_scanner_csv(file_bytes: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse uploaded scanner CSV. Returns (rows ready for DB, error strings)."""
    errors: list[str] = []
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["File must be UTF-8 encoded CSV."]

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], ["CSV file is empty."]

    header_row = rows[0]
    col_map: dict[int, str] = {}
    for idx, raw in enumerate(header_row):
        key = _HEADER_ALIASES.get(_norm_header(raw))
        if key:
            col_map[idx] = key

    required = set(_HEADER_ALIASES.values())
    if not required.issubset(set(col_map.values())):
        return [], ["CSV must have columns: Name, Timeframe"]

    parsed: list[dict[str, Any]] = []
    for line_no, row in enumerate(rows[1:], start=2):
        if not row or all(not (c or "").strip() for c in row):
            continue

        record: dict[str, str] = {}
        for idx, field in col_map.items():
            if idx < len(row):
                record[field] = row[idx].strip()

        symbol_name = record.get("symbol_name", "")
        time_frame = record.get("time_frame", "")
        if not symbol_name or not time_frame:
            errors.append(f"Row {line_no}: name and timeframe are required.")
            continue

        tf = time_frame.strip().lower()
        if tf not in SCANNER_VALID_TIMEFRAMES:
            errors.append(
                f"Row {line_no}: invalid timeframe '{time_frame}' "
                f"(use {allowed_timeframes_display()}, 1d)."
            )
            continue

        parsed.append(
            {
                "symbol_name": symbol_name.upper(),
                "time_frame": tf,
            }
        )

    if not parsed and not errors:
        return [], ["No scanner rows found in CSV."]

    return parsed, errors
