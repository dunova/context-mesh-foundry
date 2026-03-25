#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${CONTEXTGO_STORAGE_ROOT:-$HOME/.contextgo}/logs"
LOG_FILE="${CONTEXTGO_MAINTENANCE_LOG:-$LOG_DIR/contextgo_maintenance.log}"
SERVICE_LABEL="ContextGO maintenance"

log_line() {
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S %z')"
  printf "[%s] %s %s\n" "$ts" "$SERVICE_LABEL" "$1"
}

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" >/dev/null 2>&1 || true

{
  log_line "start"
  python3 "$SCRIPT_DIR/context_cli.py" maintain --repair-queue --enqueue-missing --max-enqueue 500
  log_line "done"
} >>"$LOG_FILE" 2>&1
