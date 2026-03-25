"""
Kalshi REST API client.

Authentication uses RSA key-based signing as required by Kalshi's trading API.
Credentials are loaded exclusively from environment variables — never hardcoded.

Reference: https://trading-api.kalshi.com/trade-api/v2
"""
import base64
import hashlib
import time
import uuid
from typing import Any
from urllib.parse import urljoin

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from agent.logger import get_logger
from config.settings import Settings

logger = get_logger("kalshi_client")

_BASE_URL = "https://trading-api.kalshi.com/trade-api/v2/"


class KalshiAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"Kalshi API {status_code}: {message}")
        self.status_code = status_code


class KalshiClient:
    """Thin wrapper around the Kalshi REST API with RSA-signed auth."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.kalshi_api_key
        self._private_key = serialization.load_pem_private_key(
            settings.kalshi_private_key_pem.encode(),
            password=None,
        )
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ── Auth helpers ───────────────────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """Return Kalshi-required auth headers for an RSA-signed request."""
        timestamp_ms = str(int(time.time() * 1000))
        msg = f"{timestamp_ms}{method.upper()}{path}{body}"
        signature = self._private_key.sign(
            msg.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = urljoin(_BASE_URL, path.lstrip("/"))
        headers = self._sign("GET", "/" + path.lstrip("/"))
        resp = self._session.get(url, headers=headers, params=params, timeout=10)
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, body: dict) -> Any:
        import json
        url = urljoin(_BASE_URL, path.lstrip("/"))
        body_str = json.dumps(body, separators=(",", ":"))
        headers = self._sign("POST", "/" + path.lstrip("/"), body_str)
        resp = self._session.post(url, headers=headers, data=body_str, timeout=10)
        self._raise_for_status(resp)
        return resp.json()

    def _delete(self, path: str) -> Any:
        url = urljoin(_BASE_URL, path.lstrip("/"))
        headers = self._sign("DELETE", "/" + path.lstrip("/"))
        resp = self._session.delete(url, headers=headers, timeout=10)
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        if not resp.ok:
            raise KalshiAPIError(resp.status_code, resp.text[:500])

    # ── Account ────────────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        return self._get("portfolio/balance")

    def get_positions(self) -> list[dict]:
        data = self._get("portfolio/positions")
        return data.get("market_positions", [])

    # ── Markets ────────────────────────────────────────────────────────────────

    def list_weather_markets(self, limit: int = 200) -> list[dict]:
        data = self._get(
            "markets",
            params={"status": "open", "series_ticker": "", "limit": limit},
        )
        markets = data.get("markets", [])
        # Filter to weather categories only
        return [
            m for m in markets
            if "weather" in m.get("category", "").lower()
            or "temperature" in m.get("title", "").lower()
            or "precip" in m.get("title", "").lower()
            or "snow" in m.get("title", "").lower()
        ]

    def get_market(self, market_ticker: str) -> dict:
        return self._get(f"markets/{market_ticker}")

    def get_orderbook(self, ticker: str, depth: int = 5) -> dict:
        return self._get(f"markets/{ticker}/orderbook", params={"depth": depth})

    # ── Orders ─────────────────────────────────────────────────────────────────

    def submit_limit_order(
        self,
        ticker: str,
        side: str,
        count: int,
        limit_price_cents: int,
        client_order_id: str | None = None,
    ) -> dict:
        """
        Submit a YES or NO limit order.

        :param ticker: Contract ticker (specific bin)
        :param side: "yes" or "no"
        :param count: Number of contracts (each contract = $1 max payout)
        :param limit_price_cents: Price in cents (1–99)
        :param client_order_id: Idempotency key; auto-generated if None
        """
        if not (1 <= limit_price_cents <= 99):
            raise ValueError(f"Invalid limit price: {limit_price_cents} cents")
        if side not in ("yes", "no"):
            raise ValueError(f"Invalid side: {side}")
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": count,
            "type": "limit",
            "yes_price": limit_price_cents if side == "yes" else 100 - limit_price_cents,
            "no_price": limit_price_cents if side == "no" else 100 - limit_price_cents,
            "client_order_id": client_order_id or str(uuid.uuid4()),
        }
        logger.info(
            "Submitting limit order: ticker=%s side=%s count=%d price=%d¢",
            ticker, side, count, limit_price_cents,
        )
        return self._post("portfolio/orders", body)

    def cancel_order(self, order_id: str) -> dict:
        logger.info("Cancelling order: %s", order_id)
        return self._delete(f"portfolio/orders/{order_id}")

    def get_order(self, order_id: str) -> dict:
        return self._get(f"portfolio/orders/{order_id}")
