#!/usr/bin/env bash
# unified_context_deploy.sh -- deploy ContextGO scripts and templates, patch
# launchd plists (macOS), and optionally reload launchd agents.
#
# Usage: unified_context_deploy.sh [--help]
#
# Exit codes:
#   0  Deployment completed successfully.
#   1  A required directory is missing or launchctl bootstrap failed.
#
# Environment variables:
#   CONTEXTGO_INSTALL_ROOT    Installation root  (default: ~/.local/share/contextgo)
#   CONTEXTGO_STORAGE_ROOT    Storage root       (default: ~/.contextgo)
#   CONTEXTGO_BIN_DIR         CLI shim dir       (default: ~/.local/bin)
#   PATCH_LAUNCHD             Patch plists       (default: 1)
#   RELOAD_LAUNCHD            Reload agents      (default: 1)
#   APPLY_CONTEXT_POLICY      Apply CF policy    (default: 1)
#   CREATE_CONTEXTGO_SHIM     auto|force|0       (default: auto)
set -euo pipefail
umask 077

usage() {
    cat <<EOF
Usage: $(basename "$0") [--help]

Deploy ContextGO scripts and templates to the install root, patch macOS
launchd plists, and optionally reload LaunchAgents.

Environment variables:
  CONTEXTGO_INSTALL_ROOT  Installation root (default: ~/.local/share/contextgo)
  CONTEXTGO_STORAGE_ROOT  Storage root      (default: ~/.contextgo)
  CONTEXTGO_BIN_DIR       CLI shim dir      (default: ~/.local/bin)
  PATCH_LAUNCHD           Patch plists: 1=yes, 0=no  (default: 1)
  RELOAD_LAUNCHD          Reload agents: 1=yes, 0=no  (default: 1)
  APPLY_CONTEXT_POLICY    Apply context-first policy  (default: 1)
  CREATE_CONTEXTGO_SHIM   auto|force|0  (default: auto)
EOF
    exit 0
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly REPO_ROOT
HOME_DIR="${HOME:-$(cd ~ && pwd)}"
readonly HOME_DIR
INSTALL_ROOT="${CONTEXTGO_INSTALL_ROOT:-$HOME_DIR/.local/share/contextgo}"
readonly INSTALL_ROOT
CONTEXTGO_STORAGE_ROOT="${CONTEXTGO_STORAGE_ROOT:-$HOME_DIR/.contextgo}"
readonly CONTEXTGO_STORAGE_ROOT
CONTEXTGO_BIN_DIR="${CONTEXTGO_BIN_DIR:-$HOME_DIR/.local/bin}"
readonly CONTEXTGO_BIN_DIR
PATCH_LAUNCHD="${PATCH_LAUNCHD:-1}"
readonly PATCH_LAUNCHD
RELOAD_LAUNCHD="${RELOAD_LAUNCHD:-1}"
readonly RELOAD_LAUNCHD
APPLY_CONTEXT_POLICY="${APPLY_CONTEXT_POLICY:-1}"
readonly APPLY_CONTEXT_POLICY
CREATE_CONTEXTGO_SHIM="${CREATE_CONTEXTGO_SHIM:-auto}"
readonly CREATE_CONTEXTGO_SHIM

log() { printf '[deploy] %s\n' "$*"; }

require_dir() {
    local p="$1"
    if [ ! -d "$p" ]; then
        log "ERROR: required directory missing: $p" >&2
        exit 1
    fi
}

sync_dir() {
    local src="$1" dst="$2"
    mkdir -p "$dst"
    if command -v rsync >/dev/null 2>&1; then
        # -L: follow symlinks (scripts/ may contain symlinks to src/)
        rsync -aL --delete "$src"/ "$dst"/
    else
        rm -rf "$dst"
        mkdir -p "$dst"
        cp -RL "$src"/. "$dst"/
    fi
    log "synced: $src -> $dst"
}

log "unified context deploy start"
require_dir "$REPO_ROOT"
require_dir "$REPO_ROOT/scripts"
require_dir "$REPO_ROOT/templates"

mkdir -p "$CONTEXTGO_STORAGE_ROOT/logs"
chmod 700 "$CONTEXTGO_STORAGE_ROOT" "$CONTEXTGO_STORAGE_ROOT/logs" 2>/dev/null || true

sync_dir "$REPO_ROOT/scripts"   "$INSTALL_ROOT/scripts"
sync_dir "$REPO_ROOT/templates" "$INSTALL_ROOT/templates"
if [ -d "$REPO_ROOT/src" ]; then
    sync_dir "$REPO_ROOT/src" "$INSTALL_ROOT/src"
fi
log "installed canonical runtime at: $INSTALL_ROOT"

resolve_python3() {
    local candidate
    for candidate in \
        /opt/homebrew/opt/python@3.13/libexec/bin/python3 \
        /opt/homebrew/opt/python@3.12/libexec/bin/python3 \
        /opt/homebrew/opt/python@3.11/libexec/bin/python3
    do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    command -v python3
}

maybe_create_contextgo_shim() {
    local mode="$1" python_bin shim_path existing_bin
    python_bin="$(resolve_python3)"
    shim_path="$CONTEXTGO_BIN_DIR/contextgo"
    mkdir -p "$CONTEXTGO_BIN_DIR"
    existing_bin="$(command -v contextgo 2>/dev/null || true)"

    if [ "$mode" = "0" ]; then
        log "skipping contextgo shim creation (CREATE_CONTEXTGO_SHIM=0)"
        return 0
    fi

    # If pipx manages contextgo, never overwrite its entry point
    if command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q 'contextgo'; then
        log "contextgo is managed by pipx — skipping shim creation"
        return 0
    fi

    if [ "$mode" = "auto" ] && [ -n "$existing_bin" ] && [ "$existing_bin" != "$shim_path" ]; then
        log "leaving existing contextgo on PATH untouched: $existing_bin"
        return 0
    fi

    cat >"$shim_path" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$python_bin" "$INSTALL_ROOT/scripts/context_cli.py" "\$@"
EOF
    chmod 755 "$shim_path"
    log "installed contextgo shim at: $shim_path"

    case ":$PATH:" in
        *":$CONTEXTGO_BIN_DIR:"*)
            ;;
        *)
            log "PATH does not contain $CONTEXTGO_BIN_DIR"
            log "add it with: export PATH=\"$CONTEXTGO_BIN_DIR:\$PATH\""
            ;;
    esac
}

