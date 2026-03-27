#!/usr/bin/env bash
# context_healthcheck.sh -- ContextGO local index health check.
#
# Usage: context_healthcheck.sh [--quiet] [--deep] [--help]
#
# Default mode is non-intrusive and low-overhead.
# --deep enables optional remote probes that may make network connections.
#
# Exit codes:
#   0  All core checks passed (warnings do not affect exit status).
#   1  One or more core checks failed.
#
# Environment variables:
#   CONTEXTGO_STORAGE_ROOT    Storage root (default: ~/.contextgo)
#   CONTEXTGO_REMOTE_URL      Remote sync base URL (default: http://127.0.0.1:8090/api/v1)
#   REMOTE_SYNC_HEALTH_URL    Override the full remote health URL.
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [--quiet] [--deep] [--help]

Run ContextGO health checks against the local index and daemon.

Options:
  --quiet   Suppress stdout output (results still written to log file).
  --deep    Enable optional remote sync and process probes.
  --help    Show this help and exit.

Environment variables:
  CONTEXTGO_STORAGE_ROOT  Storage root (default: ~/.contextgo)
  CONTEXTGO_REMOTE_URL    Remote sync base URL.
  REMOTE_SYNC_HEALTH_URL  Override the full remote health URL.
EOF
    exit 0
}

CONTEXTGO_STORAGE_ROOT="${CONTEXTGO_STORAGE_ROOT:-$HOME/.contextgo}"
readonly CONTEXTGO_STORAGE_ROOT
LOG_DIR="$CONTEXTGO_STORAGE_ROOT/logs"
readonly LOG_DIR
HEALTHCHECK_LOG="$LOG_DIR/healthcheck.log"
readonly HEALTHCHECK_LOG
REMOTE_SYNC_BASE_URL="${CONTEXTGO_REMOTE_URL:-http://127.0.0.1:8090/api/v1}"
readonly REMOTE_SYNC_BASE_URL
REMOTE_SYNC_HEALTH_URL="${REMOTE_SYNC_HEALTH_URL:-${REMOTE_SYNC_BASE_URL%/}/health}"
readonly REMOTE_SYNC_HEALTH_URL

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true

PRINT_STDOUT=1
DEEP_PROBE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --quiet)  PRINT_STDOUT=0 ;;
        --deep)   DEEP_PROBE=1 ;;
        --local)  ;;
        --help|-h) usage ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage
            ;;
    esac
    shift
done

TS="$(date '+%Y-%m-%d %H:%M:%S')"
STATUS=0
REPORT=""
CHECK_SUMMARY=()

record_check_result() {
    CHECK_SUMMARY+=("$1|$2|$3")
}

report_ok()   { REPORT+="  [OK]   $1\n"; }
report_warn() { REPORT+="  [WARN] $1\n"; }
report_fail() { REPORT+="  [FAIL] $1\n"; STATUS=1; }

file_size_bytes() {
    local p="$1"
    stat -f%z "$p" 2>/dev/null || stat -c%s "$p" 2>/dev/null || echo 0
}

check_launchd_runtime() {
    local uid_num state summary status="warn"
    uid_num="$(id -u)"

    if ! command -v launchctl >/dev/null 2>&1; then
        report_warn "launchctl not available; skipping LaunchAgent check"
        summary="launchctl unavailable"
        record_check_result "core.launchd_runtime" "$status" "$summary"
        return 0
    fi

    state="$(launchctl print "gui/${uid_num}/com.contextgo.daemon" 2>/dev/null \
        | awk -F'= ' '/^[[:space:]]*state = / {print $2; exit}')" || true

    if [ -z "$state" ]; then
        report_warn "launchd com.contextgo.daemon not loaded"
        summary="daemon not loaded"
        record_check_result "core.launchd_runtime" "$status" "$summary"
        return 0
    fi

    case "$state" in
        running|"spawn scheduled"|"not running")
            report_ok "launchd com.contextgo.daemon loaded (state=${state})"
            summary="state=${state}"
            status="ok"
            ;;
        *)
            report_warn "launchd com.contextgo.daemon state=${state}"
            summary="state=${state}"
            ;;
    esac

    record_check_result "core.launchd_runtime" "$status" "$summary"
}

