"""Real-time bid/ask depth and LTP via Fyers WebSocket (background worker thread)."""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

import FyresIntegration as fyi

from app.fyers_credentials import load_credentials
from app import market_tz

_TICK_QUEUE_MAX = 5000


def _short_name(fyers_symbol: str) -> str:
    sym = (fyers_symbol or "").strip()
    if ":" in sym:
        sym = sym.split(":", 1)[1]
    return sym.replace("-EQ", "").replace("-eq", "").upper()


def _ws_access_token() -> str | None:
    token = getattr(fyi, "access_token", None)
    if not token:
        return None
    try:
        store = load_credentials()
    except FileNotFoundError:
        return str(token)
    client_id = (store.get("client_id") or "").strip()
    if client_id:
        return f"{client_id}:{token}"
    return str(token)


def _make_data_socket(**kwargs):
    """FyersDataSocket is a singleton; reset so we can run depth + quote sockets."""
    from fyers_apiv3.FyersWebsocket import data_ws

    data_ws.FyersDataSocket._instance = None
    return data_ws.FyersDataSocket(**kwargs)


def _ts() -> str:
    return market_tz.now_ist().strftime("%H:%M:%S")


def _fmt_qty(qty: float) -> str:
    if abs(qty - round(qty)) < 0.01:
        return str(int(round(qty)))
    return f"{qty:.2f}"


def _sum_side_qty(message: dict, side: str) -> float:
    """Sum bid_size1..5 or ask_size1..5 from depth WebSocket message."""
    total = 0.0
    for level in range(1, 6):
        total += float(message.get(f"{side}_size{level}") or 0)
    return total


def _depth_levels_line(message: dict, side: str) -> str:
    parts: list[str] = []
    for level in range(1, 6):
        price = float(message.get(f"{side}_price{level}") or 0)
        size = float(message.get(f"{side}_size{level}") or 0)
        if price <= 0 and size <= 0:
            continue
        parts.append(f"{price:.2f}x{_fmt_qty(size)}")
    return ", ".join(parts) if parts else "—"


def _print_depth_tick(name: str, message: dict, ltp: float | None) -> None:
    bid_line = _depth_levels_line(message, "bid")
    ask_line = _depth_levels_line(message, "ask")
    bid_total = _sum_side_qty(message, "bid")
    ask_total = _sum_side_qty(message, "ask")
    ltp_part = f"LTP {ltp:.2f}" if ltp is not None and ltp > 0 else "LTP —"
    print(
        f"[WS DEPTH {_ts()}] {name} | "
        f"Bids: {bid_line} (total {bid_total:.0f}) | "
        f"Asks: {ask_line} (total {ask_total:.0f}) | {ltp_part}",
        flush=True,
    )


def _print_ltp_tick(name: str, ltp: float) -> None:
    print(f"[WS LTP   {_ts()}] {name} | Last traded price: {ltp:.2f}", flush=True)