maybe_create_contextgo_shim "$CREATE_CONTEXTGO_SHIM"

if [ "$APPLY_CONTEXT_POLICY" = "1" ] && \
   [ -f "$REPO_ROOT/scripts/apply_context_first_policy.sh" ]; then
    log "applying context-first policy to terminal entry files"
    bash "$REPO_ROOT/scripts/apply_context_first_policy.sh" \
        || log "WARNING: context-first policy apply failed (non-fatal)"
fi

if [ "$PATCH_LAUNCHD" = "1" ] && command -v launchctl >/dev/null 2>&1; then
    export CONTEXTGO_INSTALL_ROOT="$INSTALL_ROOT"
    export CONTEXTGO_STORAGE_ROOT
    python3 - <<'PY'
import os
import plistlib
import shutil
from pathlib import Path

home = Path.home()
launch = home / 'Library' / 'LaunchAgents'
install_root = Path(os.environ['CONTEXTGO_INSTALL_ROOT'])
script_dir = install_root / 'scripts'
template_dir = install_root / 'templates' / 'launchd'
launch.mkdir(parents=True, exist_ok=True)

# Resolve python3: prefer versioned brew binaries, fall back to PATH.
_python3_bin = shutil.which('python3')
for _candidate in (
    '/opt/homebrew/opt/python@3.13/libexec/bin/python3',
    '/opt/homebrew/opt/python@3.12/libexec/bin/python3',
    '/opt/homebrew/opt/python@3.11/libexec/bin/python3',
):
    if os.path.isfile(_candidate):
        _python3_bin = _candidate
        break

if _python3_bin:
    _daemon_args = [_python3_bin, str(script_dir / 'context_daemon.py')]
else:
    _daemon_args = ['/usr/bin/env', 'python3', str(script_dir / 'context_daemon.py')]

