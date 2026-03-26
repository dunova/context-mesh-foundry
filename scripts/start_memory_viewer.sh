#!/usr/bin/env bash
# start_memory_viewer.sh -- launch the ContextGO memory viewer server.
#
# Usage: start_memory_viewer.sh [--help]
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
HOST="${CONTEXTGO_VIEWER_HOST:-127.0.0.1}"
PORT="${CONTEXTGO_VIEWER_PORT:-37677}"
TOKEN="${CONTEXTGO_VIEWER_TOKEN:-}"

if [ ! -f "$SCRIPT_DIR/context_cli.py" ]; then
    printf 'ERROR: context_cli.py not found in %s\n' "$SCRIPT_DIR" >&2
    exit 1
fi

CMD=(python3 "$SCRIPT_DIR/context_cli.py" serve --host "$HOST" --port "$PORT")
if [ -n "$TOKEN" ]; then
    CMD+=("--token" "$TOKEN")
fi

printf 'Launching ContextGO Viewer on %s:%s\n' "$HOST" "$PORT"
exec "${CMD[@]}"
