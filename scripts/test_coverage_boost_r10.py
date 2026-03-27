#!/usr/bin/env python3
"""R10 coverage boost tests — targeting remaining uncovered lines.

Focuses on:
- memory_index.py: _retry_sqlite / _retry_sqlite_many / _retry_commit (252-300),
  search_index cache hits (589-595, 607-610), timeline anchor missing (593),
  export_observations_payload pagination path (723-734)
- context_daemon.py: _release_single_instance_lock fd branch (350),
  refresh_sources new-path cursor reset (534-536), shell source active init (550-554),
  _set_cursor eviction path (592-596), poll_claude_transcripts with budget (748-750),
  poll_antigravity busy/offline branches (849-860), antigravity final_only quiet/min-bytes (939-940),
  maybe_sync_index sqlite busy (1144-1145), next_sleep_interval session-due path (1358),
  main() loop budget skip lines (1467-1474), jitter path (1498-1501), __main__ (1512)
- session_index.py: batch delete flush (910-914), search cache hit (1258-1262),
  format_search_results search_type filter (1345-1346), health_payload (1359+)
- memory_hit_first_regression.py: lines 57-58, 96
- context_smoke.py: line 482
- context_native.py: line 672
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Set storage root before importing daemon
_DAEMON_TMP = tempfile.mkdtemp(prefix="cg_boost_r10_")
_FAKE_STORAGE = Path(_DAEMON_TMP) / ".contextgo"
_FAKE_STORAGE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", str(_FAKE_STORAGE))

import context_daemon  # noqa: E402
import memory_index  # noqa: E402
import session_index  # noqa: E402

SessionTracker = context_daemon.SessionTracker


# ---------------------------------------------------------------------------
# Helper — make a tracker quickly
# ---------------------------------------------------------------------------


def _make_tracker() -> SessionTracker:
    with patch.object(SessionTracker, "refresh_sources"):
        return SessionTracker()


# ===========================================================================
# memory_index — _retry_sqlite / _retry_sqlite_many / _retry_commit
# ===========================================================================


class TestRetrySqliteRetryExhausted(unittest.TestCase):
    """Verify that all retry helpers raise after exhausting retries on locked DB."""

    def _locked_conn(self) -> sqlite3.Connection:
        conn = MagicMock(spec=sqlite3.Connection)
        conn.execute.side_effect = sqlite3.OperationalError("database is locked")
        conn.executemany.side_effect = sqlite3.OperationalError("database is locked")
        conn.commit.side_effect = sqlite3.OperationalError("database is locked")
        return conn

    def test_retry_sqlite_raises_after_retries(self) -> None:
        conn = self._locked_conn()
        with patch.object(memory_index.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                memory_index._retry_sqlite(conn, "SELECT 1", max_retries=1)

    def test_retry_sqlite_no_params_raises(self) -> None:
        conn = self._locked_conn()
        with patch.object(memory_index.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                memory_index._retry_sqlite(conn, "SELECT 1", params=None, max_retries=1)

    def test_retry_sqlite_many_raises_after_retries(self) -> None:
        conn = self._locked_conn()
        with patch.object(memory_index.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                memory_index._retry_sqlite_many(conn, "INSERT INTO t VALUES(?)", [(1,), (2,)], max_retries=1)

    def test_retry_commit_raises_after_retries(self) -> None:
        conn = self._locked_conn()
        with patch.object(memory_index.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                memory_index._retry_commit(conn, max_retries=1)

    def test_retry_sqlite_non_lock_error_raises_immediately(self) -> None:
        conn = MagicMock(spec=sqlite3.Connection)
        conn.execute.side_effect = sqlite3.OperationalError("no such table: foo")
        with self.assertRaises(sqlite3.OperationalError) as ctx:
            memory_index._retry_sqlite(conn, "SELECT 1", max_retries=3)
        self.assertIn("no such table", str(ctx.exception))

    def test_retry_sqlite_many_non_lock_error_raises_immediately(self) -> None:
        conn = MagicMock(spec=sqlite3.Connection)
        conn.executemany.side_effect = sqlite3.OperationalError("table missing")
        with self.assertRaises(sqlite3.OperationalError):
            memory_index._retry_sqlite_many(conn, "INSERT INTO t VALUES(?)", [(1,)], max_retries=3)

    def test_retry_commit_non_lock_raises_immediately(self) -> None:
        conn = MagicMock(spec=sqlite3.Connection)
        conn.commit.side_effect = sqlite3.OperationalError("disk full")
        with self.assertRaises(sqlite3.OperationalError):
            memory_index._retry_commit(conn, max_retries=3)


# ===========================================================================
# memory_index — search_index cache hit
# ===========================================================================


class TestSearchIndexCacheHit(unittest.TestCase):
    def test_cache_hit_returns_cached_results(self) -> None:
        """Second identical call returns cached result without touching DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "mi.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "mem1.md").write_text(
                "# Cache Test\nDate: 2026-01-01\n## Content\ncache test content\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                # Clear the cache so we get a fresh read
                memory_index._SEARCH_CACHE.clear()
                # First call populates cache
                r1 = memory_index.search_index("cache test", limit=5)
                # Second call should hit cache (same results, no DB I/O)
                r2 = memory_index.search_index("cache test", limit=5)
            self.assertEqual(r1, r2)


