# WeatherSafeClaw — Tools & Skills

This document describes every tool and skill the WeatherSafeClaw agent uses. Each entry includes purpose, inputs, outputs, failure handling, and the soul.md rule it enforces.

---

## Tool Index

| # | Tool Name | Category | Purpose |
|---|---|---|---|
| 1 | `noaa_fetch_point_forecast` | Data | Retrieve NWS point forecast for a station |
| 2 | `noaa_fetch_hourly_forecast` | Data | Retrieve NWS hourly gridded forecast |
| 3 | `noaa_fetch_gfs_ensemble` | Data | Download GFS ensemble probabilities |
| 4 | `noaa_fetch_hrrr` | Data | Download HRRR analysis for sub-18h horizon |
| 5 | `kalshi_list_weather_markets` | Market | List open weather prediction markets |
| 6 | `kalshi_get_market` | Market | Get full details on a single market |
| 7 | `kalshi_get_orderbook` | Market | Get current orderbook for a market |
| 8 | `kalshi_submit_order` | Execution | Submit a limit order |
| 9 | `kalshi_cancel_order` | Execution | Cancel an open order |
| 10 | `kalshi_get_positions` | Portfolio | List all open positions |
| 11 | `kalshi_get_balance` | Portfolio | Get current account balance / bankroll |
| 12 | `compute_bin_probabilities` | Model | Run probability model for a set of bins |
| 13 | `compute_pcs` | Model | Compute Probability Confidence Score |
| 14 | `risk_check` | Risk | Validate a proposed trade against all risk rules |
| 15 | `compute_position_size` | Risk | Calculate dollar size for a trade at given PCS |
| 16 | `send_notification` | Notify | Send message via Telegram or Discord |
| 17 | `write_trade_log` | Logging | Persist full trade record to structured log |
| 18 | `generate_daily_summary` | Reporting | Compile and format daily summary report |

---

## Tool Specifications

---

### 1. `noaa_fetch_point_forecast`

**Purpose:** Retrieve the current NWS text-based point forecast for a given lat/lon (used to get official high/low temperature forecasts, PoP, etc.).

**Inputs:**
```python
lat: float          # Station latitude
lon: float          # Station longitude
station_id: str     # ICAO code (e.g. "KNYC"), used for cache keying
```

**Output:**
```python
{
  "station_id": str,
  "fetched_at": ISO-8601 timestamp,
  "forecasts": [
    {
      "period_name": str,         # e.g. "Tonight", "Thursday"
      "is_daytime": bool,
      "temp_f": int,
      "precip_probability_pct": int,
      "wind_speed_mph": str,
      "short_forecast": str,
      "detailed_forecast": str
    },
    ...
  ]
}
```

**Failure handling:**
- On HTTP error or timeout: return `None`, set `data_stale=True` for this station
- Log error with station ID and HTTP status
- Do NOT raise exception — allow calling code to handle gracefully

**Soul rule enforced:** Data freshness check (≤ 90 min) — if fetch returns `None`, no trades for this station.

---

### 2. `noaa_fetch_hourly_forecast`

**Purpose:** Retrieve the NWS hourly gridded forecast for the next 156 hours for a given NWS grid point. Used to get per-hour temperature, precipitation, sky cover values with confidence intervals.

**Inputs:**
```python
wfo: str        # NWS forecast office (e.g. "OKX" for New York)
grid_x: int
grid_y: int
station_id: str
```

**Output:** List of hourly forecast objects with temperature (°F), precip probability (%), wind speed (mph), and start/end times.

**Soul rule enforced:** Resolution proximity edge — hourly data within 12h of resolution time collapses uncertainty.

---

### 3. `noaa_fetch_gfs_ensemble`

**Purpose:** Download GFS model ensemble output (21 members) for a target station grid point. Computes mean, standard deviation, and percentile distribution for 2m temperature and QPF.

**Inputs:**
```python
lat: float
lon: float
station_id: str
run_time: str       # "latest" or specific YYYYMMDD/HH
forecast_hour: int  # Target hours ahead (e.g. 24, 48)
```

**Output:**
```python
{
  "station_id": str,
  "run_time": str,
  "valid_time": str,
  "variable": "TMP_2m" | "APCP_sfc",
  "members": [float, ...],          # 21 member values
  "mean": float,
  "std_dev": float,
  "percentiles": {"10": f, "25": f, "50": f, "75": f, "90": f}
}
```

