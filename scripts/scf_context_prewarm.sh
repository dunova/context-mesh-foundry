#!/bin/bash
set -euo pipefail

QUERY="${1:-}"
MODE="${2:-all}"
LIMIT="${3:-20}"

if [ -z "$QUERY" ] || [ "$QUERY" = "-h" ] || [ "$QUERY" = "--help" ]; then
  cat <<USAGE
Usage: $(basename "$0") <query> [mode] [limit]

Run SCF context prewarm for GSD workflows:
  1) Unified CLI exact search (required)
  2) Unified CLI health hint / semantic follow-up guidance

Examples:
  $(basename "$0") "phase discuss auth bug" all 20
  $(basename "$0") "CI flaky test" content 10
USAGE
  exit 0
fi

log() { echo "[scf-prewarm] $*"; }

CLI_SCRIPT="$(cd "$(dirname "$0")" && pwd)/context_cli.py"
if [ ! -f "$CLI_SCRIPT" ]; then
  log "context_cli.py not found; skipping exact search"
else
  log "running exact history search via context_cli.py"
  set +e
  python3 "$CLI_SCRIPT" search "$QUERY" --type "$MODE" --limit "$LIMIT" --literal
  OC_RC=$?
  set -e
  if [ "$OC_RC" -ne 0 ]; then
    log "search exited with code $OC_RC"
  fi
fi

# Health check is a safer shell-level proxy than trying to call MCP from bash directly.
if [ -f "$(dirname "$0")/context_healthcheck.sh" ]; then
  log "running context healthcheck (quick)"
  bash "$(dirname "$0")/context_healthcheck.sh" --quiet || true
fi

cat <<HINT

[scf-prewarm] Unified CLI follow-up:
  1. python3 scripts/context_cli.py search "$QUERY" --type "$MODE" --limit "$LIMIT" --literal
  2. python3 scripts/context_cli.py semantic "$QUERY" --limit 5
  3. 将有效结论写入 GSD phase 文档（CONTEXT/PLAN）

[scf-prewarm] Recommended query:
  "$QUERY"
HINT
