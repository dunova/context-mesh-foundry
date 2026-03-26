#!/usr/bin/env bash
# scf_context_prewarm.sh -- SCF context prewarm for GSD workflows.
#
# Usage: scf_context_prewarm.sh <query> [mode] [limit]
#
# Runs an exact history search via context_cli.py, then a quick health check.
# Results are printed to stdout for the caller to review.
#
# Exit codes:
#   0  Prewarm completed (non-zero search exit is non-fatal and also exits 0).
#   1  Required argument missing (shows usage).
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") <query> [mode] [limit]

Run SCF context prewarm for GSD workflows:
  1. Exact history search via context_cli.py (required step)
  2. Quick health check as a post-prewarm signal

Arguments:
  query   Search term (required)
  mode    Source type filter: all | content | session | ...  (default: all)
  limit   Maximum results to return                          (default: 20)

Examples:
  $(basename "$0") "phase discuss auth bug" all 20
  $(basename "$0") "CI flaky test" content 10
EOF
    exit 0
}

QUERY="${1:-}"
MODE="${2:-all}"
LIMIT="${3:-20}"

if [ -z "$QUERY" ] || [ "$QUERY" = "-h" ] || [ "$QUERY" = "--help" ]; then
    usage
fi

log() { printf '[scf-prewarm] %s\n' "$*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
CLI_SCRIPT="$SCRIPT_DIR/context_cli.py"
readonly CLI_SCRIPT
HC_SCRIPT="$SCRIPT_DIR/context_healthcheck.sh"
readonly HC_SCRIPT

if [ ! -f "$CLI_SCRIPT" ]; then
    log "WARNING: context_cli.py not found at $CLI_SCRIPT; skipping exact search"
else
    log "running exact history search"
    set +e
    python3 "$CLI_SCRIPT" search "$QUERY" --type "$MODE" --limit "$LIMIT" --literal
    RC=$?
    set -e
    if [ "$RC" -ne 0 ]; then
        log "search exited with code $RC (non-fatal)"
    fi
fi

# Health check is a shell-level proxy; safer than invoking MCP from bash.
if [ -f "$HC_SCRIPT" ]; then
    log "running context healthcheck (quick)"
    bash "$HC_SCRIPT" --quiet || true
fi

printf '\n'
printf '[scf-prewarm] Recommended follow-up steps:\n'
printf '  1. python3 "%s/context_cli.py" search "%s" --type "%s" --limit "%s" --literal\n' \
    "$SCRIPT_DIR" "$QUERY" "$MODE" "$LIMIT"
printf '  2. python3 "%s/context_cli.py" semantic "%s" --limit 5\n' \
    "$SCRIPT_DIR" "$QUERY"
printf '  3. Record useful conclusions in the GSD phase document (CONTEXT/PLAN).\n'
printf '\n'
printf '[scf-prewarm] Query used: "%s"\n' "$QUERY"
