#!/bin/bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOME_DIR="${HOME:-$(cd ~ && pwd)}"
INSTALL_ROOT="${CONTEXT_MESH_INSTALL_ROOT:-${CMF_INSTALL_ROOT:-$HOME_DIR/.local/share/context-mesh-foundry}}"
UNIFIED_CONTEXT_STORAGE_ROOT="${UNIFIED_CONTEXT_STORAGE_ROOT:-${CONTEXT_MESH_STORAGE_ROOT:-${OPENVIKING_STORAGE_ROOT:-$HOME_DIR/.unified_context_data}}}"
PATCH_LAUNCHD="${PATCH_LAUNCHD:-1}"
RELOAD_LAUNCHD="${RELOAD_LAUNCHD:-1}"
APPLY_CONTEXT_POLICY="${APPLY_CONTEXT_POLICY:-1}"

log() { echo "[deploy] $*"; }

require_dir() {
  local p="$1"
  if [ ! -d "$p" ]; then
    log "missing directory: $p"
    exit 1
  fi
}

sync_dir() {
  local src="$1" dst="$2"
  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$src"/ "$dst"/
  else
    rm -rf "$dst"
    mkdir -p "$dst"
    cp -R "$src"/. "$dst"/
  fi
  log "synced: $src -> $dst"
}

log "unified context deploy start"
require_dir "$REPO_ROOT"
require_dir "$REPO_ROOT/scripts"
require_dir "$REPO_ROOT/templates"

mkdir -p "$UNIFIED_CONTEXT_STORAGE_ROOT"
chmod 700 "$UNIFIED_CONTEXT_STORAGE_ROOT" >/dev/null 2>&1 || true
mkdir -p "$HOME_DIR/.context_system/logs"
chmod 700 "$HOME_DIR/.context_system" "$HOME_DIR/.context_system/logs" >/dev/null 2>&1 || true

sync_dir "$REPO_ROOT/scripts" "$INSTALL_ROOT/scripts"
sync_dir "$REPO_ROOT/templates" "$INSTALL_ROOT/templates"
log "installed canonical runtime at: $INSTALL_ROOT"

if [ "$APPLY_CONTEXT_POLICY" = "1" ] && [ -f "$REPO_ROOT/scripts/apply_context_first_policy.sh" ]; then
  log "applying context-first policy to terminal entry files"
  bash "$REPO_ROOT/scripts/apply_context_first_policy.sh" || log "warning: context-first policy apply failed"
fi

if [ "$PATCH_LAUNCHD" = "1" ] && command -v launchctl >/dev/null 2>&1; then
export CMF_INSTALL_ROOT="$INSTALL_ROOT"
export CONTEXT_MESH_INSTALL_ROOT="$INSTALL_ROOT"
export UNIFIED_CONTEXT_STORAGE_ROOT
python3 - <<'PY'
import plistlib
from pathlib import Path
import os

import shutil

home = Path.home()
launch = home / 'Library' / 'LaunchAgents'
install_root = Path(os.environ['CMF_INSTALL_ROOT'])
script_dir = install_root / 'scripts'
template_dir = install_root / 'templates' / 'launchd'
launch.mkdir(parents=True, exist_ok=True)

# Resolve python3 path dynamically instead of hardcoding a brew-specific path
_python3_bin = shutil.which('python3')
# Prefer the higher-version brew python if available
for _candidate in ['/opt/homebrew/opt/python@3.13/libexec/bin/python3',
                   '/opt/homebrew/opt/python@3.11/libexec/bin/python3']:
    if os.path.isfile(_candidate):
        _python3_bin = _candidate
        break

if _python3_bin:
    _daemon_program_args = [_python3_bin, str(script_dir / 'context_daemon.py')]
else:
    _daemon_program_args = ['/usr/bin/env', 'python3', str(script_dir / 'context_daemon.py')]

