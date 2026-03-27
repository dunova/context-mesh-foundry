#!/usr/bin/env bash
# apply_context_first_policy.sh -- inject the SCF context-first policy block
# into agent entry-point files (AGENTS.md / CLAUDE.md).
#
# Usage: apply_context_first_policy.sh [--help]
#
# Idempotent: removes any existing SCF block before appending the current one.
# Missing target files are silently skipped (not an error).
#
# Exit codes:
#   0  Policy applied (or skipped for absent files) successfully.
#   1  Unexpected error (e.g. mktemp failure).
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [--help]

Inject the SCF context-first policy block into agent entry-point files.
Currently targets:
  ~/.codex/AGENTS.md
  ~/.claude/CLAUDE.md
  ~/.openclaw/workspace/AGENTS.md

Idempotent -- safe to run repeatedly.
EOF
    exit 0
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
fi

log() { printf '[context-first] %s\n' "$*"; }

readonly START_MARK="<!-- SCF:CONTEXT-FIRST:START -->"
readonly END_MARK="<!-- SCF:CONTEXT-FIRST:END -->"

read -r -d '' POLICY_BLOCK <<'EOF' || true
<!-- SCF:CONTEXT-FIRST:START -->
## Unified Context First (enforced)

When a task involves existing codebase optimization/debugging, historical
decision lookup, cross-terminal handoff, or quantitative system location,
run a context prewarm BEFORE any directory scan.

Execution order (hard constraint):
1. Run the built-in exact-match search at least once:
   python3 /path/to/ContextGO/scripts/context_cli.py search "<query>" --limit 20 --literal
2. If no hits, optionally follow up with semantic search:
   python3 /path/to/ContextGO/scripts/context_cli.py semantic "<query>" --limit 5
3. Narrow scope based on results BEFORE running ls / rg on large directories.
4. Prohibited pattern: exhaustive scan of ~/, /Volumes/*, or other large trees
   without a prior context prewarm.

Task start checklist:
- [ ] context_cli exact search executed
- [ ] Hits recorded, or "no hits" noted
- [ ] Scan scope constrained by context results
<!-- SCF:CONTEXT-FIRST:END -->
EOF

strip_old_block() {
    local file="$1"
    awk -v s="$START_MARK" -v e="$END_MARK" '
        BEGIN { skip=0 }
        index($0, s) { skip=1; next }
        index($0, e) { skip=0; next }
        skip==0 { print }
    ' "$file"
}

ensure_policy() {
    local file="$1"
    if [ ! -f "$file" ]; then
        log "skip (missing): $file"
        return 0
    fi

    local tmp
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    strip_old_block "$file" > "$tmp"
    printf '\n%s\n' "$POLICY_BLOCK" >> "$tmp"
    mv "$tmp" "$file"
    trap - EXIT
    log "patched: $file"
}

FILES=(
    "$HOME/.codex/AGENTS.md"
    "$HOME/.claude/CLAUDE.md"
    "$HOME/.openclaw/workspace/AGENTS.md"
)

for f in "${FILES[@]}"; do
    ensure_policy "$f"
done

log "done"
