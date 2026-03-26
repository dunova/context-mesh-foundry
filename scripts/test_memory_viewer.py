#!/usr/bin/env python3
"""Unit tests for memory_viewer module."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import memory_viewer  # noqa: E402
from memory_viewer import Handler, _json_bytes, _maybe_sync_index, _qs_int  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers — minimal fake HTTP handler plumbing
# ---------------------------------------------------------------------------


def _make_handler(
    method: str = "GET",
    path: str = "/api/health",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    token: str = "",
) -> tuple[Handler, io.BytesIO]:
    """Create a Handler instance wired to in-memory buffers."""
    wfile = io.BytesIO()
    rfile = io.BytesIO(body)

    h = Handler.__new__(Handler)
    h.command = method
    h.path = path
    h.headers = {**(headers or {})}
    if token:
        h.headers["X-Context-Token"] = token
    h.rfile = rfile
    h.wfile = wfile
    h.request = MagicMock()
    h.client_address = ("127.0.0.1", 12345)
    h.server = MagicMock()

    # Monkey-patch send_response / send_header / end_headers to write to wfile
    h._response_lines: list[bytes] = []

    def _send_response(code: int, message: str = "") -> None:
        h._status_code = code

    def _send_header(key: str, value: str) -> None:
        pass

    def _end_headers() -> None:
        pass

    h.send_response = _send_response  # type: ignore[method-assign]
    h.send_header = _send_header  # type: ignore[method-assign]
    h.end_headers = _end_headers  # type: ignore[method-assign]
    h._status_code = 200

    return h, wfile


def _parse_json_response(wfile: io.BytesIO) -> dict:
    wfile.seek(0)
    return json.loads(wfile.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Tests: _json_bytes
# ---------------------------------------------------------------------------


class TestJsonBytes(unittest.TestCase):
    def test_serialises_dict(self) -> None:
        result = _json_bytes({"ok": True, "count": 3})
        parsed = json.loads(result.decode("utf-8"))
        self.assertEqual(parsed, {"ok": True, "count": 3})

    def test_unicode_not_escaped(self) -> None:
        result = _json_bytes({"msg": "你好"})
        self.assertIn("你好", result.decode("utf-8"))

    def test_returns_bytes(self) -> None:
        result = _json_bytes({})
        self.assertIsInstance(result, bytes)


# ---------------------------------------------------------------------------
# Tests: _maybe_sync_index
# ---------------------------------------------------------------------------


class TestMaybeSyncIndex(unittest.TestCase):
    def setUp(self) -> None:
        # Reset sync state before each test
        memory_viewer._sync_at = 0.0
        memory_viewer._sync_payload = None

    def test_calls_sync_when_cache_empty(self) -> None:
        fake_payload = {"total_observations": 5}
        with patch("memory_viewer.sync_index_from_storage", return_value=fake_payload) as m:
            result = _maybe_sync_index()
        m.assert_called_once()
        self.assertEqual(result, fake_payload)

    def test_returns_cached_when_fresh(self) -> None:
        import time

        fake_payload = {"total_observations": 7}
        memory_viewer._sync_at = time.monotonic()  # very fresh
        memory_viewer._sync_payload = dict(fake_payload)
        with patch("memory_viewer.sync_index_from_storage") as m:
            result = _maybe_sync_index()
        m.assert_not_called()
        self.assertEqual(result, fake_payload)

    def test_refreshes_when_stale(self) -> None:
        fresh_payload = {"total_observations": 99}
        memory_viewer._sync_at = 0.0  # definitely stale
        memory_viewer._sync_payload = {"total_observations": 1}
        with patch("memory_viewer.sync_index_from_storage", return_value=fresh_payload):
            result = _maybe_sync_index()
        self.assertEqual(result["total_observations"], 99)


# ---------------------------------------------------------------------------
# Tests: _qs_int (module-level query string integer parser)
# ---------------------------------------------------------------------------


class TestQsInt(unittest.TestCase):
    def test_valid_int(self) -> None:
        qs = {"limit": ["10"]}
        self.assertEqual(_qs_int(qs, "limit", 5, 1, 100), 10)

    def test_invalid_falls_back_to_default(self) -> None:
        qs = {"limit": ["abc"]}
        self.assertEqual(_qs_int(qs, "limit", 5, 1, 100), 5)

    def test_clamps_to_min(self) -> None:
        qs = {"limit": ["0"]}
        self.assertEqual(_qs_int(qs, "limit", 5, 2, 100), 2)

    def test_clamps_to_max(self) -> None:
        qs = {"limit": ["999"]}
        self.assertEqual(_qs_int(qs, "limit", 5, 1, 50), 50)

    def test_exact_min(self) -> None:
        qs = {"limit": ["1"]}
        self.assertEqual(_qs_int(qs, "limit", 5, 1, 100), 1)

    def test_exact_max(self) -> None:
        qs = {"limit": ["100"]}
        self.assertEqual(_qs_int(qs, "limit", 5, 1, 100), 100)


# ---------------------------------------------------------------------------
# Tests: Handler._authorized
# ---------------------------------------------------------------------------


class TestHandlerAuthorized(unittest.TestCase):
    def test_no_token_configured_always_authorized(self) -> None:
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = ""
            h, _ = _make_handler()
            self.assertTrue(h._authorized())
        finally:
            memory_viewer.VIEWER_TOKEN = original

    def test_with_correct_token_authorized(self) -> None:
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "mysecret"
            h, _ = _make_handler(headers={"X-Context-Token": "mysecret"})
            self.assertTrue(h._authorized())
        finally:
            memory_viewer.VIEWER_TOKEN = original

    def test_with_wrong_token_not_authorized(self) -> None:
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "correct"
            h, _ = _make_handler(headers={"X-Context-Token": "wrong"})
            self.assertFalse(h._authorized())
        finally:
            memory_viewer.VIEWER_TOKEN = original

    def test_missing_token_header_not_authorized(self) -> None:
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "required"
            h, _ = _make_handler()
            self.assertFalse(h._authorized())
        finally:
            memory_viewer.VIEWER_TOKEN = original


# ---------------------------------------------------------------------------
# Tests: Handler._send_json
# ---------------------------------------------------------------------------


class TestHandlerSendJson(unittest.TestCase):
    def test_writes_json_body(self) -> None:
        h, wfile = _make_handler()
        h._send_json(200, {"ok": True, "value": 42})
        payload = _parse_json_response(wfile)
        self.assertEqual(payload, {"ok": True, "value": 42})

    def test_status_code_set(self) -> None:
        h, _ = _make_handler()
        h._send_json(404, {"ok": False, "error": "not found"})
        self.assertEqual(h._status_code, 404)

    def test_error_response(self) -> None:
        h, wfile = _make_handler()
        h._send_json(500, {"ok": False, "error": "internal error"})
        payload = _parse_json_response(wfile)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "internal error")


# ---------------------------------------------------------------------------
# Tests: Handler._handle_health
# ---------------------------------------------------------------------------


class TestHandlerHealth(unittest.TestCase):
    def test_health_ok(self) -> None:
        h, wfile = _make_handler()
        with (
            patch("memory_viewer._maybe_sync_index", return_value={"synced": True}),
            patch("memory_viewer.index_stats", return_value={"total_observations": 10}),
        ):
            h._handle_health()
        payload = _parse_json_response(wfile)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total_observations"], 10)

    def test_health_returns_500_on_exception(self) -> None:
        h, wfile = _make_handler()
        with patch("memory_viewer._maybe_sync_index", side_effect=RuntimeError("boom")):
            h._handle_health()
        self.assertEqual(h._status_code, 500)
        payload = _parse_json_response(wfile)
        self.assertFalse(payload["ok"])
        self.assertIn("boom", payload["detail"])


# ---------------------------------------------------------------------------
# Tests: Handler._handle_search
# ---------------------------------------------------------------------------


class TestHandlerSearch(unittest.TestCase):
    def test_search_returns_results(self) -> None:
        h, wfile = _make_handler()
        fake_rows = [{"id": 1, "text": "hello"}]
        with (
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.search_index", return_value=fake_rows) as m,
        ):
            h._handle_search("query=hello&limit=10")
        payload = _parse_json_response(wfile)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"], fake_rows)
        m.assert_called_once_with(query="hello", limit=10, offset=0, source_type="all")

    def test_search_returns_500_on_exception(self) -> None:
        h, wfile = _make_handler()
        with (
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.search_index", side_effect=RuntimeError("db error")),
        ):
            h._handle_search("query=fail")
        self.assertEqual(h._status_code, 500)
        payload = _parse_json_response(wfile)
        self.assertFalse(payload["ok"])

    def test_search_default_params(self) -> None:
        h, wfile = _make_handler()
        with (
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.search_index", return_value=[]) as m,
        ):
            h._handle_search("")
        m.assert_called_once_with(query="", limit=20, offset=0, source_type="all")


# ---------------------------------------------------------------------------
# Tests: Handler._handle_timeline
# ---------------------------------------------------------------------------


class TestHandlerTimeline(unittest.TestCase):
    def test_timeline_with_anchor_zero_returns_empty(self) -> None:
        h, wfile = _make_handler()
        with (
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.timeline_index", return_value=[]) as m,
        ):
            h._handle_timeline("anchor=0")
        payload = _parse_json_response(wfile)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["timeline"], [])
        m.assert_not_called()

    def test_timeline_with_anchor_calls_timeline_index(self) -> None:
        h, wfile = _make_handler()
        fake_rows = [{"id": 5, "text": "context"}]
        with (
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.timeline_index", return_value=fake_rows) as m,
        ):
            h._handle_timeline("anchor=5&depth_before=2&depth_after=2")
        payload = _parse_json_response(wfile)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["timeline"], fake_rows)
        m.assert_called_once_with(anchor_id=5, depth_before=2, depth_after=2)

    def test_timeline_returns_500_on_exception(self) -> None:
        h, wfile = _make_handler()
        with (
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.timeline_index", side_effect=RuntimeError("fail")),
        ):
            h._handle_timeline("anchor=1")
        self.assertEqual(h._status_code, 500)


# ---------------------------------------------------------------------------
# Tests: Handler._handle_batch_fetch
# ---------------------------------------------------------------------------


class TestHandlerBatchFetch(unittest.TestCase):
    def _make_post_handler(self, body: bytes, content_length: int | None = None) -> tuple[Handler, io.BytesIO]:
        cl = str(content_length if content_length is not None else len(body))
        return _make_handler(
            method="POST",
            path="/api/observations/batch",
            headers={"Content-Length": cl},
            body=body,
        )

    def test_valid_batch_returns_observations(self) -> None:
        body = json.dumps({"ids": [1, 2, 3]}).encode()
        h, wfile = self._make_post_handler(body)
        fake_rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        with (
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.get_observations_by_ids", return_value=fake_rows),
        ):
            h._handle_batch_fetch()
        payload = _parse_json_response(wfile)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 3)

    def test_empty_body_returns_400(self) -> None:
        h, wfile = self._make_post_handler(b"", content_length=0)
        h._handle_batch_fetch()
        self.assertEqual(h._status_code, 413)

    def test_invalid_content_length_returns_400(self) -> None:
        h, wfile = self._make_post_handler(b"{}", content_length=-1)
        # Override Content-Length header to invalid value
        h.headers["Content-Length"] = "not_a_number"
        h._handle_batch_fetch()
        self.assertEqual(h._status_code, 400)

    def test_ids_not_array_returns_400(self) -> None:
        body = json.dumps({"ids": "not_array"}).encode()
        h, wfile = self._make_post_handler(body)
        with patch("memory_viewer._maybe_sync_index", return_value={}):
            h._handle_batch_fetch()
        self.assertEqual(h._status_code, 400)

    def test_too_many_ids_returns_400(self) -> None:
        original_max = memory_viewer._MAX_BATCH_IDS
        try:
            memory_viewer._MAX_BATCH_IDS = 3
            body = json.dumps({"ids": [1, 2, 3, 4, 5]}).encode()
            h, wfile = self._make_post_handler(body)
            with patch("memory_viewer._maybe_sync_index", return_value={}):
                h._handle_batch_fetch()
            self.assertEqual(h._status_code, 400)
        finally:
            memory_viewer._MAX_BATCH_IDS = original_max

    def test_invalid_json_returns_400(self) -> None:
        body = b"not json at all"
        h, wfile = self._make_post_handler(body)
        with patch("memory_viewer._maybe_sync_index", return_value={}):
            h._handle_batch_fetch()
        self.assertEqual(h._status_code, 400)


# ---------------------------------------------------------------------------
# Tests: Handler.do_GET routing
# ---------------------------------------------------------------------------


class TestHandlerDoGet(unittest.TestCase):
    def test_root_path_returns_html(self) -> None:
        h, wfile = _make_handler(path="/")
        # Track what content-type was set
        sent_headers: list[tuple[str, str]] = []
        h.send_header = lambda k, v: sent_headers.append((k, v))  # type: ignore[method-assign]

        with (
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.index_stats", return_value={}),
        ):
            h.do_GET()

        wfile.seek(0)
        content = wfile.read()
        self.assertIn(b"ContextGO", content)

    def test_api_not_found_returns_404(self) -> None:
        h, wfile = _make_handler(path="/api/nonexistent")
        original_token = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = ""  # no auth required
            h.do_GET()
        finally:
            memory_viewer.VIEWER_TOKEN = original_token
        self.assertEqual(h._status_code, 404)
        payload = _parse_json_response(wfile)
        self.assertFalse(payload["ok"])

    def test_api_unauthorized_when_token_required(self) -> None:
        h, wfile = _make_handler(path="/api/health")
        original_token = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "required_token"
            # No token in request headers
            h.do_GET()
        finally:
            memory_viewer.VIEWER_TOKEN = original_token
        self.assertEqual(h._status_code, 401)


# ---------------------------------------------------------------------------
# Tests: Handler.do_POST routing
# ---------------------------------------------------------------------------


class TestHandlerDoPost(unittest.TestCase):
    def test_post_to_nonexistent_returns_404(self) -> None:
        h, wfile = _make_handler(method="POST", path="/api/nonexistent")
        original_token = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = ""
            h.do_POST()
        finally:
            memory_viewer.VIEWER_TOKEN = original_token
        self.assertEqual(h._status_code, 404)

    def test_post_unauthorized_when_token_required(self) -> None:
        h, wfile = _make_handler(method="POST", path="/api/observations/batch")
        original_token = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "required_token"
            h.do_POST()
        finally:
            memory_viewer.VIEWER_TOKEN = original_token
        self.assertEqual(h._status_code, 401)


# ---------------------------------------------------------------------------
# Tests: Handler.do_OPTIONS
# ---------------------------------------------------------------------------


class TestHandlerDoOptions(unittest.TestCase):
    def test_options_returns_204(self) -> None:
        h, _ = _make_handler(method="OPTIONS", path="/api/health")
        h.do_OPTIONS()
        self.assertEqual(h._status_code, 204)


# ---------------------------------------------------------------------------
# Tests: CORS headers
# ---------------------------------------------------------------------------


class TestHandlerCors(unittest.TestCase):
    def test_cors_header_for_localhost_origin(self) -> None:
        h, _ = _make_handler(headers={"Origin": "http://localhost:3000"})
        sent: list[tuple[str, str]] = []
        h.send_header = lambda k, v: sent.append((k, v))  # type: ignore[method-assign]
        h._add_cors_headers()
        header_names = [k for k, _ in sent]
        self.assertIn("Access-Control-Allow-Origin", header_names)

    def test_no_cors_header_for_external_origin(self) -> None:
        h, _ = _make_handler(headers={"Origin": "http://evil.com"})
        sent: list[tuple[str, str]] = []
        h.send_header = lambda k, v: sent.append((k, v))  # type: ignore[method-assign]
        h._add_cors_headers()
        header_names = [k for k, _ in sent]
        self.assertNotIn("Access-Control-Allow-Origin", header_names)

    def test_no_cors_header_when_no_origin(self) -> None:
        h, _ = _make_handler()
        sent: list[tuple[str, str]] = []
        h.send_header = lambda k, v: sent.append((k, v))  # type: ignore[method-assign]
        h._add_cors_headers()
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
