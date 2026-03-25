"""
WeatherSafeClaw — main entry point.

Starts the scheduler and runs the trading loop per AGENTS.md Phase 1–7.
"""
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load .env before anything else so Settings() can read the vars.
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

from apscheduler.schedulers.blocking import BlockingScheduler

from agent.decision_engine import DecisionEngine
from agent.kalshi_client import KalshiClient
from agent.logger import get_logger
from agent.noaa_client import NOAAClient
from agent.notifier import Notifier
from agent.position_monitor import PositionMonitor
from agent.risk_manager import RiskManager
from config.settings import Settings

logger = get_logger("main")


def build_components(settings: Settings):
    kalshi = KalshiClient(settings)
    noaa = NOAAClient(settings)
    risk = RiskManager(settings)
    notifier = Notifier(settings)
    decision = DecisionEngine(kalshi, noaa, risk, notifier, settings)
    monitor = PositionMonitor(kalshi, noaa, risk, notifier, settings)
    return decision, monitor, notifier


def is_active_window(settings: Settings) -> bool:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    start = now.replace(
        hour=settings.active_window_start_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = now.replace(
        hour=settings.active_window_end_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    return start <= now <= end


def main() -> None:
    settings = Settings()
    decision, monitor, notifier = build_components(settings)

    scheduler = BlockingScheduler(timezone=settings.timezone)

    # Phase 1+2: data refresh + market scan every 30 min during active window
    def scan_job():
        if is_active_window(settings):
            try:
                decision.run_scan_cycle()
            except Exception as exc:
                logger.exception("Scan cycle error: %s", exc)

    # Phase 6: position monitoring every 15 min
    def monitor_job():
        try:
            monitor.run_monitor_cycle()
        except Exception as exc:
            logger.exception("Position monitor error: %s", exc)

    # Phase 7: daily wrap
    def daily_summary_job():
        try:
            decision.generate_and_send_daily_summary()
        except Exception as exc:
            logger.exception("Daily summary error: %s", exc)

    scheduler.add_job(scan_job, "interval", minutes=settings.market_scan_interval_minutes)
    scheduler.add_job(monitor_job, "interval", minutes=settings.position_monitor_interval_minutes)
    scheduler.add_job(
        daily_summary_job,
        "cron",
        hour=22,
        minute=5,
        timezone=settings.timezone,
    )

    def shutdown(signum, frame):
        logger.info("Shutdown signal received — stopping scheduler.")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("WeatherSafeClaw starting. Timezone: %s", settings.timezone)
    notifier.send("AGENT_STARTED", {"message": "WeatherSafeClaw is online."})

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("WeatherSafeClaw shut down cleanly.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
