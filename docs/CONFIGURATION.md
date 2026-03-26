# Configuration Reference / 配置参考

> [ARCHITECTURE.md](ARCHITECTURE.md) · [API.md](API.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

ContextGO is configured entirely through environment variables. All variables are optional; the defaults target local single-user operation with no external dependencies.

ContextGO 通过环境变量完成所有配置。所有变量均为可选，默认值适用于本地单用户模式，无需外部依赖。

**Quick start / 快速开始**

```bash
cp .env.example .env   # edit only what you need / 仅修改需要的项
# Never commit a populated .env file.
```

Variables are read at process startup; changes take effect only after restarting the affected process (daemon, server, or CLI invocation). The storage root is resolved once at import time by `scripts/context_config.py`.

变量在进程启动时读取，修改后需重启相应进程（daemon、server 或 CLI）才能生效。存储根目录由 `scripts/context_config.py` 在导入时一次性解析。

---

## Contents / 目录

- [Storage](#storage)
- [Viewer server](#viewer-server)
- [Daemon — source monitors](#daemon--source-monitors)
- [Daemon — timing and resources](#daemon--timing-and-resources)
- [Session index](#session-index)
- [CLI behavior](#cli-behavior)
- [Remote sync](#remote-sync)
- [Native backend](#native-backend)
- [Install and deploy](#install-and-deploy)
- [Benchmarks](#benchmarks)

---

## Storage

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_STORAGE_ROOT` | `~/.contextgo` | Root directory for all ContextGO data. Must be owned and writable by the current user. / 所有数据的根目录，须由当前用户所有且可写。 |
| `CONTEXTGO_SESSION_INDEX_DB_PATH` | `$CONTEXTGO_STORAGE_ROOT/index/session_index.db` | Override path for the session index SQLite database. / 会话索引 SQLite 数据库路径覆盖。 |
| `MEMORY_INDEX_DB_PATH` | `$CONTEXTGO_STORAGE_ROOT/index/memory_index.db` | Override path for the memory/observations SQLite database. / 记忆/观测索引 SQLite 数据库路径覆盖。 |

```bash
# Example: move storage to a separate volume
export CONTEXTGO_STORAGE_ROOT=/data/contextgo
```

---

## Viewer server

The viewer is a local HTTP server started with `context_cli serve`. It binds to loopback by default.

Viewer 是由 `context_cli serve` 启动的本地 HTTP 服务，默认绑定回环地址。

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_VIEWER_HOST` | `127.0.0.1` | Bind address. Do not set to `0.0.0.0` without also setting `CONTEXTGO_VIEWER_TOKEN`. / 绑定地址。非回环地址时必须同时设置 Token。 |
| `CONTEXTGO_VIEWER_PORT` | `37677` | TCP port. / TCP 端口。 |
| `CONTEXTGO_VIEWER_TOKEN` | _(empty)_ | Bearer token required in `X-Context-Token` for all `/api/` requests. Required for non-loopback binding. Generate with `openssl rand -hex 32`. / 非回环绑定时必填，通过 `X-Context-Token` 传递。 |
| `CONTEXTGO_VIEWER_MAX_POST_BYTES` | `1048576` | Maximum request body size (bytes) for POST endpoints. / POST 端点最大请求体大小（字节）。 |
| `CONTEXTGO_VIEWER_MAX_BATCH_IDS` | `500` | Maximum IDs in a single batch observations request. / 单次批量观测请求的最大 ID 数量。 |
| `CONTEXTGO_VIEWER_SSE_INTERVAL_SEC` | `1.0` | Server-sent events poll interval (seconds). / SSE 推送间隔（秒）。 |
| `CONTEXTGO_VIEWER_SSE_MAX_TICKS` | `120` | Maximum SSE ticks before the connection closes. / SSE 连接关闭前的最大 tick 数。 |
| `CONTEXTGO_VIEWER_SYNC_MIN_INTERVAL_SEC` | `5.0` | Minimum seconds between viewer sync operations. / viewer 同步操作的最小间隔（秒）。 |

---

## Daemon — source monitors

Each monitor controls whether the daemon watches a specific source. Boolean values accept `1`/`true`/`yes`/`on` or `0`/`false`/`no`/`off`.

每个监控开关控制 daemon 是否监听对应数据源。布尔值接受 `1`/`true`/`yes`/`on` 或 `0`/`false`/`no`/`off`。

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_ENABLE_SHELL_MONITOR` | `1` | Monitor `~/.zsh_history` and `~/.bash_history`. |
| `CONTEXTGO_ENABLE_CLAUDE_HISTORY_MONITOR` | `1` | Monitor `~/.claude/history.jsonl`. |
| `CONTEXTGO_ENABLE_CODEX_HISTORY_MONITOR` | `1` | Monitor `~/.codex/history.jsonl`. |
| `CONTEXTGO_ENABLE_CODEX_SESSION_MONITOR` | `1` | Monitor `~/.codex/sessions/` for full session JSONL files. |
| `CONTEXTGO_ENABLE_CLAUDE_TRANSCRIPTS_MONITOR` | `1` | Monitor `~/.claude/transcripts/`. |
| `CONTEXTGO_ENABLE_ANTIGRAVITY_MONITOR` | `1` | Monitor `~/.gemini/antigravity/brain/`. |
| `CONTEXTGO_ENABLE_OPENCODE_MONITOR` | `0` | Monitor OpenCode prompt history (disabled by default). |
| `CONTEXTGO_ENABLE_KILO_MONITOR` | `0` | Monitor Kilo prompt history (disabled by default). |
| `CONTEXTGO_ENABLE_REMOTE_SYNC` | `0` | Push history to the remote sync server. Requires `CONTEXTGO_REMOTE_URL`. |

---

## Daemon — timing and resources

These variables tune polling frequency, memory limits, and error handling. The defaults are conservative and suitable for most development machines.

这些变量控制轮询频率、内存限制与错误处理，默认值适合大多数开发机器。

### Core timing / 核心时序

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_POLL_INTERVAL_SEC` | `30` | Normal poll interval (seconds). / 常规轮询间隔（秒）。 |
| `CONTEXTGO_FAST_POLL_INTERVAL_SEC` | `3` | Fast poll interval when recent activity is detected. / 检测到近期活动时的快速轮询间隔。 |
| `CONTEXTGO_IDLE_SLEEP_CAP_SEC` | `180` | Maximum idle sleep cap (seconds). / 最大空闲休眠上限（秒）。 |
| `CONTEXTGO_IDLE_TIMEOUT_SEC` | `300` | Seconds of inactivity before entering idle mode. / 进入空闲模式前的不活跃时长（秒）。 |
| `CONTEXTGO_HEARTBEAT_INTERVAL_SEC` | `600` | Heartbeat log interval (seconds). / 心跳日志间隔（秒）。 |
| `CONTEXTGO_INDEX_SYNC_MIN_INTERVAL_SEC` | `20` | Minimum seconds between index sync operations. / 索引同步操作的最小间隔（秒）。 |
| `CONTEXTGO_LOOP_JITTER_SEC` | `0.7` | Random jitter added to loop sleep (float, seconds). / 循环休眠随机抖动（浮点数，秒）。 |
| `CONTEXTGO_ERROR_BACKOFF_MAX_SEC` | `30` | Maximum backoff between error retries (seconds). / 错误重试最大回退时间（秒）。 |
| `CONTEXTGO_CYCLE_BUDGET_SEC` | `8` | Maximum seconds per daemon work cycle. / daemon 单次工作周期最大耗时（秒）。 |

### Night mode / 夜间模式

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_NIGHT_POLL_START_HOUR` | `23` | Hour (0–23) at which night poll mode begins. / 夜间轮询模式开始时刻（0–23）。 |
| `CONTEXTGO_NIGHT_POLL_END_HOUR` | `7` | Hour (0–23) at which night poll mode ends. / 夜间轮询模式结束时刻（0–23）。 |
| `CONTEXTGO_NIGHT_POLL_INTERVAL_SEC` | `600` | Poll interval during night hours. / 夜间轮询间隔（秒）。 |

### Memory limits / 内存限制

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_MAX_TRACKED_SESSIONS` | `240` | Maximum in-memory tracked sessions. / 内存中最大跟踪会话数。 |
| `CONTEXTGO_MAX_FILE_CURSORS` | `800` | Maximum file byte-offset cursors in memory. / 内存中最大文件游标数。 |
| `CONTEXTGO_SESSION_TTL_SEC` | `7200` | TTL for inactive sessions in memory (seconds). / 内存中不活跃会话的生存时间（秒）。 |
| `CONTEXTGO_MAX_MESSAGES_PER_SESSION` | `500` | Maximum messages extracted per session. / 每个会话最大提取消息数。 |
| `CONTEXTGO_MAX_PENDING_FILES` | `5000` | Maximum pending outbound files queued in memory. / 内存中待发送文件队列上限。 |
| `CONTEXTGO_TRANSCRIPTS_LOOKBACK_DAYS` | `7` | Days to look back when scanning Claude transcript files. / 扫描 Claude 转录文件的回溯天数。 |

### Codex session scanning / Codex 会话扫描

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_CODEX_SESSION_SCAN_INTERVAL_SEC` | `90` | Rescan interval for Codex session files (seconds). / Codex 会话文件重新扫描间隔（秒）。 |
| `CONTEXTGO_MAX_CODEX_SESSION_FILES_PER_SCAN` | `1200` | Maximum Codex session files processed per scan pass. / 每次扫描处理的最大 Codex 会话文件数。 |

### Claude transcript scanning / Claude 转录扫描

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC` | `180` | Rescan interval for Claude transcript files (seconds). / Claude 转录文件重新扫描间隔（秒）。 |
| `CONTEXTGO_MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL` | `500` | Maximum Claude transcript files processed per poll pass. / 每次轮询处理的最大 Claude 转录文件数。 |

### Antigravity scanning / Antigravity 扫描

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_ANTIGRAVITY_SCAN_INTERVAL_SEC` | `120` | Rescan interval for Antigravity brain directories (seconds). / Antigravity brain 目录重新扫描间隔（秒）。 |
| `CONTEXTGO_MAX_ANTIGRAVITY_DIRS_PER_SCAN` | `400` | Maximum brain directories processed per scan pass. / 每次扫描处理的最大 brain 目录数。 |
| `CONTEXTGO_MAX_ANTIGRAVITY_SESSIONS` | `500` | Maximum Antigravity sessions tracked in memory. / 内存中最大 Antigravity 会话跟踪数。 |
| `CONTEXTGO_SUSPEND_ANTIGRAVITY_WHEN_BUSY` | `1` | Pause Antigravity scanning when the system is busy. / 系统繁忙时暂停 Antigravity 扫描。 |
| `CONTEXTGO_ANTIGRAVITY_BUSY_LS_THRESHOLD` | `3` | Number of active Antigravity language server processes considered "busy". / 判定为"繁忙"的 Antigravity 语言服务器进程数阈值。 |
| `CONTEXTGO_ANTIGRAVITY_INGEST_MODE` | `final_only` | `final_only` — stable documents only; `live` — ingest incrementally. / `final_only` 仅摄取稳定文档；`live` 增量摄取。 |
| `CONTEXTGO_ANTIGRAVITY_QUIET_SEC` | `180` | Quiet period (seconds) before a directory is considered stable. / 目录被视为稳定前的静默期（秒）。 |
| `CONTEXTGO_ANTIGRAVITY_MIN_DOC_BYTES` | `400` | Minimum document size (bytes) for Antigravity ingest. / Antigravity 摄取的最小文档大小（字节）。 |

---

## Session index

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_SESSION_MAX_CONTENT_CHARS` | `24000` | Maximum characters of session content stored per indexed entry. / 每个索引条目存储的最大会话内容字符数。 |
| `CONTEXTGO_SESSION_SYNC_MIN_INTERVAL_SEC` | `15` | Minimum seconds between session index sync operations. / 会话索引同步操作的最小间隔（秒）。 |
| `CONTEXTGO_SOURCE_CACHE_TTL_SEC` | `10` | TTL (seconds) for the source path discovery cache. / 数据源路径发现缓存的生存时间（秒）。 |
| `CONTEXTGO_INDEX_BATCH_SIZE` | `100` | Rows per SQLite transaction batch during sync. Increase for bulk imports; decrease if memory is constrained. Minimum: 10. / 同步时每次 SQLite 事务的批量行数。批量导入时可增大，内存受限时可减小，最小值为 10。 |
| `CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND` | _(empty)_ | Enable an experimental search backend by name. / 按名称启用实验性搜索后端。 |
| `CONTEXTGO_EXPERIMENTAL_SYNC_BACKEND` | _(empty)_ | Enable an experimental sync backend by name. / 按名称启用实验性同步后端。 |

---

## CLI behavior

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_LOCAL_SCAN_MAX_FILES` | `300` | Maximum local files scanned per `context_cli` search invocation. / 每次 `context_cli` 搜索调用扫描的最大本地文件数。 |
| `CONTEXTGO_LOCAL_SCAN_READ_BYTES` | `120000` | Maximum bytes read per file during local scanning. / 本地扫描时每个文件的最大读取字节数。 |

---

## Remote sync

Remote sync is disabled by default. Enable only when a ContextGO sync server is running.

远程同步默认关闭，仅在有 ContextGO 同步服务器时启用。

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_REMOTE_URL` | `http://127.0.0.1:8090/api/v1` | Base URL of the remote sync server. Use `https://` for non-localhost addresses. / 远程同步服务器基础 URL，非本机地址须使用 `https://`。 |
| `CONTEXTGO_ENABLE_REMOTE_MEMORY_HTTP` | `0` | Push new history entries to the remote sync server. / 推送新历史条目至远程同步服务器。 |
| `CONTEXTGO_EXPORT_HTTP_TIMEOUT_SEC` | `30` | Timeout (seconds) for outbound HTTP export requests. / 出站 HTTP 导出请求超时（秒）。 |
| `CONTEXTGO_PENDING_HTTP_TIMEOUT_SEC` | `15` | Timeout (seconds) for retrying pending outbound requests. / 重试待发 HTTP 请求超时（秒）。 |
| `CONTEXTGO_PENDING_RETRY_INTERVAL_SEC` | `60` | Seconds between retries of failed pending uploads. / 失败待发上传重试间隔（秒）。 |

---

## Native backend

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_NATIVE_TARGET_DIR` | `~/.cache/contextgo/target` | Directory for Rust/Go native build artifacts. Defaults to a user-owned cache path to prevent TOCTOU races on multi-tenant systems. / Rust/Go 构建产物目录，默认使用用户私有缓存路径以防多用户场景下的竞态。 |
| `CONTEXTGO_NATIVE_HEALTH_CACHE_TTL_SEC` | `30` | TTL (seconds) for caching the native backend health probe result. Set to `0` to disable during development. / native 后端健康探测结果的缓存生存时间（秒），开发时可设为 `0` 禁用缓存。 |
| `CONTEXTGO_ACTIVE_WORKDIR` | _(current directory)_ | Working directory passed to native processes. Set automatically by `context_native.py`. / 传递给 native 进程的工作目录，由 `context_native.py` 自动设置。 |

---

## Install and deploy

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_INSTALL_ROOT` | `~/.local/share/contextgo` | Root directory for installed ContextGO scripts. The installed smoke test reads this path to locate `context_cli.py` and `e2e_quality_gate.py`. / 已安装脚本的根目录，installed smoke 测试通过此路径定位脚本。 |
| `PATCH_LAUNCHD` | `1` | Patch the launchd plist during install (macOS only). / 安装时更新 launchd plist（仅 macOS）。 |
| `RELOAD_LAUNCHD` | `1` | Reload the launchd service after patching (macOS only). / 更新后重载 launchd 服务（仅 macOS）。 |

---

## Benchmarks

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_BENCH_QUERY` | `benchmark` | Default search query used by the benchmark harness. / 基准测试使用的默认搜索查询。 |
| `CONTEXTGO_BENCH_ITERATIONS` | `3` | Number of benchmark iterations. / 基准测试迭代次数。 |
| `CONTEXTGO_BENCH_SEARCH_LIMIT` | `5` | Search result limit used in benchmark runs. / 基准测试中的搜索结果限制。 |

---

## Verifying your configuration / 验证配置

```bash
# Print the resolved storage root / 打印实际存储根目录
python3 -c "from scripts.context_config import storage_root; print(storage_root())"

# Full health check / 完整健康检查
python3 scripts/context_cli.py health
bash scripts/context_healthcheck.sh --deep
```
