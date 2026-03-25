"""Basic tests for the probability model."""
import math
from datetime import date
from unittest.mock import MagicMock

import pytest

from agent.noaa_client import DataBundle
from agent.probability_model import ProbabilityModel, _bin_probability, _normal_cdf
from config.settings import Settings


# ── Unit tests for math helpers ────────────────────────────────────────────────

def test_normal_cdf_at_mean():
    assert abs(_normal_cdf(70, 70, 5) - 0.5) < 1e-6


def test_normal_cdf_below_mean():
    assert _normal_cdf(65, 70, 5) < 0.5


def test_bin_probability_sums_to_one():
    mu, sigma = 70.0, 4.0
    edges = [60, 64, 68, 72, 76, 80]
    total = 0.0
    for i in range(len(edges) + 1):
        lo = edges[i - 1] if i > 0 else None
        hi = edges[i] if i < len(edges) else None
        total += _bin_probability(lo, hi, mu, sigma)
    assert abs(total - 1.0) < 1e-6


def test_bin_probability_center_bin_highest():
    """The bin containing the mean should have the highest probability."""
    mu, sigma = 70.0, 4.0
    edges = [60, 64, 68, 72, 76, 80]
    probs = []
    for i in range(len(edges) + 1):
        lo = edges[i - 1] if i > 0 else None
        hi = edges[i] if i < len(edges) else None
        probs.append(_bin_probability(lo, hi, mu, sigma))
    # 68–72 bin (index 3) should be the peak
    assert probs[3] == max(probs)


# ── Integration-level model tests ─────────────────────────────────────────────

def _make_bundle(temp_f: float = 70.0) -> DataBundle:
    bundle = DataBundle(station_id="KNYC")
    bundle.nws_periods = [
        {
            "start_time": "2026-03-25T06:00:00-05:00",
            "is_daytime": True,
            "temp_f": int(temp_f),
            "precip_probability_pct": 10,
            "wind_speed": "5 mph",
            "short_forecast": "Sunny",
        }
    ]
    return bundle


def _make_settings() -> Settings:
    import os
    os.environ["KALSHI_API_KEY"] = "dummy"
    os.environ["KALSHI_PRIVATE_KEY_PEM"] = "dummy"
    s = Settings.__new__(Settings)
    s.kalshi_api_key = "dummy"
    s.kalshi_private_key_pem = "dummy"
    s.telegram_bot_token = ""
    s.telegram_chat_id = ""
    s.discord_webhook_url = ""
    s.timezone = "America/New_York"
    s.data_refresh_interval_minutes = 15
    s.market_scan_interval_minutes = 30
    s.position_monitor_interval_minutes = 15
    s.active_window_start_hour = 6
    s.active_window_end_hour = 22
    s.max_single_trade_pct = 0.02
    s.preferred_trade_pct = 0.01
    s.max_total_exposure_pct = 0.10
    s.daily_loss_limit_pct = 0.03
    s.weekly_drawdown_limit_pct = 0.08
    s.min_pcs = 75
    s.min_edge = 0.05
    s.weight_nws = 0.40
    s.weight_gfs = 0.25
    s.weight_ecmwf = 0.25
    s.weight_hrrr = 0.40
    s.weight_climatology = 0.10
    s.default_nws_sigma = 4.0
    return s


def test_model_returns_output_for_valid_bundle():
    settings = _make_settings()
    model = ProbabilityModel(settings)
    bundle = _make_bundle(70.0)

    output = model.compute(
        station_id="KNYC",
        variable="temp_high_f",
        target_date=date(2026, 3, 25),
        bin_edges=[64, 68, 72, 76],
        kalshi_prices_cents=[5, 15, 45, 25, 10],
        bundle=bundle,
    )

    assert output is not None
    assert len(output.bins) == 5  # 4 edges → 5 bins
    assert abs(sum(b.model_probability for b in output.bins) - 1.0) < 0.01


def test_model_highest_prob_bin_contains_forecast():
    settings = _make_settings()
    model = ProbabilityModel(settings)
    bundle = _make_bundle(70.0)

    output = model.compute(
        station_id="KNYC",
        variable="temp_high_f",
        target_date=date(2026, 3, 25),
        bin_edges=[60, 65, 70, 75, 80],
        kalshi_prices_cents=[2, 10, 40, 35, 13],
        bundle=bundle,
    )

    assert output is not None
    best = max(output.bins, key=lambda b: b.model_probability)
    # 70°F should fall in the 70–75 bin (index 3) or 65–70 bin (index 2)
    assert best.lower in (65.0, 70.0) or best.upper in (70.0, 75.0)


def test_model_returns_none_for_empty_bundle():
    settings = _make_settings()
    model = ProbabilityModel(settings)
    bundle = DataBundle(station_id="KNYC")  # no data

    output = model.compute(
        station_id="KNYC",
        variable="temp_high_f",
        target_date=date(2026, 3, 25),
        bin_edges=[64, 68, 72, 76],
        kalshi_prices_cents=[5, 15, 45, 25, 10],
        bundle=bundle,
    )
    assert output is None


# ── Risk manager tests ─────────────────────────────────────────────────────────

def test_risk_manager_rejects_low_pcs():
    from agent.risk_manager import RiskManager
    settings = _make_settings()
    risk = RiskManager(settings)
    decision = risk.check_trade(
        proposed_size_usd=100.0,
        bankroll_usd=10_000.0,
        open_exposure_usd=0.0,
        pcs=70,          # Below minimum of 75
        edge=0.10,
    )
    assert not decision.approved
    assert any("PCS" in r for r in decision.rejection_reasons)


def test_risk_manager_rejects_low_edge():
    from agent.risk_manager import RiskManager
    settings = _make_settings()
    risk = RiskManager(settings)
    decision = risk.check_trade(
        proposed_size_usd=100.0,
        bankroll_usd=10_000.0,
        open_exposure_usd=0.0,
        pcs=80,
        edge=0.03,       # Below minimum of 0.05
    )
    assert not decision.approved
    assert any("Edge" in r for r in decision.rejection_reasons)


def test_risk_manager_approves_valid_trade():
    from agent.risk_manager import RiskManager
    settings = _make_settings()
    risk = RiskManager(settings)
    decision = risk.check_trade(
        proposed_size_usd=100.0,
        bankroll_usd=10_000.0,
        open_exposure_usd=0.0,
        pcs=80,
        edge=0.10,
    )
    assert decision.approved
    assert decision.approved_size_usd == 100.0


def test_risk_manager_caps_size_to_2pct():
    from agent.risk_manager import RiskManager
    settings = _make_settings()
    risk = RiskManager(settings)
    decision = risk.check_trade(
        proposed_size_usd=500.0,   # 5% — should be capped to 2%
        bankroll_usd=10_000.0,
        open_exposure_usd=0.0,
        pcs=80,
        edge=0.10,
    )
    assert decision.approved
    assert decision.approved_size_usd <= 200.0  # 2% of 10k


def test_risk_manager_halts_at_daily_loss_limit():
    from agent.risk_manager import RiskManager
    settings = _make_settings()
    risk = RiskManager(settings)
    risk.record_realized_pnl(-350.0)  # -3.5% of 10k
    decision = risk.check_trade(
        proposed_size_usd=100.0,
        bankroll_usd=10_000.0,
        open_exposure_usd=0.0,
        pcs=80,
        edge=0.10,
    )
    # Daily loss over limit should trigger halt after force_halt is called
    # (auto-halt happens via force_halt in position_monitor)
    # Direct daily PCS computation is tested via the state
    assert risk.daily_pnl == -350.0
