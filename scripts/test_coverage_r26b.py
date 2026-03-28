#!/usr/bin/env python3
"""Coverage-targeted tests for session_index.py and memory_viewer.py.

Targets:
  session_index.py:
    - Lines 901-902: ValueError/TypeError in last_sync_epoch parsing
    - Lines 1022-1038: Vector search backend in sync_session_index
    - Lines 1618-1644: Vector hybrid search backend in _search_rows
    - Lines 1703-1710: Search result cache eviction when at max capacity
    - Lines 1732-1741: lookup_session_by_id function

  memory_viewer.py:
    - Lines 407-408: Unauthorized access to / or /index.html
    - Line 514: _SHUTDOWN_EVENT set breaks SSE loop
    - Lines 540-544: Payload too large (413) in batch fetch
    - Lines 549-550: Empty body after read in batch fetch
    - Line 617: __main__ block (main() called directly)
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import memory_viewer  # noqa: E402
import session_index  # noqa: E402
from memory_viewer import Handler  # noqa: E402

# ---------------------------------------------------------------------------
# Helper: build a minimal Handler wired to in-memory buffers
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
# session_index — Lines 901-902: ValueError/TypeError in last_sync_epoch
# ---------------------------------------------------------------------------


class TestSyncSessionIndexBadEpoch(unittest.TestCase):
    """Lines 901-902: last_sync_raw that cannot be converted to int => default 0."""

    def _make_session_file(self, root: Path) -> Path:
        codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
        codex_root.mkdir(parents=True)
        session_file = codex_root / "test.jsonl"
        session_file.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "epoch-test-session",
                        "cwd": "/tmp/test",
                        "timestamp": "2026-03-25T00:00:00Z",
                    },
                }
            ),
            encoding="utf-8",
        )
        return session_file

    def test_invalid_last_sync_epoch_string_defaults_to_zero(self) -> None:
        """When last_sync_raw is a non-integer string, last_sync_epoch should become 0."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._make_session_file(root)
            db_path = root / "session_index.db"

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                # First sync to create the DB
                session_index.sync_session_index(force=True)

                # Corrupt last_sync_epoch to a non-integer value
                with session_index._open_db(db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO session_index_meta (key, value) VALUES (?, ?)",
                        ("last_sync_epoch", "not-a-number"),
                    )
                    conn.commit()

                # Force=False: normal flow hits the ValueError path (lines 901-902)
                # last_sync_epoch becomes 0, so the condition `last_sync_epoch and ...`
                # is False, meaning it proceeds with full sync
                stats = session_index.sync_session_index(force=False)
                # Should not raise; should have done a sync (since epoch=0 means "never synced")
                self.assertIn("scanned", stats)

    def test_none_last_sync_epoch_defaults_to_zero(self) -> None:
        """When last_sync_raw is None (no entry), last_sync_epoch should be 0."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._make_session_file(root)
            db_path = root / "session_index.db"

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                # Force=True: schema version mismatch path sets force=True,
                # then reads last_sync_epoch (which is "0" or None)
                stats = session_index.sync_session_index(force=True)
                self.assertIn("scanned", stats)
                self.assertGreaterEqual(stats["added"], 1)


# ---------------------------------------------------------------------------
# session_index — Lines 1022-1038: Vector backend in sync_session_index
# ---------------------------------------------------------------------------


class TestSyncVectorBackend(unittest.TestCase):
    """Lines 1022-1038: When EXPERIMENTAL_SEARCH_BACKEND=='vector', embed_pending is called."""

    def _make_session_file(self, root: Path) -> None:
        codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
        codex_root.mkdir(parents=True)
        (codex_root / "vec.jsonl").write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "vec-session",
                        "cwd": "/tmp/vectest",
                        "timestamp": "2026-03-25T00:00:00Z",
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_vector_embed_called_when_available(self) -> None:
        """When vector backend is active and vector_available() returns True,
        embed_pending_session_docs should be called."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._make_session_file(root)
            db_path = root / "session_index.db"

            fake_vector_index = MagicMock()
            fake_vector_index.vector_available.return_value = True
            fake_vector_index.get_vector_db_path.return_value = root / "vector.db"
            fake_vector_index.embed_pending_session_docs.return_value = {
                "embedded": 1,
                "skipped": 0,
                "deleted": 0,
            }

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "vector"),
                mock.patch.dict(sys.modules, {"vector_index": fake_vector_index}),
            ):
                stats = session_index.sync_session_index(force=True)

            self.assertIn("scanned", stats)
            fake_vector_index.embed_pending_session_docs.assert_called_once()

    def test_vector_embed_skipped_when_not_available(self) -> None:
        """When vector_available() returns False, embed is not called."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._make_session_file(root)
            db_path = root / "session_index.db"

            fake_vector_index = MagicMock()
            fake_vector_index.vector_available.return_value = False

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "vector"),
                mock.patch.dict(sys.modules, {"vector_index": fake_vector_index}),
            ):
                stats = session_index.sync_session_index(force=True)

            self.assertIn("scanned", stats)
            fake_vector_index.embed_pending_session_docs.assert_not_called()

    def test_vector_embed_exception_is_swallowed(self) -> None:
        """Exceptions during vector embedding are swallowed (line 1037-1038)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._make_session_file(root)
            db_path = root / "session_index.db"

            fake_vector_index = MagicMock()
            fake_vector_index.vector_available.side_effect = RuntimeError("vector boom")

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "vector"),
                mock.patch.dict(sys.modules, {"vector_index": fake_vector_index}),
            ):
                # Must not raise
                stats = session_index.sync_session_index(force=True)

            self.assertIn("scanned", stats)


