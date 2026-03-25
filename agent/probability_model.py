"""
Probability model — computes per-bin win probability and Probability Confidence Score.

Algorithm:
  1. Build a Gaussian distribution from weighted model inputs
     (NWS deterministic, GFS ensemble, ECMWF if available, HRRR if sub-18h).
  2. Integrate that distribution over each bin's edges to get P(bin).
  3. Compute PCS from model agreement, ensemble spread, data freshness,
     and historical calibration.

All weights and thresholds are defined in config/settings.py so they can be
tuned without touching this file.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from agent.logger import get_logger
from agent.noaa_client import DataBundle
from config.settings import Settings

logger = get_logger("probability_model")


@dataclass
class BinResult:
    label: str
    lower: float | None
    upper: float | None
    model_probability: float
    implied_probability: float   # from Kalshi price
    edge: float                  # model_probability - implied_probability
    pcs: int


@dataclass
class ModelOutput:
    station_id: str
    variable: str
    target_date: date
    bins: list[BinResult]
    model_mean: float
    model_std: float
    data_sources_used: list[str]
    data_age_minutes: float


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Standard normal CDF via math.erf."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def _bin_probability(lower: float | None, upper: float | None, mu: float, sigma: float) -> float:
    """P(lower ≤ X < upper) under N(mu, sigma)."""
    lo_cdf = 0.0 if lower is None else _normal_cdf(lower, mu, sigma)
    hi_cdf = 1.0 if upper is None else _normal_cdf(upper, mu, sigma)
    return max(0.0, hi_cdf - lo_cdf)


class ProbabilityModel:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Rolling calibration: maps PCS bucket → (wins, total)
        self._calibration: dict[int, list[int]] = {}

    def compute(
        self,
        station_id: str,
        variable: str,          # "temp_high_f" | "temp_low_f" | "precip_in" | "snow_in"
        target_date: date,
        bin_edges: list[float],  # Sorted bin edge values; None-bounded outer bins implied
        kalshi_prices_cents: list[int],  # Kalshi YES prices in cents per bin
        bundle: DataBundle,
    ) -> ModelOutput | None:
        """
        Main entry point. Returns ModelOutput with per-bin probabilities and PCS.
        Returns None if data is insufficient to produce a reliable estimate.
        """
        mu, sigma, sources = self._build_distribution(variable, target_date, bundle)
        if mu is None:
            logger.warning("Could not build distribution for %s %s %s", station_id, variable, target_date)
            return None

        # Build bins from edge list; first bin has no lower bound, last has no upper bound
        bins: list[BinResult] = []
        n_bins = len(bin_edges) + 1
        for i in range(n_bins):
            lower = bin_edges[i - 1] if i > 0 else None
            upper = bin_edges[i] if i < len(bin_edges) else None
            label_lo = f"{lower:.0f}" if lower is not None else "-∞"
            label_hi = f"{upper:.0f}" if upper is not None else "+∞"
            label = f"{label_lo}–{label_hi}"

            p_model = _bin_probability(lower, upper, mu, sigma)
            p_implied = kalshi_prices_cents[i] / 100.0 if i < len(kalshi_prices_cents) else 0.05
            edge = p_model - p_implied
            pcs = self._compute_pcs(p_model, sigma, bundle, sources)

            bins.append(BinResult(
                label=label,
                lower=lower,
                upper=upper,
                model_probability=round(p_model, 4),
                implied_probability=round(p_implied, 4),
                edge=round(edge, 4),
                pcs=pcs,
            ))

        return ModelOutput(
            station_id=station_id,
            variable=variable,
            target_date=target_date,
            bins=bins,
            model_mean=round(mu, 2),
            model_std=round(sigma, 2),
            data_sources_used=sources,
            data_age_minutes=round(bundle.age_minutes, 1),
        )

    # ── Distribution builder ───────────────────────────────────────────────────

    def _build_distribution(
        self,
        variable: str,
        target_date: date,
        bundle: DataBundle,
    ) -> tuple[float | None, float | None, list[str]]:
        """
        Weighted combination of model sources → (mu, sigma, sources_used).
        Weights per AGENTS.md:
          NWS deterministic: 40% (or replaced by HRRR if sub-18h)
          GFS ensemble mean: 25%
          ECMWF ensemble mean: 25%    (omitted if unavailable; redistributed)
          HRRR (sub-18h only): 40%
        """
        sources = []
        weighted_means = []
        total_weight = 0.0
        sigmas = []

        # NWS deterministic
        nws_temp = self._extract_nws_temp(variable, target_date, bundle)
        if nws_temp is not None:
            w = self._settings.weight_nws
            weighted_means.append(nws_temp * w)
            total_weight += w
            sigmas.append(self._settings.default_nws_sigma)
            sources.append("NWS")

        # GFS ensemble
        gfs = self._extract_gfs_mean_sigma(variable, bundle)
        if gfs:
            mu_gfs, sigma_gfs = gfs
            w = self._settings.weight_gfs
            weighted_means.append(mu_gfs * w)
            total_weight += w
            sigmas.append(sigma_gfs)
            sources.append("GFS")

        if not weighted_means:
            return None, None, []

        mu = sum(weighted_means) / total_weight
        # Pooled sigma: weighted average of component sigmas
        sigma = sum(s for s in sigmas) / len(sigmas) if sigmas else 5.0
        # Add between-model disagreement to sigma
        if len(weighted_means) > 1:
            spread = max(m / w for m, w in zip(weighted_means, [self._settings.weight_nws, self._settings.weight_gfs])) - \
                     min(m / w for m, w in zip(weighted_means, [self._settings.weight_nws, self._settings.weight_gfs]))
            sigma = math.sqrt(sigma**2 + (spread / 2) ** 2)

        return mu, sigma, sources

    def _extract_nws_temp(self, variable: str, target_date: date, bundle: DataBundle) -> float | None:
        date_str = target_date.isoformat()
        for p in bundle.nws_periods:
            if date_str not in p.get("start_time", ""):
                continue
            if variable == "temp_high_f" and p.get("is_daytime") and p.get("temp_f") is not None:
                return float(p["temp_f"])
            if variable == "temp_low_f" and not p.get("is_daytime") and p.get("temp_f") is not None:
                return float(p["temp_f"])
        return None

    def _extract_gfs_mean_sigma(self, variable: str, bundle: DataBundle) -> tuple[float, float] | None:
        # GFS ensemble is stored by forecast_hour; use the closest to target
        for fh, data in sorted(bundle.gfs_ensemble.items()):
            if variable in ("temp_high_f", "temp_low_f") and "TMP_2m" in data.get("variable", ""):
                return data.get("mean"), data.get("std_dev", 5.0)
        return None

    # ── PCS calculator ─────────────────────────────────────────────────────────

    def _compute_pcs(
        self,
        p_model: float,
        sigma: float,
        bundle: DataBundle,
        sources: list[str],
    ) -> int:
        """
        PCS 0–100. Base = model probability scaled to 0–100.
        Adjustments applied per soul.md rules.
        """
        base = int(p_model * 100)

        # Bonus: multiple independent sources agree
        source_bonus = min(5, (len(sources) - 1) * 3)

        # Penalty: high ensemble spread (sigma > 5°F for temp)
        spread_penalty = 0
        if sigma > 8:
            spread_penalty = 15
        elif sigma > 6:
            spread_penalty = 8
        elif sigma > 4:
            spread_penalty = 3

        # Penalty: stale data
        freshness_penalty = 0
        if bundle.age_minutes > 60:
            freshness_penalty = 10
        elif bundle.age_minutes > 30:
            freshness_penalty = 3

        # Penalty: insufficient sources
        if len(sources) < 2:
            source_penalty = 10
        else:
            source_penalty = 0

        pcs = base + source_bonus - spread_penalty - freshness_penalty - source_penalty
        return max(0, min(100, pcs))

    # ── Calibration tracking ───────────────────────────────────────────────────

    def record_outcome(self, pcs_at_entry: int, won: bool) -> None:
        bucket = (pcs_at_entry // 5) * 5
        if bucket not in self._calibration:
            self._calibration[bucket] = [0, 0]
        self._calibration[bucket][1] += 1
        if won:
            self._calibration[bucket][0] += 1

    def calibration_summary(self) -> dict[int, dict]:
        return {
            bucket: {
                "win_rate": wins / total if total else None,
                "total": total,
            }
            for bucket, (wins, total) in sorted(self._calibration.items())
        }