check_cli_runtime() {
    local cli_script out sessions db_path summary status
    cli_script="${CONTEXT_CLI_SCRIPT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/context_cli.py}"
    summary="unknown"
    status="fail"

    if [ ! -f "$cli_script" ]; then
        report_fail "context_cli script missing: $cli_script -- reinstall ContextGO or set CONTEXT_CLI_SCRIPT"
        summary="context_cli missing: $cli_script"
        record_check_result "core.cli_runtime" "$status" "$summary"
        return 0
    fi

    out="$(python3 "$cli_script" health 2>&1)" || true
    if printf '%s\n' "$out" | python3 -c \
        'import json,sys; print("1" if json.loads(sys.stdin.read()).get("all_ok") else "0")' \
        2>/dev/null | grep -q '^1$'; then
        sessions="$(printf '%s\n' "$out" | python3 -c \
            'import json,sys; d=json.loads(sys.stdin.read()); print((d.get("session_search_lite") or {}).get("sessions") or 0)' \
            2>/dev/null)" || true
        db_path="$(printf '%s\n' "$out" | python3 -c \
            'import json,sys; d=json.loads(sys.stdin.read()); print((d.get("session_search_lite") or {}).get("db") or "")' \
            2>/dev/null)" || true
        report_ok "Local session index healthy (sessions=${sessions:-0})"
        if [ -n "${db_path:-}" ]; then
            report_ok "Session index database: $db_path"
        fi
        report_ok "Context path: built-in session index + local context_cli (no external bridge)"
        summary="sessions=${sessions:-0}, db=${db_path:-not returned}"
        status="ok"
    else
        local _first_line
        _first_line="$(printf '%s\n' "$out" | head -1)"
        report_fail "context_cli health check failed -- run 'python3 $cli_script health' for details (first line: ${_first_line:0:120})"
        summary="health failed; first_line=${_first_line:0:80}"
    fi

    record_check_result "core.cli_runtime" "$status" "$summary"
}

check_remote_sync_probe() {
    local http_status summary status="warn"
    http_status="$(curl -s -o /dev/null -w '%{http_code}' \
        "$REMOTE_SYNC_HEALTH_URL" --max-time 3 2>/dev/null || true)"
    http_status="${http_status: -3}"
    [ -z "$http_status" ] && http_status="000"

    if [ "$http_status" = "200" ]; then
        report_ok "Remote sync optional probe: HTTP 200"
        summary="HTTP 200"
        status="ok"
    else
        report_warn "Remote sync optional probe: HTTP ${http_status} (does not affect local path)"
        summary="HTTP ${http_status}"
    fi

    record_check_result "optional.remote_sync_probe" "$status" "$summary"
}

check_stale_claude_hooks() {
    local hit summary status
    hit="$(grep -rl 'aline-ai\|realign/claude_hooks' \
        "$HOME/.claude/settings.json" \
        "$HOME/.claude/settings.local.json" \
        2>/dev/null || true)"
    if [ -n "$hit" ]; then
        report_fail "Stale Claude hooks detected (aline/realign) in: ${hit} -- remove or update these hooks to prevent hangs"
        summary="stale hooks detected in: ${hit}"
        status="fail"
    else
        report_ok "Claude config: no stale aline hooks found"
        summary="no stale hooks"
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
        report_ok "Daemon log size: $(( $(file_size_bytes "$daemon_log") / 1048576 ))MB"
        daemon_status="present"
    elif [ -f "$previous_daemon_log" ]; then
        report_warn "Previous-generation daemon log detected: ${previous_daemon_log}"
        daemon_status="previous"
        status="warn"
    else
        report_warn "Daemon log not found (expected if daemon has not started)"
        daemon_status="missing"
        status="warn"
    fi

    if [ -f "$health_log" ]; then
        report_ok "Health check log size: $(( $(file_size_bytes "$health_log") / 1048576 ))MB"
        health_status="present"
    else
        report_warn "Health check log not found"
        health_status="missing"
        status="warn"
    fi

    pending_dir="$CONTEXTGO_STORAGE_ROOT/resources/shared/history/.pending"
    if [ -d "$pending_dir" ]; then
        pending_count="$(find "$pending_dir" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | tr -d ' ')"
        report_ok "Pending queue files: ${pending_count:-0}"
        pending_count="${pending_count:-0}"
    else
        report_ok "Pending queue directory absent (no offline backlog)"
        pending_count=0
    fi

    local summary="daemon_log=${daemon_status}, health_log=${health_status}, pending=${pending_count}"
    record_check_result "storage.logs_pending" "$status" "$summary"
}