**Soul rule enforced:** Model consensus check — large std_dev reduces PCS.

---

### 4. `noaa_fetch_hrrr`

**Purpose:** Download the latest HRRR model output for a target location. Used for high-resolution, short-range (0–18h) forecasts where HRRR outperforms GFS.

**Inputs:**
```python
lat: float
lon: float
station_id: str
forecast_hour: int  # 0–18 only
```

**Output:** Similar to GFS ensemble output but uses HRRR deterministic + neighborhood ensemble.

**Soul rule enforced:** HRRR replaces NWS deterministic weight (40%) for sub-18h windows.

---

### 5. `kalshi_list_weather_markets`

**Purpose:** Query Kalshi's API for all currently open markets in the weather category. Filters to only return markets that WeatherSafeClaw supports (temperature, precip, snowfall, wind).

**Inputs:**
```python
category: str = "weather"
status: str = "open"
limit: int = 200
```

**Output:** List of market objects with ticker, title, close_time, category, series info, and current prices.

**Soul rule enforced:** Only supported market types are returned — all others silently filtered.

---

### 6. `kalshi_get_market`

**Purpose:** Get full details of a single market including all contracts (bins), current bid/ask prices, volume, and resolution criteria.

**Inputs:**
```python
market_ticker: str
```

**Output:** Full market object including contracts array with per-bin prices.

---

### 7. `kalshi_get_orderbook`

**Purpose:** Get the current live orderbook (bid/ask ladder) for a specific contract. Used to determine realistic limit order placement.

**Inputs:**
```python
ticker: str     # Contract ticker (specific bin)
depth: int = 5  # Levels of book to retrieve
```

**Output:** Orderbook with bid/ask levels and sizes.

**Soul rule enforced:** Limit order pricing — agent bids at ask – 1 tick or mid for tight spreads.

---

### 8. `kalshi_submit_order`

**Purpose:** Submit a limit order to Kalshi.

**Inputs:**
```python
ticker: str
side: "yes" | "no"
count: int              # Number of contracts
limit_price: float      # 0.01 – 0.99 in $0.01 increments
client_order_id: str    # Unique ID for idempotency
```

**Output:** Order object with order_id, status, and creation timestamp.

**Security notes:**
- Uses RSA key-signed authentication (see `kalshi_client.py`)
- All credentials loaded exclusively from environment variables
- Never logs or prints the private key at any log level

**Soul rule enforced:** Risk Manager veto — this tool is only callable after `risk_check` passes.

---

### 9. `kalshi_cancel_order`

**Purpose:** Cancel a pending (unfilled) limit order.

**Inputs:**
```python
order_id: str
```

**Output:** Cancelled order object.

---

### 10. `kalshi_get_positions`

**Purpose:** List all open (unrealized) positions.

**Output:** List of position objects with ticker, contracts held, average entry price, current market price, and unrealized P&L.

---

### 11. `kalshi_get_balance`

**Purpose:** Retrieve current account balance and available funds.

**Output:**
```python
{
  "balance": float,           # Total account value in USD
  "available": float,         # Available to trade (not tied to open orders)
  "reserved": float           # Held in open orders/positions
}
```

**Soul rule enforced:** All position size calculations use `available` balance, never `balance`.

---

### 12. `compute_bin_probabilities`

**Purpose:** Core probability engine. Given a set of bin edges, compute the probability that the actual resolved value falls in each bin, using the weighted multi-model ensemble described in AGENTS.md.

**Inputs:**
```python
station_id: str
variable: "temp_high_f" | "temp_low_f" | "precip_in" | "snow_in"
target_date: date
bin_edges: list[float]          # e.g. [60, 64, 68, 72, 76, 80]
data_bundle: DataBundle         # Pre-fetched model data
```

**Output:**
```python
{
  "bins": [
    {"label": "60-64", "lower": 60, "upper": 64, "probability": 0.08, "implied_cents": 12},
    {"label": "64-68", "lower": 64, "upper": 68, "probability": 0.21, "implied_cents": 18},
    ...
  ],
  "total_probability": 1.0,   # Should sum to ~1.0 (normalized)
  "model_mean": float,
  "model_std": float,
  "data_sources_used": list[str]
}
```

---

### 13. `compute_pcs`

