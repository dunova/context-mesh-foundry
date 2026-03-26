# Troubleshooting

This guide covers common issues with ContextGO, organized by symptom. Run the health check first for any issue:

```bash
python3 scripts/context_cli.py health
bash scripts/context_healthcheck.sh
```

## Contents

- [Slow initial indexing](#slow-initial-indexing)
- [Viewer not reachable](#viewer-not-reachable)
- [Search returns no results](#search-returns-no-results)
- [Permission or path errors](#permission-or-path-errors)
- [Daemon not capturing sessions](#daemon-not-capturing-sessions)
- [Smoke test failures](#smoke-test-failures)
- [Installed runtime issues](#installed-runtime-issues)
- [Pre-release validation](#pre-release-validation)

---

## Slow initial indexing

**Symptom:** `python3 scripts/context_cli.py health` or `search` takes noticeably long on first run.

**Cause:** `session_index.py` needs to scan all available session history and build the SQLite index from scratch. This is a one-time cost.

**Resolution:**

1. Run `health` once to completion before issuing search queries. The index builds incrementally afterward.

2. Confirm the index files exist:
   ```bash
   ls ~/.contextgo/index/
   # Expected: session_index.db  memory_index.db
   ```

3. If you have overridden the storage root, confirm the actual path:
   ```bash
   python3 -c "from scripts.context_config import storage_root; print(storage_root())"
   ```

4. To measure actual IO or CPU bottleneck:
   ```bash
   python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
   ```

---

## Viewer not reachable

**Symptom:** After running `python3 scripts/context_cli.py serve`, `http://127.0.0.1:38880/api/health` returns a connection error.

**Cause:** Port conflict, stale process, health check not yet ready, or server bound to a non-loopback address.

**Resolution:**

1. Verify the CLI is healthy before starting the viewer:
   ```bash
   python3 scripts/context_cli.py health
   ```

2. Check for port conflicts and kill stale processes:
   ```bash
   lsof -iTCP:38880
   # If a process is listed, kill it:
   kill <PID>
   ```

3. Start the viewer and wait a moment before querying:
   ```bash
   python3 scripts/context_cli.py serve --host 127.0.0.1 --port 38880
   curl http://127.0.0.1:38880/api/health
   ```

4. Run the smoke test, which includes the viewer health check:
   ```bash
   python3 scripts/context_smoke.py
   ```

---

## Search returns no results

**Symptom:** `python3 scripts/context_cli.py search "..."` returns empty results for sessions you expect to find.

**Cause:** The daemon has not yet written recent sessions, the source directories are not being watched, or the index has not been refreshed.

**Resolution:**

1. Confirm data is present in the storage root:
   ```bash
   ls ~/.contextgo/raw/
   ls ~/.contextgo/index/
   ```

2. Verify that common source paths are present on disk (the daemon reads these):
   - `~/.codex/sessions/`
   - `~/.claude/projects/`
   - `~/.zsh_history`
   - `~/.bash_history`

3. Force an index refresh via health:
   ```bash
   python3 scripts/context_cli.py health
   ```

4. Run the full smoke and quality gate:
   ```bash
   python3 scripts/context_smoke.py
   python3 scripts/e2e_quality_gate.py
   ```

5. If using a custom storage root, confirm it is correctly set:
   ```bash
   echo $CONTEXTGO_STORAGE_ROOT
   python3 -c "from scripts.context_config import storage_root; print(storage_root())"
   ```

---

## Permission or path errors

**Symptom:** Errors like `PermissionError`, `FileNotFoundError`, or `OSError` when reading or writing index files.

**Resolution:**

1. Check that the storage root is owned by the current user:
   ```bash
   ls -ld ~/.contextgo
   ls -ld ~/.contextgo/index
   ls -ld ~/.contextgo/raw
   ```

2. Confirm current user matches directory owner:
   ```bash
   stat ~/.contextgo
   whoami
   ```

3. Run the deep health check to diagnose missing directories or permission issues:
   ```bash
   bash scripts/context_healthcheck.sh --deep
   ```

4. If the storage root was moved or deleted, recreate it and re-run smoke:
   ```bash
   mkdir -p ~/.contextgo/index ~/.contextgo/raw
   python3 scripts/context_smoke.py
   ```

5. If using `CONTEXTGO_STORAGE_ROOT` to point to a custom path, confirm the target is writable:
   ```bash
   test -w "$CONTEXTGO_STORAGE_ROOT" && echo "writable" || echo "not writable"
   ```

---

## Daemon not capturing sessions

**Symptom:** New terminal or agent sessions are not appearing in search results even after running `health`.

**Cause:** The daemon process is not running, or the source paths it watches are not in the expected locations.

**Resolution:**

1. Check if the daemon is running:
   ```bash
   ps aux | grep context_daemon
   ```

2. Start the daemon if it is not running:
   ```bash
   python3 scripts/context_daemon.py &
   ```

3. Verify the daemon can write to the storage root:
   ```bash
   python3 scripts/context_cli.py health
   ls ~/.contextgo/raw/
   ```

4. For persistent background operation, use the provided service template:
   ```bash
   ls templates/
   # launchd template for macOS, systemd-user template for Linux
   ```

---

## Smoke test failures

**Symptom:** `python3 scripts/context_smoke.py` exits with a non-zero code or reports failures.

**Resolution:**

1. Check what specific step failed in the smoke output. The smoke test runs these in order:
   - `context_cli health`
   - e2e quality gate
   - write / read / export / import
   - semantic pipeline
   - viewer serve

2. Run only the health step to isolate:
   ```bash
   python3 scripts/context_cli.py health
   ```

3. Confirm the storage root is writable and the index files exist:
   ```bash
   ls -la ~/.contextgo/index/
   ```

4. Check for syntax or import errors in any recently changed scripts:
   ```bash
   python3 -m py_compile scripts/*.py
   ```

5. Run individual tests to narrow down the failure:
   ```bash
   python3 -m pytest scripts/test_context_cli.py -v
   python3 -m pytest scripts/test_context_core.py -v
   python3 -m pytest scripts/test_session_index.py -v
   ```

---

## Installed runtime issues

**Symptom:** `python3 scripts/smoke_installed_runtime.py` fails, or scripts are missing from the installed location.

**Default installed path:** `~/.local/share/contextgo/scripts`

**Resolution:**

1. Check that required files exist at the installed path:
   ```bash
   ls ~/.local/share/contextgo/scripts/context_cli.py
   ls ~/.local/share/contextgo/scripts/e2e_quality_gate.py
   ```

2. Confirm `CONTEXTGO_INSTALL_ROOT` if you use a custom install location:
   ```bash
   echo $CONTEXTGO_INSTALL_ROOT
   ```

3. Re-run the deployment script to restore missing files:
   ```bash
   bash scripts/unified_context_deploy.sh
   ```

4. After restoring, re-run the installed smoke:
   ```bash
   python3 scripts/smoke_installed_runtime.py
   ```

---

## Pre-release validation

Run this full sequence before tagging a release:

```bash
# Syntax checks
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py

# Unit and integration tests
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_session_index.py

# End-to-end quality gate
python3 scripts/e2e_quality_gate.py

# Performance baseline
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark

# Smoke tests (working copy and installed runtime)
python3 scripts/context_smoke.py
python3 scripts/smoke_installed_runtime.py

# Health check
bash scripts/context_healthcheck.sh

# Native tests
cd native/session_scan_go && go test ./...
cd native/session_scan && CARGO_INCREMENTAL=0 cargo test
```

All commands depend on `storage_root()` defaulting to `~/.contextgo`. Confirm the current user has read/write access before running. If `CONTEXTGO_STORAGE_ROOT` is set, all paths follow that override instead.
