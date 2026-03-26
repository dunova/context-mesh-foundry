#!/usr/bin/env bash
# run_contextgo_maintenance.sh -- run ContextGO index maintenance tasks.
#
# Usage: run_contextgo_maintenance.sh [--help]
#
# Runs repair-queue and enqueue-missing against the local ContextGO index.
# All output is appended to the maintenance log file.
#
# Exit codes:
#   0  Maintenance completed successfully.
#   1  context_cli.py not found, or the maintenance command failed.
#
# Environment variables:
#   CONTEXTGO_STORAGE_ROOT    Storage root directory (default: ~/.contextgo)
#   CONTEXTGO_MAINTENANCE_LOG Override log file path.
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [--help]

Run ContextGO index maintenance: repair queue, enqueue missing entries.

Environment variables:
  CONTEXTGO_STORAGE_ROOT    Storage root (default: ~/.contextgo)
  CONTEXTGO_MAINTENANCE_LOG Log file path override.
EOF
    exit 0
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
LOG_DIR="${CONTEXTGO_STORAGE_ROOT:-$HOME/.contextgo}/logs"
readonly LOG_DIR
LOG_FILE="${CONTEXTGO_MAINTENANCE_LOG:-$LOG_DIR/contextgo_maintenance.log}"
readonly LOG_FILE
SERVICE_LABEL="ContextGO maintenance"
readonly SERVICE_LABEL

log_line() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf '[%s] %s %s\n' "$ts" "$SERVICE_LABEL" "$1"
}

if [ ! -f "$SCRIPT_DIR/context_cli.py" ]; then
    printf 'ERROR: context_cli.py not found in %s\n' "$SCRIPT_DIR" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true

{
    log_line "start"
    python3 "$SCRIPT_DIR/context_cli.py" maintain \
        --repair-queue \
        --enqueue-missing \
        --max-enqueue 500
    log_line "done"
} >> "$LOG_FILE" 2>&1
