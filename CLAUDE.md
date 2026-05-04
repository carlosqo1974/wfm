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
```

There is no test suite or linter configured.

## Architecture

Monolithic Flask MVC. Routing, domain logic, and data access are layered as follows:

| Layer | Files | Role |
|---|---|---|
| Routes | `app.py` | 20+ Flask routes for CRUD, calculation, forecast, export |
| Domain | `calculations/` | Channel-specific staffing math |
| Data | `models.py` | SQLAlchemy ORM over SQLite |
| Export | `reports/generator.py` | Excel (openpyxl) and CSV output |
| UI | `templates/` + `static/` | Jinja2 + Tailwind CSS + vanilla JS |

## Calculation Modules (`calculations/`)

Each module handles a distinct channel type:

- **`inbound.py`** — Erlang C staffing for voice queues (AHT, SL target, shrinkage, occupancy)
- **`outbound.py`** — Predictive/progressive/preview/power dialing modes with contact and right-party rates
- **`chat.py`** — Multi-session concurrency model (modified Erlang for simultaneous chats per agent)
- **`erlang.py`** — Core formulas: Erlang C, Erlang B, ASA, service level
- **`forecast.py`** — SMA, WMA, day-of-week seasonality
- **`peru_shifts.py`** — Peru-specific shift dimensioning based on staffing requirements

## Data Models (`models.py`)

- `Campaign` — Master record with all channel parameters
- `IntervalVolume` — 30/60-min interval volumes; supports weekday / Saturday / Sunday day types
- `HistoricalVolume` — Daily historical data used by forecast module
- `StaffingPlan` — Saved calculation results stored as JSON blobs

## Key Domain Concepts

- **Shrinkage**: typically 20–40%; accounts for breaks, training, absence — applied on top of raw agent count
- **Occupancy**: target 75–85%; Erlang C constraint to avoid agent burnout
- **Service Level**: e.g. "80/20" means 80% of calls answered within 20 seconds
- **Erlang C**: core formula that converts (volume, AHT, SL target) → agents needed per interval
- **Interval granularity**: configurable (30 or 60 min); volumes entered per interval per day type

## Notable Route Patterns

- `/campaigns/<id>/volumes` — GET renders interval volume input; POST saves JSON payload
- `/campaigns/<id>/calculate` — POST triggers staffing calculation, returns rendered results inline
- `/campaigns/<id>/shift-plan-peru` — Peru shift dimensioning using `peru_shifts.py`
- `/api/erlang` — JSON API for the standalone Erlang calculator at `/calculator`