patches = [
    (
        template_dir / 'com.contextmesh.daemon.plist',
        launch / 'com.contextmesh.daemon.plist',
        _daemon_program_args,
        str(script_dir),
        {
            'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
            'CONTEXT_MESH_ENABLE_SHELL_MONITOR': '0',
            'CONTEXT_MESH_ENABLE_OPENCODE_MONITOR': '0',
            'CONTEXT_MESH_ENABLE_KILO_MONITOR': '0',
            'CONTEXT_MESH_ENABLE_REMOTE_SYNC': '0',
            'CONTEXT_MESH_POLL_INTERVAL_SEC': '180',
            'CONTEXT_MESH_FAST_POLL_INTERVAL_SEC': '20',
            'CONTEXT_MESH_IDLE_SLEEP_CAP_SEC': '600',
            'CONTEXT_MESH_CODEX_SESSION_SCAN_INTERVAL_SEC': '300',
            'CONTEXT_MESH_CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC': '300',
            'CONTEXT_MESH_ANTIGRAVITY_SCAN_INTERVAL_SEC': '300',
            'CONTEXT_MESH_SUSPEND_ANTIGRAVITY_WHEN_BUSY': '1',
            'CONTEXT_MESH_ANTIGRAVITY_BUSY_LS_THRESHOLD': '2',
            'CONTEXT_MESH_ANTIGRAVITY_INGEST_MODE': 'final_only',
            'CONTEXT_MESH_ANTIGRAVITY_QUIET_SEC': '240',
            'CONTEXT_MESH_ANTIGRAVITY_MIN_DOC_BYTES': '500',
            'CONTEXT_MESH_CYCLE_BUDGET_SEC': '8',
            'CONTEXT_MESH_INDEX_SYNC_MIN_INTERVAL_SEC': '20',
            'CONTEXT_MESH_ERROR_BACKOFF_MAX_SEC': '30',
            'CONTEXT_MESH_LOOP_JITTER_SEC': '0.7',
        },
    ),
    (
        template_dir / 'com.contextmesh.healthcheck.plist',
        launch / 'com.contextmesh.healthcheck.plist',
        ['/bin/bash', str(script_dir / 'context_healthcheck.sh'), '--quiet'],
        None,
        {
            'UNIFIED_CONTEXT_STORAGE_ROOT': os.environ.get('UNIFIED_CONTEXT_STORAGE_ROOT', str(home / '.unified_context_data')),
        },
    ),
]

for template_path, plist_path, args, wd, extra_env in patches:
    if not template_path.exists():
        print(f"[deploy] skip missing template: {template_path}")
        continue
    raw = template_path.read_text(encoding='utf-8')
    raw = raw.replace('__SCRIPTS_DIR__', str(script_dir))
    raw = raw.replace('__LOG_DIR__', str(home / '.context_system' / 'logs'))
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
    print(f"[deploy] patched plist: {plist_path.name}")
PY
fi

if [ "$RELOAD_LAUNCHD" = "1" ] && command -v launchctl >/dev/null 2>&1; then
  UID_NUM="$(id -u)"
  python3 - <<PY
import subprocess, time, urllib.request
from pathlib import Path
home = Path.home()
uid_num = "${UID_NUM}"
labels = ["com.contextmesh.daemon", "com.contextmesh.healthcheck"]


def run(cmd, timeout=8):
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        print(f"[deploy] launchctl timeout: {' '.join(cmd)}")
        return 124


def wait_http_200(url, timeout_sec=60):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def wait_process(pattern, timeout_sec=20):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return True
        time.sleep(1)
    return False

for label in labels:
    plist = home / 'Library' / 'LaunchAgents' / f'{label}.plist'
    if not plist.exists():
        continue
    run(['launchctl', 'bootout', f'gui/{uid_num}', str(plist)])
    rc = run(['launchctl', 'bootstrap', f'gui/{uid_num}', str(plist)])
    if rc != 0:
        print(f'[deploy] launchctl bootstrap failed: {label}')
        raise SystemExit(1)
    run(['launchctl', 'kickstart', f'gui/{uid_num}/{label}'], timeout=5)
    print(f'[deploy] reloaded launchd: {label}')
    if label == 'com.contextmesh.daemon' and not wait_process('context_daemon.py|viking_daemon.py'):
        print('[deploy] ERROR: context daemon not detected after reload')
        raise SystemExit(1)
PY
fi

bash "$INSTALL_ROOT/scripts/context_healthcheck.sh" --quiet || true
log "unified context deploy done"
