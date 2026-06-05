"""CSV export/import for symbol settings."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from app.timeframes import VALID_TIMEFRAMES, allowed_timeframes_display

CSV_HEADERS = (
    "Symbol",
    "Time Frame",
    "Volume Diff",
    "Stop Loss %",
    "Target %",
)

_HEADER_ALIASES: dict[str, str] = {
    "symbol": "symbol_name",
    "symbol name": "symbol_name",
    "time frame": "time_frame",
    "timeframe": "time_frame",
    "time_frame": "time_frame",
    "volume diff": "volume_difference",
    "volume dif": "volume_difference",
    "volume difference": "volume_difference",
    "volume_difference": "volume_difference",
    "stop loss %": "stop_loss_pct",
    "stop loss": "stop_loss_pct",
    "stop_loss_pct": "stop_loss_pct",
    "stop loss pct": "stop_loss_pct",
    "target %": "target_pct",
    "target": "target_pct",
    "target_pct": "target_pct",
    "target pct": "target_pct",
}


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _parse_number(value: str) -> float:
    text = (value or "").strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    return float(text)


def symbols_to_csv(symbols: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    for s in symbols:
        writer.writerow(
            [
                s["symbol_name"],
                s["time_frame"],
                s["volume_difference"],
                s["stop_loss_pct"],
                s["target_pct"],
            ]
        )
    return buf.getvalue()


def parse_symbols_csv(file_bytes: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Parse uploaded CSV. Returns (rows ready for DB, list of error strings).
    """
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
        return [], [
            "CSV must have columns: Symbol, Time Frame, Volume Diff, Stop Loss %, Target %"
        ]

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
            errors.append(f"Row {line_no}: symbol and time frame are required.")
            continue

        try:
            volume_difference = _parse_number(record.get("volume_difference", ""))
            stop_loss_pct = _parse_number(record.get("stop_loss_pct", ""))
            target_pct = _parse_number(record.get("target_pct", ""))
        except ValueError:
            errors.append(f"Row {line_no}: invalid numeric value.")
            continue

        if volume_difference < 0:
            errors.append(f"Row {line_no}: volume diff cannot be negative.")
            continue
        if stop_loss_pct <= 0 or target_pct <= 0:
            errors.append(f"Row {line_no}: stop loss and target must be positive.")
            continue

        tf = time_frame.strip().lower()
        if tf not in VALID_TIMEFRAMES:
            errors.append(
                f"Row {line_no}: invalid time frame '{time_frame}' "
                f"(use {allowed_timeframes_display()})."
            )
            continue

        parsed.append(
            {
                "symbol_name": symbol_name.upper(),
                "time_frame": tf,
                "volume_difference": volume_difference,
                "stop_loss_pct": stop_loss_pct,
                "target_pct": target_pct,
            }
        )

    if not parsed and not errors:
        return [], ["No symbol rows found in CSV."]

    return parsed, errors
