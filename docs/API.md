# API Reference / API 参考

> [CONFIGURATION.md](CONFIGURATION.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

The ContextGO viewer exposes a local HTTP API for querying the memory index and streaming live updates. The HTTP handler lives in `scripts/memory_viewer.py`; `scripts/context_server.py` is a thin entry-point wrapper.

ContextGO viewer 暴露本地 HTTP API，用于查询记忆索引和流式获取实时更新。HTTP 处理逻辑位于 `scripts/memory_viewer.py`，`scripts/context_server.py` 是薄封装入口。

---

## Server / 服务器

**Default base URL:** `http://127.0.0.1:37677`

### Starting the server / 启动服务器

```bash
# Via CLI
python3 scripts/context_cli.py serve --host 127.0.0.1 --port 37677 --token <token>

# Via environment variables
CONTEXTGO_VIEWER_HOST=127.0.0.1 \
CONTEXTGO_VIEWER_PORT=37677 \
CONTEXTGO_VIEWER_TOKEN=<token> \
python3 scripts/context_cli.py serve
```

```python
# Programmatically
import sys; sys.path.insert(0, "scripts")
import context_server
context_server.apply_runtime_config(host="127.0.0.1", port=37677, token="<token>")
context_server.main()  # blocks until KeyboardInterrupt
```

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_VIEWER_HOST` | `127.0.0.1` | Bind address / 绑定地址 |
| `CONTEXTGO_VIEWER_PORT` | `37677` | Bind port / 绑定端口 |
| `CONTEXTGO_VIEWER_TOKEN` | _(empty)_ | Bearer token; required when host is not loopback / 非回环地址时必填 |

---

## Authentication / 鉴权

When `CONTEXTGO_VIEWER_TOKEN` is set, all `/api/*` requests must include the token in the `X-Context-Token` header. Comparison uses `hmac.compare_digest` to prevent timing attacks. Unauthenticated requests return `401 Unauthorized`.

设置 `CONTEXTGO_VIEWER_TOKEN` 后，所有 `/api/*` 请求须在 `X-Context-Token` 请求头中携带 token。使用 `hmac.compare_digest` 常数时间比较防止时序攻击。未鉴权请求返回 `401`。

```http
X-Context-Token: <token>
```

The root HTML page (`/`) is always served without authentication. If the server is bound to a non-loopback address and the token is not set, `main()` raises `SystemExit` at startup.

根页面 (`/`) 始终无需鉴权。若绑定非回环地址且未设置 token，`main()` 在启动时抛出 `SystemExit`。

---

## CORS

CORS headers are reflected only for loopback origins (`127.0.0.1`, `localhost`, `::1`). Allowed methods: `GET, POST, OPTIONS`. Allowed headers: `Content-Type, X-Context-Token`.

CORS 响应头仅对回环源（`127.0.0.1`、`localhost`、`::1`）生效。允许方法：`GET, POST, OPTIONS`。允许请求头：`Content-Type, X-Context-Token`。

---

## Endpoint Summary / 端点概览

| Method | Path | Auth | Description / 说明 |
|---|---|---|---|
| `OPTIONS` | `*` | No | CORS pre-flight / CORS 预检 |
| `GET` | `/` | No | Viewer web UI / Viewer 页面 |
| `GET` | `/index.html` | No | Alias for `/` |
| `GET` | `/api/health` | Yes* | Index health and sync status / 索引健康与同步状态 |
| `GET` | `/api/search` | Yes* | Full-text search over observations / 全文搜索观测记录 |
| `GET` | `/api/timeline` | Yes* | Observations surrounding an anchor ID / 锚点观测的时间线上下文 |
| `GET` | `/api/events` | Yes* | Server-Sent Events heartbeat stream / SSE 心跳流 |
| `POST` | `/api/observations/batch` | Yes* | Fetch observations by ID / 按 ID 批量获取观测记录 |

*Auth required only when `CONTEXTGO_VIEWER_TOKEN` is set.

*仅在设置 `CONTEXTGO_VIEWER_TOKEN` 时需要鉴权。

---

## Endpoints / 端点详情

### GET /api/health

Returns the current health status of the memory index.

返回记忆索引的当前健康状态。

**Response `200 OK`**

```json
{
  "ok": true,
  "checked_at": "2026-03-26T12:00:00.000000",
  "sync": { "scanned": 42, "added": 1, "updated": 0, "removed": 0 },
  "db_path": "/home/user/.contextgo/index/memory_index.db",
  "total_observations": 187,
  "latest_epoch": 1742990400
}
```

**Response `500 Internal Server Error`**

```json
{
  "ok": false,
  "error": "health check failed",
  "detail": "<exception message>",
  "checked_at": "2026-03-26T12:00:00.000000"
}
```

---

### GET /api/search

Search indexed memory observations using SQLite.

使用 SQLite 搜索已索引的记忆观测。

**Query parameters / 查询参数**

| Parameter | Type | Default | Constraints | Description / 说明 |
|---|---|---|---|---|
| `query` | string | `""` | — | Full-text query; empty returns all / 全文查询，空值返回全部 |
| `limit` | integer | `20` | 1–200 | Maximum results / 最大返回数量 |
| `offset` | integer | `0` | 0–100000 | Pagination offset / 分页偏移 |
| `source_type` | string | `"all"` | `all` \| `history` \| `conversation` | Filter by source type / 按数据源类型过滤 |

**Example / 示例**

```
GET /api/search?query=refactor+auth&limit=10&offset=0&source_type=all
```

**Response `200 OK`**

```json
{
  "ok": true,
  "sync": { "scanned": 42, "added": 0, "updated": 0, "removed": 0 },
  "count": 2,
  "results": [
    {
      "id": 15,
      "source_type": "conversation",
      "session_id": "abc123",
      "title": "Refactor auth flow",
      "content": "...",
      "tags": ["auth", "refactor"],
      "file_path": "/home/user/.contextgo/resources/shared/conversations/2026-03-26-abc123.md",
      "created_at": "2026-03-26T11:00:00",
      "created_at_epoch": 1742986800,
      "fingerprint": "sha256hex..."
    }
  ]
}
```

---

### GET /api/timeline

Retrieve observations surrounding a specific anchor observation, ordered by creation time.

检索特定锚点观测周围的观测记录，按创建时间排序。

**Query parameters / 查询参数**

| Parameter | Type | Default | Constraints | Description / 说明 |
|---|---|---|---|---|
| `anchor` | integer | `0` | 0–10000000 | Anchor observation ID; `0` returns empty / 锚点观测 ID，`0` 返回空结果 |
| `depth_before` | integer | `3` | 0–20 | Observations before the anchor / 锚点前的观测数量 |
| `depth_after` | integer | `3` | 0–20 | Observations after the anchor / 锚点后的观测数量 |

**Example / 示例**

```
GET /api/timeline?anchor=15&depth_before=3&depth_after=3
```

**Response `200 OK`**

```json
{
  "ok": true,
  "sync": { "scanned": 42, "added": 0, "updated": 0, "removed": 0 },
  "count": 7,
  "timeline": [
    {
      "id": 12,
      "source_type": "conversation",
      "session_id": "abc120",
      "title": "Earlier note",
      "content": "...",
      "tags": [],
      "file_path": "...",
      "created_at": "2026-03-25T09:00:00",
      "created_at_epoch": 1742900400,
      "fingerprint": "sha256hex..."
    }
  ]
}
```

---

### GET /api/events

Server-Sent Events stream that emits a heartbeat tick with current index statistics on a configurable interval. The stream closes after `SSE_MAX_TICKS` ticks or when the client disconnects.

以可配置间隔发送含当前索引统计的心跳 tick 的 SSE 流。达到 `SSE_MAX_TICKS` 或客户端断连后关闭。

| Variable | Default | Description / 说明 |
|---|---|---|
| `CONTEXTGO_VIEWER_SSE_INTERVAL_SEC` | `1.0` | Seconds between ticks (min 0.2) / tick 间隔（最小 0.2 秒）|
| `CONTEXTGO_VIEWER_SSE_MAX_TICKS` | `120` | Maximum ticks before stream closes / 流关闭前的最大 tick 数 |

**Response `200 OK`**

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

Each event is a `data:` line containing a JSON object:

```
data: {"at":"2026-03-26T12:00:01.000000","sync":{...},"db_path":"...","total_observations":187,"latest_epoch":1742990400}
```

---

### POST /api/observations/batch

Fetch full observation records by ID.

按 ID 批量获取完整观测记录。

**Request headers / 请求头**

```http
Content-Type: application/json
X-Context-Token: <token>
```

**Request body / 请求体**

```json
{
  "ids": [15, 16, 17],
  "limit": 100
}
```

| Field | Type | Default | Constraints | Description / 说明 |
|---|---|---|---|---|
| `ids` | integer[] | — | Max 500 (`CONTEXTGO_VIEWER_MAX_BATCH_IDS`) | Observation IDs to fetch / 待获取的观测 ID |
| `limit` | integer | `100` | 1–300 | Maximum records returned / 最大返回记录数 |

Request body size is capped by `CONTEXTGO_VIEWER_MAX_POST_BYTES` (default 1 MiB). Oversized requests return `413 Payload Too Large`.

请求体大小受 `CONTEXTGO_VIEWER_MAX_POST_BYTES`（默认 1 MiB）限制，超限返回 `413`。

**Response `200 OK`**

```json
{
  "ok": true,
  "sync": { "scanned": 42, "added": 0, "updated": 0, "removed": 0 },
  "count": 3,
  "observations": [
    {
      "id": 15,
      "source_type": "conversation",
      "session_id": "abc123",
      "title": "Refactor auth flow",
      "content": "...",
      "tags": ["auth"],
      "file_path": "...",
      "created_at": "2026-03-26T11:00:00",
      "created_at_epoch": 1742986800,
      "fingerprint": "sha256hex..."
    }
  ]
}
```

**Error status codes / 错误状态码**

| Status | Condition / 条件 |
|---|---|
| `400` | `ids` not an array, invalid JSON, invalid `Content-Length`, or too many IDs |
| `413` | Request body exceeds `MAX_POST_BYTES` |
| `500` | Internal processing error |

---

## Shared Schemas / 共享数据结构

### Observation object / 观测对象

All endpoints that return observations use this schema:

所有返回观测记录的端点均使用此结构：

| Field | Type | Description / 说明 |
|---|---|---|
| `id` | integer | Auto-assigned database ID / 数据库自动分配 ID |
| `source_type` | string | `"conversation"` or `"history"` |
| `session_id` | string | Originating session identifier / 来源会话标识符 |
| `title` | string | Short title / 简短标题 |
| `content` | string | Full text body / 完整正文 |
| `tags` | string[] | User-defined tags / 用户自定义标签 |
| `file_path` | string | Absolute path to the source markdown file / 来源 Markdown 文件绝对路径 |
| `created_at` | string | ISO 8601 timestamp / ISO 8601 时间戳 |
| `created_at_epoch` | integer | Unix epoch of `created_at` / `created_at` 的 Unix 时间戳 |
| `fingerprint` | string | SHA-256 hex digest of content (dedup key) / 内容 SHA-256 十六进制摘要（去重键） |

### Sync object / 同步对象

Included in all successful API responses. Reflects the most recent storage-sync pass, rate-limited by `CONTEXTGO_VIEWER_SYNC_MIN_INTERVAL_SEC` (default 5 s).

包含在所有成功的 API 响应中，反映最近一次存储同步的结果，受 `CONTEXTGO_VIEWER_SYNC_MIN_INTERVAL_SEC`（默认 5 秒）速率限制。

```json
{ "scanned": 42, "added": 1, "updated": 0, "removed": 0 }
```

### Error object / 错误对象

All error responses share this structure:

所有错误响应共享此结构：

```json
{
  "ok": false,
  "error": "<short reason>",
  "detail": "<optional longer description>"
}
```

`404` responses additionally include the unmatched path:

`404` 响应还包含未匹配路径：

```json
{ "ok": false, "error": "not found", "path": "/api/unknown" }
```
