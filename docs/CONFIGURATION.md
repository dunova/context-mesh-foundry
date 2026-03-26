# Configuration Reference

ContextGO is configured through environment variables. All variables are optional. The defaults are designed for local single-user operation with no external dependencies.

Copy `.env.example` to `.env` and uncomment the variables you want to change. Never commit a populated `.env` file.

## Contents

- [Storage paths](#storage-paths)
- [Viewer server](#viewer-server)
- [Daemon source monitors](#daemon-source-monitors)
- [Daemon timing and resource tuning](#daemon-timing-and-resource-tuning)
- [Session index](#session-index)
- [CLI behavior](#cli-behavior)
- [Remote sync](#remote-sync)
- [Native backend](#native-backend)
- [Deployment and install](#deployment-and-install)
- [Benchmarks](#benchmarks)

---

## Storage paths

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_STORAGE_ROOT` | `~/.contextgo` | Root directory for all ContextGO data (index databases, raw session data). Must be owned and writable by the current user. |
| `CONTEXTGO_SESSION_INDEX_DB_PATH` | `$CONTEXTGO_STORAGE_ROOT/index/session_index.db` | Override path for the session index SQLite database. |
| `MEMORY_INDEX_DB_PATH` | `$CONTEXTGO_STORAGE_ROOT/index/memory_index.db` | Override path for the memory/observations SQLite database. |

**Example:**

```bash
export CONTEXTGO_STORAGE_ROOT=/data/contextgo
```

---

## Viewer server

The viewer is a local HTTP server started with `context_cli serve`. It defaults to loopback-only binding.

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_VIEWER_HOST` | `127.0.0.1` | Bind address for the viewer HTTP server. Do not set to `0.0.0.0` without also setting `CONTEXTGO_VIEWER_TOKEN`. |
| `CONTEXTGO_VIEWER_PORT` | `37677` | TCP port for the viewer server. |
| `CONTEXTGO_VIEWER_TOKEN` | (empty) | Bearer token required in the `X-Context-Token` header for all `/api/` requests. Required when binding a non-loopback address. Use a high-entropy value: `openssl rand -hex 32`. |
| `CONTEXTGO_VIEWER_MAX_POST_BYTES` | `1048576` | Maximum request body size in bytes for viewer POST endpoints. |
| `CONTEXTGO_VIEWER_MAX_BATCH_IDS` | `500` | Maximum IDs accepted in a single batch observations request. |
| `CONTEXTGO_VIEWER_SSE_INTERVAL_SEC` | `1.0` | Server-sent events poll interval in seconds. |
| `CONTEXTGO_VIEWER_SSE_MAX_TICKS` | `120` | Maximum SSE ticks before the connection closes. |
| `CONTEXTGO_VIEWER_SYNC_MIN_INTERVAL_SEC` | `5.0` | Minimum seconds between viewer sync operations. |

---

## Daemon source monitors

Each monitor controls whether the daemon watches a specific session source. All are enabled by default unless noted.

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_ENABLE_SHELL_MONITOR` | `1` | Monitor `~/.zsh_history` and `~/.bash_history`. |
| `CONTEXTGO_ENABLE_CLAUDE_HISTORY_MONITOR` | `1` | Monitor `~/.claude/history.jsonl` for Claude Code prompt history. |
| `CONTEXTGO_ENABLE_CODEX_HISTORY_MONITOR` | `1` | Monitor `~/.codex/history.jsonl` for Codex prompt history. |
| `CONTEXTGO_ENABLE_CODEX_SESSION_MONITOR` | `1` | Monitor `~/.codex/sessions/` for full Codex session JSONL files. |
| `CONTEXTGO_ENABLE_CLAUDE_TRANSCRIPTS_MONITOR` | `1` | Monitor `~/.claude/transcripts/` for Claude transcript files. |
| `CONTEXTGO_ENABLE_ANTIGRAVITY_MONITOR` | `1` | Monitor `~/.gemini/antigravity/brain/` for Antigravity session documents. |
| `CONTEXTGO_ENABLE_OPENCODE_MONITOR` | `0` | Monitor OpenCode prompt history (disabled by default). |
| `CONTEXTGO_ENABLE_KILO_MONITOR` | `0` | Monitor Kilo prompt history (disabled by default). |
| `CONTEXTGO_ENABLE_REMOTE_SYNC` | `0` | Push history to the remote sync server. Requires `CONTEXTGO_REMOTE_URL`. |

Boolean values accept `1`, `true`, `yes`, `on` (enabled) or `0`, `false`, `no`, `off` (disabled).

---

## Daemon timing and resource tuning

These variables control the daemon's polling frequency, resource limits, and error handling. The defaults are conservative and suitable for most development machines.

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_POLL_INTERVAL_SEC` | `30` | Normal poll interval in seconds between daemon work cycles. |
| `CONTEXTGO_FAST_POLL_INTERVAL_SEC` | `3` | Fast poll interval in seconds when recent activity is detected. |
| `CONTEXTGO_IDLE_SLEEP_CAP_SEC` | `180` | Maximum idle sleep cap in seconds. |
| `CONTEXTGO_IDLE_TIMEOUT_SEC` | `300` | Seconds of inactivity before the daemon enters idle mode. |
| `CONTEXTGO_HEARTBEAT_INTERVAL_SEC` | `600` | Heartbeat log interval in seconds. |
| `CONTEXTGO_INDEX_SYNC_MIN_INTERVAL_SEC` | `20` | Minimum seconds between successive index sync operations. |
| `CONTEXTGO_LOOP_JITTER_SEC` | `0.7` | Random jitter added to loop sleep to spread load (float, seconds). |
| `CONTEXTGO_ERROR_BACKOFF_MAX_SEC` | `30` | Maximum backoff between error retries in seconds. |
| `CONTEXTGO_CYCLE_BUDGET_SEC` | `8` | Maximum seconds per daemon work cycle. |
| `CONTEXTGO_NIGHT_POLL_START_HOUR` | `23` | Hour (0-23) at which night poll mode begins. |
| `CONTEXTGO_NIGHT_POLL_END_HOUR` | `7` | Hour (0-23) at which night poll mode ends. |
| `CONTEXTGO_NIGHT_POLL_INTERVAL_SEC` | `600` | Poll interval during night hours. |
| `CONTEXTGO_MAX_TRACKED_SESSIONS` | `240` | Maximum number of in-memory tracked sessions. |
| `CONTEXTGO_MAX_FILE_CURSORS` | `800` | Maximum number of file byte-offset cursors tracked in memory. |
| `CONTEXTGO_SESSION_TTL_SEC` | `7200` | Time-to-live for inactive sessions in memory (seconds). |
| `CONTEXTGO_MAX_MESSAGES_PER_SESSION` | `500` | Maximum messages extracted per session before truncation. |
| `CONTEXTGO_MAX_PENDING_FILES` | `5000` | Maximum number of pending outbound files queued in memory. |
| `CONTEXTGO_TRANSCRIPTS_LOOKBACK_DAYS` | `7` | Number of days to look back when scanning Claude transcript files. |

### Codex session scanning

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_CODEX_SESSION_SCAN_INTERVAL_SEC` | `90` | How often to rescan Codex session files (seconds). |
| `CONTEXTGO_MAX_CODEX_SESSION_FILES_PER_SCAN` | `1200` | Maximum Codex session files processed per scan pass. |

### Claude transcript scanning

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_CLAUDE_TRANSCRIPT_SCAN_INTERVAL_SEC` | `180` | How often to rescan Claude transcript files (seconds). |
| `CONTEXTGO_MAX_CLAUDE_TRANSCRIPT_FILES_PER_POLL` | `500` | Maximum Claude transcript files processed per poll pass. |

### Antigravity scanning

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_ANTIGRAVITY_SCAN_INTERVAL_SEC` | `120` | How often to rescan Antigravity brain directories (seconds). |
| `CONTEXTGO_MAX_ANTIGRAVITY_DIRS_PER_SCAN` | `400` | Maximum Antigravity brain directories processed per scan pass. |
| `CONTEXTGO_MAX_ANTIGRAVITY_SESSIONS` | `500` | Maximum Antigravity sessions tracked in memory. |
| `CONTEXTGO_SUSPEND_ANTIGRAVITY_WHEN_BUSY` | `1` | Pause Antigravity scanning when the system is busy. |
| `CONTEXTGO_ANTIGRAVITY_BUSY_LS_THRESHOLD` | `3` | Number of active Antigravity language server processes considered "busy". |
| `CONTEXTGO_ANTIGRAVITY_INGEST_MODE` | `final_only` | Ingest mode: `final_only` (ingest only stable documents) or `live` (ingest incrementally). |
| `CONTEXTGO_ANTIGRAVITY_QUIET_SEC` | `180` | Quiet period (seconds) before an Antigravity directory is considered stable. |
| `CONTEXTGO_ANTIGRAVITY_MIN_DOC_BYTES` | `400` | Minimum document size in bytes for Antigravity ingest. |

---

## Session index

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_SESSION_MAX_CONTENT_CHARS` | `24000` | Maximum characters of session content stored per indexed entry. |
| `CONTEXTGO_SESSION_SYNC_MIN_INTERVAL_SEC` | `15` | Minimum seconds between session index sync operations. |
| `CONTEXTGO_SOURCE_CACHE_TTL_SEC` | `10` | TTL in seconds for the source path discovery cache. |
| `CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND` | (empty) | Enable an experimental search backend by name. Leave empty for the default backend. |
| `CONTEXTGO_EXPERIMENTAL_SYNC_BACKEND` | (empty) | Enable an experimental sync backend by name. Leave empty for the default backend. |

---

## CLI behavior

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_LOCAL_SCAN_MAX_FILES` | `300` | Maximum local files scanned per `context_cli` search invocation. |
| `CONTEXTGO_LOCAL_SCAN_READ_BYTES` | `120000` | Maximum bytes read per file during local scanning. |

---

## Remote sync

Remote sync is disabled by default. Enable it only when you have a running ContextGO sync server.

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_REMOTE_URL` | `http://127.0.0.1:8090/api/v1` | Base URL of the remote ContextGO sync server. Must use `https://` for non-localhost addresses. |
| `CONTEXTGO_ENABLE_REMOTE_MEMORY_HTTP` | `0` | Enable pushing new history entries to the remote sync server. |
| `CONTEXTGO_EXPORT_HTTP_TIMEOUT_SEC` | `30` | Timeout in seconds for outbound HTTP export requests. |
| `CONTEXTGO_PENDING_HTTP_TIMEOUT_SEC` | `15` | Timeout in seconds for retrying pending outbound HTTP requests. |
| `CONTEXTGO_PENDING_RETRY_INTERVAL_SEC` | `60` | Seconds between retries of failed pending HTTP uploads. |

---

## Native backend

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_NATIVE_TARGET_DIR` | `/tmp/contextgo_target` | Directory for Rust/Go native build artifacts. This is volatile by default. For persistent builds, set to a path under `~/.cache`. |
| `CONTEXTGO_NATIVE_HEALTH_CACHE_TTL_SEC` | `30` | TTL in seconds for caching the native backend health probe result. |
| `CONTEXTGO_ACTIVE_WORKDIR` | (current directory) | Working directory passed to native processes. Set automatically by `context_native.py`. |

---

## Deployment and install

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_INSTALL_ROOT` | `~/.local/share/contextgo` | Root directory for the installed ContextGO scripts. The installed smoke test (`smoke_installed_runtime.py`) reads this path to locate `context_cli.py` and `e2e_quality_gate.py`. |
| `PATCH_LAUNCHD` | `1` | Patch the launchd plist during install (macOS only). |
| `RELOAD_LAUNCHD` | `1` | Reload the launchd service after patching (macOS only). |

---

## Benchmarks

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_BENCH_QUERY` | `benchmark` | Default search query used by the benchmark harness. |
| `CONTEXTGO_BENCH_ITERATIONS` | `3` | Number of benchmark iterations. |
| `CONTEXTGO_BENCH_SEARCH_LIMIT` | `5` | Search result limit used in benchmark runs. |

---

## Environment variable precedence

All environment variables are read at startup. There is no runtime reload. Changes require restarting the affected process (daemon, server, or CLI invocation).

The storage root is resolved once at import time by `scripts/context_config.py`. Changing `CONTEXTGO_STORAGE_ROOT` mid-session has no effect until the process restarts.

## Verifying configuration

```bash
# Print the resolved storage root
python3 -c "from scripts.context_config import storage_root; print(storage_root())"

# Run a full health check to validate the environment
python3 scripts/context_cli.py health
bash scripts/context_healthcheck.sh --deep
```
