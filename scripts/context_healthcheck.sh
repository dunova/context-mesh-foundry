#!/bin/bash
# =============================================================================
# ContextGO Health Check (standalone local index)
# Default mode is non-intrusive and low-overhead.
# --deep enables optional remote probes.
# =============================================================================

set -u

CONTEXTGO_STORAGE_ROOT="${CONTEXTGO_STORAGE_ROOT:-$HOME/.contextgo}"
LOG_DIR="$CONTEXTGO_STORAGE_ROOT/logs"
HEALTHCHECK_LOG="$LOG_DIR/healthcheck.log"
REMOTE_SYNC_BASE_URL="${CONTEXTGO_REMOTE_URL:-http://127.0.0.1:8090/api/v1}"
REMOTE_SYNC_HEALTH_URL="${REMOTE_SYNC_HEALTH_URL:-${REMOTE_SYNC_BASE_URL%/}/health}"

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true

PRINT_STDOUT=1
DEEP_PROBE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --quiet) PRINT_STDOUT=0 ;;
        --local) ;;
        --deep) DEEP_PROBE=1 ;;
    esac
    shift
done

TS=$(date '+%Y-%m-%d %H:%M:%S')
STATUS=0
REPORT=""
CHECK_SUMMARY=()

record_check_result() {
    CHECK_SUMMARY+=("$1|$2|$3")
}

report_ok() { REPORT+="  ✅ $1\n"; }
report_warn() { REPORT+="  ⚠️  $1\n"; }
report_fail() { REPORT+="  ❌ $1\n"; STATUS=1; }

file_size_bytes() {
    local p="$1"
    stat -f%z "$p" 2>/dev/null || stat -c%s "$p" 2>/dev/null || echo 0
}

check_launchd_runtime() {
    local uid_num state summary status="warn"
    uid_num="$(id -u)"

    if ! command -v launchctl >/dev/null 2>&1; then
        report_warn "launchctl 不可用，跳过 LaunchAgent 检查"
        summary="launchctl 不可用"
        record_check_result "core.launchd_runtime" "$status" "$summary"
        return 0
    fi

    state=$(launchctl print "gui/${uid_num}/com.contextgo.daemon" 2>/dev/null | awk -F'= ' '/^[[:space:]]*state = / {print $2; exit}')
    if [ -z "$state" ]; then
        report_warn "launchd com.contextgo.daemon 未加载"
        summary="daemon 未加载"
        record_check_result "core.launchd_runtime" "$status" "$summary"
        return 0
    fi

    if [ "$state" = "running" ] || [ "$state" = "spawn scheduled" ] || [ "$state" = "not running" ]; then
        report_ok "launchd com.contextgo.daemon 已加载（state=${state}）"
        summary="state=${state}"
        status="ok"
    else
        report_warn "launchd com.contextgo.daemon state=$state"
        summary="state=${state}"
    fi

    record_check_result "core.launchd_runtime" "$status" "$summary"
}

check_cli_runtime() {
    local cli_script out sessions db_path summary status
    cli_script="${CONTEXT_CLI_SCRIPT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/context_cli.py}"
    summary="未知"
    status="fail"

    if [ ! -f "$cli_script" ]; then
        report_fail "context_cli 脚本缺失：$cli_script"
        summary="context_cli 脚本缺失"
        record_check_result "core.cli_runtime" "$status" "$summary"
        return 0
    fi

    out="$(python3 "$cli_script" health 2>&1)"
    if echo "$out" | python3 -c 'import json,sys; print("1" if json.loads(sys.stdin.read()).get("all_ok") else "0")' 2>/dev/null | grep -q '^1$'; then
        sessions="$(echo "$out" | python3 -c 'import json,sys; data=json.loads(sys.stdin.read()); print((data.get("session_search_lite") or {}).get("sessions") or 0)' 2>/dev/null)"
        db_path="$(echo "$out" | python3 -c 'import json,sys; data=json.loads(sys.stdin.read()); print((data.get("session_search_lite") or {}).get("db") or "")' 2>/dev/null)"
        report_ok "本地会话索引健康检查通过（sessions=${sessions:-0}）"
        if [ -n "$db_path" ]; then
            report_ok "会话索引数据库：$db_path"
        fi
        report_ok "上下文主链路：内置 session index + 本地 context_cli（零外部桥接）"
        summary="sessions=${sessions:-0}, db=${db_path:-未返回}"
        status="ok"
    else
        report_fail "context_cli health 失败"
        summary="health 失败"
    fi

    record_check_result "core.cli_runtime" "$status" "$summary"
}

check_remote_sync_probe() {
    local http_status summary status="warn"
    http_status=$(curl -s -o /dev/null -w "%{http_code}" "$REMOTE_SYNC_HEALTH_URL" --max-time 3 2>/dev/null || true)
    http_status="${http_status: -3}"
    [ -z "$http_status" ] && http_status="000"

    if [ "$http_status" = "200" ]; then
        report_ok "ContextGO 远程同步可选探针：HTTP 200"
        summary="HTTP 200"
        status="ok"
    else
        report_warn "ContextGO 远程同步可选探针：HTTP ${http_status}（不影响本地主链）"
        summary="HTTP ${http_status}"
    fi

    record_check_result "optional.remote_sync_probe" "$status" "$summary"
}

