# WeatherSafeClaw

An autonomous Kalshi weather market trading agent built on the OpenClaw framework.

WeatherSafeClaw trades **only weather markets** (temperature bins, precipitation, snowfall) using NOAA/NWS forecast data as its primary edge. It is designed to be disciplined, conservative, and relentlessly process-driven. It skips most trades. That's the point.

**Read `soul.md` before touching anything else.**

---

## Architecture Overview

```
soul.md          → Agent identity and unbreakable risk rules
AGENTS.md        → Trading loop and system architecture
TOOLS.md         → Tool and skill specifications
agent/           → Python source code
config/          → Settings and station definitions
tests/           → Unit tests
```

---

## Prerequisites

- Python 3.11+
- A funded Kalshi account with API access enabled
- Your Kalshi API key and RSA private key (from the Kalshi dashboard)
- (Optional) A Telegram bot or Discord webhook for trade notifications

---

## Setup

### 1. Fund Your Kalshi Account

1. Sign up at [kalshi.com](https://kalshi.com)
2. Complete identity verification (KYC)
3. Deposit funds — start small (e.g. $500–$1,000) while the agent is new
4. Enable API access in your account dashboard
5. Generate an RSA key pair and register your public key with Kalshi

### 2. Configure API Keys

```bash
# Copy the environment variable template
cp .env.example .env
```

Open `.env` and fill in:

```env
KALSHI_API_KEY=your-api-key-uuid-here
KALSHI_PRIVATE_KEY_PEM=-----BEGIN RSA PRIVATE KEY-----
MIIEo...your key...
-----END RSA PRIVATE KEY-----
```

> **Security:** `.env` and `kalshi-creds.txt` are in `.gitignore` and will never be committed. Never share your private key.

### 3. (Optional) Set Up Notifications

**Telegram:**
1. Message [@BotFather](https://t.me/BotFather) on Telegram to create a bot and get a token
2. Start a chat with your bot and get your chat ID (visit `https://api.telegram.org/bot{TOKEN}/getUpdates`)
3. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your-bot-token
   TELEGRAM_CHAT_ID=your-chat-id
   ```

**Discord:**
1. In your Discord server: Server Settings → Integrations → Webhooks → New Webhook
2. Copy the webhook URL and add to `.env`:
   ```
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```

### 4. Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. Load Environment Variables

```bash
# If using python-dotenv (included in requirements.txt):
export $(cat .env | grep -v '#' | xargs)

# Or source a wrapper:
set -a; source .env; set +a
```

### 6. Run Tests

```bash
pytest tests/ -v
```

All tests should pass before running live.

### 7. Start the Agent

```bash
python -m agent.main
```

The agent will:
- Start the scheduler
- Refresh NOAA data every 15 minutes
- Scan Kalshi weather markets every 30 minutes
- Monitor open positions every 15 minutes
- Send a daily P&L summary at 22:05 ET
- Log all activity to `logs/`

---

## Risk Parameters

All risk limits are defined in `config/settings.py` and enforced by the `RiskManager`. The defaults match `soul.md`:

| Parameter | Default | soul.md rule |
|---|---|---|
| Max trade size | 2% of bankroll | Hard limit |
| Preferred trade size | 1% of bankroll | Default |
| Max total open exposure | 10% of bankroll | Hard limit |
| Daily loss halt | 3% of bankroll | Automatic |
| Weekly drawdown pause | 8% of bankroll | Manual review required |
| Minimum PCS to trade | 75 | Hard limit |
| Minimum edge to trade | 5 percentage points | Hard limit |

**Do not increase these limits until the agent has at least 200 resolved trades and a verified positive Brier score trend.**

---

## Human Override Commands

If you configured Telegram or Discord notifications, you can send these commands to the bot:

| Command | Effect |
|---|---|
| `/status` | Current bankroll, open positions, daily P&L |
| `/pause` | Stop opening new trades |
| `/resume` | Resume trading |
| `/close_all` | Cancel all orders and liquidate all positions |
| `/report` | Force a daily summary report |
| `/risk` | Show current risk metrics |
| `/opportunities` | Show top 5 current opportunities |

---

## Monitoring & Logs

All activity is logged to `logs/`:

```
logs/
├── trades/
│   └── YYYY-MM-DD.jsonl     # One JSON record per trade event
├── daily/
│   └── YYYY-MM-DD.json      # Daily summary
└── errors/
    └── errors.log           # Error log
```

---

## Data Sources

WeatherSafeClaw uses only public, official government data sources:

| Source | Data | Auth Required |
|---|---|---|
| [NWS API](https://api.weather.gov) | Point forecasts, hourly gridded forecasts | None |
| [NOMADS/NCEP](https://nomads.ncep.noaa.gov) | GFS model ensembles | None |
| [AWS S3 HRRR](https://registry.opendata.aws/noaa-hrrr-pds/) | HRRR short-range forecasts | None |

No paid weather data subscriptions are required.

---

## Extending the Agent

### Adding New Stations

Edit `config/stations.py`. To get NWS grid coordinates for any US location:

```bash
curl "https://api.weather.gov/points/{latitude},{longitude}"
# Look for: gridX, gridY, cwa (WFO code)
```

### Adding New Market Types

1. Add a pattern to `_MARKET_PATTERNS` in `agent/decision_engine.py`
2. Add distribution logic for the new variable to `agent/probability_model.py`
3. Write unit tests in `tests/`

### Tuning the Model

All model weights are in `config/settings.py`. After collecting ≥100 resolved trades, review the calibration summary from `ProbabilityModel.calibration_summary()` and adjust weights to correct systematic bias.

---

## Disclaimer

This software is for educational and research purposes. Prediction market trading involves real financial risk. Past performance of any model or strategy does not guarantee future results. Always trade within your means. The risk limits in `soul.md` exist for a reason — respect them.
