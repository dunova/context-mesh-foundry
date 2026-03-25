#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HOME/.context_system/logs"
LOG_FILE="$LOG_DIR/onecontext_maintenance.log"

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" >/dev/null 2>&1 || true

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] context maintenance start"
  python3 "$SCRIPT_DIR/context_cli.py" maintain --repair-queue --enqueue-missing --max-enqueue 500
  echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] context maintenance done"
} >>"$LOG_FILE" 2>&1