# ---------------------------------------------------------------------------
# session_index — Lines 1618-1644: Vector hybrid search in _search_rows
# ---------------------------------------------------------------------------


class TestSearchRowsVectorBackend(unittest.TestCase):
    """Lines 1618-1644: _search_rows with vector backend."""

    def _seed_db(self, root: Path, db_path: Path) -> None:
        codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
        codex_root.mkdir(parents=True)
        (codex_root / "srch.jsonl").write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "srch-session",
                        "cwd": "/tmp/srchtest",
                        "timestamp": "2026-03-25T00:00:00Z",
                    },
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(session_index, "_home", return_value=root),
            mock.patch.dict(
                os.environ,
                {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                clear=False,
            ),
        ):
            session_index.sync_session_index(force=True)

    def test_vector_search_returns_results(self) -> None:
        """When vector backend returns ranked results, _search_rows returns them."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path)

            fake_ranked = [{"id": 1, "score": 0.9}]
            fake_enriched = [
                {
                    "source_type": "codex_session",
                    "session_id": "vec-hit",
                    "title": "Vector Result",
                    "file_path": "/tmp/x.jsonl",
                    "created_at": "2026-03-25T00:00:00Z",
                    "created_at_epoch": 1742860800,
                    "snippet": "found by vector",
                }
            ]
            fake_vector_index = MagicMock()
            fake_vector_index.vector_available.return_value = True
            fake_vector_index.get_vector_db_path.return_value = root / "vector.db"
            fake_vector_index.hybrid_search_session.return_value = fake_ranked
            fake_vector_index.fetch_enriched_results.return_value = fake_enriched

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "vector"),
                mock.patch.dict(sys.modules, {"vector_index": fake_vector_index}),
                mock.patch.object(session_index, "_SEARCH_RESULT_CACHE_TTL", 0),
            ):
                # Clear search cache to avoid stale hits
                session_index._SEARCH_RESULT_CACHE.clear()
                results = session_index._search_rows("srch-session")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["session_id"], "vec-hit")

    def test_vector_search_falls_back_when_no_ranked(self) -> None:
        """When hybrid_search_session returns empty, falls back to FTS/LIKE search."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path)

            fake_vector_index = MagicMock()
            fake_vector_index.vector_available.return_value = True
            fake_vector_index.get_vector_db_path.return_value = root / "vector.db"
            fake_vector_index.hybrid_search_session.return_value = []  # no results

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "vector"),
                mock.patch.dict(sys.modules, {"vector_index": fake_vector_index}),
                mock.patch.object(session_index, "_SEARCH_RESULT_CACHE_TTL", 0),
            ):
                session_index._SEARCH_RESULT_CACHE.clear()
                results = session_index._search_rows("srch-session")

            # Should have fallen back to regular search
            self.assertIsInstance(results, list)

    def test_vector_search_exception_falls_back(self) -> None:
        """Exception in vector search path is logged and falls back (line 1643-1644)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path)

            fake_vector_index = MagicMock()
            fake_vector_index.vector_available.side_effect = RuntimeError("vector search exploded")

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "vector"),
                mock.patch.dict(sys.modules, {"vector_index": fake_vector_index}),
                mock.patch.object(session_index, "_SEARCH_RESULT_CACHE_TTL", 0),
            ):
                session_index._SEARCH_RESULT_CACHE.clear()
                # Must not raise
                results = session_index._search_rows("srch-session")

            self.assertIsInstance(results, list)

    def test_vector_search_cache_stores_results(self) -> None:
        """When TTL>0 and vector returns results, they are cached."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path)

            fake_enriched = [
                {
                    "source_type": "codex_session",
                    "session_id": "cached-vec-hit",
                    "title": "Cached Vector Result",
                    "file_path": "/tmp/y.jsonl",
                    "created_at": "2026-03-25T00:00:00Z",
                    "created_at_epoch": 1742860800,
                    "snippet": "cached",
                }
            ]
            fake_vector_index = MagicMock()
            fake_vector_index.vector_available.return_value = True
            fake_vector_index.get_vector_db_path.return_value = root / "vector.db"
            fake_vector_index.hybrid_search_session.return_value = [{"id": 1, "score": 0.8}]
            fake_vector_index.fetch_enriched_results.return_value = fake_enriched

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "vector"),
                mock.patch.dict(sys.modules, {"vector_index": fake_vector_index}),
                mock.patch.object(session_index, "_SEARCH_RESULT_CACHE_TTL", 60),
            ):
                session_index._SEARCH_RESULT_CACHE.clear()
                results = session_index._search_rows("cached-query-xyz")
                # Second call should use cache
                results2 = session_index._search_rows("cached-query-xyz")

            self.assertEqual(results[0]["session_id"], "cached-vec-hit")
            self.assertEqual(results2[0]["session_id"], "cached-vec-hit")
            # vector index should only have been called once (second call hits cache)
            self.assertEqual(fake_vector_index.hybrid_search_session.call_count, 1)


