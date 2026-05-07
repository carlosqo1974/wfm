# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
./run.sh
# or directly:
python3 app.py
```

- Default port: `5000` (override with `WFM_PORT` env var)
- Debug mode: set `WFM_DEBUG=1`
- Database auto-initializes on first run at `data/wfm.db`

**Dependencies** (no requirements.txt — install manually):
```bash
pip3 install flask sqlalchemy openpyxl pulp
pip3 install pymysql   # only if using MySQL connectors
```

There is no test suite or linter configured.

## Architecture

Monolithic Flask MVC. Routing, domain logic, and data access are layered as follows:

| Layer | Files | Role |
|---|---|---|
| Routes | `app.py` | 20+ Flask routes for CRUD, calculation, forecast, export |
| Domain | `calculations/` | Channel-specific staffing math |
| Data | `models.py` | SQLAlchemy ORM over SQLite |
| Connectors | `connectors/` | External DB integration (MySQL via pymysql) |
| Export | `reports/generator.py` | Excel (openpyxl) and CSV output |
| UI | `templates/` + `static/` | Jinja2 + Tailwind CSS + vanilla JS |

## Calculation Modules (`calculations/`)

Each module has one primary entry-point function:

| Module | Entry point | What it does |
|---|---|---|
| `erlang.py` | `agents_for_service_level()` | Core Erlang C: volume + AHT + SL target → productive + gross agents |
| `inbound.py` | `build_interval_plan()` | Full interval-by-interval staffing plan for voice queues |
| `outbound.py` | `agents_needed()` | Outbound staffing for predictive/progressive/preview/power dialing |
| `chat.py` | `agents_for_chat()` | Multi-session concurrency staffing using modified Erlang C |
| `forecast.py` | `forecast_volume()` | WMA/SMA forecast with day-of-week seasonality + intraday expansion |
| `peru_shifts.py` | `dimension_shifts_peru()` | ILP (PuLP+CBC) shift assignment over demand curve; greedy fallback |

`erlang.py` uses log-gamma arithmetic to avoid factorial overflow on large agent counts.

`peru_shifts.py` allows negative start hours (shifts from the previous day) to avoid rounding to 00:00, and escalonates lunch breaks across agents to minimize coverage gaps.

## Data Models (`models.py`)

- `Campaign` — Master record; `campaign_type` is `"inbound"`, `"outbound"`, or `"chat"`; stores all channel parameters
- `IntervalVolume` — 30/60-min volumes per `day_type` (`"weekday"/"saturday"/"sunday"`); optional `aht_seconds` field overrides the campaign-level AHT for that interval
- `HistoricalVolume` — Daily totals keyed by `record_date`; input to `forecast.py`
- `StaffingPlan` — Saved calculation snapshots stored as JSON blobs
- `ShiftDefinition` — Reusable shift templates (name, paid hours, lunch minutes, days_of_week bitmask, color)
- `DBConnector` — MySQL credentials for external data import; password stored plain-text
- `ImportTemplate` — Saved SQL query + column-mapping config (`config_json`) associated with a connector

`init_db()` creates all tables and handles additive column migrations (e.g., adding `aht_seconds` to `IntervalVolume`).

## Key Domain Concepts

- **Shrinkage**: applied after raw agents calculated — `gross = productive / (1 - shrinkage)`; excludes lunch (Peruvian law)
- **Occupancy**: target 75–85%; passed as `max_occ` to Erlang C to cap agent utilization
- **Service Level**: e.g. "80/20" = 80% of contacts answered within 20 seconds
- **Day type**: three staffing profiles per campaign (weekday / Saturday / Sunday); separate from day-of-week forecast factors
- **Interval granularity**: 30 or 60 min; configured per campaign; all volume input and staffing output uses this unit

## Key Route Patterns

- `POST /campaigns/<id>/calculate` — Dispatches to the right channel module based on `campaign_type`; saves result to `StaffingPlan`; returns full JSON plan
- `POST /campaigns/<id>/volumes` — Deletes all existing `IntervalVolume` rows for the given `day_type`, then upserts from the JSON body
- `POST /campaigns/<id>/shift-plan-peru` — Loads `ShiftDefinition` records filtered by `day_type`, calls `dimension_shifts_peru()`
- `POST /api/connectors/<id>/import` — Executes SQL, normalizes columns via `_normalize_interval()` / `_normalize_date()`, upserts into `IntervalVolume` or `HistoricalVolume`
- `POST /api/templates/<id>/run` — Runs a saved `ImportTemplate` against a campaign
- `POST /api/erlang` — Standalone JSON API (mode `"inbound"` or `"trunks"`)

## Connectors (`connectors/`)

- `BaseConnector` (ABC) — `test_connection()` and `execute_query(sql, max_rows=1000)`
- `MySQLConnector` — Validates SELECT-only queries (blocks INSERT/UPDATE/DELETE; allows CTEs); serializes MySQL types to JSON
- `get_connector(db_type, ...)` in `__init__.py` — Factory; only `"mysql"` is implemented

## Important Patterns

- **Session per request**: every route opens `SessionLocal()` and closes it in a `finally` block; no global session
- `_normalize_interval()` in `app.py` coerces many time formats to `"HH:MM"` (strings, datetimes, `HOUR()` SQL output, minutes-since-midnight)
- Reports are written to `/data/reports/` and served as a download; the directory must exist (auto-created by `generator.py`)
- `static/vendor/` contains local copies of Tailwind CDN and Chart.js — no build step, no npm
