#!/usr/bin/env bash
# verify_context_first_policy.sh -- regression checks for the SCF
# context-first policy and related skill files.
#
# Usage: verify_context_first_policy.sh [--help]
#
# Verifies that apply_context_first_policy.sh has been run and that all
# expected agent entry-point files and skill markers are present and correct.
#
# Exit codes:
#   0  All checks passed.
#   1  One or more checks failed (first failure exits immediately).
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [--help]

Verify that the SCF context-first policy has been applied to all expected
agent entry-point files and that required skill markers are present.

Exits 0 when all checks pass, 1 on the first failure.
EOF
    exit 0
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
fi

ok()   { printf '[verify] OK   - %s\n' "$*"; }
fail() { printf '[verify] FAIL - %s\n' "$*" >&2; exit 1; }

require_text() {
    local file="$1"
    local pattern="$2"
    local label="$3"

    if [ ! -f "$file" ]; then
        fail "$label: file not found: $file"
    fi
    if grep -qF "$pattern" "$file" 2>/dev/null; then
        ok "$label"
    else
        fail "$label: pattern not found ('$pattern') in $file"
    fi
}

# Locate context_cli.py relative to this script (src/contextgo/), with a
# fallback to the installed `contextgo` command (avoids hard-coding paths).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
CLI_SCRIPT="$SCRIPT_DIR/../src/contextgo/context_cli.py"
readonly CLI_SCRIPT

# Policy injection checks
require_text "$HOME/.codex/AGENTS.md"              "SCF:CONTEXT-FIRST:START" "codex entry has context-first policy"
require_text "$HOME/.claude/CLAUDE.md"             "SCF:CONTEXT-FIRST:START" "claude entry has context-first policy"
require_text "$HOME/.openclaw/workspace/AGENTS.md" "SCF:CONTEXT-FIRST:START" "openclaw entry has context-first policy"

# Skill marker checks
require_text "$HOME/.codex/skills/gsd-v1/SKILL.md"  "GSD fallback rules" "codex gsd skill has fallback rules"
require_text "$HOME/.claude/skills/gsd-v1/SKILL.md" "GSD fallback rules" "claude gsd skill has fallback rules"

# CLI availability: prefer installed command, fall back to source tree path
if command -v contextgo >/dev/null 2>&1; then
    ok "context_cli entrypoint available: $(command -v contextgo)"
elif [ -f "$CLI_SCRIPT" ]; then
    ok "context_cli entrypoint available: $CLI_SCRIPT"
else
    fail "context_cli entrypoint not found: neither 'contextgo' command nor $CLI_SCRIPT"
fi

ok "context-first policy regression checks passed"