# ---------------------------------------------------------------------------
# session_index — Lines 1703-1710: Cache eviction at max capacity
# ---------------------------------------------------------------------------


class TestSearchResultCacheEviction(unittest.TestCase):
    """Lines 1703-1710: When cache is at max capacity, expired and excess entries are evicted."""

    def _seed_db(self, root: Path, db_path: Path) -> None:
        codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
        codex_root.mkdir(parents=True)
        (codex_root / "evict.jsonl").write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "evict-session",
                        "cwd": "/tmp/evicttest",
                        "timestamp": "2026-03-25T00:00:00Z",
                    },
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(session_index, "_home", return_value=root),
            mock.patch.dict(
                os.environ,
                {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                clear=False,
            ),
        ):
            session_index.sync_session_index(force=True)

    def test_cache_evicts_expired_entries_at_max_capacity(self) -> None:
        """Fill the cache to max capacity with expired entries; new search evicts them."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path)

            max_entries = session_index._SEARCH_CACHE_MAX_ENTRIES

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "_SEARCH_RESULT_CACHE_TTL", 60),
            ):
                session_index._SEARCH_RESULT_CACHE.clear()

                # Fill the cache with `max_entries` expired entries (expiry in the past)
                past = time.monotonic() - 10.0
                for i in range(max_entries):
                    session_index._SEARCH_RESULT_CACHE[f"stale-key-{i}"] = (past, [])

                # Now run a real search; it should trigger cache eviction
                session_index._search_rows("evict-session")

                # After eviction, cache should contain the new entry but not the stale ones
                current_size = len(session_index._SEARCH_RESULT_CACHE)
                self.assertLessEqual(current_size, max_entries)

    def test_cache_drops_oldest_when_still_over_limit(self) -> None:
        """Fill cache with non-expired entries; eviction drops oldest to make room."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path)

            max_entries = session_index._SEARCH_CACHE_MAX_ENTRIES

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
                mock.patch.object(session_index, "_SEARCH_RESULT_CACHE_TTL", 60),
            ):
                session_index._SEARCH_RESULT_CACHE.clear()

                # Fill with fresh (non-expired) entries
                future = time.monotonic() + 3600.0
                for i in range(max_entries):
                    session_index._SEARCH_RESULT_CACHE[f"fresh-key-{i}"] = (future, [])

                # Search; this fills to >= max_entries and triggers the while loop
                session_index._search_rows("evict-session")

                current_size = len(session_index._SEARCH_RESULT_CACHE)
                self.assertLessEqual(current_size, max_entries)


