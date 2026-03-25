# WeatherSafeClaw — Agent Definition

## Overview

**Agent Name:** WeatherSafeClaw  
**Framework:** OpenClaw  
**Domain:** Kalshi Weather Prediction Markets  
**Primary Data Source:** NOAA / National Weather Service  
**Secondary Data Sources:** GFS ensemble (NCEP), ECMWF open data, HRRR (NOAA), Weather.gov API  

This document defines the agent's architecture, decision loop, capabilities, and operational parameters. It is the engineering companion to `soul.md`, which defines values and rules. Everything here must be consistent with `soul.md`.

---

## Agent Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        WeatherSafeClaw                          │
│                                                                 │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐  │
│  │  Data Layer │───▶│ Probability  │───▶│  Decision Engine  │  │
│  │             │    │    Model     │    │                   │  │
│  │ NOAA/NWS    │    │              │    │  PCS ≥ 75?        │  │
│  │ GFS/ECMWF   │    │  Per-bin     │    │  Edge ≥ 5 pts?    │  │
│  │ HRRR/RAP    │    │  confidence  │    │  Risk OK?         │  │
│  │ Climatology │    │  scoring     │    │                   │  │
│  └─────────────┘    └──────────────┘    └────────┬──────────┘  │
│                                                  │             │
│  ┌─────────────┐    ┌──────────────┐             │             │
│  │ Risk Manager│◀───│ Order Engine │◀────────────┘             │
│  │             │    │              │                           │
│  │ Bankroll    │    │ Limit orders │                           │
│  │ Exposure    │    │ Fill monitor │                           │
│  │ Drawdown    │    │ Cancel logic │                           │
│  └──────┬──────┘    └──────────────┘                           │
│         │                                                       │
│  ┌──────▼──────┐    ┌──────────────┐                           │
│  │   Logger    │───▶│  Notifier    │                           │
│  │             │    │              │                           │
│  │ Trade log   │    │ Telegram/    │                           │
│  │ P&L log     │    │ Discord      │                           │
│  │ Error log   │    │ Daily digest │                           │
│  └─────────────┘    └──────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Trading Loop

The agent runs the following loop continuously during active trading windows (06:00–22:00 ET):

### Phase 1 — Data Refresh (every 15–30 min)

```
FOR each target weather station (KNYC, KMIA, KMDW, KBOS, KLAX, KDFW, ...):
    1. Pull latest NWS hourly/daily forecast via weather.gov API
    2. Pull GFS ensemble: mean, spread, percentiles for 2m temperature, precip, snow
    3. Pull ECMWF open ensemble (where available)
    4. Pull HRRR/RAP if within 18-hour horizon
    5. Pull climatological base rate for station × variable × day-of-year
    6. Cache all data with timestamps; flag stale data (> 90 min)
```

### Phase 2 — Market Scan (every 30 min)

```
FOR each open Kalshi weather market:
    1. Fetch current market data: ticker, bins, prices, volume, close time
    2. Map market to the corresponding NWS station(s) and observation type
    3. Identify the resolution criteria (station, variable, time window, bin edges)
    4. SKIP if: data is stale, market closes in < 10 min, market type is unknown
    
    FOR each bin in the market:
        1. Run probability model → P(bin wins | all data sources)
        2. Compute PCS (Probability Confidence Score, 0–100)
        3. Compute implied probability from Kalshi price
        4. Compute edge = P_model - P_implied
        5. Flag if: PCS ≥ 75 AND edge ≥ 0.05
    
    CHECK for multi-bin spread opportunities:
        - If two adjacent bins are flagged, evaluate combined cost
        - Accept spread if: max loss ≤ 1% bankroll AND expected value ≥ 0.08
    
    EMIT: ordered list of tradeable opportunities (by expected_value DESC)
```

### Phase 3 — Decision Gate

```
FOR each opportunity (sorted by expected_value DESC):
    1. Re-check data freshness (reject if stale since Phase 1)
    2. Risk Manager check:
       a. Current bankroll available?
       b. Daily loss limit not hit?
       c. Rolling 7-day drawdown < 8%?
       d. Adding this position keeps total exposure ≤ 10% bankroll?
       e. No correlated open position already held?
    3. If all checks pass → proceed to Phase 4
    4. If any check fails → log reason, skip, continue to next opportunity
```

### Phase 4 — Order Placement

```
1. Calculate position size:
   - PCS 75–84: size = 0.01 × bankroll
   - PCS 85–94: size = 0.015 × bankroll
   - PCS 95–100: size = 0.02 × bankroll
   - Round DOWN to nearest dollar (never round up)

2. Compute limit order price:
   - YES contracts: bid at current ask – 1 tick (patient fill)
   - If spread is < 3 ticks wide: bid at mid

3. Submit limit order via Kalshi REST API
4. Log order submission with full context (PCS, edge, model probs, data sources)
5. Set fill timeout: 3 × (data_refresh_interval)
```

