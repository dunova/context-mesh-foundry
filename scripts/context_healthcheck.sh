#!/bin/bash
# =============================================================================
# Context Mesh Health Check (standalone local index)
# Default mode is non-intrusive and low-overhead.
# --deep enables optional legacy/remote probes.
# =============================================================================

set -u

LOG_DIR="$HOME/.context_system/logs"
HEALTHCHECK_LOG="$LOG_DIR/healthcheck.log"
UNIFIED_CONTEXT_STORAGE_ROOT="${UNIFIED_CONTEXT_STORAGE_ROOT:-${OPENVIKING_STORAGE_ROOT:-$HOME/.unified_context_data}}"

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true

PRINT_STDOUT=1
DEEP_PROBE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --quiet) PRINT_STDOUT=0 ;;
        --deep) DEEP_PROBE=1 ;;
    esac
    shift
done

TS=$(date '+%Y-%m-%d %H:%M:%S')
STATUS=0
REPORT=""

report_ok() { REPORT+="  ✅ $1\n"; }
report_warn() { REPORT+="  ⚠️  $1\n"; }
report_fail() { REPORT+="  ❌ $1\n"; STATUS=1; }

file_size_bytes() {
    local p="$1"
    stat -f%z "$p" 2>/dev/null || stat -c%s "$p" 2>/dev/null || echo 0
}

check_launchd_runtime() {
    local uid_num state
    uid_num="$(id -u)"
    if ! command -v launchctl >/dev/null 2>&1; then
        report_warn "launchctl 不可用，跳过 LaunchAgent 检查"
        return 0
    fi

    state=$(launchctl print "gui/${uid_num}/com.contextmesh.daemon" 2>/dev/null | awk -F'= ' '/^[[:space:]]*state = / {print $2; exit}')
    if [ -z "$state" ]; then
        report_warn "launchd com.contextmesh.daemon 未加载"
        return 0
    fi

    if [ "$state" = "running" ] || [ "$state" = "spawn scheduled" ] || [ "$state" = "not running" ]; then
        report_ok "launchd com.contextmesh.daemon 已加载（state=${state}）"
    else
        report_warn "launchd com.contextmesh.daemon state=$state"
    fi
}

check_cli_runtime() {
    local cli_script out

    cli_script="${CONTEXT_CLI_SCRIPT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/context_cli.py}"

    if [ ! -f "$cli_script" ]; then
        report_fail "context_cli 脚本缺失：$cli_script"
        return 0
    fi

    out="$(python3 "$cli_script" health 2>&1)"
    if echo "$out" | grep -q '"all_ok": true'; then
        local sessions db_path
        sessions="$(echo "$out" | awk -F': ' '/"sessions"/ {gsub(/,/, "", $2); print $2; exit}')"
        db_path="$(echo "$out" | awk -F': ' '/"db"/ {gsub(/[",]/, "", $2); print $2; exit}')"
        report_ok "本地会话索引健康检查通过（sessions=${sessions:-0}）"
        if [ -n "$db_path" ]; then
            report_ok "会话索引数据库：$db_path"
        fi
    else
        report_fail "context_cli health 失败"
    fi

    report_ok "上下文主链路：内置 session index + 本地 context_cli（无 MCP）"
}

check_openviking_optional() {
    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8090/health" --max-time 3 2>/dev/null || true)
    http_status="${http_status: -3}"
    [ -z "$http_status" ] && http_status="000"

    if [ "$http_status" = "200" ]; then
        report_ok "openviking 可选探针：HTTP 200"
    else
        report_warn "remote sync 可选探针：HTTP ${http_status}（不影响本地主链）"
    fi
}

check_stale_claude_hooks() {
    local hit
    hit="$(rg -n 'aline-ai|realign/claude_hooks' "$HOME/.claude/settings.json" "$HOME/.claude/settings.local.json" 2>/dev/null || true)"
    if [ -n "$hit" ]; then
        report_fail "检测到失效 Claude hooks（aline/realign），可能引发卡顿"
    else
        report_ok "Claude 配置未发现失效 aline hooks"
    fi
}

check_logs_and_pending() {
    local daemon_log health_log pending_dir pending_count
    daemon_log="$LOG_DIR/viking_daemon.log"
    health_log="$LOG_DIR/healthcheck.log"

    if [ -f "$daemon_log" ]; then
        report_ok "daemon 日志大小：$(( $(file_size_bytes "$daemon_log") / 1048576 ))MB"
    else
        report_warn "daemon 日志不存在（如未启动可忽略）"
    fi

    if [ -f "$health_log" ]; then
        report_ok "healthcheck 日志大小：$(( $(file_size_bytes "$health_log") / 1048576 ))MB"
    else
        report_warn "healthcheck 日志不存在"
    fi

    pending_dir="$UNIFIED_CONTEXT_STORAGE_ROOT/resources/shared/history/.pending"
    if [ -d "$pending_dir" ]; then
        pending_count=$(ls -1 "$pending_dir"/*.md 2>/dev/null | wc -l | tr -d ' ')
        report_ok "pending 队列文件数：${pending_count:-0}"
    else
        report_ok "pending 队列目录不存在（当前无离线积压）"
    fi
}

check_deep_legacy_runtime() {
    local pids
    pids="$(pgrep -f 'context_daemon.py|viking_daemon.py|openviking_mcp.py|openviking-server' 2>/dev/null || true)"
    if [ -n "$pids" ]; then
        report_warn "检测到旧 OpenViking/MCP 进程残留：$(echo "$pids" | tr '\n' ' ' | sed 's/  */ /g')"
    else
        report_ok "未检测到旧 OpenViking/MCP 进程残留"
    fi
}

REPORT+="[$TS] Context Mesh Health Check\n"
REPORT+="─────────────────────────────────\n"
REPORT+="Core:\n"
check_launchd_runtime
check_cli_runtime
check_stale_claude_hooks

REPORT+="\nStorage/Logs:\n"
check_logs_and_pending

if [ "$DEEP_PROBE" = "1" ]; then
    REPORT+="\nOptional Deep Checks:\n"
    check_openviking_optional
    check_deep_legacy_runtime
fi

REPORT+="\n"
if [ "$STATUS" -eq 0 ]; then
    REPORT+="🟢 Context Mesh checks passed.\n"
else
    REPORT+="🔴 Context Mesh issues detected.\n"
fi
REPORT+="─────────────────────────────────\n\n"

if [ "$PRINT_STDOUT" = "1" ]; then
    echo -e "$REPORT"
fi

echo -e "$REPORT" >> "$HEALTHCHECK_LOG"

HC_SIZE=$(( $(file_size_bytes "$HEALTHCHECK_LOG") / 1048576 ))
if [ "$HC_SIZE" -gt 5 ]; then
    HC_TMPFILE="$(mktemp "${HEALTHCHECK_LOG}.XXXXXX")" && \
        tail -c 2621440 "$HEALTHCHECK_LOG" > "$HC_TMPFILE" && \
        mv "$HC_TMPFILE" "$HEALTHCHECK_LOG" || \
        rm -f "$HC_TMPFILE" 2>/dev/null
fi

exit $STATUS
