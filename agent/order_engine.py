"""
Order Engine — Phases 4–5 of the AGENTS.md trading loop.

Handles limit order submission, fill monitoring, and cancellation logic.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent.kalshi_client import KalshiClient, KalshiAPIError
from agent.logger import get_logger, write_trade_log
from agent.notifier import Notifier
from agent.risk_manager import RiskManager
from config.settings import Settings

logger = get_logger("order_engine")


@dataclass
class PendingOrder:
    order_id: str
    opportunity_ticker: str
    market_ticker: str
    side: str
    size_usd: float
    limit_price_cents: int
    submitted_at: float = field(default_factory=time.time)
    pcs_at_submission: int = 0
    edge_at_submission: float = 0.0
    fill_timeout_seconds: float = 900.0  # 3 × 5-minute refresh


class OrderEngine:
    def __init__(
        self,
        kalshi: KalshiClient,
        risk: RiskManager,
        notifier: Notifier,
        settings: Settings,
    ) -> None:
        self._kalshi = kalshi
        self._risk = risk
        self._notifier = notifier
        self._settings = settings
        self._pending: dict[str, PendingOrder] = {}

    # ── Phase 4: order placement ───────────────────────────────────────────────

    def submit_opportunity(self, opp, size_usd: float) -> None:
        """Submit a limit order for an opportunity. Phase 4 of trading loop."""
        from agent.decision_engine import TradeOpportunity  # late import to avoid cycle

        if size_usd < 1.0:
            logger.info("Size too small ($%.2f) for %s — skipping.", size_usd, opp.contract_ticker)
            return

        # Compute limit price: aim for ask – 1 tick for patient fill
        limit_cents = self._compute_limit_price(opp)

        contracts = max(1, int(size_usd))  # 1 contract = $1 max payout; we buy `size` contracts

        client_oid = str(uuid.uuid4())
        try:
            result = self._kalshi.submit_limit_order(
                ticker=opp.contract_ticker,
                side=opp.side,
                count=contracts,
                limit_price_cents=limit_cents,
                client_order_id=client_oid,
            )
        except KalshiAPIError as exc:
            logger.error("Order submission failed for %s: %s", opp.contract_ticker, exc)
            self._notifier.send("TRADE_CANCELLED", {
                "ticker": opp.contract_ticker,
                "reason": str(exc),
            })
            return

        order_id = result.get("order", {}).get("order_id", client_oid)
        pending = PendingOrder(
            order_id=order_id,
            opportunity_ticker=opp.contract_ticker,
            market_ticker=opp.market_ticker,
            side=opp.side,
            size_usd=size_usd,
            limit_price_cents=limit_cents,
            pcs_at_submission=opp.pcs,
            edge_at_submission=opp.edge,
            fill_timeout_seconds=self._settings.market_scan_interval_minutes * 60 * 3,
        )
        self._pending[order_id] = pending

        logger.info(
            "Order submitted: %s %s %d contracts @ %d¢ (order_id=%s)",
            opp.side, opp.contract_ticker, contracts, limit_cents, order_id,
        )
        self._notifier.send("TRADE_OPENED", {
            "ticker": opp.contract_ticker,
            "side": opp.side,
            "contracts": contracts,
            "limit_cents": limit_cents,
            "pcs": opp.pcs,
            "edge": opp.edge,
        })

    # ── Phase 5: fill monitoring ───────────────────────────────────────────────

    def check_pending_orders(self) -> None:
        """Poll all pending orders for fill status or timeout. Call on each refresh cycle."""
        to_remove: list[str] = []

        for order_id, pending in list(self._pending.items()):
            try:
                order = self._kalshi.get_order(order_id)
            except KalshiAPIError as exc:
                logger.warning("Could not fetch order %s: %s", order_id, exc)
                continue

            status = order.get("order", {}).get("status", "")

            if status == "filled":
                logger.info("Order filled: %s", order_id)
                self._notifier.send("TRADE_FILLED", {
                    "ticker": pending.opportunity_ticker,
                    "order_id": order_id,
                })
                write_trade_log({
                    "event": "fill",
                    "order_id": order_id,
                    "ticker": pending.opportunity_ticker,
                    "side": pending.side,
                    "size_usd": pending.size_usd,
                    "limit_cents": pending.limit_price_cents,
                    "pcs_at_submission": pending.pcs_at_submission,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                to_remove.append(order_id)

            elif status in ("cancelled", "expired"):
                logger.info("Order %s %s — removing.", order_id, status)
                to_remove.append(order_id)

            elif time.time() - pending.submitted_at > pending.fill_timeout_seconds:
                # Timeout — cancel
                logger.info("Order %s timed out — cancelling.", order_id)
                try:
                    self._kalshi.cancel_order(order_id)
                except KalshiAPIError as exc:
                    logger.warning("Cancel failed for %s: %s", order_id, exc)
                self._notifier.send("TRADE_CANCELLED", {
                    "ticker": pending.opportunity_ticker,
                    "order_id": order_id,
                    "reason": "fill timeout",
                })
                to_remove.append(order_id)

        for oid in to_remove:
            self._pending.pop(oid, None)

    @staticmethod
    def _compute_limit_price(opp) -> int:
        """
        Patient limit: target model_probability – 2 cents (never cross spread).
        Clamped to 1–99.
        """
        model_cents = int(opp.model_probability * 100)
        patient_cents = model_cents - 2
        return max(1, min(99, patient_cents))
