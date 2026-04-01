#!/usr/bin/env bash
# start_memory_viewer.sh -- launch the ContextGO memory viewer server.
#
# Usage: start_memory_viewer.sh [--help]
#
# Starts context_cli serve and blocks until interrupted (SIGINT/SIGTERM).
# Use CONTEXTGO_VIEWER_TOKEN when binding to a non-loopback address.
#
# Exit codes:
#   0  Server exited cleanly (e.g. via SIGINT).
#   1  context_cli.py not found, or the server process failed.
#
# Environment variables:
#   CONTEXTGO_VIEWER_HOST   Bind address (default: 127.0.0.1)
#   CONTEXTGO_VIEWER_PORT   TCP port      (default: 37677)
#   CONTEXTGO_VIEWER_TOKEN  Auth token; required when host is not loopback.
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [--help]

Launch the ContextGO memory viewer via context_cli serve.

Environment variables:
  CONTEXTGO_VIEWER_HOST   Bind address (default: 127.0.0.1)
  CONTEXTGO_VIEWER_PORT   TCP port      (default: 37677)
  CONTEXTGO_VIEWER_TOKEN  Auth token; required for non-loopback hosts.
EOF
    exit 0
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
HOST="${CONTEXTGO_VIEWER_HOST:-127.0.0.1}"
readonly HOST
PORT="${CONTEXTGO_VIEWER_PORT:-37677}"
readonly PORT
TOKEN="${CONTEXTGO_VIEWER_TOKEN:-}"
readonly TOKEN

if command -v contextgo >/dev/null 2>&1; then
    CMD=(contextgo serve --host "$HOST" --port "$PORT")
elif [ -f "$SCRIPT_DIR/../src/contextgo/context_cli.py" ]; then
    CMD=(python3 "$SCRIPT_DIR/../src/contextgo/context_cli.py" serve --host "$HOST" --port "$PORT")
else
    printf 'ERROR: contextgo command not found and context_cli.py not found at %s/../src/contextgo/context_cli.py\n' "$SCRIPT_DIR" >&2
    exit 1
fi

if [ -n "$TOKEN" ]; then
    CMD+=("--token" "$TOKEN")
fi

printf 'Launching ContextGO Viewer on %s:%s\n' "$HOST" "$PORT"
exec "${CMD[@]}"