**Purpose:** Compute the Probability Confidence Score (PCS) for a specific bin, integrating model agreement, data freshness, ensemble spread, and calibration history.

**Inputs:**
```python
bin_probability: float      # Output of compute_bin_probabilities for target bin
model_std: float
data_age_minutes: int
models_agreeing: int        # How many of 4 sources agree on this bin
nws_confidence: str         # "high" | "moderate" | "low" | "unknown"
recent_model_drift: float   # Change in model mean in last 24h
calibration_history: dict   # Agent's past accuracy at this PCS range
```

**Output:**
```python
{
  "pcs": int,           # 0–100
  "breakdown": {        # Contribution of each factor
    "base_probability": float,
    "model_agreement_bonus": int,
    "nws_confidence_bonus": int,
    "drift_penalty": int,
    "data_freshness_penalty": int
  },
  "tradeable": bool     # True if PCS ≥ 75
}
```

---

### 14. `risk_check`

**Purpose:** Final gate before any order is submitted. Validates the proposed trade against all risk rules from soul.md.

**Inputs:**
```python
proposed_size_usd: float
current_balance: float
current_exposure_usd: float
daily_pnl: float
rolling_7d_drawdown_pct: float
correlated_positions: list[str]     # Tickers of positions that correlate
proposed_ticker: str
```

**Output:**
```python
{
  "approved": bool,
  "rejection_reasons": list[str],   # Empty if approved
  "approved_size_usd": float        # May be reduced by risk manager
}
```

**Soul rule enforced:** This tool enforces ALL hard limits from soul.md. It cannot be bypassed.

---

### 15. `compute_position_size`

**Purpose:** Calculate the correct position size in USD for a trade given the current PCS and bankroll.

**Inputs:**
```python
pcs: int
bankroll_usd: float     # Available balance only
```

**Output:**
```python
{
  "size_usd": float,    # Always ≤ 2% of bankroll, floored to nearest dollar
  "size_pct": float,    # 0.01 or 0.015 or 0.02
  "rationale": str
}
```

---

### 16. `send_notification`

**Purpose:** Send a structured message to configured notification channels (Telegram, Discord).

**Inputs:**
```python
message_type: str       # One of the types defined in AGENTS.md
payload: dict           # Type-specific data
channels: list[str]     # ["telegram", "discord"] — defaults to all configured
```

**Failure handling:** Notification failures are logged but never interrupt trading operations.

---

### 17. `write_trade_log`

**Purpose:** Persist a complete trade record to a structured JSON log file.

**Inputs:** Full trade context object including:
- Entry/exit timestamp, ticker, direction, size, prices
- PCS at entry, edge at entry, model probabilities
- Data sources used, data age at decision time
- Resolution outcome, P&L

**Output:** File path of written log entry.

---

### 18. `generate_daily_summary`

**Purpose:** Compile all trade records for the current day and produce a human-readable summary with P&L, win rate, risk metrics, and model calibration stats.

**Inputs:**
```python
date: date
trades_log_dir: str
```

**Output:** Formatted summary string (Markdown) + structured JSON.

---

## Skill Definitions

### Skill: `weather_market_classifier`

Classifies a raw Kalshi market ticker/title into a recognized WeatherSafeClaw market type:
- Input: market title string, series ticker
- Output: market_type, target_station, target_variable, resolution_criteria
- Returns `None` if the market type is unsupported — agent skips silently

### Skill: `ensemble_to_bin_distribution`

Converts raw ensemble member values (array of floats) into bin probability distribution:
- Input: members array, bin edges
- Algorithm: kernel density estimation + discrete bin integration
- Output: per-bin probability array, normalized to 1.0

### Skill: `kalshi_resolution_mapper`

Maps Kalshi resolution rules to the correct NOAA/NWS observation:
- Input: Kalshi market resolution criteria text
- Output: station_id, variable, time_window, observation_type
- Critical for ensuring the model uses the exact same observation Kalshi will use

### Skill: `limit_price_calculator`

Calculates optimal limit order price:
- Input: orderbook, desired direction, model probability
- Output: limit price in cents
- Rule: never cross the spread; never pay more than model_probability - 2 cents

### Skill: `brier_score_updater`

Updates the rolling calibration tracker after each market resolution:
- Input: predicted probability at entry, actual outcome (1 or 0)
- Output: updated rolling Brier score
- Used to track model accuracy and PCS calibration quality over time
