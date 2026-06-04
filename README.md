# Vishal Trading Strategy - Web Application

A Flask-based trading control panel and execution service using FYERS integration.  
This app manages symbol settings, strategy scheduling, market-depth signal evaluation, trade execution, and full logging (order + app activity).

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

The strategy monitors **market depth** for a list of symbols. When buy/sell volume imbalance matches a configured **volume gap**, it enters a trade and applies **stop loss** and **target** percentages from symbol settings.

### Core rules

| Rule | Description |
|------|-------------|
| **Symbol settings** | Per symbol: symbol name, time frame, volume difference, stop loss %, target % |
| **Trading window** | Strategy runs only between **start time** and **stop time** (e.g. 09:30–15:00) |
| **One open trade** | Only **one active trade at a time** across the universe — if ACC is in a trade, no new trade in SBI until ACC exits via SL/target |
| **Max trades per day** | **Universe-wide** daily cap (default **2**). Example: 1 trade in SEC + 1 in SBI = 2 total; no further trades that day |
| **Daily schedule** | Auto-login at 09:00 IST, auto-start at configured start time, auto-stop at configured stop time |
| **Margin intent** | One stock at a time so full capital margin (e.g. 5×) can be used on a single position |

### Current scope

- FYERS login from `FyersCredentials.csv` is integrated.
- Strategy engine runs in background and checks market data every second.
- Auto scheduler is integrated.

---

## What is implemented today

### Web pages

| Page | URL | Purpose |
|------|-----|---------|
| Symbol Settings | `/` | Strategy controls + symbol table |
| Order Logs | `/order-logs` | Table of orders recorded by the app |
| App Logs | `/app-logs` | Table of user clicks and system activity |

### Symbol Settings features

- **Strategy bar (compact):**
  - API status, strategy status, available balance
  - Start / Stop time, **Max Trades** (default 2)
  - **Save**, **Login/Logout**, **Start**, **Stop**
- **Symbol table**: add, edit, delete symbols
  - Fields: symbol name, time frame, volume difference, stop loss %, target %

### Logging

- **App logs**: page views, button clicks, API actions (client + server)
- **Order logs**: stored when orders are posted via API (daily max enforced)

### Strategy backend

- SQLite persistence (`data/symbols.db`)
- FYERS service wrapper (`app/fyers_service.py`)
- Live strategy engine (`app/strategy_engine.py`)
- Clock scheduler (`app/strategy_scheduler.py`)
- REST APIs for symbols, strategy settings, logs

---

## Tech stack

- **Python 3.10+**
- **Flask 3.x**
- **SQLite** (stdlib `sqlite3`)
- HTML / CSS / vanilla JavaScript (no frontend framework)

---

## Project structure

```
Vishal Project 1/
├── main.py                 # Application entry point
├── run.bat                 # Windows one-click setup + run + open browser
├── requirements.txt
├── strategydescription.txt # Original strategy notes
├── app/
│   ├── __init__.py         # App factory
│   ├── config.py           # Paths and config
│   ├── database.py         # Schema and connections
│   ├── repository.py       # Data access layer
│   ├── fyers_credentials.py# CSV credential loader
│   ├── fyers_service.py    # FYERS auth/balance/depth/order wrapper
│   ├── strategy_engine.py  # 1-second scan, entries, exits
│   ├── strategy_scheduler.py# 09:00 login + start/stop clock scheduler
│   ├── order_logger.py     # Helper to record orders from strategy code
│   └── routes/
│       ├── pages.py        # HTML pages
│       ├── symbols.py      # Symbol CRUD API
│       ├── strategy.py     # Strategy settings & control API
│       └── logs.py         # Order and app log APIs
├── templates/              # Jinja2 templates
├── static/
│   ├── css/style.css
│   └── js/                 # symbols, strategy, app-logger, logs
└── data/
    └── symbols.db          # Created on first run (gitignored recommended)
```

---

## Installation

