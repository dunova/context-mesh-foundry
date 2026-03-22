#!/bin/bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOME_DIR="${HOME:-$(cd ~ && pwd)}"
CANON_OV="${CANON_OV_ROOT:-$REPO_ROOT}"
GSD_CANON_ROOT="${GSD_CANON_ROOT:-}"
CLAUDE_GSD_LINK="${CLAUDE_GSD_LINK:-$HOME_DIR/.claude/get-shit-done}"
UNIFIED_CONTEXT_STORAGE_ROOT="${UNIFIED_CONTEXT_STORAGE_ROOT:-${OPENVIKING_STORAGE_ROOT:-$HOME_DIR/.unified_context_data}}"
PATCH_LAUNCHD="${PATCH_LAUNCHD:-1}"
RELOAD_LAUNCHD="${RELOAD_LAUNCHD:-1}"
APPLY_CONTEXT_POLICY="${APPLY_CONTEXT_POLICY:-1}"

OV_SCRIPT_TARGETS=(
  "$HOME_DIR/.codex/skills/openviking-memory-sync/scripts"
  "$HOME_DIR/.claude/skills/openviking-memory-sync/scripts"
  "$HOME_DIR/.gemini/antigravity/skills/openviking-memory-sync/scripts"
  "$HOME_DIR/.agents/skills/openviking-memory-sync/scripts"
)

GSD_RUNTIME_TARGETS=(
  "$HOME_DIR/.claude/skills/gsd-v1"
  "$HOME_DIR/.gemini/antigravity/skills/gsd-v1"
  "$HOME_DIR/.agents/skills/gsd-v1"
)

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

sync_file_if_parent_exists() {
  local src="$1" dst="$2"
  local parent
  parent="$(dirname "$dst")"
  if [ -d "$parent" ]; then
    mkdir -p "$parent"
    cp "$src" "$dst"
    log "synced file: $src -> $dst"
  else
    log "skip (missing parent): $parent"
  fi
}

log "unified context deploy start"
require_dir "$CANON_OV"
require_dir "$CANON_OV/scripts"

mkdir -p "$UNIFIED_CONTEXT_STORAGE_ROOT"
chmod 700 "$UNIFIED_CONTEXT_STORAGE_ROOT" >/dev/null 2>&1 || true
mkdir -p "$HOME_DIR/.context_system/logs"
chmod 700 "$HOME_DIR/.context_system" "$HOME_DIR/.context_system/logs" >/dev/null 2>&1 || true

for dst in "${OV_SCRIPT_TARGETS[@]}"; do
  if [ -d "$(dirname "$dst")" ]; then
    sync_dir "$CANON_OV/scripts" "$dst"
  else
    log "skip (missing parent): $(dirname "$dst")"
  fi
done

if [ -n "$GSD_CANON_ROOT" ] && [ -d "$GSD_CANON_ROOT" ]; then
  mkdir -p "$HOME_DIR/.claude"
  if [ -L "$CLAUDE_GSD_LINK" ] || [ ! -e "$CLAUDE_GSD_LINK" ]; then
    ln -sfn "$GSD_CANON_ROOT" "$CLAUDE_GSD_LINK"
    log "linked: $CLAUDE_GSD_LINK -> $GSD_CANON_ROOT"
  else
    log "warning: $CLAUDE_GSD_LINK exists and is not a symlink; keeping as-is"
  fi

  for t in "${GSD_RUNTIME_TARGETS[@]}"; do
    if [ -d "$(dirname "$t")" ]; then
      [ -d "$GSD_CANON_ROOT/bin" ] && sync_dir "$GSD_CANON_ROOT/bin" "$t/bin"
      [ -d "$GSD_CANON_ROOT/references" ] && sync_dir "$GSD_CANON_ROOT/references" "$t/references"
    else
      log "skip (missing parent): $(dirname "$t")"
    fi
  done
else
  log "GSD runtime sync skipped (set GSD_CANON_ROOT to enable)"
fi

if [ -f "$REPO_ROOT/integrations/gsd/workflows/health.md" ]; then
  for wf_target in \
    "$HOME_DIR/.claude/skills/gsd-v1/workflows/health.md" \
    "$HOME_DIR/.gemini/antigravity/skills/gsd-v1/workflows/health.md" \
    "$HOME_DIR/.agents/skills/gsd-v1/workflows/health.md" \
    "$HOME_DIR/.codex/skills/gsd-v1/workflows/health.md"
  do
    sync_file_if_parent_exists "$REPO_ROOT/integrations/gsd/workflows/health.md" "$wf_target"
  done
fi

if [ "$APPLY_CONTEXT_POLICY" = "1" ] && [ -f "$REPO_ROOT/scripts/apply_context_first_policy.sh" ]; then
  log "applying context-first policy to terminal entry files"
  bash "$REPO_ROOT/scripts/apply_context_first_policy.sh" || log "warning: context-first policy apply failed"
