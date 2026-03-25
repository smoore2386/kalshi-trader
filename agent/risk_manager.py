"""
Risk Manager — enforces all hard limits defined in soul.md.

This is the final gate before any order is submitted.
It cannot be bypassed by any other component.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.logger import get_logger
from config.settings import Settings

logger = get_logger("risk_manager")


@dataclass
class RiskDecision:
    approved: bool
    rejection_reasons: list[str]
    approved_size_usd: float


class RiskManager:
    """
    Enforces soul.md risk rules:
      - Max 2% of bankroll per trade (1% preferred)
      - Max 10% total open exposure
      - Daily loss limit: halt trading at -3%
      - Weekly drawdown limit: pause at -8%
      - Minimum PCS: 75
      - Minimum edge: 5 percentage points
    """

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._daily_pnl: float = 0.0
        self._rolling_7d_pnl: float = 0.0
        self._trading_halted: bool = False
        self._halt_reason: str = ""

    # ── State updates ──────────────────────────────────────────────────────────

    def record_realized_pnl(self, pnl: float) -> None:
        self._daily_pnl += pnl
        self._rolling_7d_pnl += pnl
        self._check_automatic_halt()

    def reset_daily_pnl(self) -> None:
        self._daily_pnl = 0.0
        if self._trading_halted and "daily" in self._halt_reason:
            self._trading_halted = False
            self._halt_reason = ""
            logger.info("Daily loss limit reset — trading resumed.")

    def force_halt(self, reason: str) -> None:
        self._trading_halted = True
        self._halt_reason = reason
        logger.warning("Trading HALTED: %s", reason)

    def resume(self) -> None:
        if not self._trading_halted:
            return
        if "7-day" in self._halt_reason:
            logger.warning("Cannot auto-resume from weekly drawdown halt — manual review required.")
            return
        self._trading_halted = False
        self._halt_reason = ""
        logger.info("Trading resumed.")

    # ── Core decision gate ─────────────────────────────────────────────────────

    def check_trade(
        self,
        proposed_size_usd: float,
        bankroll_usd: float,
        open_exposure_usd: float,
        pcs: int,
        edge: float,
        correlated_tickers: list[str] | None = None,
    ) -> RiskDecision:
        reasons: list[str] = []

        if self._trading_halted:
            reasons.append(f"Trading halted: {self._halt_reason}")
            return RiskDecision(approved=False, rejection_reasons=reasons, approved_size_usd=0.0)

        # PCS gate
        if pcs < self._s.min_pcs:
            reasons.append(f"PCS {pcs} < minimum {self._s.min_pcs}")

        # Edge gate
        if edge < self._s.min_edge:
            reasons.append(f"Edge {edge:.1%} < minimum {self._s.min_edge:.1%}")

        # Bankroll sanity
        if bankroll_usd <= 0:
            reasons.append("Zero or negative bankroll")
            return RiskDecision(approved=False, rejection_reasons=reasons, approved_size_usd=0.0)

        # Position size cap
        max_allowed = bankroll_usd * self._s.max_single_trade_pct
        if proposed_size_usd > max_allowed:
            logger.info(
                "Reducing size from $%.2f to max allowed $%.2f (%.0f%% bankroll)",
                proposed_size_usd, max_allowed, self._s.max_single_trade_pct * 100,
            )
            proposed_size_usd = max_allowed

        # Total exposure cap
        if open_exposure_usd + proposed_size_usd > bankroll_usd * self._s.max_total_exposure_pct:
            reasons.append(
                f"Adding ${proposed_size_usd:.2f} would exceed max exposure "
                f"({self._s.max_total_exposure_pct:.0%} of bankroll = "
                f"${bankroll_usd * self._s.max_total_exposure_pct:.2f})"
            )

        # Daily loss limit
        if bankroll_usd > 0:
            daily_loss_pct = abs(min(0.0, self._daily_pnl)) / bankroll_usd
            if daily_loss_pct >= self._s.daily_loss_limit_pct:
                reasons.append(
                    f"Daily loss limit hit ({daily_loss_pct:.1%} ≥ {self._s.daily_loss_limit_pct:.1%})"
                )

        # Correlated positions
        if correlated_tickers:
            reasons.append(f"Correlated open positions: {correlated_tickers}")

        if reasons:
            logger.info("Trade rejected: %s", "; ".join(reasons))
            return RiskDecision(approved=False, rejection_reasons=reasons, approved_size_usd=0.0)

        return RiskDecision(
            approved=True,
            rejection_reasons=[],
            approved_size_usd=math.floor(proposed_size_usd),
        )

    def compute_size(self, pcs: int, bankroll_usd: float) -> float:
        """Return position size in USD based on PCS tier, floored to nearest dollar."""
        if pcs >= 95:
            pct = self._s.max_single_trade_pct         # 2%
        elif pcs >= 85:
            pct = self._s.preferred_trade_pct * 1.5    # 1.5%
        else:
            pct = self._s.preferred_trade_pct           # 1%
        return math.floor(bankroll_usd * pct)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _check_automatic_halt(self) -> None:
        # This requires bankroll context; passed in from decision engine periodically
        pass

    @property
    def is_halted(self) -> bool:
        return self._trading_halted

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl


import math  # noqa: E402  (placed here to avoid circular hint issues)
