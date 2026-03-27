#!/usr/bin/env python3
"""Lightweight memory viewer HTTP API with SSE for ContextGO.

Exposes a local-only web interface and JSON REST endpoints backed by the
memory index.  Binds to 127.0.0.1 by default; non-loopback binds require
``CONTEXTGO_VIEWER_TOKEN`` to be set.

Endpoints
---------
GET  /                          Single-page viewer UI
GET  /api/health                Health check + index stats
GET  /api/search?query=…        Full-text search
GET  /api/timeline?anchor=…     Timeline traversal
GET  /api/events                Server-Sent Events stream
POST /api/observations/batch    Fetch observations by ID
"""

from __future__ import annotations

__all__ = ["main"]

import hmac
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from context_config import env_float, env_int, env_str
except ImportError:  # pragma: no cover
    from .context_config import env_float, env_int, env_str  # type: ignore[import-not-found]

try:
    from memory_index import (
        get_observations_by_ids,
        index_stats,
        search_index,
        sync_index_from_storage,
        timeline_index,
    )
except ImportError:  # pragma: no cover
    from .memory_index import (  # type: ignore[import-not-found]
        get_observations_by_ids,
        index_stats,
        search_index,
        sync_index_from_storage,
        timeline_index,
    )


# ---------------------------------------------------------------------------
# Server configuration (resolved once at import time; mutable by context_server)
# ---------------------------------------------------------------------------

HOST: str = env_str("CONTEXTGO_VIEWER_HOST", default="127.0.0.1")
PORT: int = env_int("CONTEXTGO_VIEWER_PORT", default=37677, minimum=1)
VIEWER_TOKEN: str = env_str("CONTEXTGO_VIEWER_TOKEN", default="").strip()

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_POST_BYTES: int = env_int("CONTEXTGO_VIEWER_MAX_POST_BYTES", default=1_048_576, minimum=1024)
_MAX_BATCH_IDS: int = env_int("CONTEXTGO_VIEWER_MAX_BATCH_IDS", default=500, minimum=1)
_SSE_INTERVAL_SEC: float = env_float("CONTEXTGO_VIEWER_SSE_INTERVAL_SEC", default=1.0, minimum=0.2)
_SSE_MAX_TICKS: int = env_int("CONTEXTGO_VIEWER_SSE_MAX_TICKS", default=120, minimum=1)
_SYNC_MIN_INTERVAL_SEC: float = env_float("CONTEXTGO_VIEWER_SYNC_MIN_INTERVAL_SEC", default=5.0, minimum=0.0)

# ---------------------------------------------------------------------------
# Sync state cache  (thread-safe, double-checked locking)
# ---------------------------------------------------------------------------

_sync_lock = threading.Lock()
_sync_at: float = 0.0
_sync_payload: dict[str, Any] | None = None


def _maybe_sync_index() -> dict[str, Any]:
    """Return cached sync results, refreshing only when the TTL has expired.

    Uses a lock-free fast path so already-cached results bypass the mutex
    entirely and avoid contention under concurrent requests.
    """
    global _sync_at, _sync_payload  # noqa: PLW0603

    now = time.monotonic()

    # Fast path: read without acquiring the lock when cache is warm.
    cached = _sync_payload
    if _SYNC_MIN_INTERVAL_SEC > 0 and cached is not None and (_sync_at + _SYNC_MIN_INTERVAL_SEC) > now:
        return dict(cached)

    # Slow path: refresh outside the lock so only one expensive I/O call runs
    # at a time, but other threads can still serve the stale cache.
    with _sync_lock:
        # Re-check after acquiring the lock (another thread may have refreshed).
        if _sync_payload is not None and (_sync_at + _SYNC_MIN_INTERVAL_SEC) > time.monotonic():
            return dict(_sync_payload)
        payload = sync_index_from_storage()
        _sync_at = time.monotonic()
        _sync_payload = dict(payload)
    return payload


# ---------------------------------------------------------------------------
# Viewer HTML (single-page application)
# ---------------------------------------------------------------------------

