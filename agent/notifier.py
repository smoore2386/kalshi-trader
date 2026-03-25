"""
Notifier — sends structured messages to Telegram and/or Discord.

All notification failures are caught and logged; they never interrupt trading.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests

from config.settings import Settings

logger = logging.getLogger("notifier")

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


class Notifier:
    def __init__(self, settings: Settings) -> None:
        self._telegram_token = settings.telegram_bot_token
        self._telegram_chat_id = settings.telegram_chat_id
        self._discord_webhook = settings.discord_webhook_url
        self._http = requests.Session()

    def send(self, message_type: str, payload: dict) -> None:
        """Send a structured notification to all configured channels."""
        text = self._format(message_type, payload)
        if self._telegram_token and self._telegram_chat_id:
            self._send_telegram(text)
        if self._discord_webhook:
            self._send_discord(text)
        logger.info("[NOTIFY] %s: %s", message_type, json.dumps(payload, default=str)[:200])

    def _format(self, message_type: str, payload: dict) -> str:
        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines = [f"[WeatherSafeClaw] {message_type} @ {ts}"]
        for k, v in payload.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _send_telegram(self, text: str) -> None:
        url = _TELEGRAM_URL.format(token=self._telegram_token)
        try:
            resp = self._http.post(
                url,
                json={"chat_id": self._telegram_chat_id, "text": text[:4096]},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)

    def _send_discord(self, text: str) -> None:
        try:
            resp = self._http.post(
                self._discord_webhook,
                json={"content": text[:2000]},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Discord notification failed: %s", exc)
