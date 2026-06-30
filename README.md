# Vishal Trading Strategy - Web Application

A Flask-based trading control panel and execution service using FYERS integration.  
This app manages **watchlist** symbol settings, a **scanner** macro gate, strategy scheduling, market-depth signal evaluation, trade execution, and full logging (order + app activity).

---

## Table of contents

- [Project specification](#project-specification)
- [What is implemented today](#what-is-implemented-today)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Usage guide](#usage-guide)
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

1. Re-counts scanner symbols vs **previous day close** (live LTP vs static prev close)
2. Picks the **live trade side** (whichever count is higher: above or below prev close)
3. Scans watchlist symbols for **market depth** imbalance
4. Optionally applies **VWAP crossover** (toggle)
5. Enters at most **one trade at a time**, respecting the daily cap

### Core rules

| Rule | Description |
|------|-------------|
| **Watchlist settings** | Per symbol: name, time frame, volume difference (depth buffer), stop loss %, target % |
| **Scanner settings** | Per symbol: name, timeframe (e.g. `1d`) — used only for macro bias, not for placing trades |
| **Scanner bias** | Continuous live count: more stocks **above** prev close → **BUY only**; more **below** → **SELL only**; equal → **no entries** |
| **Trading window** | Strategy runs only between **start time** and **stop time** (e.g. 09:30–15:00) |
| **One open trade** | Only **one active trade at a time** across the watchlist |
| **One trade per symbol / day** | Each watchlist symbol can be entered **at most once per day** (after SL, target, or any exit — no re-entry until next session) |
| **Max trades per day** | **Universe-wide** daily cap (default **2**) |
| **Daily schedule** | Auto-login at 09:00 IST, fetch scanner prev closes, auto-start at configured start time, auto-stop at stop time |
| **VWAP switch** | Global toggle — when **off**, VWAP is skipped for watchlist entries |

### Current scope

- FYERS login from `FyersCredentials.csv`
- Background strategy engine (1-second scan loop)
- Scanner prev-close fetch via FYERS history API
- Auto scheduler (login / start / stop)
- WebSocket + REST market depth and LTP

---

## What is implemented today

### Web pages

| Page | URL | Purpose |
|------|-----|---------|
| Symbol Settings | `/` | Strategy controls, VWAP toggle, watchlist table, live order book |
| Scanner | `/scanner` | Scanner symbol CRUD, prev close, live BUY/SELL bias summary |
| Order Logs | `/order-logs` | Entry/exit rows and P&amp;L |
| App Logs | `/app-logs` | UI clicks and system activity |

### Symbol Settings (`/`)

- **Strategy bar:** API status, strategy status, balance, start/stop times, max trades, timezone, **VWAP** checkbox, Save, Login, Start, Stop
- **Watchlist table:** add / edit / delete symbols; CSV import/export
- **Live order book:** per-symbol depth totals, signal, VWAP status

### Scanner (`/scanner`)

- Manage scanner symbols (add, edit, delete, CSV load/download)
- **Refresh prev close** — fetches previous trading day close per symbol/timeframe
- Live summary: count above/below prev close, **Live trade side** (`BUY only` / `SELL only` / `NEUTRAL`)
- Auto-import from root `scanner.csv` on first run if DB is empty

### Logging

- **App logs:** page views, clicks, strategy/scheduler/scanner events
- **Order logs:** entries and exits with SL/target/P&amp;L

### Strategy backend

- SQLite (`data/symbols.db`)
- `app/fyers_service.py` — FYERS auth, history, depth, orders, WebSocket
- `app/scanner_service.py` — prev close prep + live scanner bias
- `app/strategy_engine.py` — 1-second loop, entries, exits
- `app/strategy_scheduler.py` — 09:00 login, prev-close refresh, auto start/stop

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
├── scanner.csv              # Bootstrap scanner list (Name, Timeframe) — optional
├── FyersCredentials.csv     # FYERS API credentials (keep private)
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── repository.py
│   ├── scanner_csv.py       # Scanner CSV parse/export
│   ├── scanner_service.py   # Prev close + live bias
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
│   └── js/                  # symbols, scanner, strategy, market_book, logs
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

1. Ensure `scanner.csv` exists in the project root (or manage symbols on **Scanner** page).
2. At **09:00 IST** (or on **Start**): app logs in to FYERS and fetches **previous day close** for all scanner symbols.
3. Set watchlist on **Symbol Settings**, configure start/stop, max trades, **VWAP** toggle → **Save**.
4. Click **Start** during the trading window — engine scans every second.

### Symbol Settings (`/`)

1. Set **Start**, **Stop**, **Max** trades, **Timezone**, **VWAP** → **Save**.
2. **Start** — auto-login if needed, refresh scanner prev closes, start engine.
3. Add/edit watchlist symbols (volume diff = depth buffer).
4. **Stop** — halts strategy, squares off open positions, resets session.

**Max trades example:** Max = 2 → two entries total across all watchlist symbols for the day.

### Scanner (`/scanner`)

1. Review imported symbols (e.g. 20 pharma names with `1d` timeframe).
2. **Login** to API (from Symbol Settings or refresh flow).
3. **Refresh prev close** — loads yesterday’s close for each scanner symbol (static for the day).
4. During market hours, the summary updates every few seconds:
   - **Above prev close** / **Below prev close** counts
   - **Live trade side** — which direction watchlist entries are allowed

Edit the list anytime via UI or **Load CSV**. Symbol count can be 19, 20, 21, etc.; bias logic adapts automatically.

### Order Logs & App Logs

- **Order logs** — all entries/exits and P&amp;L.
- **App logs** — scanner bias changes, scheduler, API actions, UI events.

### Side navigation

- Symbol Settings  
- **Scanner**  
- Order Logs  
- App Logs  

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
  "tsl": 1
}
```

### Scanner — `/api/scanner`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/scanner` | List scanner symbols |
| GET | `/api/scanner/status` | Live LTP vs prev close + bias summary |
| POST | `/api/scanner/refresh-prev-close` | Fetch previous day close for all scanner symbols |
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
  "time_frame": "1d"
}
```

**Scanner CSV format (`scanner.csv`):**

```csv
Name,Timeframe
SUNPHARMA,1d
CIPLA,1d
```

### Strategy — `/api/strategy`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/strategy` | Settings + engine status |
| PUT | `/api/strategy/times` | Save schedule, max trades, timezone, `vwap_enabled` |
| POST | `/api/strategy/login` | Login via CSV credentials |
| POST | `/api/strategy/logout` | Logout (strategy must be stopped) |
| POST | `/api/strategy/start` | Auto-login, refresh scanner prev close, start engine |
| POST | `/api/strategy/stop` | Stop engine, square off, reset session |
| GET | `/api/strategy/balance` | FYERS available balance |