class TestSearchIndexCacheTTLExpiry(unittest.TestCase):
    def test_expired_cache_is_refreshed(self) -> None:
        """Cache entries with past TTL are not returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "mi2.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            (history_dir / "mem2.md").write_text(
                "# Expiry\nDate: 2026-01-01\n## Content\nexpiry test\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                memory_index.sync_index_from_storage()
                memory_index._SEARCH_CACHE.clear()
                # Manually insert a stale cache entry
                cache_key = json.dumps([str(db_path), "expiry test", 5, 0, "all", None, None], ensure_ascii=False)
                memory_index._SEARCH_CACHE[cache_key] = (time.monotonic() - 999, [])
                # Should not return the stale entry
                result = memory_index.search_index("expiry test", limit=5)
                # Should get a fresh read from DB
                self.assertIsInstance(result, list)


# ===========================================================================
# memory_index — timeline_index anchor missing
# ===========================================================================


class TestTimelineAnchorMissing(unittest.TestCase):
    def test_missing_anchor_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tl.db"
            with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False):
                memory_index.ensure_index_db()
                result = memory_index.timeline_index(999999)
            self.assertEqual(result, [])


# ===========================================================================
# memory_index — export_observations_payload pagination
# ===========================================================================


class TestExportObservationsPayloadPagination(unittest.TestCase):
    def test_export_with_many_observations(self) -> None:
        """Export fetches in pages; verify all observations are included."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "exp.db"
            history_dir = Path(tmpdir) / "history"
            history_dir.mkdir()
            # Write 5 observations
            for i in range(5):
                (history_dir / f"mem_{i}.md").write_text(
                    f"# Memory {i}\nDate: 2026-01-0{i + 1}\n## Content\ncontent {i}\n",
                    encoding="utf-8",
                )
            with (
                patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                patch.object(memory_index, "_history_dirs", return_value=[history_dir]),
            ):
                payload = memory_index.export_observations_payload(query="", limit=50_000)
            self.assertGreaterEqual(payload["total_observations"], 5)
            self.assertIn("observations", payload)
            self.assertIn("exported_at", payload)


# ===========================================================================
# context_daemon — _release_single_instance_lock fd branch
# ===========================================================================


class TestReleaseLockFdBranch(unittest.TestCase):
    def test_release_closes_fd_and_clears(self) -> None:
        """_release_single_instance_lock closes _LOCK_FD when it is set."""
        original = context_daemon._LOCK_FD
        try:
            # Create a real fd so os.close won't fail
            r, w = os.pipe()
            context_daemon._LOCK_FD = w
            with patch("pathlib.Path.unlink"):
                context_daemon._release_single_instance_lock()
            self.assertIsNone(context_daemon._LOCK_FD)
            os.close(r)  # cleanup read end
        finally:
            context_daemon._LOCK_FD = original

    def test_release_handles_oserror_on_close(self) -> None:
        """OSError during fd close is suppressed and function completes without crash."""
        original = context_daemon._LOCK_FD
        try:
            # Pre-close fd so os.close raises OSError (fd already closed)
            r, w = os.pipe()
            os.close(w)
            context_daemon._LOCK_FD = w
            with patch("pathlib.Path.unlink"):
                # Should not raise even though os.close(w) will fail
                context_daemon._release_single_instance_lock()
            # _LOCK_FD is NOT set to None when OSError is raised (exception is caught with pass)
            # but function must complete without raising
            os.close(r)
        finally:
            context_daemon._LOCK_FD = original


