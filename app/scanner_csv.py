"""CSV export/import for scanner symbols (Name, Timeframe, Volume Diff)."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from app.timeframes import SCANNER_VALID_TIMEFRAMES, allowed_timeframes_display

CSV_HEADERS = ("Name", "Timeframe", "Volume Diff")

_HEADER_ALIASES: dict[str, str] = {
    "name": "symbol_name",
    "symbol": "symbol_name",
    "symbol name": "symbol_name",
    "symbol_name": "symbol_name",
    "timeframe": "time_frame",
    "time frame": "time_frame",
    "time_frame": "time_frame",
    "volume diff": "volume_difference",
    "volume difference": "volume_difference",
    "volume_difference": "volume_difference",
    "vol diff": "volume_difference",
}


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _parse_number(value: str) -> float:
    text = (value or "").strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    return float(text)


def scanner_to_csv(symbols: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    for s in symbols:
        writer.writerow(
            [
                s["symbol_name"],
                s.get("time_frame") or "1d",
                s.get("volume_difference", 0),
            ]
        )
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

    mapped = set(col_map.values())
    if "symbol_name" not in mapped or "time_frame" not in mapped:
        return [], [
            "CSV must have columns: Name, Timeframe, Volume Diff (Volume Diff optional, default 0)"
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
            errors.append(f"Row {line_no}: name and timeframe are required.")
            continue

        tf = time_frame.strip().lower()
        if tf not in SCANNER_VALID_TIMEFRAMES:
            errors.append(
                f"Row {line_no}: invalid timeframe '{time_frame}' "
                f"(use {allowed_timeframes_display()}, 1d)."
            )
            continue

        try:
            vol_raw = record.get("volume_difference", "").strip()
            volume_difference = _parse_number(vol_raw) if vol_raw else 0.0
        except ValueError:
            errors.append(f"Row {line_no}: invalid volume difference.")
            continue

        if volume_difference < 0:
            errors.append(f"Row {line_no}: volume difference cannot be negative.")
            continue

        parsed.append(
            {
                "symbol_name": symbol_name.upper(),
                "time_frame": tf,
                "volume_difference": volume_difference,
            }
        )

    if not parsed and not errors:
        return [], ["No scanner rows found in CSV."]

    return parsed, errors
