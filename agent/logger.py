"""
Structured logger — writes JSON trade records to logs/.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


_LOG_DIR = Path(__file__).parent.parent / "logs"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"weathersafeclaw.{name}")


def write_trade_log(record: dict) -> None:
    """Append a structured trade event to today's log file."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = _LOG_DIR / "trades" / f"{today}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    record.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
