# Vishal Trading Strategy - Web Application

A Flask-based trading control panel and execution service using FYERS integration.  
This app manages **watchlist** symbol settings, a **scanner** macro gate, strategy scheduling, market-depth signal evaluation, trade execution with **broker-side bracket exits**, and full logging (order + app activity).

---

## Table of contents

- [Project specification](#project-specification)
- [Order flow (entry and exits)](#order-flow-entry-and-exits)
- [What is implemented today](#what-is-implemented-today)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Usage guide](#usage-guide)
- [Testing](#testing)
- [REST API reference](#rest-api-reference)
- [Database](#database)
- [For developers](#for-developers)
- [Trading Logic (Conditions)](#trading-logic-conditions)
- [Known Notes](#known-notes)

---

## Project specification

### Strategy overview

The strategy has two symbol lists:

| List | Purpose |
|------|---------|
| **Watchlist** (`symbol_settings`) | Symbols where trades are actually placed when depth (+ optional VWAP) passes |
| **Scanner** (`scanner_settings` / `scanner.csv`) | Broader universe used to decide **whether only BUY or only SELL** is allowed right now |

Each second during the trading window, the engine:

1. Evaluates a **depth signal** for every scanner symbol and takes the **majority side**
2. Scans watchlist symbols for **market depth** imbalance in that same direction
3. Optionally applies a **VWAP entry pocket + previous-close direction** filter (toggle + per-symbol range down / range up)
4. Sizes the order from **account balance × leverage**
5. Enters at most **one trade at a time**, respecting the daily cap
6. Parks the **target and stop loss as live orders at the broker** and cancels the loser when one fills

### Core rules

| Rule | Description |
|------|-------------|
| **Watchlist settings** | Per symbol: name, time frame, volume difference (depth buffer), stop loss %, target %, **entry range down %**, **entry range up %** |
| **Scanner settings** | Per symbol: name, timeframe, volume difference — used only for macro bias, never for placing trades |
| **Scanner bias** | Live depth signal per scanner symbol; a side needs a **majority** (`floor(n/2)+1`) to unlock entries in that direction |
| **Trading window** | Strategy runs only between **start time** and **stop time** (e.g. 09:30–15:00) |
| **One open trade** | Only **one active trade at a time** across the watchlist, and no new entry until its exit legs are settled |
| **Max trades per day** | **Universe-wide** daily cap (default **2**) |
| **Position size** | `quantity = floor(available_balance × leverage_multiplier / share_price)` |
| **Daily schedule** | Auto-login at 09:00 IST, auto-start at configured start time, auto-stop at stop time |
| **VWAP switch** | Global toggle — when **off**, the VWAP entry pocket and previous-close check are skipped for watchlist entries |

### Current scope

- FYERS login from `FyersCredentials.csv`
- Background strategy engine (1-second scan loop)
- Depth-based scanner majority gate
- Exposure-based position sizing
- Limit entry at the **live ask (BUY) / bid (SELL)** once LTP is inside the VWAP entry pocket
- Bracket exit orders (target limit / stop-loss SL-L) with OCO cancellation
- Auto scheduler (login / start / stop)
- WebSocket + REST market depth and LTP

---

## Order flow (entry and exits)

### Order types used

Confirmed against the FYERS v3 API (`type`: `1` Limit · `2` Market · `3` SL-M · `4` SL-L; `side`: `1` Buy · `-1` Sell):

| Purpose | FYERS type | Payload |
|---------|-----------|---------|
| Entry | `1` Limit | `limitPrice` = live **ask** on BUY, live **bid** on SELL |
| Target | `1` Limit | `limitPrice` = target price |
| Stop loss | `4` SL-L | `stopPrice` = stop price, `limitPrice` = stop ± buffer |

The target **cannot** be a stop order: FYERS requires a sell trigger to sit **below** LTP and a buy trigger **above** it, so a sell trigger at the profit target is rejected. SL-L direction rules are enforced by the engine — sell legs use `limitPrice ≤ stopPrice`, buy legs use `limitPrice ≥ stopPrice`. All prices are rounded to the ₹0.05 tick.

### Worked example (BUY at 100, SL 2%, target 2%)

| Step | Order sent |
|------|-----------|
| 1. Entry | BUY **limit** at the live ask (the price sellers are offering right now) |
| 2. Target leg | SELL **limit** @ **102.00** |
| 3. Stop leg | SELL **SL-L**, trigger **98.00**, limit **97.90** |
| 4. One fills | Say target trades at 102.05 → the SL order is cancelled |
| 5. Position released | Trade closed at the real traded price; only now can the next entry happen |

For a SELL entry the legs mirror: BUY limit at the target below entry, BUY SL-L with trigger above entry and `limitPrice` above the trigger.

### VWAP pocket is the gate; the limit uses the live price

Two percentages replace a single buffer. Example: VWAP = 100, **range down = 2%**, **range up = 5%**.

- Dead zone next to VWAP (no trade): **98–102**
- **SELL** pocket: **102 < LTP < 105** (coming down, but not that close to VWAP)
- **BUY** pocket: **95 < LTP < 98** (coming up, but not that close to VWAP)

The second check is the last **completed candle close** vs VWAP:

- **SELL** — previous close is **above** VWAP, and live LTP is in the sell pocket. Example: previous close 103, LTP becomes 103 → send the sell.
- **BUY** — previous close is **below** VWAP, and live LTP is in the buy pocket.

Buy vs sell still also needs scanner + watchlist depth. The order itself is a **limit at the live book**:

- BUY → current **ask**
- SELL → current **bid**

If LTP leaves the pocket before that limit fills, the resting order is cancelled — no market flatten and no P&amp;L.

### Levels come from the real fill

After the entry is filled, the engine reads the order's **traded price** from the FYERS orderbook and recomputes SL/target from it, then rewrites those levels on the trade record. The depth-derived estimate is only a fallback.

### Cancellation is confirmed, not assumed

When one leg fills, the other is cancelled and then **re-read from the orderbook** to prove it is gone (up to 3 attempts). If it cannot be confirmed:

- the leg is tracked as pending cleanup,
- **new entries are blocked** (`PENDING_LEG_CANCEL` in the logs),
- every tick retries the cancel until the broker confirms it,
- if that leftover leg executes instead, the resulting position is flattened at market and logged as `RECONCILE_FLATTEN`.

### Fallbacks

| Situation | Behavior |
|-----------|----------|
| A leg is rejected at placement (e.g. margin) | Trade runs in `hybrid` mode: the accepted leg stays at the broker, the missing side is watched locally on LTP |
| Both legs rejected / not connected | Trade runs in `local` mode — the original in-memory SL/target monitoring |
| A leg is cancelled or expires externally | Dropped to local monitoring, logged |
| Orderbook polling keeps failing (10 ticks) | Dropped to local monitoring, logged |
| Manual **Stop**, EOD, or square-off | Pending legs are cancelled **first**, then the market exit is sent. An unfilled VWAP limit is cancelled with no flatten. |
| LTP leaves the VWAP pocket before the entry limit fills | Resting limit cancelled; trade closed as `UNFILLED` with zero P&amp;L |
| A leg fills during that cancel | That fill is recorded as the exit; no second order is sent |
| App restarts with an open trade | Leg ids are restored from the trade record and monitoring resumes |

`exit_mode` (`broker` / `hybrid` / `local`) is stored on each trade and shown in the order log detail.

---

## What is implemented today

### Web pages

| Page | URL | Purpose |
|------|-----|---------|
| Symbol Settings | `/` | Strategy controls, VWAP toggle, leverage, watchlist table, live order book |
| Scanner | `/scanner` | Scanner symbol CRUD, live depth majority summary |
| Order Logs | `/order-logs` | Entry/exit rows, position sizing, exit legs, P&amp;L |
| App Logs | `/app-logs` | UI clicks and system activity |

### Symbol Settings (`/`)

- **Strategy bar:** API status, strategy status, balance, start/stop times, max trades, timezone, leverage multiplier, **VWAP** checkbox, Save, Login, Start, Stop
- **Watchlist table:** add / edit / delete symbols (volume diff, SL%, target%, **entry range down %**, **entry range up %**); CSV import/export
- **Live order book:** per-symbol depth totals, signal, VWAP pocket status

### Scanner (`/scanner`)

- Manage scanner symbols (add, edit, delete, CSV load/download), each with its own **volume difference**
- Live summary: majority needed, BUY signals, SELL signals, no-signal count, and the resulting **live trade side**
- Auto-import from root `scanner.csv` on first run if DB is empty

### Logging

- **App logs:** page views, clicks, strategy/scheduler/scanner events
- **Order logs:** every order the engine sends — entry, target leg, stop leg, cancellations, exits — plus sizing and P&amp;L

### Strategy backend

- SQLite (`data/symbols.db`)
- `app/fyers_service.py` — FYERS auth, history, depth, orders, cancellation, order status, WebSocket
- `app/scanner_service.py` — depth signal per scanner symbol + live majority bias
- `app/strategy_engine.py` — 1-second loop, entries, exit legs, OCO handling
- `app/strategy_scheduler.py` — 09:00 login, auto start/stop

---

## Tech stack

- **Python 3.10+**
- **Flask 3.x**
- **SQLite** (stdlib `sqlite3`)
- HTML / CSS / vanilla JavaScript

---

## Project structure

```
Vishal Project 1/
├── main.py
├── run.bat
├── requirements.txt
├── scanner.csv              # Bootstrap scanner list (Name, Timeframe, Volume Diff) — optional
├── FyersCredentials.csv     # FYERS API credentials (keep private)
├── FyresIntegration.py      # Raw FYERS SDK layer (orders, cancel, orderbook, websockets)
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── repository.py
│   ├── scanner_csv.py       # Scanner CSV parse/export
│   ├── scanner_service.py   # Depth signal + live majority bias
│   ├── symbols_csv.py       # Watchlist CSV parse/export
│   ├── fyers_credentials.py
│   ├── fyers_service.py
│   ├── fyers_market_ws.py
│   ├── strategy_engine.py
│   ├── strategy_scheduler.py
│   ├── order_logger.py
│   └── routes/
│       ├── pages.py
│       ├── symbols.py
│       ├── scanner.py
│       ├── strategy.py
│       └── logs.py
├── templates/
├── static/
│   ├── css/style.css
│   └── js/                  # symbols, scanner, strategy, market_book, order_logs, logs
├── tests/
│   └── test_end_to_end.py   # End-to-end order flow test (fake broker)
└── data/
    └── symbols.db
```

---

## Installation

### 1. Open the project

```bash
cd "d:\Desktop\python projects\Vishal Project 1"
```

### 2. Virtual environment (recommended)

```bash
python -m venv .venv
```

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run

**Windows launcher:**

```text
run.bat
```

**Manual:**

```bash
python main.py
```

Open: **http://127.0.0.1:5000**

---

## Usage guide

### Morning workflow

1. Ensure `scanner.csv` exists in the project root (or manage symbols on the **Scanner** page).
2. At **09:00 IST**: the app logs in to FYERS automatically.
3. Set the watchlist on **Symbol Settings**, configure start/stop, max trades, leverage, **VWAP** toggle → **Save**.
4. Click **Start** during the trading window — the engine scans every second.

### Symbol Settings (`/`)

1. Set **Start**, **Stop**, **Max** trades, **Timezone**, **Leverage**, **VWAP** → **Save**.
2. **Start** — auto-login if needed, then start the engine.
3. Add/edit watchlist symbols (volume diff = depth buffer; **range down** = dead zone next to VWAP; **range up** = outer cap).
4. **Stop** — halts the strategy, cancels any live exit legs, squares off open positions, resets the session.

**Max trades example:** Max = 2 → two entries total across all watchlist symbols for the day.

**Leverage example:** balance ₹1,00,000 with leverage 5 → ₹5,00,000 exposure; a ₹5,000 share gives 100 quantity.

### Scanner (`/scanner`)

1. Review imported symbols and set a **volume difference** per symbol.
2. **Login** to the API (from Symbol Settings).
3. During market hours the summary updates every few seconds:
   - **Majority needed**, **BUY signals**, **SELL signals**, **no signal / missing**
   - **Live trade side** — which direction watchlist entries are allowed

Edit the list anytime via UI or **Load CSV**. Symbol count can be 10, 20, 100 — the majority adapts automatically.

### Order Logs & App Logs

- **Order logs** — every order sent (entry, target leg, SL leg, cancels, exits), position sizing, and P&amp;L. Click a row for the full FYERS request/response of each leg.
- **App logs** — scanner bias changes, scheduler, API actions, UI events.

---

## Testing

An end-to-end test drives the real engine tick loop against a fake FYERS broker — scanner bias, depth signal, entry, exit legs, OCO cancellation, and the Flask API the UI reads.

```powershell
.\.venv\Scripts\python.exe tests\test_end_to_end.py
```

It runs entirely offline against a **temporary database** (it aborts if it is not pointed at one) and never touches `data/symbols.db` or the FYERS API. Scenarios covered:

| Scenario | What it proves |
|----------|----------------|
| BUY → target fills | Both legs placed with correct types/prices, SL cancelled, trade closed at the traded price, position released |
| SELL → stop fills | Short-side legs mirror correctly, target cancelled |
| SL leg rejected | Falls back to `hybrid` mode and still exits locally on the stop |
| Cancel not confirmed | Retries, blocks new entries, resumes once the broker confirms |
| EOD square-off | Cancels both legs before the market exit |
| VWAP entry pocket | BUY only in 95–98, SELL only in 102–105 (2% down / 5% up); dead zone 98–102 is blocked; previous close must sit on the correct side of VWAP |
| API surface | `/api/strategy` and `/api/logs/orders` expose the leg data the UI renders |

Exit code is `0` when every check passes.

---

## REST API reference

Base URL: `http://127.0.0.1:5000`

### Symbols — `/api/symbols`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/symbols` | List watchlist symbols |
| GET | `/api/symbols/market-book` | Live depth snapshot for watchlist |
| GET | `/api/symbols/<id>` | Get one symbol |
| POST | `/api/symbols` | Create symbol |
| PUT | `/api/symbols/<id>` | Update symbol |
| DELETE | `/api/symbols/<id>` | Delete symbol |
| GET | `/api/symbols/export.csv` | Download watchlist CSV |
| POST | `/api/symbols/import.csv` | Replace watchlist from CSV |

**Create / update body (JSON):**

```json
{
  "symbol_name": "ACC",
  "time_frame": "5m",
  "volume_difference": 5000,
  "stop_loss_pct": 1.5,
  "target_pct": 2.0,
  "entry_range_down_pct": 2.0,
  "entry_range_up_pct": 5.0
}
```

`entry_range_up_pct` must be greater than `entry_range_down_pct`. Defaults if omitted: down **2**, up **5**.

**Watchlist CSV columns:** `Symbol`, `Time Frame`, `Volume Diff`, `Stop Loss %`, `Target %`, `Entry Range Down %`, `Entry Range Up %`. Older files with `Entry Buffer %` still load as range down (up defaults to 5).

### Scanner — `/api/scanner`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/scanner` | List scanner symbols |
| GET | `/api/scanner/status` | Live depth signals + majority bias summary |
| GET | `/api/scanner/export.csv` | Download scanner CSV |
| POST | `/api/scanner/import.csv` | Replace scanner list from CSV |
| GET | `/api/scanner/<id>` | Get one scanner symbol |
| POST | `/api/scanner` | Create scanner symbol |
| PUT | `/api/scanner/<id>` | Update scanner symbol |
| DELETE | `/api/scanner/<id>` | Delete scanner symbol |

**Create / update body (JSON):**

```json
{
  "symbol_name": "SUNPHARMA",
  "time_frame": "1m",
  "volume_difference": 20000
}
```

Valid `time_frame` values: `1m`, `2m`, `3m`, `4m`, `5m`, `10m`, `15m`, `20m`, `30m`, `1h`, `1d`.

**Scanner CSV format (`scanner.csv`):**

```csv
Name,Timeframe,Volume Diff
SUNPHARMA,1m,20000
CIPLA,1m,25000
```

### Strategy — `/api/strategy`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/strategy` | Settings + engine status (open position, exit legs, pending cancels) |
| PUT | `/api/strategy/times` | Save schedule, max trades, timezone, `vwap_enabled`, `leverage_multiplier` |
| POST | `/api/strategy/login` | Login via CSV credentials |
| POST | `/api/strategy/logout` | Logout (strategy must be stopped) |
| POST | `/api/strategy/start` | Auto-login if needed, start engine |
| POST | `/api/strategy/stop` | Stop engine, cancel legs, square off, reset session |
| GET | `/api/strategy/balance` | FYERS available balance |

**PUT body example:**

```json
{
  "start_time": "09:30",
  "stop_time": "15:00",
  "max_trades": 2,
  "timezone": "Asia/Kolkata",
  "vwap_enabled": true,
  "leverage_multiplier": 5
}
```

### Logs — `/api/logs`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/logs/orders` | Trades, entry/exit events, and summary (filters supported) |
| GET | `/api/logs/orders/<trade_id>` | Full trade detail incl. every leg request/response |
| DELETE | `/api/logs/orders/<trade_id>` | Delete one trade log |
| DELETE | `/api/logs/orders` | Delete trades matching the current filter |
| POST | `/api/logs/orders` | Record an order |
| GET | `/api/logs/app` | List app logs |
| POST | `/api/logs/app` | Record activity |

---

## Database

SQLite file: `data/symbols.db`

| Table | Purpose |
|-------|---------|
| `symbol_settings` | Watchlist: symbol, timeframe, volume_difference, SL%, target%, **entry_range_down_pct**, **entry_range_up_pct** |
| `scanner_settings` | Scanner: symbol, timeframe, volume_difference |
| `strategy_settings` | Single row: times, max trades, timezone, `vwap_enabled`, `leverage_multiplier`, running/API flags |
| `trades` | Open/closed trades with a JSON `details` blob |
| `order_logs` | Order audit trail (entry, legs, cancels, exits) |
| `app_logs` | User and system activity |

**Useful `trades.details` keys**

| Key | Meaning |
|-----|---------|
| `entry_order_id`, `entry_fill_price`, `entry_limit_price`, `entry_order_type` | Broker id, real fill, limit price, and MARKET vs LIMIT |
| `exit_mode` | `broker` / `hybrid` / `local` |
| `target_order_id`, `sl_order_id` | Live exit leg ids |
| `sl_trigger_price`, `sl_limit_price` | SL-L trigger and its limit |
| `target_leg_request/response`, `sl_leg_request/response` | Raw FYERS payloads |
| `exit_leg`, `exit_leg_state`, `exit_leg_cancels`, `exit_via` | Which leg executed, its orderbook row, cancellation results |
| `available_balance`, `leverage_multiplier`, `exposure`, `share_value`, `order_value` | Position sizing audit |
| `vwap`, `entry_ltp`, `prev_close`, `entry_range_down_pct`, `entry_range_up_pct`, `vwap_band_low`, `vwap_band_high` | Session VWAP, last completed close, and the side pocket (low–high) at entry |

On first run, if `scanner_settings` is empty and `scanner.csv` exists in the project root, symbols are imported automatically.

---

## For developers

### Place an order from strategy code

```python
from app import fyers_service

# Market entry (side: 1 buy, -1 sell)
fyers_service.place_market_order("ACC", 1, 10)

# Target leg
fyers_service.place_limit_order("ACC", -1, 10, 1275.0)

# Stop leg (trigger + limit, tick-rounded)
fyers_service.place_sl_limit_order("ACC", -1, 10, 1225.0, 1223.75)

# Cancel and inspect
fyers_service.cancel_order("25090200123456")
fyers_service.fetch_order_states(["25090200123456"])
```

### Check if more trades are allowed today

```python
from app.repository import can_take_more_trades, count_trades_today

if can_take_more_trades():
    # place trade ...
```

### Key runtime modules

| Module | Role |
|--------|------|
| `FyresIntegration.py` | FYERS SDK calls: `build_order_payload`, `place_order_with_meta`, `cancel_order_with_meta`, `orders_by_ids` |
| `app/fyers_service.py` | Login, history, depth, VWAP, order placement/cancellation/status, tick rounding, WebSocket |
| `app/scanner_service.py` | `evaluate_depth_signal()`, `majority_threshold()`, `get_live_bias()` |
| `app/strategy_engine.py` | 1s loop, scanner gate, entries, exit legs, OCO cancellation |
| `app/strategy_scheduler.py` | 09:00 login, auto start/stop |

### Console output

Each second while scanning:

```
[SCANNER 10:15:01] buy=12 sell=8 none=0 missing=0 total=20 majority_need=11 bias=BUY -> only BUY entries
[DEPTH 10:15:01] ACC ws age=0.3s bid_p=1250.00 ask_p=1250.05 book_buy=30000 book_sell=12000 ... signal=BUY vwap=1250.10 ltp=1220.00 band=1187.6-1225.1 range=2-5% prev_close=1210.00 band_ok=True
[EXIT LEGS] ACC entry=1250.00 target=1275.00(PLACED) sl_trigger=1225.00 sl_limit=1223.75(PLACED) mode=broker
[EXIT LEGS] ACC cancel SL leg 25090200123456: ok after 1 attempt(s)
[TRADE] ACC BUY entry=... @ 1250.00 exit=... @ 1275.20 reason=TARGET pnl=252.00 (entry=FILLED, exit=FILLED)
```

Scanner bias is recomputed **every tick** — not once at open.

### Configuration

- `app/config.py` — `DATABASE_PATH`, `SCANNER_CSV_PATH`
- `SECRET_KEY` — environment variable for production
- `app/strategy_engine.py` — `SL_LIMIT_BUFFER_PCT` (SL-L limit offset, default 0.10%), `LEG_CANCEL_ATTEMPTS`, `LEG_POLL_MAX_ERRORS`

---

## Trading Logic (Conditions)

### Scan frequency

- Watchlist + scanner bias: every **1 second** while the strategy is running and inside the trading window.
- Scanner UI status: polled every **3 seconds** on `/scanner`.

### Step 1 — Scanner live bias (continuous)

For each symbol in `scanner_settings`, using its own `volume_difference`:

```
buy_diff  = total_bid_qty - total_ask_qty
sell_diff = total_ask_qty - total_bid_qty

buy_diff  >= volume_difference  ->  BUY signal
sell_diff >= volume_difference  ->  SELL signal
otherwise                       ->  no signal
```

**Majority** = `floor(n/2) + 1` of all scanner symbols (11 of 20, 6 of 10, 51 of 100).

| Condition | Allowed entries |
|-----------|-----------------|
| `buy_count >= majority` | **BUY only** on watchlist |
| `sell_count >= majority` | **SELL only** on watchlist |
| neither reaches majority | **None** (wait) |

Symbols with no depth are counted as missing and never help a side reach the majority.

### Step 2 — Watchlist market depth (per symbol)

Only symbols whose depth signal **matches** the current scanner bias are considered. Same formula as above, using the watchlist symbol's own `volume_difference` (e.g. 5000 means the buy side must exceed the sell side by at least 5000).

### Step 3 — VWAP entry pocket (optional)

Controlled by the **VWAP** checkbox (`vwap_enabled`). When enabled, session VWAP is calculated from today's candles:

`sum(typical_price × volume) / sum(volume)`

That VWAP is compared **every second** to the symbol's **live WebSocket LTP** and to the last **completed candle close**. Each watchlist symbol has two percentages:

| Setting | Example | Meaning |
|---------|---------|---------|
| **Entry range down** | 2% | Dead zone next to VWAP. No trade this close to VWAP. |
| **Entry range up** | 5% | Outer cap. No trade farther than this from VWAP. |

Example: VWAP = 100, range down = 2%, range up = 5%

| Signal | Allowed | Rejected |
|--------|---------|----------|
| BUY | Previous close **below** 100, and **95 < LTP < 98** | LTP in the 98–102 dead zone, LTP ≤ 95, LTP ≥ 98, or previous close ≥ VWAP |
| SELL | Previous close **above** 100, and **102 < LTP < 105** | LTP in the 98–102 dead zone, LTP ≥ 105, LTP ≤ 102, or previous close ≤ VWAP |

Worked prices:

| LTP | Side | Trade? |
|-----|------|--------|
| 96 | BUY | Yes — inside 95–98, if previous close is below VWAP |
| 99 | BUY | No — inside the 2% dead zone |
| 103 | SELL | Yes — inside 102–105, if previous close is above VWAP |
| 101 | SELL | No — inside the 2% dead zone |

Buy vs sell still also needs scanner + watchlist depth. A missing previous close skips the entry. A resting unfilled limit is cancelled only if live LTP leaves that pocket.

When the VWAP switch is off, this gate is skipped entirely. If VWAP or LTP is missing, the entry is skipped.

### Step 4 — Position sizing

```
exposure = available_balance × leverage_multiplier
quantity = floor(exposure / share_price)
```

If the balance cannot fund a single share (or is unavailable), the engine still sends **1 share** so the attempt and the broker's response are logged. Every sizing input is stored on the trade and shown in the order log. `share_price` is the live ask (BUY) or bid (SELL).

### Step 5 — Entry execution

- Among candidates passing scanner + depth + (optional) VWAP, pick the **strongest volume margin**.
- Re-check depth immediately before ordering.
- Send a **limit order at the live ask (BUY) or live bid (SELL)**. The VWAP pocket is only a filter; 95/98/102/105 are never the order price.
- The order stays `PENDING` and occupies the one-position slot until it fills, is cancelled, or is rejected. Exit legs are placed only after the fill.

### Step 6 — Bracket exits

- Place the **target limit** and **stop-loss SL-L** orders, both full quantity, both opposite to the entry.
- Poll both order ids every tick; when one fills, cancel the other and confirm the cancellation.
- Close the trade at the leg's real traded price with reason `TARGET` or `SL`.
- The position is only released — and the next entry only allowed — once that cleanup is confirmed.

See [Order flow](#order-flow-entry-and-exits) for the fallback behavior when a leg is rejected or cannot be cancelled.

### Other conditions

| Rule | Behavior |
|------|----------|
| **One open trade** | No new entry while any position is open |
| **Same symbol re-entry** | After a trade exits, the same symbol can be entered again if conditions fire and the daily cap is not reached |
| **Max trades / day** | Stop new entries when the daily cap is reached |
| **Trading window** | No entries outside start–stop times |
| **Stop loss** | Broker SL-L leg; locally monitored (`LTP ≤ SL` for BUY, `LTP ≥ SL` for SELL) if the leg is not live |
| **Target** | Broker limit leg; locally monitored (`LTP ≥ target` for BUY, `LTP ≤ target` for SELL) if the leg is not live |
| **EOD** | Legs cancelled and open positions squared off at stop time |

### Scheduler

| Time | Action |
|------|--------|
| **09:00 IST** | Auto-login |
| **Start time** | Auto-start strategy (within a grace window after start time only) |
| **Stop time** | Auto-stop strategy, cancel legs, square off |

---

## Known Notes

- Flask dev server is for local use; use a production WSGI server for deployment.
- Keep `FyersCredentials.csv` out of git.
- `scanner.csv` in the repo is a template; the live list is stored in SQLite after the first import.
- If the server restarts, a stale `is_running` in the DB is reconciled when engine status is read, and any open trade's exit legs are resumed from the trade record.
- Both exit legs are full quantity on the same side. If FYERS applies a fresh-order margin check to the second leg it may be rejected — the trade then runs in `hybrid` mode (visible in the order log). Verify with a small position on the first live run.
- Exit legs sit at the broker as **DAY** orders. If the app is stopped without a square-off, cancel any leftover legs from the FYERS terminal.

---

## License

Private / project use — add a license if you plan to distribute.