### 1. Clone or open the project

```bash
cd "d:\Desktop\python projects\Vishal Project 1"
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv .venv
```

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\Activate.ps1
```

**Windows (CMD):**

```cmd
.\.venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the application

**Option A — double-click launcher (Windows):**

```text
run.bat
```

This will:
- create `.venv` if missing
- install `requirements.txt`
- start `main.py`
- open **http://127.0.0.1:5000** in your browser

**Option B — manual:**

```bash
python main.py
```

Open in your browser: **http://127.0.0.1:5000**

---

## Usage guide

### Symbol Settings (`/`)

1. Set **Start**, **Stop**, and **Max** trades, then click **Save**.
2. Click **Start**:
   - If not logged in, app auto-logins to FYERS using `FyersCredentials.csv`
   - Starts background strategy engine
3. Optional: use **Login** button manually to test FYERS session.
4. Use **+ Add Symbol** to add symbols; **Edit** / **Delete** on each row.
5. **Stop** halts the strategy (and squares off open position in current implementation).
6. **Logout** disconnects API (allowed only when strategy is stopped).

**Max trades example**

- Max = **2**: one trade in SEC and one in SBI counts as 2 — no more trades that day.
- Max = **3**: up to three trades total across any symbols.

The status line shows trades used today vs the limit.
Balance appears in the top strategy bar after login.

### Order Logs (`/order-logs`)

View all orders recorded through the application. Orders appear when:

