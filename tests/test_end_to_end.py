"""
End-to-end test for the entry + bracket-exit order flow.

Drives the real engine tick loop (scanner bias -> depth signal ->
market or VWAP-band limit entry -> exit legs -> OCO cancel -> position
released) against a fake Fyers broker, then checks the same data through
the Flask API the UI uses.

No external test runner needed:

    .venv\\Scripts\\python.exe tests/test_end_to_end.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Point the app at a throwaway database BEFORE create_app() runs init_db,
# otherwise the test would write to the live data/symbols.db.
TMP_DIR = Path(tempfile.mkdtemp(prefix="e2e_"))
TEST_DB = TMP_DIR / "e2e.db"

from app.config import Config  # noqa: E402

Config.DATABASE_PATH = TEST_DB
Config.SCANNER_CSV_PATH = TMP_DIR / "absent-scanner.csv"

import app as app_pkg  # noqa: E402
from app import fyers_market_ws  # noqa: E402

# Keep background threads out of a deterministic test.
app_pkg.start_scheduler = lambda: None
fyers_market_ws.ensure_worker = lambda: None

from app import (  # noqa: E402
    create_app,
    database,
    fyers_service,
    market_tz,
    repository,
    strategy_engine as se,
)


def assert_isolated_db() -> None:
    active = Path(database._db_path) if database._db_path else None
    if active != TEST_DB:
        raise SystemExit(
            f"ABORT: tests are pointed at {active}, not the temp DB {TEST_DB}"
        )
    if repository.list_symbols() or repository.list_scanner_symbols():
        raise SystemExit("ABORT: expected an empty temp database")

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, got, expected) -> None:
    if got == expected:
        PASSED.append(label)
        print(f"  OK  {label}: {got!r}")
    else:
        FAILED.append(label)
        print(f"  BAD {label}: {got!r} != {expected!r}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------
# Fake Fyers broker
# --------------------------------------------------------------------------
class FakeBroker:
    """Minimal stand-in for the Fyers order + market-data APIs."""

    def __init__(self) -> None:
        self.book: dict[str, dict] = {}
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self.depth: dict[str, dict] = {}
        self.seq = 0
        self.reject_sl = False
        self.cancel_confirms = True
        self.balance = 10_000.0

    # ---- helpers ----
    def _next_id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}{self.seq}"

    def _record(self, order_id: str, kind: str, side: int, qty: int, **prices) -> None:
        self.book[order_id] = {
            "id": order_id,
            "status": fyers_service.ORDER_STATUS_PENDING,
            "status_label": "PENDING",
            "filled_qty": 0.0,
            "remaining_qty": float(qty),
            "qty": float(qty),
            "traded_price": None,
            "limit_price": prices.get("limit_price", 0),
            "stop_price": prices.get("stop_price", 0),
            "order_type": kind,
            "message": "",
        }
        self.placed.append(
            {"id": order_id, "kind": kind, "side": side, "qty": qty, **prices}
        )

    def fill(self, order_id: str, price: float, qty: float | None = None) -> None:
        order = self.book[order_id]
        order.update(
            {
                "status": fyers_service.ORDER_STATUS_FILLED,
                "status_label": "FILLED",
                "filled_qty": float(qty if qty is not None else order["qty"]),
                "traded_price": price,
            }
        )

    def legs(self, kind: str, tag: str | None = None) -> list[dict]:
        return [
            p
            for p in self.placed
            if p["kind"] == kind and (tag is None or p.get("tag") == tag)
        ]

    def reset(self) -> None:
        self.placed.clear()
        self.cancelled.clear()

    # ---- order API ----
    def place_market_order(self, symbol_name: str, side: int, quantity: int = 1) -> dict:
        order_id = self._next_id("M")
        self._record(order_id, "MARKET", side, quantity, tag="entry")
        # Market orders fill instantly at the configured fill price.
        self.fill(order_id, self.depth[symbol_name.upper()]["fill_price"])
        return {
            "request": {"symbol": symbol_name, "qty": quantity, "type": 2, "side": side},
            "response": {"s": "ok", "code": 1101, "id": order_id},
        }

    def place_limit_order(
        self, symbol_name, side, quantity, limit_price, order_tag="target"
    ) -> dict:
        order_id = self._next_id("T")
        self._record(
            order_id, "LIMIT", side, quantity, limit_price=limit_price, tag=order_tag
        )
        return {
            "request": {"type": 1, "side": side, "limitPrice": limit_price},
            "response": {"s": "ok", "code": 1101, "id": order_id},
        }

    def place_sl_limit_order(
        self, symbol_name, side, quantity, trigger_price, limit_price,
        order_tag="stoploss",
    ) -> dict:
        if self.reject_sl:
            return {
                "request": {"type": 4, "side": side, "stopPrice": trigger_price},
                "response": {"s": "error", "code": -99, "message": "insufficient margin"},
            }
        order_id = self._next_id("S")
        self._record(
            order_id, "SL-L", side, quantity,
            stop_price=trigger_price, limit_price=limit_price, tag=order_tag,
        )
        return {
            "request": {
                "type": 4, "side": side,
                "stopPrice": trigger_price, "limitPrice": limit_price,
            },
            "response": {"s": "ok", "code": 1101, "id": order_id},
        }

    def cancel_order(self, order_id: str) -> dict:
        self.cancelled.append(order_id)
        if not self.cancel_confirms:
            return {
                "request": {"id": order_id},
                "response": {"s": "error", "message": "cancel rejected"},
            }
        order = self.book.get(order_id)
        if order and order["status"] != fyers_service.ORDER_STATUS_FILLED:
            order.update(
                {"status": fyers_service.ORDER_STATUS_CANCELLED, "status_label": "CANCELLED"}
            )
        return {"request": {"id": order_id}, "response": {"s": "ok", "code": 1103}}

    def fetch_order_states(self, order_ids: list[str]) -> dict:
        return {"orders": {i: self.book[i] for i in order_ids if i in self.book}}

    # ---- market data API ----
    def get_market_depth(self, symbol_name: str) -> dict | None:
        row = self.depth.get((symbol_name or "").upper())
        if not row:
            return None
        return {
            "symbol": f"NSE:{symbol_name.upper()}-EQ",
            "bid_qty": row["bid_qty"],
            "ask_qty": row["ask_qty"],
            "bid_price": row["bid_price"],
            "ask_price": row["ask_price"],
            "qty_source": "full_book",
            "source": "websocket",
            "cache_age_sec": 0.1,
        }

    def get_ltp(self, symbol_name: str) -> float | None:
        row = self.depth.get((symbol_name or "").upper())
        return row["ltp"] if row else None


BROKER = FakeBroker()


def set_depth(symbol, bid_qty, ask_qty, price, fill_price=None, ltp=None):
    BROKER.depth[symbol.upper()] = {
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "bid_price": price - 0.05,
        "ask_price": price + 0.05,
        "ltp": ltp if ltp is not None else price,
        "fill_price": fill_price if fill_price is not None else price,
    }


def install_broker() -> None:
    fyers_service.is_connected = lambda: True
    fyers_service.sync_market_websocket = lambda: None
    fyers_service.is_market_ws_active = lambda: True
    fyers_service.tick_depth_refresh = lambda names: None
    fyers_service.fetch_balance = lambda: (BROKER.balance, {"source": "fake"})
    fyers_service.get_cached_balance = lambda: BROKER.balance
    fyers_service.get_market_depth = BROKER.get_market_depth
    fyers_service.fetch_market_depth_immediate = BROKER.get_market_depth
    fyers_service.get_ltp = BROKER.get_ltp
    fyers_service.place_market_order = BROKER.place_market_order
    fyers_service.place_limit_order = BROKER.place_limit_order
    fyers_service.place_sl_limit_order = BROKER.place_sl_limit_order
    fyers_service.cancel_order = BROKER.cancel_order
    fyers_service.fetch_order_states = BROKER.fetch_order_states


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------
SCANNER_SYMBOLS = ["SCAN1", "SCAN2", "SCAN3", "SCAN4", "SCAN5"]
WATCH_SYMBOLS = ["ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON"]


def seed() -> None:
    now = market_tz.now()
    repository.update_strategy_config(
        start_time=(now - timedelta(hours=1)).strftime("%H:%M"),
        stop_time=(now + timedelta(hours=1)).strftime("%H:%M"),
        max_trades=10,
        timezone="Asia/Kolkata",
        vwap_enabled=False,
        leverage_multiplier=5,
    )
    repository.set_strategy_running(True)

    for name in SCANNER_SYMBOLS:
        repository.create_scanner_symbol(name, "5m", 100)
        set_depth(name, 900, 100, 50.0)  # buy_diff 800 -> BUY bias
    for name in WATCH_SYMBOLS:
        repository.create_symbol(name, "5m", 500, 2.0, 2.0)
        set_depth(name, 100, 100, 100.0)  # flat -> no signal


def scanner_bias(direction: str) -> None:
    for name in SCANNER_SYMBOLS:
        if direction == "BUY":
            set_depth(name, 900, 100, 50.0)
        else:
            set_depth(name, 100, 900, 50.0)


def arm_entry(symbol: str, direction: str, price: float, fill_price: float) -> None:
    """Give one watchlist symbol a passing depth signal."""
    if direction == "BUY":
        set_depth(symbol, 1000, 100, price, fill_price=fill_price)
    else:
        set_depth(symbol, 100, 1000, price, fill_price=fill_price)


def disarm(symbol: str, price: float) -> None:
    set_depth(symbol, 100, 100, price)


def turn_vwap(enabled: bool) -> None:
    settings = repository.get_strategy_settings()
    repository.update_strategy_config(
        settings["start_time"],
        settings["stop_time"],
        settings["max_trades"],
        settings["timezone"],
        vwap_enabled=enabled,
        leverage_multiplier=settings["leverage_multiplier"],
    )


def test_vwap_band_math() -> None:
    section("VWAP LTP band: BUY 98-100, SELL 100-102")
    low, high = fyers_service.vwap_band_prices(100, 2)
    check("band low is 2% below VWAP", round(low, 2), 98.0)
    check("band high is 2% above VWAP", round(high, 2), 102.0)
    check("buy limit sits at band low", fyers_service.vwap_entry_limit_price("BUY", 100, 2), 98.0)
    check("sell limit sits at band high", fyers_service.vwap_entry_limit_price("SELL", 100, 2), 102.0)

    ok, _, _ = fyers_service.passes_vwap_band_filter("BUY", 100, 99, 2)
    check("buy inside band", ok, True)
    ok, _, _ = fyers_service.passes_vwap_band_filter("BUY", 100, 100, 2)
    check("buy at VWAP", ok, True)
    ok, _, _ = fyers_service.passes_vwap_band_filter("BUY", 100, 97.99, 2)
    check("buy below band blocked", ok, False)
    ok, _, _ = fyers_service.passes_vwap_band_filter("BUY", 100, 100.01, 2)
    check("buy above VWAP blocked", ok, False)

    ok, _, _ = fyers_service.passes_vwap_band_filter("SELL", 100, 101, 2)
    check("sell inside band", ok, True)
    ok, _, _ = fyers_service.passes_vwap_band_filter("SELL", 100, 100, 2)
    check("sell at VWAP", ok, True)
    ok, _, _ = fyers_service.passes_vwap_band_filter("SELL", 100, 102.01, 2)
    check("sell above band blocked", ok, False)
    ok, _, _ = fyers_service.passes_vwap_band_filter("SELL", 100, 99.99, 2)
    check("sell below VWAP blocked", ok, False)


def test_vwap_band_gate_on_tick() -> None:
    section("VWAP-on entries are limits at the band edge")
    orig = fyers_service.get_vwap_with_meta
    fyers_service.get_vwap_with_meta = lambda *a, **k: {"vwap": 100.0}
    turn_vwap(True)
    BROKER.reset()
    scanner_bias("BUY")
    set_depth("ALPHA", 1000, 100, 100.10, fill_price=100.00, ltp=103.0)
    se._tick()
    check("buy blocked when LTP is above VWAP", len(BROKER.legs("LIMIT", "entry")), 0)
    check("no market entry while blocked", len(BROKER.legs("MARKET")), 0)

    set_depth("ALPHA", 1000, 100, 100.10, fill_price=100.00, ltp=99.0)
    se._tick()
    entries = BROKER.legs("LIMIT", "entry")
    check("buy limit sent when LTP is in 98-100", len(entries), 1)
    check("buy limit price is band low", entries[0]["limit_price"], 98.0)
    check("buy limit is a buy", entries[0]["side"], 1)
    check("no market entry on VWAP path", len(BROKER.legs("MARKET")), 0)
    check("exit legs wait for the fill", len(BROKER.legs("SL-L")), 0)
    check("qty sized off the 98 limit", entries[0]["qty"], int(10_000 * 5 / 98.0))
    trade = repository.get_open_trade()
    check("pending trade occupies the slot", trade["entry_status"], "PENDING")

    se._tick()
    check("no duplicate limit while pending", len(BROKER.legs("LIMIT", "entry")), 1)

    BROKER.fill(entries[0]["id"], 98.0)
    se._tick()
    check("target placed after fill", len(BROKER.legs("LIMIT", "target")), 1)
    check("sl placed after fill", len(BROKER.legs("SL-L")), 1)
    check("filled trade status", repository.get_open_trade()["entry_status"], "FILLED")

    se.square_off_all_open_trades("STOP")
    repository.delete_trades(today_only=True)
    disarm("ALPHA", 100.10)
    BROKER.reset()
    fyers_service.get_vwap_with_meta = orig
    turn_vwap(False)


def test_vwap_sell_limit_at_band_high() -> None:
    section("VWAP sell limit sits at the high of the band")
    orig = fyers_service.get_vwap_with_meta
    fyers_service.get_vwap_with_meta = lambda *a, **k: {"vwap": 100.0}
    turn_vwap(True)
    BROKER.reset()
    scanner_bias("SELL")
    set_depth("BETA", 100, 1000, 100.10, fill_price=100.00, ltp=101.0)
    se._tick()
    entries = BROKER.legs("LIMIT", "entry")
    check("sell limit sent when LTP is in 100-102", len(entries), 1)
    check("sell limit price is band high", entries[0]["limit_price"], 102.0)
    check("sell limit is a sell", entries[0]["side"], -1)
    check("no market sell on VWAP path", len(BROKER.legs("MARKET")), 0)

    se.square_off_all_open_trades("STOP")
    repository.delete_trades(today_only=True)
    disarm("BETA", 100.10)
    BROKER.reset()
    fyers_service.get_vwap_with_meta = orig
    turn_vwap(False)


def test_unfilled_limit_cancelled_when_ltp_leaves_band() -> None:
    section("Unfilled VWAP limit is cancelled when LTP leaves the band")
    orig = fyers_service.get_vwap_with_meta
    fyers_service.get_vwap_with_meta = lambda *a, **k: {"vwap": 100.0}
    turn_vwap(True)
    BROKER.reset()
    scanner_bias("BUY")
    set_depth("GAMMA", 1000, 100, 100.10, fill_price=100.00, ltp=99.0)
    se._tick()
    entries = BROKER.legs("LIMIT", "entry")
    check("limit parked inside the band", len(entries), 1)
    trade_id = repository.get_open_trade()["id"]

    set_depth("GAMMA", 1000, 100, 100.10, fill_price=100.00, ltp=103.0)
    se._tick()
    closed = repository.get_trade(trade_id)
    check("resting limit cancelled", entries[0]["id"] in BROKER.cancelled, True)
    check("no market flatten of an unfilled limit", len(BROKER.legs("MARKET")), 0)
    check("exit reason is unfilled", closed["exit_reason"], "UNFILLED")
    check("zero pnl", closed["pnl"], 0.0)
    check("slot released", repository.get_open_trade(), None)
    check("position released", se.get_open_position(), None)

    repository.delete_trades(today_only=True)
    disarm("GAMMA", 100.10)
    BROKER.reset()
    fyers_service.get_vwap_with_meta = orig
    turn_vwap(False)


def test_buy_target_hit(app):
    section("BUY entry -> target limit fills -> SL cancelled")
    BROKER.reset()
    scanner_bias("BUY")
    arm_entry("ALPHA", "BUY", 100.10, fill_price=100.00)

    se._tick()

    entry = BROKER.legs("MARKET")
    target = BROKER.legs("LIMIT")
    stop = BROKER.legs("SL-L")
    check("one market entry sent", len(entry), 1)
    check("entry side buy", entry[0]["side"], 1)
    check("target leg placed", len(target), 1)
    check("sl leg placed", len(stop), 1)

    # 10,000 balance x 5 leverage / 100.10 ask = 499 shares
    expected_qty = int(10_000 * 5 / 100.10)
    check("exposure-based quantity", entry[0]["qty"], expected_qty)
    check("legs use same quantity", target[0]["qty"], expected_qty)

    # SL/target come off the real fill (100.00), not the 100.10 ask
    check("target = entry +2%", target[0]["limit_price"], 102.00)
    check("target leg is a sell", target[0]["side"], -1)
    check("sl trigger = entry -2%", stop[0]["stop_price"], 98.00)
    check("sl limit below trigger", stop[0]["limit_price"], 97.90)
    check("sl leg is a sell", stop[0]["side"], -1)

    trade = repository.get_open_trade()
    check("trade open", trade is not None, True)
    check("trade levels realigned to fill", (trade["stop_loss"], trade["target"]),
          (98.0, 102.0))
    check("exit mode", trade["exit_mode"], "broker")

    # Another symbol also signals, but one-position-at-a-time must hold
    arm_entry("BETA", "BUY", 200.0, fill_price=200.0)
    se._tick()
    check("no second entry while position open", len(BROKER.legs("MARKET")), 1)
    check("still one open trade", repository.get_open_trade()["id"], trade["id"])
    disarm("BETA", 200.0)

    # Target executes at the broker
    target_id = target[0]["id"]
    BROKER.fill(target_id, 102.05)
    se._tick()

    closed = repository.get_trade(trade["id"])
    check("sl leg cancelled", BROKER.cancelled, [stop[0]["id"]])
    check("sl order is dead", BROKER.book[stop[0]["id"]]["status"],
          fyers_service.ORDER_STATUS_CANCELLED)
    check("exit reason", closed["exit_reason"], "TARGET")
    check("exit price = broker traded price", closed["exit_price"], 102.05)
    check("pnl", round(closed["pnl"], 2), round((102.05 - 100.0) * expected_qty, 2))
    check("exit recorded as broker leg", closed["exit_via"], "broker_leg")
    check("position released", se.get_open_position(), None)
    check("no leftover legs", se.has_pending_leg_cancels(), False)
    check("no extra orders sent", len(BROKER.legs("MARKET")), 1)
    return closed


def test_same_symbol_reentry_after_exit():
    section("Same symbol can re-enter after the previous trade has exited")
    BROKER.reset()
    scanner_bias("BUY")
    arm_entry("ALPHA", "BUY", 100.10, fill_price=100.00)
    se._tick()
    check("same symbol re-enters after exit", len(BROKER.legs("MARKET")), 1)
    second = repository.get_open_trade()
    check("re-entry is a new trade id", second is not None, True)
    se.square_off_all_open_trades("STOP")
    disarm("ALPHA", 100.0)
    check("position released after re-entry cleanup", repository.get_open_trade(), None)


def test_sell_stop_hit():
    section("SELL entry -> SL-L fills -> target cancelled")
    BROKER.reset()
    scanner_bias("SELL")
    disarm("ALPHA", 100.0)
    arm_entry("GAMMA", "SELL", 500.0, fill_price=500.0)

    se._tick()
    target = BROKER.legs("LIMIT")[0]
    stop = BROKER.legs("SL-L")[0]
    check("short target is a buy", target["side"], 1)
    check("short target = entry -2%", target["limit_price"], 490.00)
    check("short sl is a buy", stop["side"], 1)
    check("short sl trigger = entry +2%", stop["stop_price"], 510.00)
    check("short sl limit above trigger", stop["limit_price"], 510.55)

    trade = repository.get_open_trade()
    BROKER.fill(stop["id"], 510.20)
    se._tick()

    closed = repository.get_trade(trade["id"])
    check("target leg cancelled", BROKER.cancelled, [target["id"]])
    check("exit reason", closed["exit_reason"], "SL")
    check("short loss", round(closed["pnl"], 2),
          round((500.0 - 510.20) * trade["quantity"], 2))
    check("position released", se.get_open_position(), None)
    disarm("GAMMA", 500.0)


def test_rejected_sl_falls_back_to_local():
    section("SL leg rejected (margin) -> local monitoring still exits")
    BROKER.reset()
    scanner_bias("BUY")
    BROKER.reject_sl = True
    arm_entry("BETA", "BUY", 200.0, fill_price=200.0)

    se._tick()
    trade = repository.get_open_trade()
    check("target leg still placed", len(BROKER.legs("LIMIT")), 1)
    check("sl leg missing", len(BROKER.legs("SL-L")), 0)
    check("exit mode hybrid", trade["exit_mode"], "hybrid")

    # Price falls through the stop: local monitoring must exit at market
    set_depth("BETA", 100, 100, 195.0, ltp=195.0)
    se._tick()

    closed = repository.get_trade(trade["id"])
    check("local exit fired", closed["exit_reason"], "SL")
    check("exited with a market order", closed["exit_via"], "market_order")
    check("orphan target leg cancelled", BROKER.legs("LIMIT")[0]["id"] in BROKER.cancelled,
          True)
    check("position released", se.get_open_position(), None)
    BROKER.reject_sl = False
    disarm("BETA", 200.0)


def test_unconfirmed_cancel_blocks_next_entry():
    section("Cancel not confirmed -> no new entry until the leg is cleared")
    BROKER.reset()
    scanner_bias("BUY")
    arm_entry("DELTA", "BUY", 300.0, fill_price=300.0)

    se._tick()
    trade = repository.get_open_trade()
    target = BROKER.legs("LIMIT")[0]
    stop = BROKER.legs("SL-L")[0]

    BROKER.cancel_confirms = False
    BROKER.fill(target["id"], 306.10)
    se._tick()

    closed = repository.get_trade(trade["id"])
    check("trade still closed on the fill", closed["exit_reason"], "TARGET")
    check("cancel retried", len(BROKER.cancelled), se.LEG_CANCEL_ATTEMPTS)
    check("leftover leg tracked", se.has_pending_leg_cancels(), True)

    # A fresh signal must still be refused while the leftover cancel is live
    BROKER.reset()
    disarm("DELTA", 300.0)
    arm_entry("EPSILON", "BUY", 100.0, fill_price=100.0)
    se._tick()
    check("no entry while leg unconfirmed", len(BROKER.legs("MARKET")), 0)
    check("no open trade", repository.get_open_trade(), None)

    # Broker accepts the cancel -> trading resumes on the next tick
    BROKER.cancel_confirms = True
    se._tick()
    check("leftover leg cleared", se.has_pending_leg_cancels(), False)
    check("sl order cancelled", BROKER.book[stop["id"]]["status"],
          fyers_service.ORDER_STATUS_CANCELLED)
    check("entry taken after cleanup", len(BROKER.legs("MARKET")), 1)
    return repository.get_open_trade()


def test_eod_square_off(open_trade):
    section("EOD square-off cancels both legs before the market exit")
    BROKER.reset()
    legs_before = [
        o for o in BROKER.book
        if BROKER.book[o]["status"] == fyers_service.ORDER_STATUS_PENDING
    ]
    check("two legs live before square-off", len(legs_before), 2)

    se.square_off_all_open_trades("EOD")

    closed = repository.get_trade(open_trade["id"])
    check("both legs cancelled", sorted(BROKER.cancelled), sorted(legs_before))
    check("one market exit sent", len(BROKER.legs("MARKET")), 1)
    check("exit reason", closed["exit_reason"], "EOD")
    check("exit via market order", closed["exit_via"], "market_order")
    check("no open trade left", repository.get_open_trade(), None)
    check("nothing pending", se.has_pending_leg_cancels(), False)


def test_api_surface(app, first_trade):
    section("Flask API shows the bracket data the UI renders")
    client = app.test_client()

    status = client.get("/api/strategy").get_json()
    check("strategy endpoint ok", "is_running" in status, True)
    check("no open position", status["open_position"], None)
    check("pending legs exposed", status["pending_leg_cancels"], [])

    payload = client.get("/api/logs/orders").get_json()
    kinds = {(r["trade_id"], r["event_type"]) for r in payload["events"]}
    check("entry event present", (first_trade["id"], "ENTRY") in kinds, True)
    check("exit event present", (first_trade["id"], "EXIT") in kinds, True)
    check("summary counts closed trades", payload["summary"]["closed_trades"] > 0, True)

    detail = client.get(f"/api/logs/orders/{first_trade['id']}").get_json()
    check("target order id in detail", detail["target_order_id"] is not None, True)
    check("sl order id in detail", detail["sl_order_id"] is not None, True)
    check("sl trigger in detail", detail["sl_trigger_price"], 98.0)
    check("sl limit in detail", detail["sl_limit_price"], 97.9)
    check("executed leg in detail", detail["exit_leg"], "TARGET")
    check("target leg request stored", detail["target_leg_request"]["type"], 1)
    check("sl leg request stored", detail["sl_leg_request"]["type"], 4)

    order_logs = repository.list_order_logs()
    statuses = {r["status"] for r in order_logs}
    for expected in ("TARGET_PLACED", "SL_PLACED", "SL_CANCEL_OK", "EXIT_TARGET_FILLED"):
        check(f"order log has {expected}", expected in statuses, True)
    types = {r["order_type"] for r in order_logs}
    check("order log records LIMIT leg", "LIMIT" in types, True)
    check("order log records SL-L leg", "SL-L" in types, True)


def main() -> int:
    install_broker()
    app = create_app()
    with app.app_context():
        assert_isolated_db()
        seed()
        test_vwap_band_math()
        test_vwap_band_gate_on_tick()
        test_vwap_sell_limit_at_band_high()
        test_unfilled_limit_cancelled_when_ltp_leaves_band()
        first_trade = test_buy_target_hit(app)
        test_same_symbol_reentry_after_exit()
        test_sell_stop_hit()
        test_rejected_sl_falls_back_to_local()
        open_trade = test_unconfirmed_cancel_blocks_next_entry()
        test_eod_square_off(open_trade)
        test_api_surface(app, first_trade)

    total = len(PASSED) + len(FAILED)
    print(f"\n{'=' * 60}")
    if FAILED:
        print(f"FAILED {len(FAILED)}/{total}")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print(f"ALL {total} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