check_stale_claude_hooks() {
    local hit
    hit="$(rg -n 'aline-ai|realign/claude_hooks' "$HOME/.claude/settings.json" "$HOME/.claude/settings.local.json" 2>/dev/null || true)"
    local summary status
    if [ -n "$hit" ]; then
        report_fail "检测到失效 Claude hooks（aline/realign），可能引发卡顿"
        summary="检测到失效 hooks"
        status="fail"
    else
        report_ok "Claude 配置未发现失效 aline hooks"
        summary="未发现失效 hooks"
        status="ok"
    fi

    record_check_result "core.claude_hooks" "$status" "$summary"
}

check_logs_and_pending() {
    local daemon_log previous_daemon_log health_log pending_dir pending_count
    daemon_log="$LOG_DIR/contextgo_daemon.log"
    previous_daemon_log="$LOG_DIR/context_daemon.log"
    health_log="$LOG_DIR/healthcheck.log"
    local status="ok"
    local daemon_status="missing"
    local health_status="missing"

    if [ -f "$daemon_log" ]; then
        report_ok "ContextGO daemon 日志大小：$(( $(file_size_bytes "${daemon_log}") / 1048576 ))MB"
        daemon_status="present"
    elif [ -f "$previous_daemon_log" ]; then
        report_warn "检测到上一代 ContextGO daemon 日志：${previous_daemon_log}（旧 context_daemon.log）"
        daemon_status="previous"
        status="warn"
    else
        report_warn "ContextGO daemon 日志不存在（如未启动可忽略）"
        daemon_status="missing"
        status="warn"
    fi

    if [ -f "$health_log" ]; then
        report_ok "healthcheck 日志大小：$(( $(file_size_bytes "$health_log") / 1048576 ))MB"
        health_status="present"
    else
        report_warn "healthcheck 日志不存在"
        health_status="missing"
        status="warn"
    fi

    pending_dir="$CONTEXTGO_STORAGE_ROOT/resources/shared/history/.pending"
    if [ -d "$pending_dir" ]; then
        pending_count=$(ls -1 "$pending_dir"/*.md 2>/dev/null | wc -l | tr -d ' ')
        report_ok "pending 队列文件数：${pending_count:-0}"
        pending_count=${pending_count:-0}
    else
        report_ok "pending 队列目录不存在（当前无离线积压）"
        pending_count=0
    fi
    local summary="daemon_log=${daemon_status}, health_log=${health_status}, pending=${pending_count}"
    record_check_result "storage.logs_pending" "$status" "$summary"
}

check_remote_processes() {
    local pids
    pids="$(pgrep -f 'context_daemon.py|contextgo-remote' 2>/dev/null || true)"
    local summary status
    if [ -n "$pids" ]; then
        report_warn "检测到可选远程同步进程：$(echo "$pids" | tr '\n' ' ' | sed 's/  */ /g')"
        summary="pids=${pids}"
        status="warn"
    else
        report_ok "未检测到可选远程同步进程"
        summary="none"
        status="ok"
    fi

    record_check_result "optional.remote_processes" "$status" "$summary"
}

REPORT+="[$TS] ContextGO Health Check\n"
REPORT+="─────────────────────────────────\n"
REPORT+="Core:\n"
check_launchd_runtime
check_cli_runtime
check_stale_claude_hooks

REPORT+="\nStorage/Logs:\n"
check_logs_and_pending

if [ "$DEEP_PROBE" = "1" ]; then
    REPORT+="\nOptional Deep Checks:\n"
    check_remote_sync_probe
    check_remote_processes
fi

SUMMARY_TOTAL=${#CHECK_SUMMARY[@]}
SUMMARY_WARN=0
SUMMARY_FAIL=0
SUMMARY_FAILED_NAMES=()
for entry in "${CHECK_SUMMARY[@]}"; do
    IFS='|' read -r name status detail <<< "$entry"
    case "$status" in
        fail)
            SUMMARY_FAIL=$((SUMMARY_FAIL + 1))
            SUMMARY_FAILED_NAMES+=("$name")
            ;;
        warn)
            SUMMARY_WARN=$((SUMMARY_WARN + 1))
            ;;
    esac
done
SUMMARY_STATUS="pass"
if [ "$SUMMARY_FAIL" -gt 0 ]; then
    SUMMARY_STATUS="fail"
fi
REPORT+="\nSummary:\n"
REPORT+="  状态：${SUMMARY_STATUS}  总检查数：${SUMMARY_TOTAL}  警告：${SUMMARY_WARN}  失败：${SUMMARY_FAIL}\n"
if [ "$SUMMARY_FAIL" -gt 0 ]; then
    REPORT+="  失败项：${SUMMARY_FAILED_NAMES[*]}\n"
fi

REPORT+="\n"
if [ "$STATUS" -eq 0 ]; then
    REPORT+="🟢 ContextGO checks passed.\n"
else
    REPORT+="🔴 ContextGO issues detected.\n"
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