check_remote_processes() {
    local pids summary status
    pids="$(pgrep -f 'context_daemon\.py|contextgo-remote' 2>/dev/null || true)"
    if [ -n "$pids" ]; then
        # shellcheck disable=SC2001
        report_warn "Optional remote sync processes detected: $(printf '%s' "$pids" | tr '\n' ' ' | sed 's/  */ /g')"
        summary="pids=${pids}"
        status="warn"
    else
        report_ok "No optional remote sync processes detected"
        summary="none"
        status="ok"
    fi

    record_check_result "optional.remote_processes" "$status" "$summary"
}

# ---------------------------------------------------------------------------
# Run checks
# ---------------------------------------------------------------------------
REPORT+="[$TS] ContextGO Health Check\n"
REPORT+="-----------------------------------\n"
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

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
SUMMARY_TOTAL="${#CHECK_SUMMARY[@]}"
SUMMARY_WARN=0
SUMMARY_FAIL=0
SUMMARY_FAILED_NAMES=()

for entry in "${CHECK_SUMMARY[@]}"; do
    IFS='|' read -r name chk_status _detail <<< "$entry"
    case "$chk_status" in
        fail)
            SUMMARY_FAIL=$(( SUMMARY_FAIL + 1 ))
            SUMMARY_FAILED_NAMES+=("$name")
            ;;
        warn)
            SUMMARY_WARN=$(( SUMMARY_WARN + 1 ))
            ;;
    esac
done

SUMMARY_STATUS="pass"
if [ "$SUMMARY_FAIL" -gt 0 ]; then
    SUMMARY_STATUS="fail"
fi

REPORT+="\nSummary:\n"
REPORT+="  status=${SUMMARY_STATUS}  total=${SUMMARY_TOTAL}  warnings=${SUMMARY_WARN}  failures=${SUMMARY_FAIL}\n"
if [ "$SUMMARY_FAIL" -gt 0 ]; then
    REPORT+="  failed: ${SUMMARY_FAILED_NAMES[*]}\n"
fi

REPORT+="\n"
if [ "$STATUS" -eq 0 ]; then
    REPORT+="[PASS] ContextGO checks passed.\n"
else
    REPORT+="[FAIL] ContextGO issues detected.\n"
fi
REPORT+="-----------------------------------\n\n"

if [ "$PRINT_STDOUT" = "1" ]; then
    printf '%b' "$REPORT"
fi

printf '%b' "$REPORT" >> "$HEALTHCHECK_LOG"

# Rotate log when it exceeds 5 MB (keep last ~2.5 MB)
HC_SIZE=$(( $(file_size_bytes "$HEALTHCHECK_LOG") / 1048576 ))
if [ "$HC_SIZE" -gt 5 ]; then
    HC_TMPFILE="$(mktemp "${HEALTHCHECK_LOG}.XXXXXX")"
    tail -c 2621440 "$HEALTHCHECK_LOG" > "$HC_TMPFILE" \
        && mv "$HC_TMPFILE" "$HEALTHCHECK_LOG" \
        || rm -f "$HC_TMPFILE" 2>/dev/null
fi

exit "$STATUS"
