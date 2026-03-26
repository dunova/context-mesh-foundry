#!/usr/bin/env python3
"""Lightweight memory viewer API + SSE for ContextGO."""

from __future__ import annotations

from datetime import datetime
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from urllib.parse import parse_qs, urlparse

try:
    from context_config import env_float, env_int, env_str
except Exception:  # pragma: no cover
    from .context_config import env_float, env_int, env_str  # type: ignore[import-not-found]

try:
    from memory_index import (
        get_observations_by_ids,
        index_stats,
        search_index,
        sync_index_from_storage,
        timeline_index,
    )
except Exception:  # pragma: no cover
    from .memory_index import (  # type: ignore[import-not-found]
        get_observations_by_ids,
        index_stats,
        search_index,
        sync_index_from_storage,
        timeline_index,
    )


HOST = env_str("CONTEXTGO_VIEWER_HOST", default="127.0.0.1")
PORT = env_int("CONTEXTGO_VIEWER_PORT", default=37677, minimum=1)
VIEWER_TOKEN = env_str("CONTEXTGO_VIEWER_TOKEN", default="").strip()
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_POST_BYTES = env_int("CONTEXTGO_VIEWER_MAX_POST_BYTES", default=1048576, minimum=1024)
MAX_BATCH_IDS = env_int("CONTEXTGO_VIEWER_MAX_BATCH_IDS", default=500, minimum=1)
SSE_INTERVAL_SEC = env_float("CONTEXTGO_VIEWER_SSE_INTERVAL_SEC", default=1.0, minimum=0.2)
SSE_MAX_TICKS = env_int("CONTEXTGO_VIEWER_SSE_MAX_TICKS", default=120, minimum=1)
SYNC_MIN_INTERVAL_SEC = env_float("CONTEXTGO_VIEWER_SYNC_MIN_INTERVAL_SEC", default=5.0, minimum=0.0)

_SYNC_STATE: dict = {"at": 0.0, "payload": None}
_SYNC_LOCK = threading.Lock()

_VIEWER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ContextGO Viewer</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f0f2f5;
      color: #1a1a2e;
      min-height: 100vh;
    }
    header {
      background: #1a1a2e;
      color: #e8eaf6;
      padding: 14px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    header h1 { font-size: 1.1rem; font-weight: 600; letter-spacing: 0.02em; }
    #status-dot {
      width: 10px; height: 10px; border-radius: 50%;
      background: #555; flex-shrink: 0; transition: background 0.4s;
    }
    #status-dot.live { background: #4caf50; }
    #status-dot.err  { background: #f44336; }
    #stats { font-size: 0.8rem; color: #90a4ae; }
    main { max-width: 960px; margin: 0 auto; padding: 24px 16px; }
    .search-row {
      display: flex; gap: 8px; margin-bottom: 20px;
    }
    #q {
      flex: 1; padding: 9px 12px;
      border: 1px solid #c5cae9; border-radius: 6px;
      font-size: 0.95rem; outline: none;
      transition: border-color 0.2s;
    }
    #q:focus { border-color: #5c6bc0; }
    button {
      padding: 9px 20px; background: #5c6bc0; color: #fff;
      border: none; border-radius: 6px; font-size: 0.95rem;
      cursor: pointer; white-space: nowrap; transition: background 0.2s;
    }
    button:hover { background: #3949ab; }
    #out {
      white-space: pre-wrap; word-break: break-word;
      background: #fff; border: 1px solid #e0e0e0;
      border-radius: 8px; padding: 16px;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 0.82rem; line-height: 1.55;
      min-height: 120px; max-height: 70vh; overflow-y: auto;
      color: #263238;
    }
    #err-banner {
      display: none; background: #ffebee; color: #c62828;
      border: 1px solid #ef9a9a; border-radius: 6px;
      padding: 10px 14px; margin-bottom: 16px; font-size: 0.88rem;
    }
  </style>
