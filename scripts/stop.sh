#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/.streamlit.pid"
LOG_FILE="$ROOT_DIR/.streamlit.log"
PORT="${PORT:-8181}"

stop_pid() {
  local pid="$1"
  if [ -z "$pid" ]; then
    return 0
  fi
  if kill -0 "$pid" >/dev/null 2>&1; then
    echo "Stopping process $pid..."
    kill "$pid" || true
    for _ in {1..20}; do
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        return 0
      fi
      sleep 0.2
    done
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "Process $pid still running; forcing stop..."
      kill -9 "$pid" || true
    fi
  fi
}

if [ -f "$PID_FILE" ]; then
  stop_pid "$(cat "$PID_FILE")"
  rm -f "$PID_FILE"
else
  echo "No PID file found."
fi

if command -v lsof >/dev/null 2>&1; then
  stale_pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$stale_pids" ]; then
    echo "Stopping stale listeners on port $PORT: $stale_pids"
    for stale_pid in $stale_pids; do
      stop_pid "$stale_pid"
    done
  fi
fi

echo "Log file: $LOG_FILE"
