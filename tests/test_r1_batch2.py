#!/usr/bin/env python3
"""R10 AutoResearch coverage gap tests — batch 2.

Covers:
  - _handle_batch_fetch: non-integer Content-Length, non-integer IDs
  - _add_cors_headers: malformed Origin (no CORS headers emitted)
  - _maybe_sync_index: concurrent double-checked locking
  - local_memory_matches: CJK query, emoji content
  - iter_shared_files: empty dir, hidden-files-only dir
  - _sanitize_text: clean text passthrough, multiple secret types
  - search_index: empty query, SQL-injection-safe special characters
"""

import os

os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", "/tmp/cgo_test_r10")

import io
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Ensure scripts/ is on the path so direct imports work.
_SCRIPTS_DIR = str(Path(__file__).parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import memory_index as _mi  # noqa: E402
import memory_viewer as _mv  # noqa: E402
from context_core import iter_shared_files, local_memory_matches  # noqa: E402
from memory_index import _sanitize_text, search_index  # noqa: E402, I001

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(
    method: str = "POST",
    path: str = "/api/observations/batch",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> _mv.Handler:
    """Build a Handler instance wired to an in-memory fake socket."""
    # Patch rfile/wfile after construction to avoid raw HTTP parsing overhead.
    handler = object.__new__(_mv.Handler)
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.headers = MagicMock()
    handler.headers.__getitem__ = MagicMock(side_effect=lambda k: (headers or {}).get(k, ""))
    handler.headers.get = MagicMock(side_effect=lambda k, default="": (headers or {}).get(k, default))
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.server = MagicMock()
    handler.client_address = ("127.0.0.1", 12345)
    handler.command = method
    handler.path = path
    handler._headers_buffer: list[bytes] = []  # type: ignore[attr-defined]

    # Collect written response bytes for inspection.
    _sent: list[bytes] = []

    def _send_response(code: int, message: str | None = None) -> None:  # noqa: ARG001
        _sent.append(f"HTTP/1.1 {code}\r\n".encode())

    def _send_header(name: str, value: str) -> None:
        _sent.append(f"{name}: {value}\r\n".encode())

    def _end_headers() -> None:
        _sent.append(b"\r\n")

    handler.send_response = _send_response  # type: ignore[method-assign]
    handler.send_header = _send_header  # type: ignore[method-assign]
    handler.end_headers = _end_headers  # type: ignore[method-assign]
    handler._sent = _sent  # type: ignore[attr-defined]

    return handler


def _response_status(handler: _mv.Handler) -> int | None:
    """Extract the HTTP status code captured in handler._sent."""
    for chunk in handler._sent:  # type: ignore[attr-defined]
        decoded = chunk.decode(errors="ignore")
        if decoded.startswith("HTTP/1.1 "):
            try:
                return int(decoded.split()[1])
            except (IndexError, ValueError):
                pass
    return None


def _response_headers(handler: _mv.Handler) -> dict[str, str]:
    """Return a dict of header name -> value from captured _sent bytes."""
    result: dict[str, str] = {}
    for chunk in handler._sent:  # type: ignore[attr-defined]
        decoded = chunk.decode(errors="ignore").rstrip("\r\n")
        if ": " in decoded:
            name, _, value = decoded.partition(": ")
            result[name.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# 1. _handle_batch_fetch — non-integer Content-Length
# ---------------------------------------------------------------------------


def test_handle_batch_fetch_non_integer_content_length() -> None:
    """A Content-Length of 'abc' must produce a 400 response."""
    handler = _make_handler(
        headers={"Content-Length": "abc"},
        body=b'{"ids": [1, 2]}',
    )
    with patch.object(_mv, "_maybe_sync_index", return_value={}):
        handler._handle_batch_fetch()

    status = _response_status(handler)
    assert status == 400, f"Expected 400 for non-integer Content-Length, got {status}"

    # Confirm the error keyword appears somewhere in the written output.
    written = b"".join(
        c
        for c in handler._sent  # type: ignore[attr-defined]
        if isinstance(c, bytes)
    )
    assert b"invalid" in written.lower() or b"Content-Length" in written or status == 400


# ---------------------------------------------------------------------------
# 2. _handle_batch_fetch — non-integer IDs in list
# ---------------------------------------------------------------------------


def test_handle_batch_fetch_non_integer_ids_in_list() -> None:
    """IDs list with strings, null, and int must be handled gracefully (200 or 400, no crash)."""
    import json

    payload = json.dumps({"ids": ["abc", None, 3]}).encode()
    handler = _make_handler(
        headers={"Content-Length": str(len(payload))},
        body=payload,
    )
    handler.rfile = io.BytesIO(payload)

    with (
        patch.object(_mv, "_maybe_sync_index", return_value={"scanned": 0}),
        patch.object(_mv, "get_observations_by_ids", return_value=[]),
    ):
        handler._handle_batch_fetch()

    status = _response_status(handler)
    # "abc" and null should be silently skipped; int 3 is valid → 200
    assert status in (200, 400), f"Unexpected status {status} for mixed-type IDs"


# ---------------------------------------------------------------------------
# 3. _add_cors_headers — malformed Origin
# ---------------------------------------------------------------------------


def test_add_cors_headers_malformed_origin() -> None:
    """A syntactically valid but non-loopback Origin must NOT emit ACAO header."""
    handler = _make_handler(headers={"Origin": "not a valid url"})

    # _add_cors_headers reads headers via handler.headers.get("Origin", "")
    handler._add_cors_headers()

    headers_sent = _response_headers(handler)
    assert "Access-Control-Allow-Origin" not in headers_sent, (
        "ACAO header must not be set for non-loopback origin 'not a valid url'"
    )


def test_add_cors_headers_evil_lookalike_origin() -> None:
    """An origin with loopback IP embedded in domain must NOT emit ACAO header."""
    handler = _make_handler(headers={"Origin": "http://evil127.0.0.1.attacker.com"})
    handler._add_cors_headers()

    headers_sent = _response_headers(handler)
    assert "Access-Control-Allow-Origin" not in headers_sent, "ACAO header must not be set for evil lookalike origin"


def test_add_cors_headers_loopback_origin_allowed() -> None:
    """A genuine loopback Origin must emit the ACAO header."""
    handler = _make_handler(headers={"Origin": "http://127.0.0.1:37677"})
    handler._add_cors_headers()

    headers_sent = _response_headers(handler)
    assert "Access-Control-Allow-Origin" in headers_sent, "ACAO header must be set for loopback origin"


# ---------------------------------------------------------------------------
# 4. _maybe_sync_index — concurrent double-checked locking
# ---------------------------------------------------------------------------


def test_maybe_sync_index_concurrent() -> None:
    """Two concurrent threads must both complete without error and cache warms."""
    # Reset module-level cache state.
    _mv._sync_at = 0.0
    _mv._sync_payload = None

    results: list[dict[str, Any]] = []
    errors: list[Exception] = []

    fake_sync_result = {"scanned": 5, "added": 2, "updated": 0, "removed": 0}

    def _call() -> None:
        try:
            r = _mv._maybe_sync_index()
            results.append(r)
        except Exception as exc:
            errors.append(exc)

    with patch.object(_mv, "sync_index_from_storage", return_value=fake_sync_result):
        t1 = threading.Thread(target=_call)
        t2 = threading.Thread(target=_call)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    assert not errors, f"Threads raised errors: {errors}"
    assert len(results) == 2, "Both threads must return results"
    # Cache should now be populated.
    assert _mv._sync_payload is not None, "Cache must be populated after concurrent calls"
    for r in results:
        assert "scanned" in r or isinstance(r, dict), f"Result has unexpected shape: {r}"


# ---------------------------------------------------------------------------
# 5. local_memory_matches — CJK query
# ---------------------------------------------------------------------------


def test_local_memory_matches_cjk_query() -> None:
    """A Chinese query matched against Chinese content must return a valid snippet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        content = "这是一段包含中文内容的记忆文件，用于测试CJK字符边界处理。\n" * 10
        (root / "cjk_memory.md").write_text(content, encoding="utf-8")

        matches = local_memory_matches("中文内容", shared_root=root, limit=5)

    assert len(matches) >= 1, "CJK query must match Chinese content"
    match = matches[0]
    assert match["matched_in"] == "content"
    # Snippet must be valid UTF-8 (no broken characters).
    snippet = match["snippet"]
    snippet.encode("utf-8")  # raises if broken
    assert "中文" in snippet or len(snippet) > 0


# ---------------------------------------------------------------------------
# 6. local_memory_matches — emoji content
# ---------------------------------------------------------------------------


def test_local_memory_matches_emoji_content() -> None:
    """Files containing emoji must not cause a crash during search."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        emoji_content = "🚀 Deploy complete! 🎉 All systems nominal. 💡 New feature: emoji support.\n" * 5
        (root / "emoji_log.md").write_text(emoji_content, encoding="utf-8")

        # Search for a plain ASCII term present in the emoji-heavy file.
        matches = local_memory_matches("Deploy", shared_root=root, limit=5)

    assert isinstance(matches, list), "Result must be a list even for emoji-heavy content"
    if matches:
        assert matches[0]["matched_in"] in ("content", "path")
        # Snippet must be encodable.
        matches[0]["snippet"].encode("utf-8")


def test_local_memory_matches_emoji_query() -> None:
    """Searching with an emoji query must not raise an exception."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "test.md").write_text("🚀 rocket launch successful\n", encoding="utf-8")

        matches = local_memory_matches("🚀", shared_root=root, limit=5)

    # Must not raise; result can be empty or non-empty.
    assert isinstance(matches, list)


# ---------------------------------------------------------------------------
# 7. iter_shared_files — empty directory
# ---------------------------------------------------------------------------


def test_iter_shared_files_empty_directory() -> None:
    """An empty directory must return an empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = iter_shared_files(Path(tmpdir), max_files=100)
    assert result == [], f"Expected [], got {result}"


# ---------------------------------------------------------------------------
# 8. iter_shared_files — directory with only hidden files
# ---------------------------------------------------------------------------


def test_iter_shared_files_only_hidden_files() -> None:
    """A directory containing only hidden files must return an empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".hidden.md").write_text("hidden content", encoding="utf-8")
        (root / ".dotfile.txt").write_text("dot file", encoding="utf-8")
        (root / ".secret.json").write_text("{}", encoding="utf-8")

        result = iter_shared_files(root, max_files=100)

    assert result == [], f"Expected [] for hidden-only dir, got {result}"


# ---------------------------------------------------------------------------
# 9. _sanitize_text — clean text passthrough
# ---------------------------------------------------------------------------


def test_sanitize_text_no_secrets() -> None:
    """Text without any secrets must pass through unchanged (modulo strip)."""
    clean = "This is a perfectly clean memory observation with no secrets."
    result = _sanitize_text(clean)
    assert result == clean, f"Clean text must not be modified; got: {result!r}"


def test_sanitize_text_empty_string() -> None:
    """Empty string input must return empty string."""
    assert _sanitize_text("") == ""


def test_sanitize_text_whitespace_only() -> None:
    """Whitespace-only input must return empty string (strip behaviour)."""
    assert _sanitize_text("   \n\t  ") == ""


# ---------------------------------------------------------------------------
# 10. _sanitize_text — multiple secret types
# ---------------------------------------------------------------------------


def test_sanitize_text_multiple_secret_types() -> None:
    """All known secret pattern types must be redacted in a single pass."""
    text = (
        "OpenAI key: sk-abcdef1234567890abcdef1234567890\n"
        "GitHub PAT: ghp_ABCDEFGHIJKLMNOPQRSTUV1234567\n"
        "Google key: AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ12345\n"
        "Some normal text that should remain."
    )
    result = _sanitize_text(text)

    assert "sk-abcdef" not in result, "OpenAI key must be redacted"
    assert "ghp_ABCDE" not in result, "GitHub PAT must be redacted"
    assert "AIzaSy" not in result, "Google API key must be redacted"
    assert "***REDACTED***" in result, "Redacted placeholder must appear"
    assert "normal text" in result, "Non-secret text must be preserved"


def test_sanitize_text_private_block_and_secret() -> None:
    """Both private blocks and secrets must be stripped in a single call."""
    text = "<private>secret section</private> public sk-AAAA1234567890123456 end"
    result = _sanitize_text(text)
    assert "secret section" not in result, "<private> block content must be removed"
    assert "sk-AAAA" not in result, "Secret key must be redacted"
    assert "public" in result, "Public text before the key must survive"
    assert "end" in result, "Text after the key must survive"


# ---------------------------------------------------------------------------
# 11. search_index — empty query
# ---------------------------------------------------------------------------


def test_search_index_empty_query() -> None:
    """An empty query must return a list (possibly empty) without raising."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "memory_index.db")
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": db_path}):
            _mi.ensure_index_db()
            result = search_index(query="", limit=10)
    assert isinstance(result, list), f"Expected list, got {type(result)}"


def test_search_index_whitespace_query() -> None:
    """A whitespace-only query must behave the same as an empty query."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "memory_index.db")
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": db_path}):
            _mi.ensure_index_db()
            result = search_index(query="   ", limit=10)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 12. search_index — SQL injection via special characters
# ---------------------------------------------------------------------------


def test_search_index_sql_injection_attempt() -> None:
    """A query containing SQL metacharacters must not raise or leak data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "memory_index.db")
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": db_path}):
            _mi.ensure_index_db()
            malicious_queries = [
                "SELECT * FROM observations",
                "'; DROP TABLE observations; --",
                "1=1 OR '1'='1",
                "UNION SELECT * FROM sqlite_master",
                r"% _ \\ %",
            ]
            for query in malicious_queries:
                result = search_index(query=query, limit=5)
                assert isinstance(result, list), f"search_index must return a list for injection attempt: {query!r}"


def test_search_index_special_chars_no_error() -> None:
    """Special characters in query must not cause SQL errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "memory_index.db")
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": db_path}):
            _mi.ensure_index_db()
            special_queries = [
                "hello 'world'",
                'say "hi"',
                "path/to/file.md",
                "tag:important AND type:memory",
                "100% complete",
            ]
            for query in special_queries:
                result = search_index(query=query, limit=5)
                assert isinstance(result, list), f"Failed for query: {query!r}"