</head>
<body>
<header>
  <h1>ContextGO Viewer</h1>
  <span id="stats"></span>
  <span id="status-dot" title="SSE stream status"></span>
</header>
<main>
  <div id="err-banner"></div>
  <div class="search-row">
    <input id="q" type="search" placeholder="Search memory..." autocomplete="off">
    <button onclick="run()">Search</button>
  </div>
  <pre id="out">Enter a query and press Search.</pre>
</main>
<script>
async function run() {
  const q = document.getElementById('q').value.trim();
  const out = document.getElementById('out');
  const banner = document.getElementById('err-banner');
  banner.style.display = 'none';
  out.textContent = 'Searching...';
  try {
    const res = await fetch(
      '/api/search?query=' + encodeURIComponent(q) + '&limit=20',
      { headers: { 'Accept': 'application/json' } }
    );
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error('HTTP ' + res.status + ': ' + (j.error || res.statusText));
    }
    const j = await res.json();
    out.textContent = JSON.stringify(j, null, 2);
  } catch (e) {
    out.textContent = '';
    banner.textContent = 'Search failed: ' + e.message;
    banner.style.display = 'block';
  }
}

document.getElementById('q').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') run();
});

(function startSSE() {
  const dot = document.getElementById('status-dot');
  const stats = document.getElementById('stats');
  const es = new EventSource('/api/events');
  es.onopen = () => dot.className = 'live';
  es.onmessage = function(e) {
    try {
      const d = JSON.parse(e.data);
      const n = d.total_observations != null ? d.total_observations : '?';
      document.title = 'ContextGO Viewer (' + n + ')';
      stats.textContent = n + ' observations';
      dot.className = 'live';
    } catch (_) {}
  };
  es.onerror = function() {
    dot.className = 'err';
    setTimeout(startSSE, 5000);
    es.close();
  };
})();
</script>
</body>
</html>"""


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _maybe_sync_index() -> dict:
    now = time.monotonic()
    with _SYNC_LOCK:
        cached = _SYNC_STATE.get("payload")
        if (
            SYNC_MIN_INTERVAL_SEC > 0
            and cached is not None
            and (_SYNC_STATE.get("at", 0.0) + SYNC_MIN_INTERVAL_SEC) > now
        ):
            return dict(cached)
    payload = sync_index_from_storage()
    with _SYNC_LOCK:
        _SYNC_STATE["at"] = now
        _SYNC_STATE["payload"] = dict(payload)
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "ContextGOViewer/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        return

    def _parse_int(self, value: str, default: int, min_v: int, max_v: int) -> int:
        try:
            parsed = int(value)
        except (ValueError, TypeError):
            return default
        return max(min_v, min(max_v, parsed))

    def _authorized(self) -> bool:
        if not VIEWER_TOKEN:
            return True
        got = self.headers.get("X-Context-Token", "").strip()
        if not got:
            return False
        # Use hmac.compare_digest to prevent timing-based token enumeration.
        return hmac.compare_digest(got, VIEWER_TOKEN)

    def _cors_headers(self) -> None:
        # Only allow same-origin or loopback clients; no wildcard.
        origin = self.headers.get("Origin", "")
        if origin:
            self.send_header("Vary", "Origin")
            # Reflect only loopback origins.
            if any(lh in origin for lh in ("127.0.0.1", "localhost", "::1")):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, X-Context-Token",
                )

    def _send_json(self, status: int, payload: dict) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            self._send_html(_VIEWER_HTML)
            return

        if parsed.path.startswith("/api/") and not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        if parsed.path == "/api/health":
            try:
                sync = _maybe_sync_index()
                stats = index_stats()
            except Exception as exc:
                self._send_json(
                    500,
                    {
                        "ok": False,
                        "error": "health check failed",
                        "detail": str(exc),
                        "checked_at": datetime.now().isoformat(),
                    },
                )
                return
            self._send_json(
                200,
                {
                    "ok": True,
                    "checked_at": datetime.now().isoformat(),
                    "sync": sync,
                    **stats,
                },
            )
            return

        if parsed.path == "/api/search":
            qs = parse_qs(parsed.query)
            query = (qs.get("query", [""])[0] or "").strip()
            limit = self._parse_int(qs.get("limit", ["20"])[0] or "20", 20, 1, 200)
            offset = self._parse_int(qs.get("offset", ["0"])[0] or "0", 0, 0, 100_000)
            source_type = (qs.get("source_type", ["all"])[0] or "all").strip()
            try:
                sync = _maybe_sync_index()
                rows = search_index(
                    query=query, limit=limit, offset=offset, source_type=source_type
                )
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": "search failed", "detail": str(exc)})
                return
            self._send_json(200, {"ok": True, "sync": sync, "count": len(rows), "results": rows})
            return

        if parsed.path == "/api/timeline":
            qs = parse_qs(parsed.query)
            anchor = self._parse_int(qs.get("anchor", ["0"])[0] or "0", 0, 0, 10_000_000)
            before = self._parse_int(qs.get("depth_before", ["3"])[0] or "3", 3, 0, 20)
            after = self._parse_int(qs.get("depth_after", ["3"])[0] or "3", 3, 0, 20)
            try:
                sync = _maybe_sync_index()
                rows = (
                    timeline_index(anchor_id=anchor, depth_before=before, depth_after=after)
                    if anchor > 0
                    else []
                )
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": "timeline failed", "detail": str(exc)})
                return
            self._send_json(
                200, {"ok": True, "sync": sync, "count": len(rows), "timeline": rows}
            )
            return

        if parsed.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._cors_headers()
            self.end_headers()
            for _ in range(SSE_MAX_TICKS):
                try:
                    sync = _maybe_sync_index()
                    data = {
                        "at": datetime.now().isoformat(),
                        "sync": sync,
                        **index_stats(),
                    }
                    chunk = (
                        f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
                    )
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(SSE_INTERVAL_SEC)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
            return

        self._send_json(404, {"ok": False, "error": "not found", "path": parsed.path})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path.startswith("/api/") and not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        if parsed.path != "/api/observations/batch":
            self._send_json(404, {"ok": False, "error": "not found", "path": parsed.path})
            return

        try:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                self._send_json(400, {"ok": False, "error": "invalid Content-Length"})
                return

            if length <= 0 or length > MAX_POST_BYTES:
                self._send_json(
                    413,
                    {
                        "ok": False,
                        "error": "payload too large",
                        "max_bytes": MAX_POST_BYTES,
                    },
                )
                return

            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw) if raw.strip() else {}
            ids = data.get("ids") or []

            if not isinstance(ids, list):
                self._send_json(400, {"ok": False, "error": "ids must be an array"})
                return

            if len(ids) > MAX_BATCH_IDS:
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": "too many ids",
                        "max": MAX_BATCH_IDS,
                        "received": len(ids),
                    },
                )
                return

            limit = self._parse_int(str(data.get("limit") or "100"), 100, 1, 300)
            sync = _maybe_sync_index()

            parsed_ids: list[int] = []
            for x in ids:
                try:
                    parsed_ids.append(int(x))
                except (ValueError, TypeError):
                    continue

            rows = get_observations_by_ids(parsed_ids[:MAX_BATCH_IDS], limit=limit)
            self._send_json(
                200,
                {"ok": True, "sync": sync, "count": len(rows), "observations": rows},
            )
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid JSON body"})
        except Exception as exc:
            self._send_json(
                500, {"ok": False, "error": "internal error", "detail": str(exc)}
            )


def main() -> None:
    if HOST not in LOOPBACK_HOSTS and not VIEWER_TOKEN:
        raise SystemExit(
            "CONTEXTGO_VIEWER_TOKEN must be set when binding a non-loopback host."
        )
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ContextGO Viewer listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