patches = [
    (
        template_dir / 'com.contextgo.daemon.plist',
        launch / 'com.contextgo.daemon.plist',
        _daemon_args,
        str(script_dir),
        {
            'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
            'CONTEXTGO_ENABLE_SHELL_MONITOR': '0',
            'CONTEXTGO_ENABLE_OPENCODE_MONITOR': '0',
            'CONTEXTGO_ENABLE_KILO_MONITOR': '0',
            'CONTEXTGO_ENABLE_REMOTE_SYNC': '0',
            'CONTEXTGO_POLL_INTERVAL_SEC': '180',
            'CONTEXTGO_FAST_POLL_INTERVAL_SEC': '20',
            'CONTEXTGO_IDLE_SLEEP_CAP_SEC': '600',
            'CONTEXTGO_CODEX_SESSION_SCAN_INTERVAL_SEC': '300',
            'CONTEXTGO_CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC': '300',
            'CONTEXTGO_ANTIGRAVITY_SCAN_INTERVAL_SEC': '300',
            'CONTEXTGO_SUSPEND_ANTIGRAVITY_WHEN_BUSY': '1',
            'CONTEXTGO_ANTIGRAVITY_BUSY_LS_THRESHOLD': '2',
            'CONTEXTGO_ANTIGRAVITY_INGEST_MODE': 'final_only',
            'CONTEXTGO_ANTIGRAVITY_QUIET_SEC': '240',
            'CONTEXTGO_ANTIGRAVITY_MIN_DOC_BYTES': '500',
            'CONTEXTGO_CYCLE_BUDGET_SEC': '8',
            'CONTEXTGO_INDEX_SYNC_MIN_INTERVAL_SEC': '20',
            'CONTEXTGO_ERROR_BACKOFF_MAX_SEC': '30',
            'CONTEXTGO_LOOP_JITTER_SEC': '0.7',
        },
    ),
    (
        template_dir / 'com.contextgo.healthcheck.plist',
        launch / 'com.contextgo.healthcheck.plist',
        ['/bin/bash', str(script_dir / 'context_healthcheck.sh'), '--quiet'],
        None,
        {
            'CONTEXTGO_STORAGE_ROOT': os.environ.get(
                'CONTEXTGO_STORAGE_ROOT', str(home / '.contextgo')
            ),
        },
    ),
]

for template_path, plist_path, args, wd, extra_env in patches:
    if not template_path.exists():
        print(f'[deploy] skip missing template: {template_path}')
        continue
    raw = template_path.read_text(encoding='utf-8')
    raw = raw.replace('__SCRIPTS_DIR__', str(script_dir))
    raw = raw.replace('__LOG_DIR__', str(home / '.contextgo' / 'logs'))
    raw = raw.replace('__HOME__', str(home))
    plist_path.write_text(raw, encoding='utf-8')
    with plist_path.open('rb') as f:
        data = plistlib.load(f)
    data['ProgramArguments'] = args
    env = data.get('EnvironmentVariables', {})
    env.setdefault('HOME', str(home))
    env.update(extra_env)
    data['EnvironmentVariables'] = env
    if wd:
        data['WorkingDirectory'] = wd
    with plist_path.open('wb') as f:
        plistlib.dump(data, f, sort_keys=False)
    print(f'[deploy] patched plist: {plist_path.name}')
PY
fi

if [ "$RELOAD_LAUNCHD" = "1" ] && command -v launchctl >/dev/null 2>&1; then
    UID_NUM="$(id -u)"
    export UID_NUM
    python3 - <<'PY'
import os
import subprocess
import time
from pathlib import Path

home = Path.home()
uid_num = os.environ["UID_NUM"]
labels = ["com.contextgo.daemon", "com.contextgo.healthcheck"]


def run(cmd, timeout=8):
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        ).returncode
    except subprocess.TimeoutExpired:
        print(f"[deploy] launchctl timeout: {' '.join(cmd)}")
        return 124


def wait_process(pattern, timeout_sec=20):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if (
            subprocess.run(
                ["pgrep", "-f", pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        ):
            return True
        time.sleep(1)
    return False


for label in labels:
    plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist.exists():
        print(f"[deploy] skip (plist not found): {label}")
        continue
    run(["launchctl", "bootout", f"gui/{uid_num}", str(plist)])
    rc = run(["launchctl", "bootstrap", f"gui/{uid_num}", str(plist)])
    if rc != 0:
        print(f"[deploy] ERROR: launchctl bootstrap failed: {label}")
        raise SystemExit(1)
    run(["launchctl", "kickstart", f"gui/{uid_num}/{label}"], timeout=5)
    print(f"[deploy] reloaded launchd: {label}")
    if label == "com.contextgo.daemon" and not wait_process("context_daemon.py"):
        print("[deploy] ERROR: context daemon not detected after reload")
        raise SystemExit(1)
PY
fi

bash "$INSTALL_ROOT/scripts/context_healthcheck.sh" --quiet || true
log "verify with: contextgo health"
log "unified context deploy done"