### Phase 5 — Fill Monitoring

```
WHILE order is open:
    EVERY data refresh cycle:
        1. Check fill status
        2. Re-evaluate current PCS with updated data
        3. IF PCS dropped below 70: CANCEL the order immediately
        4. IF fill timeout exceeded: CANCEL the order, log reason
        5. IF filled: move to Phase 6
```

### Phase 6 — Position Monitoring

```
FOR each open position:
    EVERY 15 minutes:
        1. Re-run probability model for the held bin
        2. IF new PCS < 60 AND position is still profitable: consider exit at profit
        3. IF market resolves: record outcome, update win/loss tracker, update model calibration
        4. Log current unrealized P&L
    
    ON resolution:
        1. Record: entry_price, exit_price, PCS_at_entry, actual_outcome, P&L
        2. Update Brier score rolling average
        3. Update win rate tracker
        4. Trigger daily summary if this was the last position of the day
```

### Phase 7 — Daily Wrap (22:00 ET or last resolution)

```
1. Compile daily summary:
   - Trades taken: count, total size
   - Win/loss record for the day
   - Net P&L (realized)
   - Unrealized P&L on open positions
   - Opportunities scanned vs. skipped (ratio)
   - Bankroll current vs. start-of-day
   - Any risk limits approached or hit

2. Send summary via configured notifier (Telegram/Discord)
3. Write structured log entry to logs/daily/YYYY-MM-DD.json
4. Persist updated bankroll and model calibration state
```

---

## Supported Market Types

| Market Variable | Primary Station(s) | Resolution Source | Horizon |
|---|---|---|---|
| Daily High Temperature | KNYC, KMIA, KMDW, KBOS, KLAX, KDFW, KSEA, KDEN, KATL, KORD | NWS official observation | ≤ 5 days |
| Daily Low Temperature | Same as above | NWS official observation | ≤ 5 days |
| Measurable Precipitation | Same as above | NWS observation + CoCoRaHS | ≤ 3 days |
| Snowfall (24h accumulation) | KNYC, KBOS, KORD, KDEN, KSEA | NWS / ASOS observation | ≤ 72 hours |
| Wind (max gust) | KNYC, KMIA | NWS ASOS | ≤ 48 hours |

---

## Model Inputs & Weighting

### Temperature Probability Model

```
Input: target_station, target_date, bin_edges, data_bundle

1. NWS Point Forecast deterministic value → base probability (Gaussian centered on forecast, σ from NWS confidence interval)
2. GFS ensemble:
   - Mean and σ from 21-member ensemble for station grid point
   - Probability of each bin = integral of N(μ_GFS, σ_GFS) over bin edges
   - Weight: 25%
3. ECMWF ensemble (50 members, where available):
   - Same calculation as GFS
   - Weight: 25%
4. HRRR (if horizon ≤ 18h):
   - Replace NWS deterministic weight with HRRR deterministic (higher resolution)
   - Weight: 40% for sub-18h, 0% otherwise
5. Climatological prior:
   - Station × calendar-week × variable distribution (30-year normal)
   - Applied as Bayesian prior with weight decreasing as forecast confidence increases
   - Maximum weight: 10% (at 5-day range), minimum: 3% (at sub-24h)

PCS calibration:
   - σ_ensemble (spread of model agreement): low spread → higher PCS
   - NWS confidence classification (if available): "High confidence" → PCS boost +5
   - Model consensus direction: all models agree on same bin → PCS boost +5
   - Recent model drift: large 24h shift in forecast → PCS penalty -10
```

### Precipitation Probability Model

```
Input: target_station, target_date, precip_threshold (typically 0.01"), data_bundle

1. NWS PoP (Probability of Precipitation) from point forecast: direct input
2. GFS QPF ensemble: P(QPF > threshold) from 21 members
3. ECMWF QPF ensemble: P(QPF > threshold) from 50 members
4. HRRR QPF (sub-18h): deterministic QPF with neighborhood ensemble
5. Weighted combination per weighting scheme above
6. Climatological wet-day frequency for station × month: Bayesian prior

Special rules:
   - Never trade precipitation when a frontal system is within 50 miles and NWS confidence is "low"
   - Require cross-model agreement ≥ 3/4 sources for PCS ≥ 75
```

---

## Risk Manager Specification

```python
class RiskManager:
    max_single_trade_pct: float = 0.02        # 2% of bankroll
    preferred_trade_pct: float = 0.01         # 1% of bankroll
    max_total_exposure_pct: float = 0.10      # 10% of bankroll open at once
    daily_loss_limit_pct: float = 0.03        # halt trading after 3% daily loss
    weekly_drawdown_limit_pct: float = 0.08   # pause and review after 8% weekly drawdown
    min_pcs: int = 75                          # minimum Probability Confidence Score
    min_edge_pts: float = 0.05                # minimum edge over Kalshi implied prob
```

