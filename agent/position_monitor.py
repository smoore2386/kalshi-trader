"""
Position Monitor — Phase 6 of the AGENTS.md trading loop.

Monitors open positions, tracks P&L, and updates model calibration on resolution.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent.kalshi_client import KalshiClient, KalshiAPIError
from agent.logger import get_logger, write_trade_log
from agent.noaa_client import NOAAClient
from agent.notifier import Notifier
from agent.risk_manager import RiskManager
from config.settings import Settings

logger = get_logger("position_monitor")


class PositionMonitor:
    """Tracks open positions and records outcomes when markets resolve."""

    def __init__(
        self,
        kalshi: KalshiClient,
        noaa: NOAAClient,
        risk: RiskManager,
        notifier: Notifier,
        settings: Settings,
    ) -> None:
        self._kalshi = kalshi
        self._noaa = noaa
        self._risk = risk
        self._notifier = notifier
        self._settings = settings
        # Tracks positions we previously saw as open
        self._known_positions: dict[str, dict] = {}

    def run_monitor_cycle(self) -> None:
        """Phase 6: check all open positions for resolution and P&L changes."""
        try:
            current_positions = {
                p["ticker"]: p for p in self._kalshi.get_positions()
            }
        except KalshiAPIError as exc:
            logger.error("Failed to fetch positions: %s", exc)
            return

        # Detect resolved positions (were open, now gone from API)
        resolved_tickers = set(self._known_positions) - set(current_positions)
        for ticker in resolved_tickers:
            self._handle_resolution(ticker, self._known_positions[ticker])

        # Log current unrealized P&L
        total_unrealized = 0.0
        for ticker, pos in current_positions.items():
            unrealized = pos.get("unrealized_pnl", 0.0) or 0.0
            total_unrealized += unrealized
            logger.debug("Position %s: unrealized P&L $%.2f", ticker, unrealized)

        if current_positions:
            logger.info(
                "Open positions: %d | Total unrealized P&L: $%.2f",
                len(current_positions), total_unrealized,
            )

        # Check for risk warnings
        balance_data = self._kalshi.get_balance()
        bankroll = balance_data.get("balance", 0.0)
        if bankroll > 0:
            exposure_pct = (
                sum(p.get("value", 0.0) for p in current_positions.values()) / bankroll
            )
            if exposure_pct > self._settings.max_total_exposure_pct * 0.8:
                self._notifier.send("RISK_WARNING", {
                    "type": "exposure",
                    "exposure_pct": round(exposure_pct, 4),
                    "limit_pct": self._settings.max_total_exposure_pct,
                })

        self._known_positions = current_positions

    def _handle_resolution(self, ticker: str, last_known: dict) -> None:
        """Called when a position is no longer open — it has resolved."""
        pnl = last_known.get("realized_pnl") or last_known.get("unrealized_pnl", 0.0)
        won = pnl > 0

        logger.info("Position resolved: %s | P&L: $%.2f | Won: %s", ticker, pnl, won)

        self._risk.record_realized_pnl(pnl)

        write_trade_log({
            "event": "resolution",
            "ticker": ticker,
            "pnl": pnl,
            "won": won,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self._notifier.send("POSITION_RESOLVED", {
            "ticker": ticker,
            "pnl": round(pnl, 2),
            "won": won,
        })

        # Check if daily loss limit was breached
        balance_data = self._kalshi.get_balance()
        bankroll = balance_data.get("balance", 0.0)
        if bankroll > 0:
            daily_loss_pct = abs(min(0.0, self._risk.daily_pnl)) / bankroll
            if daily_loss_pct >= self._settings.daily_loss_limit_pct:
                self._risk.force_halt(
                    f"Daily loss limit reached: {daily_loss_pct:.1%} ≥ {self._settings.daily_loss_limit_pct:.1%}"
                )
                self._notifier.send("RISK_HALT", {
                    "reason": "daily loss limit",
                    "daily_loss_pct": round(daily_loss_pct, 4),
                })
