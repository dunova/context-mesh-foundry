# Troubleshooting / 故障排查

> [CONFIGURATION.md](CONFIGURATION.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [API.md](API.md)

Start here for any issue — run the health check first.

遇到任何问题，请先运行健康检查：

```bash
contextgo health
bash scripts/context_healthcheck.sh
```

---

## Contents / 目录

- [Slow initial indexing](#slow-initial-indexing)
- [Viewer not reachable](#viewer-not-reachable)
- [Search returns no results](#search-returns-no-results)
- [Permission or path errors](#permission-or-path-errors)
- [Daemon not capturing sessions](#daemon-not-capturing-sessions)
- [Native binary not found](#native-binary-not-found)
- [Smoke test failures](#smoke-test-failures)
- [Installed runtime issues](#installed-runtime-issues)
- [Pre-release validation checklist](#pre-release-validation-checklist)

---

## Slow initial indexing

**首次索引慢**

**Symptom / 症状:** `health` or `search` takes noticeably long on first run.

**Cause / 原因:** `session_index.py` scans all available session history and builds the SQLite index from scratch. This is a one-time cost.

**Resolution / 解决方法:**

1. Run `health` once to completion before issuing search queries; the index builds incrementally afterward.

2. Confirm index files exist:
   ```bash
   ls ~/.contextgo/index/
   # Expected: session_index.db  memory_index.db
   ```

3. Verify the resolved storage root if you have overridden it:
   ```bash
   python3 -c "from contextgo.context_config import storage_root; print(storage_root())"
   ```

4. Measure the actual bottleneck:
   ```bash
   python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
   ```

---

## Viewer not reachable

**Viewer 无法访问**

**Symptom / 症状:** After running `contextgo serve`, `http://127.0.0.1:37677/api/health` returns a connection error.

**Cause / 原因:** Port conflict, stale process, or server bound to a non-loopback address without a token.

**Resolution / 解决方法:**

1. Verify the CLI is healthy before starting the viewer:
   ```bash
   contextgo health
   ```

2. Check for port conflicts and kill any stale process:
   ```bash
   lsof -iTCP:37677
   kill <PID>  # if a process is listed
   ```

3. Start the viewer and probe it:
   ```bash
   contextgo serve --host 127.0.0.1 --port 37677
   curl http://127.0.0.1:37677/api/health
   ```

4. Run the smoke test, which includes the viewer health check:
   ```bash
   contextgo smoke --sandbox
   ```

---

## Search returns no results

**搜索无结果**

**Symptom / 症状:** `contextgo search "..."` returns empty results for sessions you expect to find.

**Cause / 原因:** The daemon has not written recent sessions yet, source directories are not being watched, or the index has not been refreshed.

**Resolution / 解决方法:**

1. Confirm data is present in the storage root:
   ```bash
   ls ~/.contextgo/raw/
   ls ~/.contextgo/index/
   ```

2. Verify that common source paths exist on disk:
   - `~/.codex/sessions/`
   - `~/.claude/projects/`
   - `~/.zsh_history`
   - `~/.bash_history`

3. Force an index refresh:
   ```bash
   contextgo health
   ```

4. If using a custom storage root, confirm it is correctly set:
   ```bash
   echo $CONTEXTGO_STORAGE_ROOT
   python3 -c "from contextgo.context_config import storage_root; print(storage_root())"
   ```

5. Run the quality gate for a full diagnostic:
   ```bash
   python3 scripts/e2e_quality_gate.py
   ```

---

## Permission or path errors

**权限或路径错误**

**Symptom / 症状:** `PermissionError`, `FileNotFoundError`, or `OSError` when reading or writing index files.

**Resolution / 解决方法:**

1. Check that the storage root is owned by the current user:
   ```bash
   ls -ld ~/.contextgo ~/.contextgo/index ~/.contextgo/raw
   stat ~/.contextgo; whoami
   ```

2. Run the deep health check:
   ```bash
   bash scripts/context_healthcheck.sh --deep
   ```

3. If the storage root was moved or deleted, recreate it:
   ```bash
   mkdir -p ~/.contextgo/index ~/.contextgo/raw
   contextgo smoke
   ```

4. If using a custom path, confirm it is writable:
   ```bash
   test -w "$CONTEXTGO_STORAGE_ROOT" && echo "writable" || echo "not writable"
   ```

---

## Daemon not capturing sessions

**Daemon 未采集会话**

**Symptom / 症状:** New terminal or agent sessions do not appear in search results even after running `health`.

**Cause / 原因:** The daemon process is not running, or source paths are not in the expected locations.

**Resolution / 解决方法:**

1. Check whether the daemon is running:
   ```bash
   ps aux | grep context_daemon
   ```

2. Start the daemon if it is not running:
   ```bash
   python3 src/contextgo/context_daemon.py &
   ```

3. Verify the daemon can write to the storage root:
   ```bash
   contextgo health
   ls ~/.contextgo/raw/
   ```

4. For persistent background operation, install via the provided service template:
   ```bash
   ls docs/templates/
   # launchd template for macOS, systemd-user template for Linux
   bash scripts/unified_context_deploy.sh
   ```

---

## Native binary not found

**找不到 native 二进制**

**Symptom / 症状:** `health` or `native-scan` reports the native backend is unavailable, or `context_native.py` logs a warning that no binary was found.

**Cause / 原因:** The Rust or Go binary has not been built, was removed, or is not in the expected path.

**Resolution / 解决方法:**

1. Check native backend status:
   ```bash
   contextgo health
   contextgo native-scan --backend auto --query test
   ```

2. Build the Go binary:
   ```bash
   cd native/session_scan_go
   go build -o session_scan_go .
   ```

3. Build the Rust binary:
   ```bash
   cd native/session_scan
   CARGO_TARGET_DIR="${CONTEXTGO_NATIVE_TARGET_DIR:-$HOME/.cache/contextgo/target}" \
     cargo build --release
   ```

4. Verify the binary is discoverable:
   ```bash
   python3 src/contextgo/context_native.py
   ```

5. The health probe result is cached (default TTL 30 s). To disable caching during development:
   ```bash
   export CONTEXTGO_NATIVE_HEALTH_CACHE_TTL_SEC=0
   ```

---

## Smoke test failures

**Smoke 测试失败**

**Symptom / 症状:** `contextgo smoke --sandbox` exits with a non-zero code.

The smoke test runs these steps in order:

Smoke 测试按以下顺序执行：

1. `contextgo health`
2. e2e quality gate
3. write / read / export / import
4. semantic pipeline
5. viewer serve

**Resolution / 解决方法:**

1. Isolate with just the health step:
   ```bash
   contextgo health
   ```

2. Check for syntax errors in recently changed scripts:
   ```bash
   python3 -m py_compile src/contextgo/*.py
   ```

3. Run individual test files to narrow down the failure:
   ```bash
   python3 -m pytest tests/test_context_cli.py -v
   python3 -m pytest tests/test_context_core.py -v
   python3 -m pytest tests/test_session_index.py -v
   python3 -m pytest tests/test_context_native.py -v
   python3 -m pytest tests/test_context_smoke.py -v
   ```

4. Confirm the storage root is writable and index files exist:
   ```bash
   ls -la ~/.contextgo/index/
   ```

---

## Installed runtime issues

**已安装运行时问题**

**Symptom / 症状:** `smoke_installed_runtime.py` fails, or scripts are missing from the installed location.

**Default installed path / 默认安装路径:** `~/.local/share/contextgo`

**Resolution / 解决方法:**

1. Verify required files exist at the installed path:
   ```bash
   ls ~/.local/share/contextgo/src/contextgo/context_cli.py
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

4. Re-run the installed smoke:
   ```bash
   contextgo smoke
   ```

---

## Pre-release validation checklist

**发布前验证清单**

Run this full sequence before tagging a release. All commands assume the current user has read/write access to `~/.contextgo` (or `$CONTEXTGO_STORAGE_ROOT` if overridden).

发布打 tag 前运行以下完整序列。所有命令要求当前用户对 `~/.contextgo`（或 `$CONTEXTGO_STORAGE_ROOT`）有读写权限。

```bash
# 1. Syntax checks / 语法检查
bash -n scripts/*.sh
python3 -m py_compile src/contextgo/*.py

# 2. Unit and integration tests / 单元与集成测试
python3 -m pytest tests/

# 3. End-to-end quality gate / 端到端质量门控
python3 scripts/e2e_quality_gate.py

# 4. Performance baseline / 性能基线
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark

# 5. Smoke tests / Smoke 测试
contextgo smoke --sandbox
python3 scripts/smoke_installed_runtime.py

# 6. Health check / 健康检查
bash scripts/context_healthcheck.sh

# 7. Native tests / native 测试
cd native/session_scan_go && go test ./...
cd native/session_scan && CARGO_INCREMENTAL=0 cargo test
```