The Risk Manager is called before every order submission and is the final gate. It has veto power over every trade. It cannot be overridden by the probability model or the decision engine.

---

## Scheduler Configuration

```yaml
scheduler:
  data_refresh_interval_minutes: 15
  market_scan_interval_minutes: 30
  position_monitor_interval_minutes: 15
  active_window_start: "06:00"
  active_window_end: "22:00"
  timezone: "America/New_York"
  daily_summary_time: "22:05"
  weekly_review_time: "06:00"
  weekly_review_day: "monday"
```

---

## Notifier Integration

The agent supports the following notification channels (configure in `.env`):

| Channel | Use |
|---|---|
| **Telegram** | Trade alerts, daily summaries, risk limit warnings |
| **Discord** | Same as Telegram (backup) |
| **Log files** | All events, full detail, structured JSON |

Notification types:
- `TRADE_OPENED`: ticker, direction, size, price, PCS, edge
- `TRADE_FILLED`: fill price, slippage vs. model
- `TRADE_CANCELLED`: reason (timeout, PCS drop, risk limit)
- `POSITION_RESOLVED`: outcome, P&L, calibration update
- `RISK_WARNING`: daily loss %, exposure %, drawdown %
- `RISK_HALT`: reason, expected resume time
- `DAILY_SUMMARY`: full stats digest
- `DATA_STALE`: station, data source, last update time

---

## Human Override

The agent supports the following human commands via Telegram/Discord bot:

| Command | Action |
|---|---|
| `/status` | Current bankroll, open positions, daily P&L |
| `/pause` | Suspend new order placement (open positions continue monitoring) |
| `/resume` | Resume trading (only if no active risk halt) |
| `/close_all` | Cancel all open orders and liquidate all open positions at market |
| `/report` | Force-generate and send current daily summary |
| `/risk` | Show current risk metrics (exposure, drawdown, daily loss) |
| `/opportunities` | Show top 5 current opportunities queued by the model |

All human overrides are logged with a timestamp and source.

---

## Data Sources & Attribution

| Source | URL / API | Authentication | Rate Limit |
|---|---|---|---|
| NWS Point Forecast | `https://api.weather.gov/points/{lat},{lon}` | None required | 1 req/sec |
| NWS Gridded Forecast | `https://api.weather.gov/gridpoints/{wfo}/{x},{y}/forecast/hourly` | None required | 1 req/sec |
| NOAA GFS Ensemble | NOMADS (`https://nomads.ncep.noaa.gov/`) | None required | Respectful use |
| ECMWF Open Data | `https://data.ecmwf.int/forecasts/` | Free registration | Per-download |
| HRRR Archive / Live | NOMADS + AWS S3 (noaa-hrrr-bdp-pds) | None required | S3 free tier |
| Kalshi REST API | `https://trading-api.kalshi.com/trade-api/v2` | RSA key auth | Per plan |

---

## Directory Structure

```
kalshi-trader/
├── soul.md                    # Agent identity and unbreakable rules
├── AGENTS.md                  # This file — architecture and trading loop
├── TOOLS.md                   # Tool/skill descriptions for OpenClaw
├── README.md                  # Setup and deployment guide
├── requirements.txt           # Python dependencies
├── .env.example               # Environment variable template (no secrets)
├── .gitignore                 # Excludes all credential files
│
├── agent/
│   ├── __init__.py
│   ├── main.py                # Entry point — starts scheduler
│   ├── decision_engine.py     # Phase 2–3: scan, score, gate
│   ├── order_engine.py        # Phase 4–5: placement and fill monitoring
│   ├── position_monitor.py    # Phase 6: open position lifecycle
│   ├── risk_manager.py        # Bankroll, exposure, drawdown guardrails
│   ├── probability_model.py   # PCS calculation, bin probability estimation
│   ├── kalshi_client.py       # Kalshi REST API wrapper
│   ├── noaa_client.py         # NOAA/NWS + ensemble data fetcher
│   ├── notifier.py            # Telegram/Discord integration
│   └── logger.py              # Structured logging
│
├── config/
│   ├── settings.py            # All configurable parameters
│   └── stations.yaml          # Station definitions (ICAO → lat/lon → NWS grid)
│
├── data/
│   ├── cache/                 # Short-term data cache (gitignored)
│   └── climatology/           # 30-year normals per station (CSV)
│
├── logs/
│   ├── trades/                # Per-trade JSON records
│   ├── daily/                 # Daily summary JSON
│   └── errors/                # Error log
│
└── tests/
    ├── test_probability_model.py
    ├── test_risk_manager.py
    └── test_kalshi_client.py
```