class MarketWebSocketManager:
    """
    Thread 1 (fyers-ws-worker): WebSocket connect/stop, tick printing, console output.
    Flask / HTTP (thread 2+): only enqueues commands and reads the in-memory cache.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._depth: dict[str, dict[str, Any]] = {}
        self._ltp: dict[str, float] = {}
        self._depth_socket = None
        self._quote_socket = None
        self._fyers_symbols: list[str] = []
        self._active = False
        self._command_queue: queue.Queue[tuple] = queue.Queue()
        self._tick_queue: queue.Queue[tuple] = queue.Queue(maxsize=_TICK_QUEUE_MAX)
        self._worker_thread: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    def ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="fyers-ws-worker",
                daemon=True,
            )
            self._worker_thread.start()
            print("[Fyers WS] background worker thread started", flush=True)

    def is_active(self) -> bool:
        return self._active

    def _enqueue_tick(self, item: tuple) -> None:
        try:
            self._tick_queue.put_nowait(item)
        except queue.Full:
            try:
                self._tick_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._tick_queue.put_nowait(item)
            except queue.Full:
                pass

    def _on_depth_message(self, message: dict) -> None:
        if message.get("type") != "dp":
            return
        fyers_sym = message.get("symbol")
        if not fyers_sym:
            return

        bid_price = float(message.get("bid_price1") or 0)
        ask_price = float(message.get("ask_price1") or 0)
        bid_qty = _sum_side_qty(message, "bid")
        ask_qty = _sum_side_qty(message, "ask")
        key = _short_name(fyers_sym)
        entry = {
            "symbol": fyers_sym,
            "bid_price": bid_price,
            "bid_qty": bid_qty,
            "ask_price": ask_price,
            "ask_qty": ask_qty,
            "source": "websocket",
            "updated_at": time.time(),
        }
        with self._lock:
            self._depth[key] = entry
            cached_ltp = self._ltp.get(key)
        self._enqueue_tick(("depth", key, dict(message), cached_ltp))

    def _on_quote_message(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type not in ("sf", None):
            return
        fyers_sym = message.get("symbol")
        ltp = message.get("ltp")
        if not fyers_sym or ltp is None:
            return
        key = _short_name(fyers_sym)
        ltp_val = float(ltp)
        with self._lock:
            self._ltp[key] = ltp_val
        self._enqueue_tick(("ltp", key, ltp_val))

    def _drain_tick_queue(self, max_items: int = 200) -> None:
        for _ in range(max_items):
            try:
                item = self._tick_queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "depth":
                _, name, msg, cached_ltp = item
                _print_depth_tick(name, msg, cached_ltp)
            elif kind == "ltp":
                _, name, ltp_val = item
                _print_ltp_tick(name, ltp_val)
            elif kind == "log":
                print(item[1], flush=True)

    def _start_depth_socket(self, fyers_symbols: list[str]) -> bool:
        access = _ws_access_token()
        if not access:
            return False

        sock_holder: list[Any] = []

        def onopen() -> None:
            sock_holder[0].subscribe(symbols=fyers_symbols, data_type="DepthUpdate")
            sock_holder[0].keep_running()

        def onerror(message) -> None:
            self._enqueue_tick(("log", f"[Fyers WS depth] error: {message}"))

        def onclose(message) -> None:
            self._enqueue_tick(("log", f"[Fyers WS depth] closed: {message}"))

        fyi.ensure_websocket_ssl_for_fyers()
        sock = _make_data_socket(
            access_token=access,
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=onopen,
            on_close=onclose,
            on_error=onerror,
            on_message=self._on_depth_message,
        )
        sock_holder.append(sock)
        self._depth_socket = sock
        sock.connect()
        return True

    def _start_quote_socket(self, fyers_symbols: list[str]) -> bool:
        access = _ws_access_token()
        if not access:
            return False

        sock_holder: list[Any] = []

        def onopen() -> None:
            sock_holder[0].subscribe(symbols=fyers_symbols, data_type="SymbolUpdate")
            sock_holder[0].keep_running()

        def onerror(message) -> None:
            self._enqueue_tick(("log", f"[Fyers WS quote] error: {message}"))

        def onclose(message) -> None:
            self._enqueue_tick(("log", f"[Fyers WS quote] closed: {message}"))

        fyi.ensure_websocket_ssl_for_fyers()
        sock = _make_data_socket(
            access_token=access,
            log_path="",
            litemode=True,
            write_to_file=False,
            reconnect=True,
            on_connect=onopen,
            on_close=onclose,
            on_error=onerror,
            on_message=self._on_quote_message,
        )
        sock_holder.append(sock)
        self._quote_socket = sock
        sock.connect()
        return True

    def _stop_impl(self) -> None:
        for sock in (self._depth_socket, self._quote_socket):
            if sock is None:
                continue
            try:
                sock.close_connection()
            except Exception as e:
                print(f"[Fyers WS] close error: {e}", flush=True)

        self._depth_socket = None
        self._quote_socket = None
        self._active = False
        self._fyers_symbols = []
        with self._lock:
            self._depth.clear()
            self._ltp.clear()

    def _start_impl(self, symbol_names: list[str]) -> bool:
        from app.fyers_service import to_fyers_symbol

        names = sorted(
            {(n or "").strip().upper() for n in symbol_names if (n or "").strip()}
        )
        if not names:
            self._stop_impl()
            return False

        fyers_symbols = [to_fyers_symbol(n) for n in names]
        if self._active and fyers_symbols == self._fyers_symbols:
            return True

        self._stop_impl()
        self._fyers_symbols = fyers_symbols

        depth_ok = self._start_depth_socket(fyers_symbols)
        quote_ok = self._start_quote_socket(fyers_symbols)
        self._active = depth_ok or quote_ok

        if self._active:
            print(
                f"[Fyers WS] connected depth={depth_ok} quote_ltp={quote_ok} "
                f"symbols={len(fyers_symbols)}",
                flush=True,
            )
        else:
            print("[Fyers WS] failed to connect market websocket", flush=True)
        return self._active

    def _worker_loop(self) -> None:
        while True:
            self._drain_tick_queue()
            try:
                cmd = self._command_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            op = cmd[0]
            try:
                if op == "start":
                    self._start_impl(cmd[1])
                elif op == "stop":
                    self._stop_impl()
                    print("[Fyers WS] stopped", flush=True)
                elif op == "sync":
                    self._start_impl(cmd[1])
            except Exception as e:
                print(f"[Fyers WS] worker error ({op}): {e}", flush=True)
            finally:
                self._command_queue.task_done()
                self._drain_tick_queue()

    def _enqueue_command(self, cmd: tuple) -> None:
        self.ensure_worker()
        self._command_queue.put(cmd)

    def start_async(self, symbol_names: list[str]) -> None:
        self._enqueue_command(("start", list(symbol_names)))

    def sync_async(self, symbol_names: list[str]) -> None:
        self._enqueue_command(("sync", list(symbol_names)))

    def stop_async(self) -> None:
        self._enqueue_command(("stop",))

    def get_depth(self, symbol_name: str) -> dict[str, Any] | None:
        key = _short_name(symbol_name)
        with self._lock:
            entry = self._depth.get(key)
            if not entry:
                return None
            age = time.time() - float(entry.get("updated_at") or 0)
            return {**entry, "cache_age_sec": round(age, 1)}

    def get_ltp(self, symbol_name: str) -> float | None:
        key = _short_name(symbol_name)
        with self._lock:
            ltp = self._ltp.get(key)
            if ltp is not None and ltp > 0:
                return ltp
            depth = self._depth.get(key)
        if depth:
            bid = float(depth.get("bid_price") or 0)
            ask = float(depth.get("ask_price") or 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
        return None


_manager = MarketWebSocketManager()


def ensure_worker() -> None:
    _manager.ensure_worker()


def is_active() -> bool:
    return _manager.is_active()


def start(symbol_names: list[str]) -> None:
    """Non-blocking: queue WebSocket start on the background worker."""
    _manager.start_async(symbol_names)


def sync_symbols(symbol_names: list[str]) -> None:
    """Non-blocking: queue symbol resubscribe on the background worker."""
    _manager.sync_async(symbol_names)


def stop() -> None:
    """Non-blocking: queue WebSocket stop on the background worker."""
    _manager.stop_async()


def get_depth(symbol_name: str) -> dict[str, Any] | None:
    return _manager.get_depth(symbol_name)


def get_ltp(symbol_name: str) -> float | None:
    return _manager.get_ltp(symbol_name)