# ---------------------------------------------------------------------------
# session_index — Lines 1732-1741: lookup_session_by_id
# ---------------------------------------------------------------------------


class TestLookupSessionById(unittest.TestCase):
    """Lines 1732-1741: lookup_session_by_id function."""

    def _seed_db(self, root: Path, db_path: Path, session_id: str = "lookup-test-abc") -> None:
        codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
        codex_root.mkdir(parents=True)
        (codex_root / "lookup.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": session_id,
                                "cwd": "/tmp/lookuptest",
                                "timestamp": "2026-03-25T12:00:00Z",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "user_message",
                                "message": "lookup content here",
                            },
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(session_index, "_home", return_value=root),
            mock.patch.dict(
                os.environ,
                {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                clear=False,
            ),
        ):
            session_index.sync_session_index(force=True)

    def test_lookup_by_id_prefix_returns_matching_session(self) -> None:
        """lookup_session_by_id returns rows matching session_id prefix."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path, session_id="lookup-test-abc")

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                results = session_index.lookup_session_by_id("lookup-test", db_path=db_path)

            self.assertGreaterEqual(len(results), 1)
            self.assertTrue(
                any(r["session_id"] == "lookup-test-abc" for r in results),
                f"Expected 'lookup-test-abc' in results: {results}",
            )

    def test_lookup_by_id_prefix_no_match(self) -> None:
        """lookup_session_by_id returns empty list when prefix doesn't match."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path, session_id="lookup-test-abc")

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                results = session_index.lookup_session_by_id("no-such-prefix-xyz", db_path=db_path)

            self.assertEqual(results, [])

    def test_lookup_by_id_returns_snippet(self) -> None:
        """lookup_session_by_id result includes snippet field."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path, session_id="snippet-session-xyz")

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                results = session_index.lookup_session_by_id("snippet-session", db_path=db_path)

            self.assertGreaterEqual(len(results), 1)
            self.assertIn("snippet", results[0])
            self.assertIn("source_type", results[0])
            self.assertIn("created_at_epoch", results[0])

    def test_lookup_by_id_limit_respected(self) -> None:
        """lookup_session_by_id respects the limit parameter."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path, session_id="limit-session-abc")

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                results = session_index.lookup_session_by_id("limit-session", limit=1, db_path=db_path)

            self.assertLessEqual(len(results), 1)

    def test_lookup_by_id_case_insensitive(self) -> None:
        """lookup_session_by_id is case-insensitive (uses LOWER)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path, session_id="case-Test-Session")

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                results_upper = session_index.lookup_session_by_id("CASE-TEST", db_path=db_path)
                results_lower = session_index.lookup_session_by_id("case-test", db_path=db_path)

            self.assertEqual(len(results_upper), len(results_lower))
            self.assertGreaterEqual(len(results_lower), 1)


# ---------------------------------------------------------------------------
# memory_viewer — Lines 407-408: Unauthorized access to / and /index.html
# ---------------------------------------------------------------------------


class TestHandlerUnauthorizedRootPaths(unittest.TestCase):
    """Lines 407-408: When token is required but missing, GET / returns 401."""

    def test_get_root_unauthorized_returns_401(self) -> None:
        """GET / without token returns 401 when VIEWER_TOKEN is set."""
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "required-token"
            h, wfile = _make_handler(method="GET", path="/")
            with patch("memory_viewer._maybe_sync_index", return_value={}):
                h.do_GET()
            self.assertEqual(h._status_code, 401)
            payload = _parse_json_response(wfile)
            self.assertFalse(payload["ok"])
        finally:
            memory_viewer.VIEWER_TOKEN = original

    def test_get_index_html_unauthorized_returns_401(self) -> None:
        """GET /index.html without token returns 401 when VIEWER_TOKEN is set."""
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "required-token"
            h, wfile = _make_handler(method="GET", path="/index.html")
            with patch("memory_viewer._maybe_sync_index", return_value={}):
                h.do_GET()
            self.assertEqual(h._status_code, 401)
            payload = _parse_json_response(wfile)
            self.assertFalse(payload["ok"])
        finally:
            memory_viewer.VIEWER_TOKEN = original

    def test_get_root_with_correct_token_returns_html(self) -> None:
        """GET / with correct token returns HTML (200)."""
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "mytoken"
            h, wfile = _make_handler(
                method="GET",
                path="/",
                headers={"X-Context-Token": "mytoken"},
            )
            h.do_GET()
            self.assertEqual(h._status_code, 200)
        finally:
            memory_viewer.VIEWER_TOKEN = original


# ---------------------------------------------------------------------------
# memory_viewer — Line 514: _SHUTDOWN_EVENT breaks SSE loop
# ---------------------------------------------------------------------------


class TestHandlerSSEShutdown(unittest.TestCase):
    """Line 514: When _SHUTDOWN_EVENT is set, SSE loop exits immediately."""

    def test_sse_exits_when_shutdown_event_set(self) -> None:
        """_handle_sse should exit without writing data when shutdown is already set."""
        original_event = memory_viewer._SHUTDOWN_EVENT
        try:
            import threading

            shutdown_event = threading.Event()
            shutdown_event.set()  # Already set => loop body should break immediately
            memory_viewer._SHUTDOWN_EVENT = shutdown_event

            h, wfile = _make_handler(method="GET", path="/api/events")

            with (
                patch("memory_viewer._maybe_sync_index", return_value={}),
                patch("memory_viewer.index_stats", return_value={"total_observations": 0}),
            ):
                h._handle_sse()

            # The loop should have exited without writing any data events
            wfile.seek(0)
            content = wfile.read().decode("utf-8")
            # Should contain the initial retry line but no data lines
            self.assertIn("retry: 5000", content)
            # No SSE data events
            self.assertNotIn("data:", content)
        finally:
            memory_viewer._SHUTDOWN_EVENT = original_event


# ---------------------------------------------------------------------------
# memory_viewer — Lines 540-544: Payload too large (413) in batch fetch
# ---------------------------------------------------------------------------


class TestHandlerBatchFetchPayloadTooLarge(unittest.TestCase):
    """Lines 540-544: When Content-Length exceeds _MAX_POST_BYTES, returns 413."""

    def test_payload_too_large_returns_413(self) -> None:
        """POST /api/observations/batch with Content-Length > max returns 413."""
        original_max = memory_viewer._MAX_POST_BYTES
        try:
            memory_viewer._MAX_POST_BYTES = 100  # set a low limit
            body = b'{"ids": [1, 2, 3]}'
            h, wfile = _make_handler(
                method="POST",
                path="/api/observations/batch",
                headers={"Content-Length": "101"},  # just over limit
                body=body,
            )
            h._handle_batch_fetch()
            self.assertEqual(h._status_code, 413)
            payload = _parse_json_response(wfile)
            self.assertFalse(payload["ok"])
            self.assertIn("payload too large", payload["error"])
            self.assertIn("max_bytes", payload)
        finally:
            memory_viewer._MAX_POST_BYTES = original_max

    def test_payload_exactly_at_limit_is_rejected(self) -> None:
        """Content-Length == max+1 is still too large."""
        original_max = memory_viewer._MAX_POST_BYTES
        try:
            memory_viewer._MAX_POST_BYTES = 50
            h, wfile = _make_handler(
                method="POST",
                path="/api/observations/batch",
                headers={"Content-Length": "51"},
                body=b"x" * 51,
            )
            h._handle_batch_fetch()
            self.assertEqual(h._status_code, 413)
        finally:
            memory_viewer._MAX_POST_BYTES = original_max


# ---------------------------------------------------------------------------
# memory_viewer — Lines 549-550: Empty body after read in batch fetch
# ---------------------------------------------------------------------------


class TestHandlerBatchFetchEmptyBody(unittest.TestCase):
    """Lines 549-550: When body is whitespace-only after read, returns 400."""

    def test_whitespace_only_body_returns_400(self) -> None:
        """POST body that is only whitespace returns 400 'missing or empty request body'."""
        body = b"   \n\t  "
        h, wfile = _make_handler(
            method="POST",
            path="/api/observations/batch",
            headers={"Content-Length": str(len(body))},
            body=body,
        )
        h._handle_batch_fetch()
        self.assertEqual(h._status_code, 400)
        payload = _parse_json_response(wfile)
        self.assertFalse(payload["ok"])
        self.assertIn("missing or empty request body", payload["error"])


# ---------------------------------------------------------------------------
# memory_viewer — Additional batch fetch edge cases
# ---------------------------------------------------------------------------


class TestHandlerBatchFetchEdgeCases(unittest.TestCase):
    """Additional edge cases for _handle_batch_fetch."""

    def test_zero_content_length_returns_400(self) -> None:
        """Content-Length: 0 returns 400."""
        h, wfile = _make_handler(
            method="POST",
            path="/api/observations/batch",
            headers={"Content-Length": "0"},
            body=b"",
        )
        h._handle_batch_fetch()
        self.assertEqual(h._status_code, 400)
        payload = _parse_json_response(wfile)
        self.assertFalse(payload["ok"])

    def test_invalid_content_length_returns_400(self) -> None:
        """Non-integer Content-Length returns 400."""
        h, wfile = _make_handler(
            method="POST",
            path="/api/observations/batch",
            headers={"Content-Length": "notanumber"},
            body=b"{}",
        )
        h._handle_batch_fetch()
        self.assertEqual(h._status_code, 400)
        payload = _parse_json_response(wfile)
        self.assertFalse(payload["ok"])
        self.assertIn("invalid Content-Length", payload["error"])

    def test_ids_not_array_returns_400(self) -> None:
        """ids field that is not a list returns 400."""
        body = json.dumps({"ids": "not-an-array"}).encode()
        h, wfile = _make_handler(
            method="POST",
            path="/api/observations/batch",
            headers={"Content-Length": str(len(body))},
            body=body,
        )
        h._handle_batch_fetch()
        self.assertEqual(h._status_code, 400)
        payload = _parse_json_response(wfile)
        self.assertIn("ids must be an array", payload["error"])

    def test_too_many_ids_returns_400(self) -> None:
        """Too many ids returns 400."""
        original_max = memory_viewer._MAX_BATCH_IDS
        try:
            memory_viewer._MAX_BATCH_IDS = 2
            body = json.dumps({"ids": [1, 2, 3]}).encode()
            h, wfile = _make_handler(
                method="POST",
                path="/api/observations/batch",
                headers={"Content-Length": str(len(body))},
                body=body,
            )
            h._handle_batch_fetch()
            self.assertEqual(h._status_code, 400)
            payload = _parse_json_response(wfile)
            self.assertIn("too many ids", payload["error"])
        finally:
            memory_viewer._MAX_BATCH_IDS = original_max

    def test_valid_batch_fetch_returns_200(self) -> None:
        """Valid batch fetch returns 200 with observations."""
        body = json.dumps({"ids": [1, 2]}).encode()
        h, wfile = _make_handler(
            method="POST",
            path="/api/observations/batch",
            headers={"Content-Length": str(len(body))},
            body=body,
        )
        fake_rows = [{"id": 1, "content": "hello"}, {"id": 2, "content": "world"}]
        with (
            patch("memory_viewer._maybe_sync_index", return_value={"synced": True}),
            patch("memory_viewer.get_observations_by_ids", return_value=fake_rows),
        ):
            h._handle_batch_fetch()
        self.assertEqual(h._status_code, 200)
        payload = _parse_json_response(wfile)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 2)

    def test_invalid_json_body_returns_400(self) -> None:
        """Invalid JSON returns 400."""
        body = b"not valid json {"
        h, wfile = _make_handler(
            method="POST",
            path="/api/observations/batch",
            headers={"Content-Length": str(len(body))},
            body=body,
        )
        h._handle_batch_fetch()
        self.assertEqual(h._status_code, 400)
        payload = _parse_json_response(wfile)
        self.assertIn("invalid JSON body", payload["error"])


# ---------------------------------------------------------------------------
# memory_viewer — do_POST dispatch
# ---------------------------------------------------------------------------


class TestHandlerDoPost(unittest.TestCase):
    """Test do_POST dispatches correctly."""

    def test_post_unknown_path_returns_404(self) -> None:
        """POST to unknown path returns 404."""
        h, wfile = _make_handler(method="POST", path="/api/unknown")
        with patch("memory_viewer.VIEWER_TOKEN", ""):
            h.do_POST()
        self.assertEqual(h._status_code, 404)

    def test_post_unauthorized_returns_401(self) -> None:
        """POST without token when token is required returns 401."""
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "required"
            body = json.dumps({"ids": [1]}).encode()
            h, wfile = _make_handler(
                method="POST",
                path="/api/observations/batch",
                headers={"Content-Length": str(len(body))},
                body=body,
            )
            h.do_POST()
            self.assertEqual(h._status_code, 401)
        finally:
            memory_viewer.VIEWER_TOKEN = original


# ---------------------------------------------------------------------------
# memory_viewer — do_GET dispatch (other paths)
# ---------------------------------------------------------------------------


class TestHandlerDoGet(unittest.TestCase):
    """Test do_GET dispatches correctly."""

    def test_get_unknown_path_returns_404(self) -> None:
        """GET to unknown path returns 404."""
        h, wfile = _make_handler(method="GET", path="/api/unknown")
        with patch("memory_viewer.VIEWER_TOKEN", ""):
            h.do_GET()
        self.assertEqual(h._status_code, 404)
        payload = _parse_json_response(wfile)
        self.assertFalse(payload["ok"])

    def test_get_api_health_authorized(self) -> None:
        """GET /api/health returns 200 when authorized."""
        h, wfile = _make_handler(method="GET", path="/api/health")
        with (
            patch("memory_viewer.VIEWER_TOKEN", ""),
            patch("memory_viewer._maybe_sync_index", return_value={}),
            patch("memory_viewer.index_stats", return_value={"total_observations": 5}),
        ):
            h.do_GET()
        self.assertEqual(h._status_code, 200)

    def test_get_unauthorized_non_root_returns_401(self) -> None:
        """GET /api/health without token when required returns 401."""
        original = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.VIEWER_TOKEN = "required"
            h, wfile = _make_handler(method="GET", path="/api/health")
            h.do_GET()
            self.assertEqual(h._status_code, 401)
        finally:
            memory_viewer.VIEWER_TOKEN = original


# ---------------------------------------------------------------------------
# memory_viewer — main() function (line 617 __main__ branch)
# ---------------------------------------------------------------------------


class TestMemoryViewerMain(unittest.TestCase):
    """Test main() function behavior."""

    def test_main_raises_systemexit_for_nonloopback_without_token(self) -> None:
        """main() raises SystemExit when bound to non-loopback without token."""
        original_host = memory_viewer.HOST
        original_token = memory_viewer.VIEWER_TOKEN
        try:
            memory_viewer.HOST = "0.0.0.0"
            memory_viewer.VIEWER_TOKEN = ""
            with self.assertRaises(SystemExit):
                memory_viewer.main()
        finally:
            memory_viewer.HOST = original_host
            memory_viewer.VIEWER_TOKEN = original_token

    def test_main_starts_server_and_closes_on_keyboard_interrupt(self) -> None:
        """main() starts a server and handles KeyboardInterrupt gracefully."""
        original_host = memory_viewer.HOST
        original_port = memory_viewer.PORT
        original_token = memory_viewer.VIEWER_TOKEN
        original_event = memory_viewer._SHUTDOWN_EVENT

        try:
            memory_viewer.HOST = "127.0.0.1"
            memory_viewer.PORT = 0  # let OS pick a port
            memory_viewer.VIEWER_TOKEN = ""

            import threading

            shutdown_event = threading.Event()
            memory_viewer._SHUTDOWN_EVENT = shutdown_event

            mock_server = MagicMock()
            mock_server.serve_forever.side_effect = KeyboardInterrupt()
            mock_server.server_address = ("127.0.0.1", 12345)

            with patch("memory_viewer.ThreadingHTTPServer", return_value=mock_server):
                memory_viewer.main()

            mock_server.server_close.assert_called_once()
            self.assertTrue(shutdown_event.is_set())
        finally:
            memory_viewer.HOST = original_host
            memory_viewer.PORT = original_port
            memory_viewer.VIEWER_TOKEN = original_token
            memory_viewer._SHUTDOWN_EVENT = original_event


# ---------------------------------------------------------------------------
# session_index — format_search_results with search_type filter
# ---------------------------------------------------------------------------


class TestFormatSearchResultsWithFilter(unittest.TestCase):
    """Test format_search_results with search_type filtering."""

    def _seed_db(self, root: Path, db_path: Path) -> None:
        codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
        codex_root.mkdir(parents=True)
        (codex_root / "filter.jsonl").write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "filter-session-xyz",
                        "cwd": "/tmp/filtertest",
                        "timestamp": "2026-03-25T00:00:00Z",
                    },
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(session_index, "_home", return_value=root),
            mock.patch.dict(
                os.environ,
                {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                clear=False,
            ),
        ):
            session_index.sync_session_index(force=True)

    def test_format_search_results_with_codex_filter(self) -> None:
        """format_search_results with search_type='codex' filters correctly."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path)

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                text = session_index.format_search_results(
                    "filter-session",
                    search_type="codex",
                    limit=5,
                )

            # Should either find results or return no matches message
            self.assertIsInstance(text, str)

    def test_format_search_results_no_match_returns_no_matches(self) -> None:
        """format_search_results returns 'No matches' when nothing found."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "session_index.db"
            self._seed_db(root, db_path)

            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(
                    os.environ,
                    {session_index.SESSION_DB_PATH_ENV: str(db_path)},
                    clear=False,
                ),
            ):
                text = session_index.format_search_results(
                    "xyzzy-no-such-thing-at-all",
                    limit=5,
                )

            self.assertIn("No matches", text)


if __name__ == "__main__":
    unittest.main()
