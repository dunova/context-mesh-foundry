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
## Unified Smart Recall (enforced)

Use ContextGO only when it materially helps:
- cold start / new window
- continuation / handoff / project history
- new topic with low overlap to the current task
- structural questions: architecture, dependency, call graph, blast radius

Do NOT use ContextGO for:
- same-topic back-and-forth
- short acknowledgements
- pure rewrite / translation / chat

Execution order:
1. Exact identifiers, file paths, stack traces, function names:
   contextgo search "<query>" --limit 5 --literal
2. Continuations, history, broad task recall:
   contextgo semantic "<topic>" --limit 3
3. If a code graph is available and the task is structural, use graph first for
   structure and ContextGO second for historical decisions.
4. Summarize in 2-3 sentences. Never paste raw long output.
5. Prohibited pattern: exhaustive scan of ~/, /Volumes/*, or other large trees
   without narrowing scope first.
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
