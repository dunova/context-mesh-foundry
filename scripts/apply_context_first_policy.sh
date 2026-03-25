#!/bin/bash
set -euo pipefail

log() { echo "[context-first] $*"; }

START_MARK="<!-- SCF:CONTEXT-FIRST:START -->"
END_MARK="<!-- SCF:CONTEXT-FIRST:END -->"

read -r -d '' POLICY_BLOCK <<'EOF' || true
<!-- SCF:CONTEXT-FIRST:START -->
## Unified Context First（强制）

当任务涉及“已有代码库优化/调试、历史决策回溯、跨终端接力、量化系统定位”时，必须先做上下文预热，再做目录扫描。

执行顺序（硬约束）：
1. 先跑本仓库内置精确检索（至少一次）：
- `python3 /path/to/context-mesh-foundry/scripts/context_cli.py search "<query>" --limit 20 --literal`
2. 未命中再补本地语义检索（可选）：
- `python3 /path/to/context-mesh-foundry/scripts/context_cli.py semantic "<query>" --limit 5`
3. 基于命中结果缩小范围后，才允许 `ls`/`rg` 扫描代码目录。
4. 禁止模式：未预热就直接穷举 `~/`、`/Volumes/*`、或其他大目录。

任务起步自检：
- [ ] 已执行 context_cli 精确检索
- [ ] 已记录命中会话或明确“无命中”
- [ ] 扫描范围已被上下文结果约束
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
    log "skip missing: $file"
    return 0
  fi

  local tmp
  tmp="$(mktemp)"
  strip_old_block "$file" >"$tmp"
  printf '\n%s\n' "$POLICY_BLOCK" >>"$tmp"
  mv "$tmp" "$file"
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
