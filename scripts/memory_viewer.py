#!/usr/bin/env python3
"""Lightweight memory viewer API + SSE for Context Mesh Foundry."""

from __future__ import annotations

from datetime import datetime
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


HOST = env_str("CONTEXT_MESH_VIEWER_HOST", "CONTEXT_VIEWER_HOST", default="127.0.0.1")
PORT = env_int("CONTEXT_MESH_VIEWER_PORT", "CONTEXT_VIEWER_PORT", default=37677, minimum=1)
VIEWER_TOKEN = env_str("CONTEXT_MESH_VIEWER_TOKEN", "CONTEXT_VIEWER_TOKEN", default="").strip()
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_POST_BYTES = env_int("CONTEXT_MESH_VIEWER_MAX_POST_BYTES", "CONTEXT_VIEWER_MAX_POST_BYTES", default=1048576, minimum=1024)
MAX_BATCH_IDS = env_int("CONTEXT_MESH_VIEWER_MAX_BATCH_IDS", "CONTEXT_VIEWER_MAX_BATCH_IDS", default=500, minimum=1)
SSE_INTERVAL_SEC = env_float("CONTEXT_MESH_VIEWER_SSE_INTERVAL_SEC", "CONTEXT_VIEWER_SSE_INTERVAL_SEC", default=1.0, minimum=0.2)
SSE_MAX_TICKS = env_int("CONTEXT_MESH_VIEWER_SSE_MAX_TICKS", "CONTEXT_VIEWER_SSE_MAX_TICKS", default=120, minimum=1)


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ContextMeshViewer/1.0"

    def log_message(self, fmt: str, *args):
        return

    def _parse_int(self, value: str, default: int, min_v: int, max_v: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            return default
        return max(min_v, min(max_v, parsed))

    def _authorized(self) -> bool:
        if not VIEWER_TOKEN:
            return True
        got = self.headers.get("X-Context-Token", "").strip()
        return bool(got and got == VIEWER_TOKEN)

    def _send_json(self, status: int, payload: dict):
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(
                """<!doctype html><html><head><meta charset="utf-8"><title>Context Mesh Viewer</title></head>
<body style="font-family: -apple-system, sans-serif; max-width: 960px; margin: 24px auto;">
<h1>Context Mesh Viewer</h1>
<input id="q" style="width:70%" placeholder="搜索记忆关键词"/><button onclick="run()">Search</button>
<pre id="out" style="white-space: pre-wrap; background:#f6f8fa; padding:12px;"></pre>
<script>
async function run(){
  const q = document.getElementById('q').value || '';
  const r = await fetch('/api/search?query='+encodeURIComponent(q)+'&limit=20');
  const j = await r.json();
  document.getElementById('out').textContent = JSON.stringify(j,null,2);
}
const es = new EventSource('/api/events');
es.onmessage = (e)=>{ try{ const d=JSON.parse(e.data); document.title='Context Mesh Viewer ('+d.total_observations+')'; }catch(_){} };
</script></body></html>"""
            )
            return

        if parsed.path.startswith("/api/") and not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        if parsed.path == "/api/health":
            sync = sync_index_from_storage()
            self._send_json(200, {"ok": True, "checked_at": datetime.now().isoformat(), "sync": sync, **index_stats()})
            return

        if parsed.path == "/api/search":
            qs = parse_qs(parsed.query)
            query = (qs.get("query", [""])[0] or "").strip()
            limit = self._parse_int(qs.get("limit", ["20"])[0] or "20", 20, 1, 200)
            offset = self._parse_int(qs.get("offset", ["0"])[0] or "0", 0, 0, 100000)
            source_type = (qs.get("source_type", ["all"])[0] or "all").strip()
            sync = sync_index_from_storage()
            rows = search_index(query=query, limit=limit, offset=offset, source_type=source_type)
            self._send_json(200, {"sync": sync, "count": len(rows), "results": rows})
            return

        if parsed.path == "/api/timeline":
            qs = parse_qs(parsed.query)
            anchor = self._parse_int(qs.get("anchor", ["0"])[0] or "0", 0, 0, 10_000_000)
            before = self._parse_int(qs.get("depth_before", ["3"])[0] or "3", 3, 0, 20)
            after = self._parse_int(qs.get("depth_after", ["3"])[0] or "3", 3, 0, 20)
            sync = sync_index_from_storage()
            rows = timeline_index(anchor_id=anchor, depth_before=before, depth_after=after) if anchor > 0 else []
            self._send_json(200, {"sync": sync, "count": len(rows), "timeline": rows})
            return

        if parsed.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for _ in range(SSE_MAX_TICKS):
                try:
                    sync = sync_index_from_storage()
                    data = {"at": datetime.now().isoformat(), "sync": sync, **index_stats()}
                    chunk = f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(SSE_INTERVAL_SEC)
                except (BrokenPipeError, ConnectionResetError):
                    break
                except Exception:
                    break
            return

        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if parsed.path != "/api/observations/batch":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_POST_BYTES:
                self._send_json(413, {"ok": False, "error": "payload too large"})
                return
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw) if raw else {}
            ids = data.get("ids") or []
            if not isinstance(ids, list):
                self._send_json(400, {"ok": False, "error": "ids must be array"})
                return
            if len(ids) > MAX_BATCH_IDS:
                self._send_json(400, {"ok": False, "error": "too many ids"})
                return
            limit = self._parse_int(str(data.get("limit") or "100"), 100, 1, 300)
            sync = sync_index_from_storage()
            parsed_ids = []
            for x in ids:
                try:
                    parsed_ids.append(int(x))
                except Exception:
                    continue
            rows = get_observations_by_ids(parsed_ids[:MAX_BATCH_IDS], limit=limit)
            self._send_json(200, {"sync": sync, "count": len(rows), "observations": rows})
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid request payload"})


def main():
    if HOST not in LOOPBACK_HOSTS and not VIEWER_TOKEN:
        raise SystemExit("CONTEXT_VIEWER_TOKEN is required when binding non-loopback host.")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Context Mesh Viewer listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
