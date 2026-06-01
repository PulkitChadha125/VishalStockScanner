# Vishal Trading Strategy — Web Application

A Flask-based web application for configuring a market-depth trading strategy, managing symbol settings, and reviewing order and activity logs. The UI uses a dark theme (black primary, blue accents) with a side navigation drawer.

Broker API integration and the live strategy engine are **not connected yet** — the control panel and APIs are in place as stubs so you can wire the broker later.

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
- [Roadmap](#roadmap)

---

## Project specification

### Strategy overview

The strategy monitors **market depth** for a list of symbols. When buy/sell volume imbalance matches a configured **volume gap**, it enters a trade and applies **stop loss** and **target** percentages from symbol settings.

### Core rules (from product spec)

| Rule | Description |
|------|-------------|
| **Symbol settings** | Per symbol: name, time frame, stop loss %, target % (volume gap planned) |
| **Trading window** | Strategy runs only between **start time** and **stop time** (e.g. 09:30–15:00) |
| **One open trade** | Only **one active trade at a time** across the universe — if ACC is in a trade, no new trade in SBI until ACC exits via SL/target |
| **Max trades per day** | **Universe-wide** daily cap (default **2**). Example: 1 trade in SEC + 1 in SBI = 2 total; no further trades that day |
| **Daily reset** | After stop time (e.g. 15:00), strategy stops; next day auto-login ~09:00, strategy starts at configured start time (~09:30) |
| **Margin intent** | One stock at a time so full capital margin (e.g. 5×) can be used on a single position |

### Planned (not yet in UI/engine)

- Volume gap per symbol
- Scheduler: auto broker login at 09:00, auto start at start time
- Live market-depth scanning and order placement via broker API

---

## What is implemented today

### Web pages

| Page | URL | Purpose |
|------|-----|---------|
| Symbol Settings | `/` | Strategy controls + symbol table |
| Order Logs | `/order-logs` | Table of orders recorded by the app |
| App Logs | `/app-logs` | Table of user clicks and system activity |

### Symbol Settings features

- **Strategy bar (compact)**
  - API status, strategy status
  - Start / Stop time, **Max Trades** (default 2)
  - **Save**, **Login**, **Start**, **Stop**
- **Symbol table**: add, edit, delete symbols
  - Fields: symbol name, time frame, stop loss %, target %

### Logging

- **App logs**: page views, button clicks, API actions (client + server)
- **Order logs**: stored when orders are posted via API (daily max enforced)

### Backend

- SQLite persistence (`data/symbols.db`)
- REST APIs for symbols, strategy settings, logs
- Stub strategy endpoints (login/start/stop) ready for broker wiring

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
├── requirements.txt
├── strategydescription.txt # Original strategy notes
├── app/
│   ├── __init__.py         # App factory
│   ├── config.py           # Paths and config
│   ├── database.py         # Schema and connections
│   ├── repository.py       # Data access layer
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

```bash
python main.py
```

Open in your browser: **http://127.0.0.1:5000**

---

## Usage guide

### Symbol Settings (`/`)

1. **Login** — Marks API as connected (stub until broker credentials are added).
2. Set **Start**, **Stop**, and **Max** trades, then click **Save**.
3. Click **Start** to mark the strategy as running (stub).
4. Use **+ Add Symbol** to add symbols; **Edit** / **Delete** on each row.
5. **Stop** halts the strategy; **Logout** disconnects API (only when strategy is stopped).

**Max trades example**

- Max = **2**: one trade in SEC and one in SBI counts as 2 — no more trades that day.
- Max = **3**: up to three trades total across any symbols.

The status line under the bar shows trades used today vs the limit.

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

### Strategy — `/api/strategy`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/strategy` | Settings + `trades_taken_today`, `can_take_more_trades` |
| PUT | `/api/strategy/times` | Save start/stop times and `max_trades` |
| POST | `/api/strategy/login` | Login stub |
| POST | `/api/strategy/logout` | Logout stub |
| POST | `/api/strategy/start` | Start stub (requires API “connected”) |
| POST | `/api/strategy/stop` | Stop stub |

**PUT body example:**

```json
{
  "start_time": "09:30",
  "stop_time": "15:00",
  "max_trades": 2
}
```

### Logs — `/api/logs`

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
| `symbol_settings` | Per-symbol configuration |
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

### Wire broker API later

Replace stub logic in `app/routes/strategy.py`:

- `login_api` / `logout_api` — real broker session  
- `start_strategy` / `stop_strategy` — start/stop background worker  
- Add market-depth loop that respects one-open-trade and `max_trades`

### Configuration

- `app/config.py` — database path under `data/symbols.db`
- `SECRET_KEY` — set environment variable `SECRET_KEY` in production

---

## Roadmap

- [ ] Broker API login and session management  
- [ ] Volume gap field per symbol  
- [ ] Live strategy engine (market depth, entries, SL/target)  
- [ ] Scheduler (09:00 login, start at configured time, 15:00 stop)  
- [ ] One open trade enforcement in engine  
- [ ] Production deployment (e.g. Gunicorn, HTTPS)

---

## License

Private / project use — add a license if you plan to distribute.
