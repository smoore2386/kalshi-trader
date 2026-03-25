"""
All agent configuration — loaded from environment variables at startup.

No credentials are hardcoded here. See .env.example for required variables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _req(var: str) -> str:
    """Read a required environment variable or raise a clear error."""
    value = os.environ.get(var)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{var}' is not set. "
            f"Copy .env.example to .env and fill in your values."
        )
    return value


def _opt(var: str, default: str = "") -> str:
    return os.environ.get(var, default)


@dataclass
class Settings:
    # ── Kalshi credentials (required) ─────────────────────────────────────────
    kalshi_api_key: str = field(default_factory=lambda: _req("KALSHI_API_KEY"))
    kalshi_private_key_pem: str = field(default_factory=lambda: _req("KALSHI_PRIVATE_KEY_PEM"))

    # ── Notification (optional) ────────────────────────────────────────────────
    telegram_bot_token: str = field(default_factory=lambda: _opt("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _opt("TELEGRAM_CHAT_ID"))
    discord_webhook_url: str = field(default_factory=lambda: _opt("DISCORD_WEBHOOK_URL"))

    # ── Scheduling ─────────────────────────────────────────────────────────────
    timezone: str = field(default_factory=lambda: _opt("TIMEZONE", "America/New_York"))
    data_refresh_interval_minutes: int = 15
    market_scan_interval_minutes: int = 30
    position_monitor_interval_minutes: int = 15
    active_window_start_hour: int = 6     # 06:00 local
    active_window_end_hour: int = 22      # 22:00 local

    # ── Risk management (soul.md hard limits — do not relax) ──────────────────
    max_single_trade_pct: float = 0.02    # 2% of bankroll max per trade
    preferred_trade_pct: float = 0.01    # 1% preferred size
    max_total_exposure_pct: float = 0.10 # 10% max open exposure
    daily_loss_limit_pct: float = 0.03   # halt at -3% daily loss
    weekly_drawdown_limit_pct: float = 0.08  # pause at -8% 7-day drawdown

    # ── Entry thresholds ───────────────────────────────────────────────────────
    min_pcs: int = 75                     # Minimum Probability Confidence Score
    min_edge: float = 0.05               # Minimum edge over implied probability (5 pts)

    # ── Model weights ──────────────────────────────────────────────────────────
    weight_nws: float = 0.40             # NWS deterministic forecast weight
    weight_gfs: float = 0.25             # GFS ensemble weight
    weight_ecmwf: float = 0.25          # ECMWF ensemble weight
    weight_hrrr: float = 0.40           # HRRR weight (replaces NWS for sub-18h)
    weight_climatology: float = 0.10    # Climatological prior (max)
    default_nws_sigma: float = 4.0      # Default uncertainty (°F) for NWS point forecast
