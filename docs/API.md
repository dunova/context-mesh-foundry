# API Reference / API 参考

> Related: [CONFIGURATION.md](CONFIGURATION.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

This document covers all HTTP endpoints exposed by the ContextGO viewer server.
The implementation lives in `scripts/memory_viewer.py`; `scripts/context_server.py`
is a thin wrapper that re-exports `main()` and `apply_runtime_config()`.

本文档涵盖 ContextGO viewer 服务暴露的所有 HTTP 端点。HTTP 处理逻辑位于 `scripts/memory_viewer.py`，`scripts/context_server.py` 是对外的薄封装层。

## Server

**Default base URL:** `http://127.0.0.1:37677`

**Configuration** (environment variables or `context_cli.py serve` flags):

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_VIEWER_HOST` | `127.0.0.1` | Bind address |
| `CONTEXTGO_VIEWER_PORT` | `37677` | Bind port |
| `CONTEXTGO_VIEWER_TOKEN` | _(empty)_ | Bearer token; required when host is not loopback |

## Authentication

When `CONTEXTGO_VIEWER_TOKEN` is set, every `/api/*` request must include the
token in the `X-Context-Token` header.  Comparison uses `hmac.compare_digest`
to prevent timing attacks.  Unauthenticated requests to `/api/*` routes return
`401 Unauthorized`.  The root HTML page (`/`) is always served without auth.

```
X-Context-Token: <token>
```

If the server is bound to a non-loopback address and the token is not set,
`main()` raises `SystemExit` at startup.

## CORS

CORS headers are reflected only for loopback origins (`127.0.0.1`, `localhost`,
`::1`).  Non-loopback origins receive no `Access-Control-Allow-Origin` header.
Allowed methods: `GET, POST, OPTIONS`.  Allowed headers: `Content-Type,
X-Context-Token`.

## Endpoint Summary / 端点概览

| Method | Path | Auth | Description |
|---|---|---|---|
| `OPTIONS` | `*` | No | CORS pre-flight |
| `GET` | `/` | No | Viewer web UI |
| `GET` | `/index.html` | No | Alias for `/` |
| `GET` | `/api/health` | Yes* | Index health and sync status |
| `GET` | `/api/search` | Yes* | Full-text search over observations |
| `GET` | `/api/timeline` | Yes* | Observations surrounding an anchor ID |
| `GET` | `/api/events` | Yes* | Server-Sent Events heartbeat stream |
| `POST` | `/api/observations/batch` | Yes* | Fetch observations by ID |

*Auth required only when `CONTEXTGO_VIEWER_TOKEN` is set.

---

## Endpoints

### OPTIONS *

Pre-flight CORS request handler.

**Response:** `204 No Content` with CORS headers.

---

### GET /

Returns the built-in ContextGO Viewer web UI (single-page HTML).

**Auth required:** No

**Response:** `200 OK`

```
Content-Type: text/html; charset=utf-8
```

The page connects to `/api/events` via Server-Sent Events and provides a
search input that calls `/api/search`.

---

### GET /index.html

Alias for `GET /`.

---

### GET /api/health

Returns the current health status of the memory index.

**Auth required:** Yes (when token is configured)

**Response:** `200 OK`

```json
{
  "ok": true,
  "checked_at": "2026-03-26T12:00:00.000000",
  "sync": {
    "scanned": 42,
    "added": 1,
    "updated": 0,
    "removed": 0
  },
  "db_path": "/home/user/.contextgo/index/memory_index.db",
  "total_observations": 187,
  "latest_epoch": 1742990400
}
```

**Error response:** `500 Internal Server Error`

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

Search indexed memory observations.

**Auth required:** Yes (when token is configured)

**Query parameters:**

| Parameter | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `query` | string | `""` | — | Full-text search query; empty returns all |
| `limit` | integer | `20` | 1–200 | Maximum results to return |
| `offset` | integer | `0` | 0–100000 | Pagination offset |
| `source_type` | string | `"all"` | `"all"` \| `"history"` \| `"conversation"` | Filter by source type |

**Example:**

```
GET /api/search?query=refactor+auth&limit=10&offset=0&source_type=all
```

**Response:** `200 OK`

```json
{
  "ok": true,
  "sync": {
    "scanned": 42,
    "added": 0,
    "updated": 0,
    "removed": 0
  },
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

**Error response:** `500 Internal Server Error`

```json
{
  "ok": false,
  "error": "search failed",
  "detail": "<exception message>"
}
```

---

### GET /api/timeline

Retrieve observations surrounding a specific anchor observation, ordered by
creation time.

**Auth required:** Yes (when token is configured)

**Query parameters:**

| Parameter | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `anchor` | integer | `0` | 0–10000000 | Observation ID to anchor on; `0` returns an empty timeline |
| `depth_before` | integer | `3` | 0–20 | Number of observations before the anchor |
| `depth_after` | integer | `3` | 0–20 | Number of observations after the anchor |

**Example:**

```
GET /api/timeline?anchor=15&depth_before=3&depth_after=3
```

**Response:** `200 OK`

```json
{
  "ok": true,
  "sync": {
    "scanned": 42,
    "added": 0,
    "updated": 0,
    "removed": 0
  },
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

**Error response:** `500 Internal Server Error`

```json
{
  "ok": false,
  "error": "timeline failed",
  "detail": "<exception message>"
}
```

---

### GET /api/events

Server-Sent Events (SSE) stream that emits a heartbeat tick with current index
statistics on a configurable interval.

**Auth required:** Yes (when token is configured)

**Configuration:**

| Variable | Default | Description |
|---|---|---|
| `CONTEXTGO_VIEWER_SSE_INTERVAL_SEC` | `1.0` | Seconds between ticks (min 0.2) |
| `CONTEXTGO_VIEWER_SSE_MAX_TICKS` | `120` | Maximum ticks before the stream closes |

**Response:** `200 OK`

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

The stream closes after `SSE_MAX_TICKS` ticks or when the client disconnects.

---

### POST /api/observations/batch

Fetch full observation records by ID.

**Auth required:** Yes (when token is configured)

**Request headers:**

```
Content-Type: application/json
Content-Length: <bytes>
X-Context-Token: <token>   (when configured)
```

**Request body:**

```json
{
  "ids": [15, 16, 17],
  "limit": 100
}
```

| Field | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `ids` | array of integers | — | Max 500 entries (env `CONTEXTGO_VIEWER_MAX_BATCH_IDS`) | Observation IDs to fetch |
| `limit` | integer | `100` | 1–300 | Maximum records returned |

**Payload size limit:** Configurable via `CONTEXTGO_VIEWER_MAX_POST_BYTES`
(default 1 MiB).  Requests exceeding the limit receive `413 Payload Too Large`.

**Response:** `200 OK`

```json
{
  "ok": true,
  "sync": {
    "scanned": 42,
    "added": 0,
    "updated": 0,
    "removed": 0
  },
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

**Error responses:**

| Status | Condition |
|---|---|
| `400` | `ids` is not an array, invalid `Content-Length`, invalid JSON body, or too many IDs |
| `413` | Request body exceeds `MAX_POST_BYTES` |
| `500` | Internal processing error |

```json
{ "ok": false, "error": "<reason>", "detail": "<optional exception message>" }
```

---

## Common Error Shape

All error responses share this structure:

```json
{
  "ok": false,
  "error": "<short reason string>",
  "detail": "<optional longer description>"
}
```

404 responses include the unmatched path:

```json
{ "ok": false, "error": "not found", "path": "/api/unknown" }
```

---

## Sync Object

Several endpoints include a `sync` field in their response.  This reflects the
result of the most recent storage-sync pass (rate-limited by
`CONTEXTGO_VIEWER_SYNC_MIN_INTERVAL_SEC`, default 5 s):

```json
{
  "scanned": 42,
  "added": 1,
  "updated": 0,
  "removed": 0
}
```

---

## Observation Object

All endpoints that return observations use this schema:

| Field | Type | Description |
|---|---|---|
| `id` | integer | Auto-assigned database ID |
| `source_type` | string | `"conversation"` or `"history"` |
| `session_id` | string | Originating session identifier |
| `title` | string | Short title of the memory |
| `content` | string | Full text body |
| `tags` | array of strings | User-defined tags |
| `file_path` | string | Absolute path to the source markdown file |
| `created_at` | string | ISO 8601 creation timestamp |
| `created_at_epoch` | integer | Unix epoch of `created_at` |
| `fingerprint` | string | SHA-256 hex digest of content (used for dedup) |

---

## Launching the Server

Via the CLI:

```bash
python3 scripts/context_cli.py serve --host 127.0.0.1 --port 37677 --token mysecret
```

Via environment variables:

```bash
CONTEXTGO_VIEWER_HOST=127.0.0.1 \
CONTEXTGO_VIEWER_PORT=37677 \
CONTEXTGO_VIEWER_TOKEN=mysecret \
python3 scripts/context_cli.py serve
```

Programmatically:

```python
import sys
sys.path.insert(0, "scripts")
import context_server
context_server.apply_runtime_config(host="127.0.0.1", port=37677, token="mysecret")
context_server.main()  # blocks until KeyboardInterrupt
```
