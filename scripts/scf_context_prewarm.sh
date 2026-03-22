#!/bin/bash
set -euo pipefail

QUERY="${1:-}"
MODE="${2:-all}"
LIMIT="${3:-20}"

if [ -z "$QUERY" ] || [ "$QUERY" = "-h" ] || [ "$QUERY" = "--help" ]; then
  cat <<USAGE
Usage: $(basename "$0") <query> [mode] [limit]

Run SCF context prewarm for GSD workflows:
  1) OneContext exact search (required)
  2) OpenViking health hint / semantic follow-up guidance

Examples:
  $(basename "$0") "phase discuss auth bug" all 20
  $(basename "$0") "CI flaky test" content 10
USAGE
  exit 0
fi

log() { echo "[scf-prewarm] $*"; }

OC_BIN=""
if [ -n "${ONECONTEXT_BIN:-}" ] && command -v "$ONECONTEXT_BIN" >/dev/null 2>&1; then
  OC_BIN="$ONECONTEXT_BIN"
elif command -v onecontext >/dev/null 2>&1; then
  OC_BIN="onecontext"
elif command -v aline >/dev/null 2>&1; then
  OC_BIN="aline"
fi

if [ -z "$OC_BIN" ]; then
  log "onecontext/aline not found; skipping exact search"
else
  log "running exact history search via $OC_BIN"
  set +e
  "$OC_BIN" search "$QUERY" -t "$MODE" -l "$LIMIT" --no-regex
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

[scf-prewarm] MCP semantic follow-up (run inside an MCP-capable AI terminal):
  1. search_onecontext_history(query, "all", 20, true)
  2. query_viking_memory(query, 5)
  3. 将有效结论写入 GSD phase 文档（CONTEXT/PLAN）

[scf-prewarm] Recommended query:
  "$QUERY"
HINT
