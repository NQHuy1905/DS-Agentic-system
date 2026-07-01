#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.dev.pids"
LOG_DIR="$SCRIPT_DIR/logs"
CLIENT_LOG="$LOG_DIR/client.log"
SERVER_LOG="$LOG_DIR/server.log"

[[ -f "$SCRIPT_DIR/client/.env" ]] || cp "$SCRIPT_DIR/client/.env.example" "$SCRIPT_DIR/client/.env"
[[ -f "$SCRIPT_DIR/server/.env" ]] || cp "$SCRIPT_DIR/server/.env.example" "$SCRIPT_DIR/server/.env"

start() {
  if [[ -f "$PID_FILE" ]]; then
    echo "Already running. Run './dev.sh stop' first."
    exit 1
  fi

  mkdir -p "$LOG_DIR"

  echo "Starting client (Vite)..."
  cd "$SCRIPT_DIR/client"
  setsid npm run dev > "$CLIENT_LOG" 2>&1 &
  echo $! >> "$PID_FILE"

  echo "Starting server (FastAPI)..."
  cd "$SCRIPT_DIR/server"
  # --no-capture-output + python -u: stream logs live to server.log instead of
  # buffering them inside `conda run` until the process exits.
  setsid conda run --no-capture-output -n research python3 -u run.py > "$SERVER_LOG" 2>&1 &
  echo $! >> "$PID_FILE"

  echo "Client log : $CLIENT_LOG"
  echo "Server log : $SERVER_LOG"
  echo "PIDs       : $(paste -sd' ' "$PID_FILE")"
  echo "Run './dev.sh stop' to shut down."
}

stop_pid() {
  local pid=$1
  if kill -0 -- "-$pid" 2>/dev/null; then
    kill -- "-$pid" 2>/dev/null || true
    sleep 1
    kill -9 -- "-$pid" 2>/dev/null || true
    echo "Stopped process group $pid"
  else
    echo "PG $pid already gone"
  fi
}

stop() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "No .dev.pids file found. Servers may not be running."
    exit 1
  fi

  while IFS= read -r pid; do
    stop_pid "$pid"
  done < "$PID_FILE"

  pkill -f "$SCRIPT_DIR/client/node_modules/.bin/vite" 2>/dev/null || true

  rm -f "$PID_FILE"
  echo "All stopped."
}

case "${1:-}" in
  start) start ;;
  stop)  stop  ;;
  *)
    echo "Usage: $0 {start|stop}"
    exit 1
    ;;
esac
