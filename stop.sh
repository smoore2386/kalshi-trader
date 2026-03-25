#!/usr/bin/env bash
# stop.sh — Stop the running WeatherSafeClaw agent gracefully.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .agent.pid ]; then
  echo "No .agent.pid found — is the agent running?"
  exit 1
fi

PID=$(cat .agent.pid)
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  rm .agent.pid
  echo "Agent (PID $PID) stopped."
else
  echo "Process $PID not found — already stopped."
  rm -f .agent.pid
fi