fi

if [ "$PATCH_LAUNCHD" = "1" ] && command -v launchctl >/dev/null 2>&1; then
export CANON_OV_ROOT="$CANON_OV"
export CANON_OV_SCRIPTS_ROOT="$CANON_OV/scripts"
export UNIFIED_CONTEXT_STORAGE_ROOT
python3 - <<'PY'
import plistlib
from pathlib import Path
import os

import shutil

home = Path.home()
launch = home / 'Library' / 'LaunchAgents'
script_dir = Path(os.environ['CANON_OV_SCRIPTS_ROOT'])

# Resolve python3 path dynamically instead of hardcoding a brew-specific path
_python3_bin = shutil.which('python3')
# Prefer the higher-version brew python if available
for _candidate in ['/opt/homebrew/opt/python@3.13/libexec/bin/python3',
                   '/opt/homebrew/opt/python@3.11/libexec/bin/python3']:
    if os.path.isfile(_candidate):
        _python3_bin = _candidate
        break

if _python3_bin:
    _daemon_program_args = [_python3_bin, str(script_dir / 'viking_daemon.py')]
else:
    _daemon_program_args = ['/usr/bin/env', 'python3', str(script_dir / 'viking_daemon.py')]

patches = [
    (
        launch / 'com.openviking.daemon.plist',
        _daemon_program_args,
        str(script_dir),
        {
            'VIKING_ENABLE_SHELL_MONITOR': '0',
            'VIKING_ENABLE_OPENCODE_MONITOR': '0',
            'VIKING_ENABLE_KILO_MONITOR': '0',
            'VIKING_POLL_INTERVAL_SEC': '180',
            'VIKING_FAST_POLL_INTERVAL_SEC': '20',
            'VIKING_IDLE_SLEEP_CAP_SEC': '600',
            'VIKING_CODEX_SESSION_SCAN_INTERVAL_SEC': '300',
            'VIKING_CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC': '300',
            'VIKING_ANTIGRAVITY_SCAN_INTERVAL_SEC': '300',
            'VIKING_SUSPEND_ANTIGRAVITY_WHEN_BUSY': '1',
            'VIKING_ANTIGRAVITY_BUSY_LS_THRESHOLD': '2',
            'VIKING_ANTIGRAVITY_INGEST_MODE': 'final_only',
            'VIKING_ANTIGRAVITY_QUIET_SEC': '240',
            'VIKING_ANTIGRAVITY_MIN_DOC_BYTES': '500',
            'VIKING_CYCLE_BUDGET_SEC': '8',
            'VIKING_INDEX_SYNC_MIN_INTERVAL_SEC': '20',
            'VIKING_ERROR_BACKOFF_MAX_SEC': '30',
            'VIKING_LOOP_JITTER_SEC': '0.7',
        },
    ),
    (
        launch / 'com.openviking.server.plist',
        ['/bin/bash', str(script_dir / 'start_openviking.sh')],
        str(script_dir),
        {
            'OPENVIKING_SKIP_PIP': '1',
            'OPENVIKING_ALLOW_NAS_GENERATOR': '0',
            'OPENVIKING_PORT_WAIT_SEC': '30',
            'OPENVIKING_GENERATOR_TIMEOUT_SEC': '15',
            'OPENVIKING_CONFIG_GENERATOR': '',
            'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
            'UNIFIED_CONTEXT_STORAGE_ROOT': os.environ.get('UNIFIED_CONTEXT_STORAGE_ROOT', str(home / '.unified_context_data')),
        },
    ),
    (
        launch / 'com.context.healthcheck.plist',
        ['/bin/bash', str(script_dir / 'context_healthcheck.sh'), '--quiet'],
        None,
        {
            'UNIFIED_CONTEXT_STORAGE_ROOT': os.environ.get('UNIFIED_CONTEXT_STORAGE_ROOT', str(home / '.unified_context_data')),
            'RECALL_SCRIPT': str(home / '.agents' / 'skills' / 'recall' / 'scripts' / 'recall.py'),
        },
    ),
]

for plist_path, args, wd, extra_env in patches:
    if not plist_path.exists():
        print(f"[deploy] skip missing plist: {plist_path}")
        continue
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
labels = ["com.openviking.server", "com.openviking.daemon", "com.context.healthcheck"]


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
    if label == 'com.openviking.server' and not wait_http_200('http://127.0.0.1:8090/health'):
        print('[deploy] ERROR: openviking server health check did not reach HTTP 200')
        raise SystemExit(1)
    if label == 'com.openviking.daemon' and not wait_process('viking_daemon.py'):
        print('[deploy] ERROR: viking_daemon.py not detected after reload')
        raise SystemExit(1)
PY
fi

bash "$CANON_OV/scripts/context_healthcheck.sh" --quiet || true
log "unified context deploy done"
