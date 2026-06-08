"""Enrich trade records for the Order Logs detail popup."""

from __future__ import annotations

import json

from app import fyers_service, repository


def enrich_trade_for_display(trade: dict, persist: bool = True) -> dict:
    """Fill missing VWAP and API fields for older trades."""
    enriched = dict(trade)
    updates: dict = {}

    sym = repository.get_symbol_by_name(trade["symbol_name"])
    time_frame = (
        enriched.get("time_frame")
        or (sym["time_frame"] if sym else None)
        or "5m"
    )
    enriched["time_frame"] = time_frame

    if enriched.get("vwap") is None:
        vwap_meta = fyers_service.get_vwap_with_meta(
            trade["symbol_name"], time_frame
        )
        if vwap_meta and vwap_meta.get("vwap") is not None:
            enriched["vwap"] = vwap_meta["vwap"]
            updates["vwap"] = vwap_meta["vwap"]
            updates["time_frame"] = time_frame
            if vwap_meta.get("request"):
                enriched["vwap_api_request"] = vwap_meta["request"]
                updates["vwap_api_request"] = vwap_meta["request"]
            if vwap_meta.get("response"):
                enriched["vwap_api_response"] = vwap_meta["response"]
                updates["vwap_api_response"] = vwap_meta["response"]
            if vwap_meta.get("candle_count") is not None:
                updates["vwap_candle_count"] = vwap_meta["candle_count"]

    if enriched.get("entry_api_request") is None or enriched.get(
        "entry_api_response"
    ) is None:
        log = repository.find_entry_app_log_for_trade(trade)
        if log:
            details = _parse_log_details(log.get("details"))
            if enriched.get("entry_api_request") is None and details.get("request"):
                enriched["entry_api_request"] = details["request"]
                updates["entry_api_request"] = details["request"]
            if enriched.get("entry_api_response") is None:
                response = details.get("response") or details.get("entry_api_response")
                if response:
                    enriched["entry_api_response"] = response
                    updates["entry_api_response"] = response

    if enriched.get("entry_api_request") is None:
        reconstructed = _reconstruct_order_request(trade)
        if reconstructed:
            enriched["entry_api_request"] = reconstructed
            updates["entry_api_request"] = reconstructed

    if persist and updates:
        repository.merge_trade_details(trade["id"], updates)

    return enriched


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