_VIEWER_HTML: str = """<!doctype html>
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
    .search-row { display: flex; gap: 8px; margin-bottom: 20px; }
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
  out.textContent = 'Searching\u2026';
  try {
    const res = await fetch(
      '/api/search?query=' + encodeURIComponent(q) + '&limit=20',
      { headers: { 'Accept': 'application/json' } }
    );
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error('HTTP ' + res.status + ': ' + (j.error || res.statusText));
    }
    out.textContent = JSON.stringify(await res.json(), null, 2);
  } catch (e) {
    out.textContent = '';
    banner.textContent = 'Search failed: ' + e.message;
    banner.style.display = 'block';
  }
}

document.getElementById('q').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') run();
});

(function connectSSE() {
  const dot = document.getElementById('status-dot');
  const stats = document.getElementById('stats');
  const es = new EventSource('/api/events');
  es.onopen = () => { dot.className = 'live'; };
  es.onmessage = function(e) {
    try {
      const d = JSON.parse(e.data);
      const n = d.total_observations ?? '?';
      document.title = 'ContextGO Viewer (' + n + ')';
      stats.textContent = n + ' observations';
      dot.className = 'live';
    } catch (_) {}
  };
  es.onerror = function() {
    dot.className = 'err';
    es.close();
    setTimeout(connectSSE, 5000);
  };
})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _json_bytes(payload: dict[str, Any]) -> bytes:
    """Serialise *payload* to a UTF-8 encoded JSON byte string."""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _clamp_int(value: str, default: int, min_v: int, max_v: int) -> int:
    """Parse *value* as an integer clamped to [*min_v*, *max_v*]."""
    try:
        return max(min_v, min(max_v, int(value)))
    except (ValueError, TypeError):
        return default


def _qs_str(qs: dict[str, list[str]], key: str, default: str = "") -> str:
    """Return the first value for *key* in *qs*, stripped, or *default*."""
    return (qs.get(key, [default])[0] or default).strip()


def _qs_int(qs: dict[str, list[str]], key: str, default: int, min_v: int, max_v: int) -> int:
    """Return the first value for *key* in *qs* as a clamped integer."""
    return _clamp_int(qs.get(key, [str(default)])[0] or str(default), default, min_v, max_v)


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

# Security headers sent with every response.
_SECURITY_HEADERS: list[tuple[str, str]] = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
]

# CSP for the HTML viewer page.
_CSP_HTML = (
    "default-src 'self'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "connect-src 'self'; "
    "img-src 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'"
)


class Handler(BaseHTTPRequestHandler):
    """Request handler for the ContextGO viewer HTTP server."""

    server_version = "ContextGOViewer/1.0"

    # Silence per-request access logging; errors surface as JSON responses.
    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        return

    # ------------------------------------------------------------------
    # Security helpers
    # ------------------------------------------------------------------

    def _authorized(self) -> bool:
        """Return ``True`` when the request carries a valid auth token.

        Uses :func:`hmac.compare_digest` to prevent timing-based attacks.
        """
        if not VIEWER_TOKEN:
            return True
        got = self.headers.get("X-Context-Token", "").strip()
        return bool(got) and hmac.compare_digest(got, VIEWER_TOKEN)

    def _add_security_headers(self) -> None:
        """Emit common security headers on the current response."""
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)

    def _add_cors_headers(self) -> None:
        """Emit CORS headers that permit only loopback origins.

        The Origin header is parsed with :func:`urllib.parse.urlparse` so that
        only the *hostname* component is tested against the loopback allowlist.
        This prevents bypass attempts such as
        ``http://evil127.0.0.1.attacker.com`` that would pass a naive
        substring check.
        """
        origin = self.headers.get("Origin", "")
        if not origin:
            return
        self.send_header("Vary", "Origin")
        try:
            parsed_origin = urlparse(origin)
            origin_host = parsed_origin.hostname or ""
        except Exception:  # noqa: BLE001
            origin_host = ""
        if origin_host in _LOOPBACK_HOSTS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Context-Token")

    # ------------------------------------------------------------------
    # Response writers
    # ------------------------------------------------------------------

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """Write a JSON response with the given HTTP *status* code."""
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._add_security_headers()
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        """Write an HTML response with a restrictive Content-Security-Policy."""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", _CSP_HTML)
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_unauthorized(self) -> None:
        self._send_json(401, {"ok": False, "error": "unauthorized"})

    def _send_not_found(self, path: str) -> None:
        self._send_json(404, {"ok": False, "error": "not found", "path": path})

    # ------------------------------------------------------------------
    # Route dispatch
    # ------------------------------------------------------------------

    def do_OPTIONS(self) -> None:
        """Handle CORS pre-flight requests."""
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Dispatch GET requests to the appropriate handler."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_html(_VIEWER_HTML)
            return

        if not self._authorized():
            self._send_unauthorized()
            return

        match path:
            case "/api/health":
                self._handle_health()
            case "/api/search":
                self._handle_search(parsed.query)
            case "/api/timeline":
                self._handle_timeline(parsed.query)
            case "/api/events":
                self._handle_sse()
            case _:
                self._send_not_found(path)

    def do_POST(self) -> None:
        """Dispatch POST requests to the appropriate handler."""
        parsed = urlparse(self.path)
        path = parsed.path

        if not self._authorized():
            self._send_unauthorized()
            return

        match path:
            case "/api/observations/batch":
                self._handle_batch_fetch()
            case _:
                self._send_not_found(path)

    # ------------------------------------------------------------------
    # Endpoint implementations
    # ------------------------------------------------------------------

    def _handle_health(self) -> None:
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
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return
        self._send_json(
            200,
            {
                "ok": True,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "sync": sync,
                **stats,
            },
        )

    def _handle_search(self, query_string: str) -> None:
        qs = parse_qs(query_string)
        query = _qs_str(qs, "query")
        limit = _qs_int(qs, "limit", 20, 1, 200)
        offset = _qs_int(qs, "offset", 0, 0, 100_000)
        source_type = _qs_str(qs, "source_type", "all")
        try:
            sync = _maybe_sync_index()
            rows = search_index(query=query, limit=limit, offset=offset, source_type=source_type)
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "search failed", "detail": str(exc)})
            return
        self._send_json(200, {"ok": True, "sync": sync, "count": len(rows), "results": rows})

    def _handle_timeline(self, query_string: str) -> None:
        qs = parse_qs(query_string)
        anchor = _qs_int(qs, "anchor", 0, 0, 10_000_000)
        before = _qs_int(qs, "depth_before", 3, 0, 20)
        after = _qs_int(qs, "depth_after", 3, 0, 20)
        try:
            sync = _maybe_sync_index()
            rows = timeline_index(anchor_id=anchor, depth_before=before, depth_after=after) if anchor > 0 else []
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "timeline failed", "detail": str(exc)})
            return
        self._send_json(200, {"ok": True, "sync": sync, "count": len(rows), "timeline": rows})

    def _handle_sse(self) -> None:
        """Stream index stats as Server-Sent Events until the client disconnects."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("retry", "5000")
        self._add_security_headers()
        self._add_cors_headers()
        self.end_headers()

        for _ in range(_SSE_MAX_TICKS):
            try:
                data: dict[str, Any] = {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "sync": _maybe_sync_index(),
                    **index_stats(),
                }
                self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(_SSE_INTERVAL_SEC)
            except (BrokenPipeError, ConnectionResetError, OSError):
                break

    def _handle_batch_fetch(self) -> None:
        """Fetch a batch of observations by their IDs."""
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._send_json(400, {"ok": False, "error": "invalid Content-Length"})
            return

        if length <= 0 or length > _MAX_POST_BYTES:
            self._send_json(
                413,
                {"ok": False, "error": "payload too large", "max_bytes": _MAX_POST_BYTES},
            )
            return

        try:
            raw = self.rfile.read(length).decode("utf-8")
            body: dict[str, Any] = json.loads(raw) if raw.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "invalid JSON body"})
            return

        ids = body.get("ids") or []
        if not isinstance(ids, list):
            self._send_json(400, {"ok": False, "error": "ids must be an array"})
            return

        if len(ids) > _MAX_BATCH_IDS:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": "too many ids",
                    "max": _MAX_BATCH_IDS,
                    "received": len(ids),
                },
            )
            return

        parsed_ids: list[int] = []
        for x in ids:
            try:
                parsed_ids.append(int(x))
            except (ValueError, TypeError):
                continue

        limit = _clamp_int(str(body.get("limit") or "100"), 100, 1, 300)

        try:
            sync = _maybe_sync_index()
            rows = get_observations_by_ids(parsed_ids[:_MAX_BATCH_IDS], limit=limit)
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "internal error", "detail": str(exc)})
            return

        self._send_json(200, {"ok": True, "sync": sync, "count": len(rows), "observations": rows})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the ContextGO viewer HTTP server.

    Raises:
        SystemExit: When a non-loopback bind address is used without a token.
    """
    if HOST not in _LOOPBACK_HOSTS and not VIEWER_TOKEN:
        raise SystemExit("CONTEXTGO_VIEWER_TOKEN must be set when binding a non-loopback host.")
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
