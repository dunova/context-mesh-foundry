#!/usr/bin/env bash
# auto_update.sh -- Automatic ContextGO version governance.
#
# Pulls latest from GitHub, upgrades the pipx runtime, redeploys
# LaunchAgents, and removes stale version artifacts.  Designed to
# run as a periodic LaunchAgent on macOS.
#
# Exit codes:
#   0  Already up-to-date or successfully upgraded.
#   1  Update failed (logged, non-fatal for launchd).
#
# Environment variables:
#   CONTEXTGO_REPO_DIR    Local git clone  (default: ~/ContextGO)
#   CONTEXTGO_LOG_DIR     Log directory    (default: ~/.contextgo/logs)
set -euo pipefail

REPO_DIR="${CONTEXTGO_REPO_DIR:-$HOME/ContextGO}"
LOG_DIR="${CONTEXTGO_LOG_DIR:-$HOME/.contextgo/logs}"
LOG_FILE="$LOG_DIR/auto_update.log"
LOCK_FILE="$LOG_DIR/.auto_update.lock"

mkdir -p "$LOG_DIR"

log() { printf '[auto-update %s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG_FILE"; }

# --- Lock: prevent concurrent runs ---
cleanup() { rm -f "$LOCK_FILE"; }
trap cleanup EXIT
if [ -f "$LOCK_FILE" ]; then
    pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "another auto-update is running (pid=$pid), skipping"
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"

# --- Step 1: Git pull ---
if [ ! -d "$REPO_DIR/.git" ]; then
    log "ERROR: git repo not found at $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"
git fetch origin main --tags --quiet 2>&1 | tee -a "$LOG_FILE" || true

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse origin/main)"

if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
    log "already up-to-date at $(git describe --tags --always)"
    # Still check if pipx version matches
    LATEST_TAG="$(git tag --sort=-v:refname | head -1)"
    PIPX_VERSION="$(pipx list 2>/dev/null | grep -A1 'contextgo' | grep -oP '\d+\.\d+\.\d+' | head -1 || echo unknown)"
    TAG_VERSION="${LATEST_TAG#v}"
    if [ "$PIPX_VERSION" = "$TAG_VERSION" ]; then
        log "pipx version matches: $PIPX_VERSION"
        exit 0
    else
        log "pipx version mismatch: pipx=$PIPX_VERSION, tag=$TAG_VERSION — upgrading"
    fi
else
    log "update available: $(git describe --tags --always) -> $(git rev-parse --short origin/main)"
    git reset --hard origin/main --quiet 2>&1 | tee -a "$LOG_FILE"
    log "git updated to $(git describe --tags --always)"
fi

# --- Step 2: Upgrade pipx install ---
if command -v pipx >/dev/null 2>&1; then
    log "upgrading pipx install..."
    if pipx install --force '.[vector]' 2>&1 | tee -a "$LOG_FILE"; then
        NEW_VERSION="$(contextgo health 2>/dev/null | head -1 || echo 'unknown')"
        log "pipx upgrade complete: $NEW_VERSION"
    else
        log "WARNING: pipx upgrade failed, trying from PyPI..."
        pipx upgrade contextgo 2>&1 | tee -a "$LOG_FILE" || true
    fi
else
    log "ERROR: pipx not found"
    exit 1
fi

# --- Step 3: Redeploy (LaunchAgents + scripts) ---
DEPLOY_SCRIPT="$REPO_DIR/scripts/unified_context_deploy.sh"
if [ -f "$DEPLOY_SCRIPT" ]; then
    log "running unified deploy..."
    RELOAD_LAUNCHD=1 PATCH_LAUNCHD=1 bash "$DEPLOY_SCRIPT" 2>&1 | tee -a "$LOG_FILE"
    log "deploy complete"
else
    log "WARNING: unified_context_deploy.sh not found, skipping deploy"
fi

# --- Step 4: Clean stale artifacts ---
# Remove old context-mesh-foundry paths if they exist
OLD_INSTALL="$HOME/.local/share/context-mesh-foundry"
if [ -d "$OLD_INSTALL" ]; then
    log "removing stale context-mesh-foundry install at $OLD_INSTALL"
    rm -rf "$OLD_INSTALL"
fi

# Remove old LaunchAgents with wrong labels
for old_plist in \
    "$HOME/Library/LaunchAgents/com.contextmesh.daemon.plist" \
    "$HOME/Library/LaunchAgents/com.contextmesh.healthcheck.plist" \
    "$HOME/Library/LaunchAgents/com.context.skill-sync.plist"
do
    if [ -f "$old_plist" ]; then
        label="$(defaults read "$old_plist" Label 2>/dev/null || true)"
        if [ -n "$label" ]; then
            launchctl bootout "gui/$(id -u)" "$old_plist" 2>/dev/null || true
        fi
        rm -f "$old_plist"
        log "removed stale plist: $(basename "$old_plist")"
    fi
done

# Remove old storage roots if empty
for old_storage in "$HOME/.unified_context_data" "$HOME/.context_system"; do
    if [ -d "$old_storage" ]; then
        # Only remove if it has no real data (just logs)
        file_count="$(find "$old_storage" -type f -not -name '*.log' -not -name '*.err' | wc -l)"
        if [ "$file_count" -eq 0 ]; then
            rm -rf "$old_storage"
            log "removed empty stale storage: $old_storage"
        else
            log "keeping stale storage with data: $old_storage ($file_count files)"
        fi
    fi
done

# --- Step 5: Verify ---
if command -v contextgo >/dev/null 2>&1; then
    log "verification:"
    contextgo health 2>&1 | head -5 | tee -a "$LOG_FILE"
    log "auto-update complete"
else
    log "WARNING: contextgo not on PATH after update"
fi