**PUT body example:**

```json
{
  "start_time": "09:30",
  "stop_time": "15:00",
  "max_trades": 2,
  "timezone": "Asia/Kolkata",
  "vwap_enabled": true
}
```

### Logs — `/api/logs`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/logs/orders` | List order logs (filters supported) |
| POST | `/api/logs/orders` | Record an order |
| GET | `/api/logs/app` | List app logs |
| POST | `/api/logs/app` | Record activity |

---

## Database

SQLite file: `data/symbols.db`

| Table | Purpose |
|-------|---------|
| `symbol_settings` | Watchlist: symbol, timeframe, volume_difference, SL%, target% |
| `scanner_settings` | Scanner: symbol, timeframe, prev_close, prev_close_fetched_at |
| `strategy_settings` | Single row: times, max trades, timezone, `vwap_enabled`, running/API flags |
| `trades` | Open/closed trades with optional JSON details |
| `order_logs` | Order audit trail |
| `app_logs` | User and system activity |

On first run, if `scanner_settings` is empty and `scanner.csv` exists in the project root, symbols are imported automatically.

---

## For developers

### Record an order from strategy code

```python
from app.order_logger import log_order

log_order(
    symbol_name="ACC",
    side="SELL",
    order_type="MARKET",
    quantity=10,
    status="PLACED",
    price=1250.5,
    stop_loss=1.5,
    target=2.0,
)
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
| `app/fyers_service.py` | Login, history, prev close, depth, VWAP, orders, WebSocket |
| `app/scanner_service.py` | `prepare_prev_closes()`, `get_live_bias()` |
| `app/strategy_engine.py` | 1s scan loop, scanner filter, entries, SL/target exits |
| `app/strategy_scheduler.py` | 09:00 login + prev close, auto start/stop |

### Console output

Each second while scanning:

```
[SCANNER 10:15:01] buy=12 sell=8 flat=0 missing=0 total=20 bias=BUY -> only BUY entries
[DEPTH 10:15:01] ACC ... signal=BUY vwap cross ...
```

Scanner bias is recomputed **every tick** — not once at open.

### Configuration

- `app/config.py` — `DATABASE_PATH`, `SCANNER_CSV_PATH`
- `SECRET_KEY` — environment variable for production

---

## Trading Logic (Conditions)

### Scan frequency

- Watchlist + scanner bias: every **1 second** while strategy is running and inside the trading window.
- Scanner UI status: polled every **3 seconds** on `/scanner`.

### Step 1 — Scanner live bias (continuous)

For each symbol in `scanner_settings`:

- Compare **current LTP** vs **previous day close** (fetched once per day; static).
- Count:
  - **Above:** `LTP > prev_close`
  - **Below:** `LTP < prev_close`
  - **Flat:** `LTP == prev_close` (not counted toward either side)
  - **Missing:** no prev close or no LTP

**Allowed trade direction** (re-evaluated every second):

| Condition | Allowed entries |
|-----------|-----------------|
| `above_count > below_count` | **BUY only** on watchlist |
| `below_count > above_count` | **SELL only** on watchlist |
| `above_count == below_count` | **None** (neutral — wait) |

Works for any scanner size (19, 20, 21, …). Example with 20 symbols: 12 above and 8 below → **BUY only**. If LTP moves later to 9 above / 11 below → switches to **SELL only** on the next tick.

**Prev close fetch timing:**

- Auto at **09:00** login (scheduler)
- On **Strategy Start**
- Manual **Refresh prev close** on Scanner page

### Step 2 — Watchlist market depth (per symbol)

Only symbols whose depth signal **matches** the current scanner bias are considered.

**BUY (when scanner allows BUY):**

```
buy_diff = bid_qty - ask_qty
buy_diff >= volume_difference  →  depth signal BUY
```

**SELL (when scanner allows SELL):**

```
sell_diff = ask_qty - bid_qty
sell_diff >= volume_difference  →  depth signal SELL
```

`volume_difference` is the configured **buffer** (e.g. 5000 means buy side must exceed sell side by at least 5000).

### Step 3 — VWAP filter (optional)

Controlled by **VWAP** checkbox in strategy settings (`vwap_enabled`).

When **enabled**, watchlist symbol must pass VWAP **crossover** on its configured timeframe:

| Signal | Crossover rule |
|--------|----------------|
| BUY | Previous-prev candle close &lt; VWAP **and** previous candle close &gt; VWAP |
| SELL | Previous-prev candle close &gt; VWAP **and** previous candle close &lt; VWAP |

Session VWAP: `sum(typical_price × volume) / sum(volume)` for today’s candles.

When **disabled**, VWAP is skipped entirely.

### Step 4 — Entry selection & execution

- Among watchlist candidates passing scanner + depth + (optional) VWAP, pick the **strongest volume margin**.
- Re-check depth immediately before order.
- Place **market order**; record trade with SL/target/TSL from symbol settings.

### Step 5 — Trailing stop loss (TSL)

Per-symbol **TSL (%)** in Symbol Settings. Set **0** for fixed SL only.

- **BUY:** for each `tsl`% price rises above entry, SL moves up by `tsl`% of entry price.
- **SELL:** for each `tsl`% price falls below entry, SL moves down by `tsl`% of entry price.

Example BUY (entry 100, initial SL 90, TSL 1%): LTP 101 (+1%) → SL 91; LTP 102 (+2%) → SL 92.

Checked every second while the trade is open. Exit on trailed SL or target.

### Other conditions

| Rule | Behavior |
|------|----------|
| **One open trade** | No new entry while any position is open |
| **One trade per symbol / day** | No re-entry in a symbol that already had an entry today |
| **Max trades / day** | Stop new entries when daily cap reached |
| **Trading window** | No entries outside start–stop times |
| **Stop loss / TSL** | BUY: exit when LTP ≤ SL (SL trails if TSL &gt; 0); SELL: exit when LTP ≥ SL |
| **Target** | BUY: exit when LTP ≥ target; SELL: exit when LTP ≤ target |
| **EOD** | Open positions squared off at stop time |

### Scheduler

| Time | Action |
|------|--------|
| **09:00 IST** | Auto-login; fetch scanner prev closes |
| **Start time** | Auto-start strategy (within grace window after start) |
| **Stop time** | Auto-stop strategy |

---

## Known Notes

- Flask dev server is for local use; use a production WSGI server for deployment.
- Keep `FyersCredentials.csv` out of git.
- `scanner.csv` in the repo is a template; live list is stored in SQLite after first import.
- If the server restarts, stale `is_running` in DB is reconciled when the engine status is read.

---

## License

Private / project use — add a license if you plan to distribute.