- The strategy engine calls `log_order()` (see [For developers](#for-developers)), or
- A client posts to `POST /api/logs/orders`

If the daily max is reached, new orders are rejected with HTTP **403**.

### App Logs (`/app-logs`)

View clicks, navigation, and server-side actions (symbol changes, strategy saves, etc.).

### Side navigation

Use the left drawer:

- Symbol Settings  
- Order Logs  
- App Logs  

On small screens, use the **☰** menu button to open the drawer.

---

## REST API reference

Base URL: `http://127.0.0.1:5000`

### Symbols — `/api/symbols`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/symbols` | List all symbols |
| GET | `/api/symbols/<id>` | Get one symbol |
| POST | `/api/symbols` | Create symbol |
| PUT | `/api/symbols/<id>` | Update symbol |
| DELETE | `/api/symbols/<id>` | Delete symbol |

**Create / update body (JSON):**

```json
{
  "symbol_name": "ACC",
  "time_frame": "5m",
  "stop_loss_pct": 1.5,
  "target_pct": 2.0
}
```

### Strategy - `/api/strategy`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/strategy` | Settings + `trades_taken_today`, `can_take_more_trades` |
| PUT | `/api/strategy/times` | Save start/stop times and `max_trades` |
| POST | `/api/strategy/login` | Login to FYERS using CSV credentials |
| POST | `/api/strategy/logout` | Logout from FYERS runtime session |
| POST | `/api/strategy/start` | Auto-login if needed, then start engine |
| POST | `/api/strategy/stop` | Stop stub |
| GET | `/api/strategy/balance` | Fetch current balance from FYERS |

**PUT body example:**

```json
{
  "start_time": "09:30",
  "stop_time": "15:00",
  "max_trades": 2
}
```

### Logs - `/api/logs`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/logs/orders` | List order logs |
| POST | `/api/logs/orders` | Record an order (respects daily max) |
| GET | `/api/logs/app` | List app activity logs |
| POST | `/api/logs/app` | Record activity (used by frontend logger) |

**Order body example:**

```json
{
  "symbol_name": "SBIN",
  "side": "SELL",
  "order_type": "MARKET",
  "quantity": 10,
  "price": 625.5,
  "status": "PLACED",
  "stop_loss": 1.5,
  "target": 2.0
}
```

---

## Database

SQLite file: `data/symbols.db`

| Table | Purpose |
|-------|---------|
| `symbol_settings` | Per-symbol config (symbol, timeframe, volume_difference, SL%, target%) |
| `strategy_settings` | Single row: times, max trades, running/API flags |
| `order_logs` | Placed orders |
| `app_logs` | User and system activity |

The `data/` folder is created automatically on first run.

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
from app.repository import can_take_more_trades, get_strategy_settings, count_trades_today

if can_take_more_trades():
    settings = get_strategy_settings()
    # place trade ...
```

### Strategy runtime files

- `app/fyers_service.py`: wraps FYERS login, balance, depth, order placement
- `app/strategy_engine.py`: core loop and trade lifecycle
- `app/strategy_scheduler.py`: clock-based auto login/start/stop

### Console depth prints

Each scan prints depth details per symbol in terminal:
- bid/ask prices
- bid/ask quantities
- buy/sell volume differences
- threshold (`volume_difference`)
- resulting signal (`BUY`, `SELL`, or `NONE`)

### Configuration

- `app/config.py` — database path under `data/symbols.db`
- `SECRET_KEY` — set environment variable `SECRET_KEY` in production

---

## Trading Logic (Conditions)

### Entry scan frequency
- Strategy scans all configured symbols every **1 second** while running.

### Buy condition (volume depth)
- For a symbol:
  - `buy_diff = bid_qty - ask_qty`
  - If `buy_diff >= volume_difference`, raw signal = **BUY**

### Sell condition (volume depth)
- For a symbol:
  - `sell_diff = ask_qty - bid_qty`
  - If `sell_diff >= volume_difference`, raw signal = **SELL**

### VWAP filter (required before entry)
- VWAP is calculated from FYERS historical candles on the symbol's configured **time frame** (1m, 3m, 5m, 15m, 30m, 1h).
- Session VWAP formula: `sum(((high+low+close)/3) * volume) / sum(volume)` for today's candles.
- **BUY entry allowed only if:** `entry_price > VWAP`
- **SELL entry allowed only if:** `entry_price < VWAP`
- If VWAP is unavailable or filter fails, trade is skipped and logged.

### One-trade-at-a-time condition
- If one position is open, no new entries are taken in any symbol.

### Entry execution
- On valid signal + VWAP pass:
  - Place market order (BUY side=1, SELL side=-1)
  - Record order log with status `ENTRY`
  - Compute and store SL / target prices

### Stop loss condition
- If open position is BUY:
  - `sl_price = entry * (1 - stop_loss_pct/100)`
  - Exit when `LTP <= sl_price`
- If open position is SELL:
  - `sl_price = entry * (1 + stop_loss_pct/100)`
  - Exit when `LTP >= sl_price`
- Exit order is logged as `EXIT_SL`.

### Target condition
- If open position is BUY:
  - `target_price = entry * (1 + target_pct/100)`
  - Exit when `LTP >= target_price`
- If open position is SELL:
  - `target_price = entry * (1 - target_pct/100)`
  - Exit when `LTP <= target_price`
- Exit order is logged as `EXIT_TARGET`.

### Max trades condition
- Daily universe-wide cap:
  - If `trades_taken_today >= max_trades`, no more entries for the day.
- Scheduler/engine logs this state in app logs.

### Time-window condition
- Strategy evaluates entries only in configured `start_time -> stop_time`.
- Auto scheduler behavior:
  - **09:00 IST**: auto-login once per day
  - **Start time**: auto-start strategy once per day
  - **Stop time**: auto-stop strategy once per day

### Logging behavior
- **Order logs** include each entry/exit with side, price, qty, stop loss, target, status.
- **App logs** capture:
  - page views
  - UI clicks
  - strategy/scheduler events
  - API actions

## Known Notes

- This app currently uses Flask dev server; use production WSGI for deployment.
- Keep `FyersCredentials.csv` out of git and rotate secrets if exposed.
- If server restarts, runtime state is reconciled so stale DB `is_running` is reset.

---

## License

Private / project use — add a license if you plan to distribute.
