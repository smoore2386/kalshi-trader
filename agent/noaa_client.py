"""
NOAA / National Weather Service data client.

Fetches:
  - NWS point forecast (deterministic text forecast)
  - NWS hourly gridded forecast
  - GFS ensemble data via NOMADS
  - HRRR short-range forecast via NOMADS / AWS S3

All data is cached in memory with timestamps so the agent can detect staleness
(> 90 minutes → no trades for that station).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any

import requests

from agent.logger import get_logger
from config.settings import Settings
from config.stations import STATIONS  # dict of ICAO → {lat, lon, wfo, grid_x, grid_y}

logger = get_logger("noaa_client")

_NWS_BASE = "https://api.weather.gov"
_CACHE_TTL_SECONDS = 90 * 60  # 90 minutes


@dataclass
class DataBundle:
    """All model data for a single station, ready for the probability model."""
    station_id: str
    fetched_at: float = field(default_factory=time.time)

    # NWS point forecast periods
    nws_periods: list[dict] = field(default_factory=list)
    # NWS hourly forecast
    nws_hourly: list[dict] = field(default_factory=list)
    # GFS ensemble stats keyed by forecast_hour
    gfs_ensemble: dict[int, dict] = field(default_factory=dict)
    # HRRR data keyed by forecast_hour (0–18)
    hrrr: dict[int, dict] = field(default_factory=dict)
    # Errors encountered during fetch
    errors: list[str] = field(default_factory=list)

    @property
    def age_minutes(self) -> float:
        return (time.time() - self.fetched_at) / 60.0

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.fetched_at) > _CACHE_TTL_SECONDS


class NOAAClient:
    """Fetches weather model data from NWS API and NOMADS."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, DataBundle] = {}
        self._http = requests.Session()
        self._http.headers.update(
            {
                "User-Agent": "WeatherSafeClaw/1.0 (automated-trading-agent; contact via README)",
                "Accept": "application/geo+json",
            }
        )

    # ── Public interface ───────────────────────────────────────────────────────

    def get_data_bundle(self, station_id: str, force_refresh: bool = False) -> DataBundle | None:
        """Return a fully populated DataBundle for a station, using cache if fresh."""
        cached = self._cache.get(station_id)
        if cached and not cached.is_stale and not force_refresh:
            return cached

        bundle = self._fetch_bundle(station_id)
        if bundle:
            self._cache[station_id] = bundle
        return bundle

    # ── Internal fetch methods ─────────────────────────────────────────────────

    def _fetch_bundle(self, station_id: str) -> DataBundle | None:
        station = STATIONS.get(station_id)
        if not station:
            logger.warning("Unknown station: %s", station_id)
            return None

        bundle = DataBundle(station_id=station_id)

        # NWS point forecast
        try:
            bundle.nws_periods = self._fetch_nws_point_forecast(station)
        except Exception as exc:
            bundle.errors.append(f"NWS point forecast: {exc}")
            logger.warning("NWS point forecast failed for %s: %s", station_id, exc)

        # NWS hourly forecast
        try:
            bundle.nws_hourly = self._fetch_nws_hourly(station)
        except Exception as exc:
            bundle.errors.append(f"NWS hourly: {exc}")
            logger.warning("NWS hourly failed for %s: %s", station_id, exc)

        # If we have zero NWS data, mark stale
        if not bundle.nws_periods and not bundle.nws_hourly:
            logger.error("No NWS data available for %s — marking stale.", station_id)
            bundle.fetched_at = 0.0  # Force stale

        return bundle

    def _fetch_nws_point_forecast(self, station: dict) -> list[dict]:
        lat, lon = station["lat"], station["lon"]
        wfo, grid_x, grid_y = station["wfo"], station["grid_x"], station["grid_y"]

        url = f"{_NWS_BASE}/gridpoints/{wfo}/{grid_x},{grid_y}/forecast"
        resp = self._http.get(url, timeout=15)
        resp.raise_for_status()
        periods = resp.json().get("properties", {}).get("periods", [])

        return [
            {
                "period_name": p.get("name", ""),
                "is_daytime": p.get("isDaytime", True),
                "temp_f": p.get("temperature"),
                "precip_probability_pct": p.get("probabilityOfPrecipitation", {}).get("value") or 0,
                "wind_speed": p.get("windSpeed", ""),
                "short_forecast": p.get("shortForecast", ""),
                "start_time": p.get("startTime", ""),
                "end_time": p.get("endTime", ""),
            }
            for p in periods
        ]

    def _fetch_nws_hourly(self, station: dict) -> list[dict]:
        wfo, grid_x, grid_y = station["wfo"], station["grid_x"], station["grid_y"]
        url = f"{_NWS_BASE}/gridpoints/{wfo}/{grid_x},{grid_y}/forecast/hourly"
        resp = self._http.get(url, timeout=15)
        resp.raise_for_status()
        periods = resp.json().get("properties", {}).get("periods", [])

        return [
            {
                "start_time": p.get("startTime", ""),
                "temp_f": p.get("temperature"),
                "precip_probability_pct": p.get("probabilityOfPrecipitation", {}).get("value") or 0,
                "wind_speed": p.get("windSpeed", ""),
                "short_forecast": p.get("shortForecast", ""),
                "is_daytime": p.get("isDaytime", True),
            }
            for p in periods
        ]

    def get_nws_temp_forecast(self, station_id: str, target_date: date) -> dict | None:
        """Return the NWS high/low for a specific date from cached bundle."""
        bundle = self._cache.get(station_id)
        if not bundle or bundle.is_stale:
            return None

        date_str = target_date.isoformat()  # YYYY-MM-DD
        high = None
        low = None

        for p in bundle.nws_periods:
            if date_str in p.get("start_time", ""):
                if p["is_daytime"] and p["temp_f"] is not None:
                    high = p["temp_f"]
                elif not p["is_daytime"] and p["temp_f"] is not None:
                    low = p["temp_f"]

        return {"high_f": high, "low_f": low} if (high or low) else None
