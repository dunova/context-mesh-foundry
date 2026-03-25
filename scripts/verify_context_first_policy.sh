#!/bin/bash
set -euo pipefail

ok() { echo "[verify] OK  - $*"; }
fail() { echo "[verify] FAIL - $*"; exit 1; }

require_text() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if [ ! -f "$file" ]; then
    fail "$label: missing file $file"
  fi
  if rg -n --fixed-strings "$pattern" "$file" >/dev/null 2>&1; then
    ok "$label"
  else
    fail "$label: pattern not found ($pattern) in $file"
  fi
}

require_text "$HOME/.codex/AGENTS.md" "SCF:CONTEXT-FIRST:START" "codex 入口已注入 context-first"
require_text "$HOME/.claude/CLAUDE.md" "SCF:CONTEXT-FIRST:START" "claude 入口已注入 context-first"
require_text "$HOME/.openclaw/workspace/AGENTS.md" "SCF:CONTEXT-FIRST:START" "openclaw 入口已注入 context-first"

require_text "$HOME/.codex/skills/openviking-memory-sync/SKILL.md" "默认触发（不限 GSD）" "codex openviking 技能有非 GSD 默认触发"
require_text "$HOME/.claude/skills/openviking-memory-sync/SKILL.md" "默认触发（不限 GSD）" "claude openviking 技能有非 GSD 默认触发"

require_text "$HOME/.codex/skills/gsd-v1/SKILL.md" "GSD 关闭时的兜底规则（新增）" "codex gsd 技能有关闭兜底"
require_text "$HOME/.claude/skills/gsd-v1/SKILL.md" "GSD 关闭时的兜底规则（新增）" "claude gsd 技能有关闭兜底"

if [ -f "/Volumes/AI/GitHub/context-mesh-foundry/scripts/context_cli.py" ]; then
  ok "context_cli 入口可用"
else
  fail "context_cli 入口不可用"
fi

ok "context-first policy regression checks passed"
