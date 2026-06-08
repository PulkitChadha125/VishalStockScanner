"""Enrich trade records for the Order Logs detail popup."""

from __future__ import annotations

import json
import re

from app import fyers_service, repository


def enrich_trade_for_display(trade: dict, persist: bool = True) -> dict:
    """Fill missing VWAP, volume, deviation, and API fields for older trades."""
    enriched = dict(trade)
    updates: dict = {}

    sym = repository.get_symbol_by_name(trade["symbol_name"])
    time_frame = (
        enriched.get("time_frame")
        or (sym["time_frame"] if sym else None)
        or "5m"
    )
    enriched["time_frame"] = time_frame

    if sym:
        if enriched.get("stop_loss_pct") is None:
            enriched["stop_loss_pct"] = sym["stop_loss_pct"]
            updates["stop_loss_pct"] = sym["stop_loss_pct"]
        if enriched.get("target_pct") is None:
            enriched["target_pct"] = sym["target_pct"]
            updates["target_pct"] = sym["target_pct"]
        if enriched.get("volume_difference") is None:
            enriched["volume_difference"] = sym["volume_difference"]
            updates["volume_difference"] = sym["volume_difference"]

    vwap_meta = None
    if (
        enriched.get("vwap") is None
        or enriched.get("prev_prev_close") is None
        or enriched.get("prev_close") is None
    ):
        vwap_meta = fyers_service.get_vwap_with_meta(
            trade["symbol_name"], time_frame
        )

    if vwap_meta:
        if enriched.get("vwap") is None and vwap_meta.get("vwap") is not None:
            enriched["vwap"] = vwap_meta["vwap"]
            updates["vwap"] = vwap_meta["vwap"]
            updates["time_frame"] = time_frame
        if enriched.get("prev_prev_close") is None:
            prev_prev = vwap_meta.get("prev_prev_close")
            if prev_prev is not None:
                enriched["prev_prev_close"] = prev_prev
                updates["prev_prev_close"] = prev_prev
        if enriched.get("prev_close") is None:
            prev = vwap_meta.get("prev_close")
            if prev is not None:
                enriched["prev_close"] = prev
                updates["prev_close"] = prev
        if vwap_meta.get("request") and enriched.get("vwap_api_request") is None:
            enriched["vwap_api_request"] = vwap_meta["request"]
            updates["vwap_api_request"] = vwap_meta["request"]
        if vwap_meta.get("response") and enriched.get("vwap_api_response") is None:
            enriched["vwap_api_response"] = vwap_meta["response"]
            updates["vwap_api_response"] = vwap_meta["response"]
        if vwap_meta.get("candle_count") is not None:
            updates.setdefault("vwap_candle_count", vwap_meta["candle_count"])

    _enrich_from_app_logs(enriched, trade, updates)

    if enriched.get("entry_api_request") is None:
        reconstructed = _reconstruct_order_request(trade)
        if reconstructed:
            enriched["entry_api_request"] = reconstructed
            updates["entry_api_request"] = reconstructed

    if persist and updates:
        repository.merge_trade_details(trade["id"], updates)

    return enriched


def _enrich_from_app_logs(enriched: dict, trade: dict, updates: dict) -> None:
    entry_log = repository.find_entry_app_log_for_trade(trade)
    signal_log = repository.find_signal_app_log_for_trade(trade)

    for log in (entry_log, signal_log):
        if not log:
            continue
        details = _parse_log_details(log.get("details"))
        depth = details.get("depth") or {}

        if enriched.get("book_buy_qty") is None and depth.get("bid_qty") is not None:
            enriched["book_buy_qty"] = float(depth["bid_qty"])
            updates["book_buy_qty"] = enriched["book_buy_qty"]
        if enriched.get("book_sell_qty") is None and depth.get("ask_qty") is not None:
            enriched["book_sell_qty"] = float(depth["ask_qty"])
            updates["book_sell_qty"] = enriched["book_sell_qty"]

        if enriched.get("entry_api_request") is None and details.get("request"):
            enriched["entry_api_request"] = details["request"]
            updates["entry_api_request"] = details["request"]
        if enriched.get("entry_api_response") is None:
            response = details.get("response") or details.get("entry_api_response")
            if response:
                enriched["entry_api_response"] = response
                updates["entry_api_response"] = response

    if enriched.get("volume_trigger") is None:
        buy = enriched.get("book_buy_qty")
        sell = enriched.get("book_sell_qty")
        if buy is not None and sell is not None:
            if trade.get("side") == "BUY":
                trigger = float(buy) - float(sell)
            else:
                trigger = float(sell) - float(buy)
            enriched["volume_trigger"] = trigger
            updates["volume_trigger"] = trigger

    if enriched.get("volume_difference") is None and signal_log:
        desc = signal_log.get("description") or ""
        match = re.search(r"need>=(\d+(?:\.\d+)?)", desc)
        if match:
            threshold = float(match.group(1))
            enriched["volume_difference"] = threshold
            updates["volume_difference"] = threshold


def _parse_log_details(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _reconstruct_order_request(trade: dict) -> dict | None:
    try:
        import FyresIntegration as fyi

        side = 1 if trade.get("side") == "BUY" else -1
        sym = fyers_service.to_fyers_symbol(trade["symbol_name"])
        qty = int(trade.get("quantity") or 1)
        return fyi.build_order_payload(sym, qty, 2, side, 0)
    except Exception:
        return None
