#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/.streamlit.pid"
LOG_FILE="$ROOT_DIR/.streamlit.log"
PORT="${PORT:-8181}"

cd "$ROOT_DIR"

if [ -f "$PID_FILE" ]; then
  existing_pid="$(cat "$PID_FILE")"
  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" >/dev/null 2>&1; then
    echo "Dashboard is already running."
    echo "URL: http://127.0.0.1:$PORT"
    echo "PID: $existing_pid"
    echo "Logs: $LOG_FILE"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if [ ! -d .venv ]; then
  echo ".venv not found; running ./scripts/init.sh first..."
  ./scripts/init.sh
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ -z "${POLYGON_API_KEY:-}" ] && ! grep -q "^[[:space:]]*POLYGON_API_KEY[[:space:]]*=" .streamlit/secrets.toml 2>/dev/null; then
  echo "Warning: POLYGON_API_KEY is not set. You can paste the key in the dashboard sidebar." >&2
fi

echo "Starting Gan Portfolio Dashboard on port $PORT..."
nohup streamlit run app.py --server.port "$PORT" --server.address 127.0.0.1 >"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 2
if kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1; then
  echo "Started."
  echo "URL: http://127.0.0.1:$PORT"
  echo "PID: $(cat "$PID_FILE")"
  echo "Logs: $LOG_FILE"
else
  echo "Failed to start. Recent logs:" >&2
  tail -n 80 "$LOG_FILE" >&2 || true
  exit 1
fi
