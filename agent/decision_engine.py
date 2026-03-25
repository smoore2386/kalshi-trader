"""
Decision Engine — Phases 1–3 of the AGENTS.md trading loop.

Scans open Kalshi weather markets, runs the probability model,
and emits a prioritized list of trade opportunities.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from agent.kalshi_client import KalshiClient, KalshiAPIError
from agent.logger import get_logger, write_trade_log
from agent.noaa_client import NOAAClient
from agent.notifier import Notifier
from agent.probability_model import ProbabilityModel
from agent.risk_manager import RiskManager
from agent.order_engine import OrderEngine
from config.settings import Settings
from config.stations import STATIONS

logger = get_logger("decision_engine")

# Maps Kalshi market title keywords → (variable, station_id)
_MARKET_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"new york.*high|nyc.*high|knyc.*high", re.I), "temp_high_f", "KNYC"),
    (re.compile(r"new york.*low|nyc.*low|knyc.*low", re.I), "temp_low_f", "KNYC"),
    (re.compile(r"miami.*high|kmia.*high", re.I), "temp_high_f", "KMIA"),
    (re.compile(r"miami.*low|kmia.*low", re.I), "temp_low_f", "KMIA"),
    (re.compile(r"chicago.*high|kmdw.*high|kord.*high", re.I), "temp_high_f", "KMDW"),
    (re.compile(r"chicago.*low|kmdw.*low|kord.*low", re.I), "temp_low_f", "KMDW"),
    (re.compile(r"boston.*high|kbos.*high", re.I), "temp_high_f", "KBOS"),
    (re.compile(r"boston.*low|kbos.*low", re.I), "temp_low_f", "KBOS"),
    (re.compile(r"los angeles.*high|lax.*high|klax.*high", re.I), "temp_high_f", "KLAX"),
    (re.compile(r"dallas.*high|kdfw.*high", re.I), "temp_high_f", "KDFW"),
    (re.compile(r"seattle.*high|ksea.*high", re.I), "temp_high_f", "KSEA"),
    (re.compile(r"denver.*high|kden.*high", re.I), "temp_high_f", "KDEN"),
]


@dataclass
class TradeOpportunity:
    market_ticker: str
    contract_ticker: str
    station_id: str
    variable: str
    bin_label: str
    model_probability: float
    implied_probability: float
    edge: float
    pcs: int
    expected_value: float
    side: str   # "yes" or "no"
    close_time: str


class DecisionEngine:
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
        self._model = ProbabilityModel(settings)
        self._order_engine = OrderEngine(kalshi, risk, notifier, settings)

    # ── Main scan cycle ────────────────────────────────────────────────────────

    def run_scan_cycle(self) -> None:
        """Phase 1 + 2 + 3: refresh data, scan markets, gate and queue trades."""
        logger.info("=== Scan cycle started ===")

        # Phase 1: refresh NOAA data for all target stations
        for station_id in STATIONS:
            self._noaa.get_data_bundle(station_id)

        # Phase 2: market scan
        opportunities = self._scan_markets()
        logger.info("Found %d tradeable opportunities.", len(opportunities))

        for opp in opportunities:
            logger.info(
                "Opportunity: %s | PCS=%d edge=%.1f%% EV=%.3f",
                opp.contract_ticker, opp.pcs, opp.edge * 100, opp.expected_value,
            )

        # Phase 3: decision gate → submit orders
        if not opportunities:
            return

        balance = self._kalshi.get_balance()
        bankroll = balance.get("available", 0.0)
        positions = self._kalshi.get_positions()
        open_exposure = sum(
            p.get("value", 0.0) for p in positions
        )

        for opp in opportunities:
            size = self._risk.compute_size(opp.pcs, bankroll)
            decision = self._risk.check_trade(
                proposed_size_usd=size,
                bankroll_usd=bankroll,
                open_exposure_usd=open_exposure,
                pcs=opp.pcs,
                edge=opp.edge,
            )
            if decision.approved:
                self._order_engine.submit_opportunity(opp, decision.approved_size_usd)
                open_exposure += decision.approved_size_usd
            else:
                logger.info(
                    "Skipped %s: %s", opp.contract_ticker, "; ".join(decision.rejection_reasons)
                )

        logger.info("=== Scan cycle complete ===")

    # ── Market scanner ─────────────────────────────────────────────────────────

    def _scan_markets(self) -> list[TradeOpportunity]:
        try:
            markets = self._kalshi.list_weather_markets()
        except KalshiAPIError as exc:
            logger.error("Failed to list markets: %s", exc)
            return []

        opportunities: list[TradeOpportunity] = []
        now = datetime.now(timezone.utc)

        for market in markets:
            try:
                opps = self._evaluate_market(market, now)
                opportunities.extend(opps)
            except Exception as exc:
                logger.warning("Error evaluating market %s: %s", market.get("ticker"), exc)

        # Sort by expected value descending
        opportunities.sort(key=lambda o: o.expected_value, reverse=True)
        return opportunities

    def _evaluate_market(self, market: dict, now: datetime) -> list[TradeOpportunity]:
        title = market.get("title", "")
        ticker = market.get("ticker", "")

        # Identify station and variable
        station_id, variable = self._classify_market(title, ticker)
        if not station_id:
            return []

        # Check data freshness
        bundle = self._noaa._cache.get(station_id)
        if not bundle or bundle.is_stale:
            logger.debug("No fresh data for %s — skipping market %s", station_id, ticker)
            return []

        # Skip markets closing in < 10 minutes
        close_str = market.get("close_time", "")
        if close_str:
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                minutes_to_close = (close_dt - now).total_seconds() / 60
                if minutes_to_close < 10:
                    return []
            except ValueError:
                pass

        # Get full market detail with contracts
        try:
            detail = self._kalshi.get_market(ticker)
        except KalshiAPIError:
            return []

        contracts = detail.get("market", {}).get("contracts", []) or []
        if not contracts:
            return []

        # Extract bin edges and prices
        bin_edges: list[float] = []
        prices_cents: list[int] = []
        contract_tickers: list[str] = []

        for c in contracts:
            floor_strike = c.get("floor_strike")
            cap_strike = c.get("cap_strike")
            if floor_strike is not None:
                bin_edges.append(float(floor_strike))
            prices_cents.append(int(c.get("last_price", 50)))
            contract_tickers.append(c.get("ticker", ""))

        if not bin_edges:
            return []

        # Run probability model
        target_date = now.date()
        model_output = self._model.compute(
            station_id=station_id,
            variable=variable,
            target_date=target_date,
            bin_edges=sorted(set(bin_edges)),
            kalshi_prices_cents=prices_cents,
            bundle=bundle,
        )
        if not model_output:
            return []

        # Find eligible bins
        opps: list[TradeOpportunity] = []
        for i, bin_result in enumerate(model_output.bins):
            if bin_result.pcs < self._settings.min_pcs:
                continue
            if bin_result.edge < self._settings.min_edge:
                continue

            ev = bin_result.edge * (1 - bin_result.implied_probability)
            ct = contract_tickers[i] if i < len(contract_tickers) else ""

            opps.append(TradeOpportunity(
                market_ticker=ticker,
                contract_ticker=ct,
                station_id=station_id,
                variable=variable,
                bin_label=bin_result.label,
                model_probability=bin_result.model_probability,
                implied_probability=bin_result.implied_probability,
                edge=bin_result.edge,
                pcs=bin_result.pcs,
                expected_value=ev,
                side="yes",
                close_time=close_str,
            ))

        return opps

    @staticmethod
    def _classify_market(title: str, ticker: str) -> tuple[str | None, str | None]:
        text = f"{title} {ticker}"
        for pattern, variable, station_id in _MARKET_PATTERNS:
            if pattern.search(text):
                return station_id, variable
        return None, None

    # ── Daily summary ──────────────────────────────────────────────────────────

    def generate_and_send_daily_summary(self) -> None:
        summary = {
            "daily_pnl": self._risk.daily_pnl,
            "is_halted": self._risk.is_halted,
            "calibration": self._model.calibration_summary(),
        }
        logger.info("Daily summary: %s", summary)
        self._notifier.send("DAILY_SUMMARY", summary)
        self._risk.reset_daily_pnl()