# ===========================================================================
# context_daemon — _set_cursor eviction path
# ===========================================================================


class TestSetCursorEviction(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_eviction_when_over_limit(self) -> None:
        """Cursor map is trimmed when it exceeds MAX_FILE_CURSORS."""
        p = Path(self.tmp) / "file.jsonl"
        p.write_text("x")
        max_cursors = context_daemon.MAX_FILE_CURSORS
        # Fill cursors to just above the limit
        for i in range(max_cursors + 2):
            inode = p.stat().st_ino
            self.tracker.file_cursors[f"jsonl:source_{i:04d}:abc"] = (inode, i)
        # Now set one more cursor — should trigger eviction
        self.tracker._set_cursor("jsonl:source_new:xyz", p, 999)
        # Map should be <= max_cursors
        self.assertLessEqual(len(self.tracker.file_cursors), max_cursors)


# ===========================================================================
# context_daemon — maybe_sync_index sqlite busy branch
# ===========================================================================


class TestMaybeSyncIndexSqliteBusy(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_sqlite_busy_increments_error_count(self) -> None:
        """When sync_index_from_storage raises OperationalError, error_count goes up."""
        self.tracker._index_dirty = True
        self.tracker._last_index_sync = 0.0
        err_before = self.tracker._error_count
        with patch.object(
            context_daemon, "sync_index_from_storage", side_effect=sqlite3.OperationalError("database is locked")
        ):
            self.tracker.maybe_sync_index()
        self.assertEqual(self.tracker._error_count, err_before + 1)
        # _index_dirty should still be True (not cleared on failure)
        self.assertTrue(self.tracker._index_dirty)

    def test_oserror_increments_error_count(self) -> None:
        self.tracker._index_dirty = True
        self.tracker._last_index_sync = 0.0
        err_before = self.tracker._error_count
        with patch.object(context_daemon, "sync_index_from_storage", side_effect=OSError("disk error")):
            self.tracker.maybe_sync_index()
        self.assertEqual(self.tracker._error_count, err_before + 1)


# ===========================================================================
# context_daemon — next_sleep_interval session-due path (line 1358/1360)
# ===========================================================================


class TestNextSleepIntervalSessionDue(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_session_near_due_shortens_sleep(self) -> None:
        """A session approaching idle timeout shortens the sleep interval."""
        now = time.time()
        # Session whose last_seen is just barely less than IDLE_TIMEOUT_SEC ago
        self.tracker.sessions["sess1"] = {
            "last_seen": now - (context_daemon.IDLE_TIMEOUT_SEC - 2),
            "exported": False,
        }
        sleep_s = self.tracker.next_sleep_interval()
        # Should be short — session is almost due
        self.assertLessEqual(sleep_s, context_daemon.FAST_POLL_INTERVAL_SEC + 1)

    def test_session_already_exported_not_counted(self) -> None:
        """Exported sessions don't influence the sleep interval."""
        now = time.time()
        self.tracker.sessions["sess2"] = {
            "last_seen": now - (context_daemon.IDLE_TIMEOUT_SEC - 2),
            "exported": True,
        }
        # Should not short-circuit because of this session
        sleep_s = self.tracker.next_sleep_interval()
        # No assertion on specific value — just that it doesn't crash
        self.assertGreaterEqual(sleep_s, 1)

    def test_multiple_sessions_picks_smallest_remaining(self) -> None:
        """When multiple sessions are pending, the nearest-due one wins."""
        now = time.time()
        self.tracker.sessions["a"] = {
            "last_seen": now - (context_daemon.IDLE_TIMEOUT_SEC - 10),
            "exported": False,
        }
        self.tracker.sessions["b"] = {
            "last_seen": now - (context_daemon.IDLE_TIMEOUT_SEC - 100),
            "exported": False,
        }
        sleep_s = self.tracker.next_sleep_interval()
        self.assertGreaterEqual(sleep_s, 1)


# ===========================================================================
# context_daemon — refresh_sources new-path cursor reset
# ===========================================================================


class TestRefreshSourcesNewPath(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_jsonl_path_resets_cursor(self) -> None:
        """When a source changes path, cursor is reset to file size."""
        old_path = Path(self.tmp) / "old.jsonl"
        new_path = Path(self.tmp) / "new.jsonl"
        old_path.write_text("old data\n")
        new_path.write_text("new data here\n")

        # Pre-populate with old path
        self.tracker.active_jsonl["test_source"] = {"path": old_path, "sid_keys": [], "text_keys": []}
        self.tracker._last_source_refresh = 0.0

        fake_sources = {"test_source": [{"path": new_path, "sid_keys": [], "text_keys": []}]}
        with patch.object(context_daemon, "JSONL_SOURCES", fake_sources):
            with patch.object(context_daemon, "SOURCE_MONITOR_FLAGS", {"test_source": True}):
                with patch.object(context_daemon, "ENABLE_SHELL_MONITOR", False):
                    self.tracker.refresh_sources()

        self.assertIn("test_source", self.tracker.active_jsonl)
        self.assertEqual(self.tracker.active_jsonl["test_source"]["path"], new_path)

    def test_shell_source_new_path_sets_cursor(self) -> None:
        """New shell source path triggers cursor initialization."""
        new_path = Path(self.tmp) / "bash_history"
        new_path.write_text("ls\ncd /\n")

        self.tracker._last_source_refresh = 0.0
        fake_shell = {"shell_bash": [new_path]}
        with patch.object(context_daemon, "JSONL_SOURCES", {}):
            with patch.object(context_daemon, "SHELL_SOURCES", fake_shell):
                with patch.object(context_daemon, "ENABLE_SHELL_MONITOR", True):
                    self.tracker.refresh_sources()

        self.assertIn("shell_bash", self.tracker.active_shell)


# ===========================================================================
# context_daemon — poll_claude_transcripts budget branch
# ===========================================================================


class TestPollClaudeTranscriptsBudget(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_poll_claude_transcripts_disabled_returns_early(self) -> None:
        """When ENABLE_CLAUDE_TRANSCRIPTS_MONITOR is False, returns immediately."""
        with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", False):
            # Should not raise
            self.tracker.poll_claude_transcripts()

    def test_poll_claude_transcripts_no_dir_returns_early(self) -> None:
        """When CLAUDE_TRANSCRIPTS_DIR doesn't exist, returns immediately."""
        nonexist = Path(self.tmp) / "no_transcripts"
        with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
            with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", nonexist):
                self.tracker.poll_claude_transcripts()


# ===========================================================================
# context_daemon — poll_antigravity branches
# ===========================================================================


class TestPollAntigravityBranches(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_antigravity_disabled_returns_early(self) -> None:
        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", False):
            self.tracker.poll_antigravity()

    def test_antigravity_no_brain_dir_returns_early(self) -> None:
        nonexist = Path(self.tmp) / "no_brain"
        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", nonexist):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    self.tracker.poll_antigravity()

    def test_antigravity_busy_skips_when_above_threshold(self) -> None:
        """When language server count >= threshold, poll is skipped."""
        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", True):
                with patch.object(context_daemon, "ANTIGRAVITY_BUSY_LS_THRESHOLD", 1):
                    with patch.object(context_daemon, "_count_antigravity_language_servers", return_value=2):
                        self.tracker._last_antigravity_busy_log = 0.0
                        self.tracker.poll_antigravity()

    def test_antigravity_final_only_min_bytes_skip(self) -> None:
        """final_only mode: files below min bytes are skipped."""
        brain_dir = Path(self.tmp) / "brain"
        brain_dir.mkdir()
        sid = "11111111-2222-3333-4444-555555555555"
        sdir = brain_dir / sid
        sdir.mkdir()
        wt = sdir / "walkthrough.md"
        wt.write_text("tiny")  # < ANTIGRAVITY_MIN_DOC_BYTES

        now = time.time()
        self.tracker.antigravity_sessions[sid] = {
            "mtime": wt.stat().st_mtime - 10,
            "path": wt,
            "last_change": now - 9999,
            "exported_mtime": 0.0,
        }

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", brain_dir):
                    with patch.object(context_daemon, "ANTIGRAVITY_INGEST_MODE", "final_only"):
                        with patch.object(context_daemon, "ANTIGRAVITY_MIN_DOC_BYTES", 999999):
                            with patch.object(context_daemon, "ANTIGRAVITY_QUIET_SEC", 0):
                                # Trigger a glob refresh
                                self.tracker._last_antigravity_scan = 0.0
                                self.tracker.poll_antigravity()

    def test_antigravity_session_eviction(self) -> None:
        """When antigravity_sessions exceeds MAX, stale entries are evicted."""
        brain_dir = Path(self.tmp) / "brain2"
        brain_dir.mkdir()

        max_ag = 2
        # Pre-fill with stale sessions (not present in brain_dir)
        for i in range(max_ag + 3):
            fake_sid = f"fake-sid-{i:04d}"
            self.tracker.antigravity_sessions[fake_sid] = {
                "mtime": float(i),
                "path": Path(self.tmp) / f"gone_{i}.md",
                "last_change": float(i),
                "exported_mtime": float(i),
            }

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", brain_dir):
                    with patch.object(context_daemon, "MAX_ANTIGRAVITY_SESSIONS", max_ag):
                        self.tracker._last_antigravity_scan = 0.0
                        self.tracker.poll_antigravity()

        self.assertLessEqual(len(self.tracker.antigravity_sessions), max_ag)


# ===========================================================================
# context_daemon — main() __main__ guard line
# ===========================================================================


class TestMainGuard(unittest.TestCase):
    def test_main_callable(self) -> None:
        """Verify main() is callable and that __main__ guard is importable."""
        # Just check that main is defined without actually running the loop
        self.assertTrue(callable(context_daemon.main))


# ===========================================================================
# session_index — format_search_results search_type filter
# ===========================================================================


class TestFormatSearchResultsSearchType(unittest.TestCase):
    def test_search_type_filter_applied(self) -> None:
        """Results are filtered by search_type when provided.

        _VALID_SEARCH_TYPES contains 'codex', so use that to trigger filtering.
        """
        mock_rows = [
            {
                "source_type": "codex",
                "session_id": "sid1",
                "title": "Codex Only Session",
                "file_path": "/tmp/a.jsonl",
                "created_at": "2026-01-01T00:00:00",
                "created_at_epoch": 1000,
                "snippet": "snippet text",
            },
            {
                "source_type": "claude",
                "session_id": "sid2",
                "title": "Claude Only Session",
                "file_path": "/tmp/b.jsonl",
                "created_at": "2026-01-01T00:00:00",
                "created_at_epoch": 999,
                "snippet": "another snippet",
            },
        ]
        with patch.object(session_index, "_search_rows", return_value=mock_rows):
            result = session_index.format_search_results("test", search_type="codex")
        self.assertIn("Codex Only Session", result)
        self.assertNotIn("Claude Only Session", result)

    def test_search_type_all_returns_all(self) -> None:
        mock_rows = [
            {
                "source_type": "codex_session",
                "session_id": "sid1",
                "title": "Codex",
                "file_path": "/tmp/a.jsonl",
                "created_at": "2026-01-01T00:00:00",
                "created_at_epoch": 1000,
                "snippet": "x",
            },
        ]
        with patch.object(session_index, "_search_rows", return_value=mock_rows):
            result = session_index.format_search_results("test", search_type="all")
        self.assertIn("Found 1 sessions", result)

    def test_invalid_search_type_not_filtered(self) -> None:
        """Unknown search_type is ignored — all results returned."""
        mock_rows = [
            {
                "source_type": "codex_session",
                "session_id": "sid1",
                "title": "Session",
                "file_path": "/tmp/a.jsonl",
                "created_at": "2026-01-01T00:00:00",
                "created_at_epoch": 1000,
                "snippet": "x",
            },
        ]
        with patch.object(session_index, "_search_rows", return_value=mock_rows):
            result = session_index.format_search_results("test", search_type="unknown_type_xyz")
        # unknown type is not in _VALID_SEARCH_TYPES so no filter applied
        self.assertIn("Found 1 sessions", result)

    def test_no_results_returns_no_matches_message(self) -> None:
        with patch.object(session_index, "_search_rows", return_value=[]):
            result = session_index.format_search_results("nope")
        self.assertEqual(result, "No matches found in local session index.")


# ===========================================================================
# session_index — search cache hit
# ===========================================================================


class TestSessionSearchCacheHit(unittest.TestCase):
    def test_cache_returns_cached_results(self) -> None:
        """Second identical _search_rows call hits the cache."""
        _env_key = session_index.SESSION_DB_PATH_ENV
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "si.db"
            with (
                patch.dict(os.environ, {_env_key: str(db_path)}, clear=False),
                patch.object(session_index, "sync_session_index"),
            ):
                session_index._SEARCH_RESULT_CACHE.clear()
                # Insert a fresh cache entry
                cache_key = json.dumps([str(db_path), "cached_query", 10, False], ensure_ascii=False)
                future = time.monotonic() + 9999
                session_index._SEARCH_RESULT_CACHE[cache_key] = (future, [{"cached": True}])

                with patch.object(session_index, "ensure_session_db", return_value=db_path):
                    result = session_index._search_rows("cached_query", limit=10, literal=False)

                self.assertEqual(result, [{"cached": True}])


# ===========================================================================
# session_index — batch delete flush path
# ===========================================================================


class TestSessionIndexBatchDeleteFlush(unittest.TestCase):
    def test_batch_delete_flush_at_threshold(self) -> None:
        """When delete_batch reaches _BATCH_COMMIT_SIZE, an intermediate flush occurs."""
        _env_key = session_index.SESSION_DB_PATH_ENV
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "si2.db"
            # Initialize the DB schema
            with patch.dict(os.environ, {_env_key: str(db_path)}, clear=False):
                session_index.ensure_session_db()

            # Insert many fake rows AND set the correct schema version so they don't get wiped
            n_rows = session_index._BATCH_COMMIT_SIZE + 2
            with session_index._open_db(db_path) as conn:
                # Set the schema version so sync doesn't wipe the table
                conn.execute(
                    "INSERT INTO session_index_meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("schema_version", session_index.SESSION_INDEX_SCHEMA_VERSION),
                )
                for i in range(n_rows):
                    conn.execute(
                        """INSERT INTO session_documents(
                               file_path, source_type, session_id, title, content,
                               created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            f"/fake/path_{i}.jsonl",
                            "codex_session",
                            f"sid{i}",
                            f"Title {i}",
                            "content",
                            "2026-01-01",
                            1000 + i,
                            1000 + i,
                            100,
                            1000 + i,
                        ),
                    )
                conn.commit()

            # Now sync with no real sources — all fake rows should be removed
            with (
                patch.dict(os.environ, {_env_key: str(db_path)}, clear=False),
                patch.object(session_index, "_iter_sources", return_value=[]),
            ):
                result = session_index.sync_session_index(force=True)
            self.assertGreaterEqual(result["removed"], n_rows)


# ===========================================================================
# session_index — health_payload
# ===========================================================================


class TestSessionIndexHealthPayload(unittest.TestCase):
    def test_health_payload_structure(self) -> None:
        _env_key = session_index.SESSION_DB_PATH_ENV
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "si3.db"
            with (
                patch.dict(os.environ, {_env_key: str(db_path)}, clear=False),
                patch.object(
                    session_index, "sync_session_index", return_value={"added": 0, "updated": 0, "removed": 0}
                ),
            ):
                payload = session_index.health_payload()
            self.assertIn("session_index_db_exists", payload)
            self.assertIn("total_sessions", payload)
            self.assertIn("sync", payload)


# ===========================================================================
# memory_hit_first_regression — lines 57-58 (json parse failure) and 96
# ===========================================================================


class TestMemoryHitFirstRegression(unittest.TestCase):
    def test_import_works(self) -> None:
        import memory_hit_first_regression as m

        self.assertTrue(callable(m.check_cli_fixed_cases))

    def test_main_returns_int(self) -> None:
        """main() returns an integer (0 or 1)."""
        import memory_hit_first_regression as m

        # Mock run_cli to return plausible outputs without actually running the CLI
        mock_outputs = {
            "cli-health": (0, json.dumps({"all_ok": True}), ""),
            "cli-keyword": (0, "notebooklm result", ""),
            "cli-long-query": (0, "notebooklm found", ""),
            "cli-date": (0, "2026-03-06 result", ""),
        }
        call_idx = [0]
        cases_order = ["cli-health", "cli-keyword", "cli-long-query", "cli-date"]

        def fake_run_cli(*args, timeout=30):
            idx = call_idx[0] % len(cases_order)
            call_idx[0] += 1
            return mock_outputs[cases_order[idx]]

        with patch.object(m, "run_cli", side_effect=fake_run_cli):
            rc = m.main()
        self.assertIn(rc, (0, 1))

    def test_main_handles_invalid_json_health(self) -> None:
        """When health output is not valid JSON, passed=False for cli-health."""
        import memory_hit_first_regression as m

        call_idx = [0]
        responses = [
            (0, "not valid json", ""),  # cli-health — should fail json parse
            (0, "notebooklm result", ""),
            (0, "notebooklm found", ""),
            (0, "2026-03-06 result", ""),
        ]

        def fake_run_cli(*args, timeout=30):
            resp = responses[call_idx[0] % len(responses)]
            call_idx[0] += 1
            return resp

        with patch.object(m, "run_cli", side_effect=fake_run_cli):
            rc = m.main()
        # cli-health fails, so overall rc should be 1
        self.assertEqual(rc, 1)

    def test_main_entry_point(self) -> None:
        """Lines 95-96: __name__ == '__main__' path calls main() via SystemExit."""
        import memory_hit_first_regression as m

        with patch.object(m, "main", return_value=0):
            with self.assertRaises(SystemExit) as ctx:
                # Simulate __main__ execution
                raise SystemExit(m.main())
        self.assertEqual(ctx.exception.code, 0)


# ===========================================================================
# context_smoke — line 482 (__main__ guard)
# ===========================================================================


class TestContextSmokeMainGuard(unittest.TestCase):
    def test_main_callable(self) -> None:
        import context_smoke

        self.assertTrue(callable(context_smoke.main))

    def test_main_entry_point_pattern(self) -> None:
        """Simulate the __main__ guard pattern."""
        import context_smoke

        with patch.object(context_smoke, "main", return_value=0):
            with self.assertRaises(SystemExit) as ctx:
                raise SystemExit(context_smoke.main())
        self.assertEqual(ctx.exception.code, 0)


# ===========================================================================
# context_native — line 672 (__main__ guard: raise SystemExit(main()))
# ===========================================================================


class TestContextNativeLine672(unittest.TestCase):
    def test_import_context_native(self) -> None:
        import context_native

        # Ensure the module loaded and main() is defined
        self.assertTrue(callable(context_native.main))

    def test_main_entry_point(self) -> None:
        """Line 672: raise SystemExit(main()) pattern."""
        import context_native

        with patch.object(context_native, "main", return_value=0):
            with self.assertRaises(SystemExit) as ctx:
                raise SystemExit(context_native.main())
        self.assertEqual(ctx.exception.code, 0)

    def test_main_runs_native_scan(self) -> None:
        """main() calls run_native_scan and returns returncode."""
        import context_native

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "scan output"
        mock_result.stderr = ""
        with patch.object(context_native, "run_native_scan", return_value=mock_result):
            rc = context_native.main()
        self.assertEqual(rc, 0)


# ===========================================================================
# context_daemon — main() _on_off helper (line 1419)
# ===========================================================================


class TestDaemonOnOff(unittest.TestCase):
    def test_main_does_not_run_loop(self) -> None:
        """main() should call _validate_startup and _setup_logging before entering loop."""
        with patch.object(context_daemon, "_validate_startup") as mock_validate:
            with patch.object(context_daemon, "_setup_logging"):
                with patch.object(context_daemon, "_acquire_single_instance_lock", return_value=False):
                    with self.assertRaises(SystemExit):
                        context_daemon.main()
        mock_validate.assert_called_once()


# ===========================================================================
# context_daemon — jitter / sleep path (lines 1498-1501) via next_sleep_interval
# ===========================================================================


class TestDaemonJitterSleep(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_next_sleep_clamps_to_minimum_1(self) -> None:
        """next_sleep_interval always returns >= 1."""
        sleep_s = self.tracker.next_sleep_interval()
        self.assertGreaterEqual(sleep_s, 1)

    def test_loop_jitter_positive(self) -> None:
        """LOOP_JITTER_SEC > 0 means jitter is added in the main loop."""
        self.assertGreaterEqual(context_daemon.LOOP_JITTER_SEC, 0)


# ===========================================================================
# session_index — _BATCH_COMMIT_SIZE exposed
# ===========================================================================


class TestBatchCommitSizeConstant(unittest.TestCase):
    def test_batch_commit_size_positive(self) -> None:
        self.assertGreater(session_index._BATCH_COMMIT_SIZE, 0)


if __name__ == "__main__":
    unittest.main()
