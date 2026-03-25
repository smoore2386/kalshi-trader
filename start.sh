#!/usr/bin/env bash
# start.sh — Launch WeatherSafeClaw in the background.
# Usage: ./start.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Discord is handled natively by OpenClaw — no webhook URL required here.

# Ensure venv exists
if [ ! -f ".venv/bin/python3" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

mkdir -p logs/trades logs/daily logs/errors

echo "Starting WeatherSafeClaw..."
nohup .venv/bin/python3 -m agent.main \
  >> logs/errors/agent.log 2>&1 &

PID=$!
echo "$PID" > .agent.pid
echo "Agent started — PID $PID"
echo "Logs: logs/errors/agent.log"
echo "Stop with: kill \$(cat .agent.pid)"
