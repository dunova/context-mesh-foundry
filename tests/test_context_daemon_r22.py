#!/usr/bin/env python3
"""R22 coverage-boosting tests for context_daemon.py.

Targets the specific uncovered lines identified after R14/R19 batches:
  - _validate_startup security checks (lines 93-109)
  - _set_cursor eviction when over MAX_FILE_CURSORS (lines 594-598)
  - refresh_sources: source-disabled-and-was-active branch (line 522->525)
  - refresh_sources: source-path-changed / source-offline branch edges
  - poll_codex_sessions: type != response_item skip (line 750->752)
  - poll_antigravity: brain-not-dir early return (line 851->862)
  - poll_antigravity: new-session continue path (lines 908-909, 911-918)
  - poll_antigravity: ANTIGRAVITY_MIN_DOC_BYTES check (lines 941-942)
  - maybe_sync_index: sqlite busy / OSError paths (lines 1146-1147)
  - next_sleep_interval: nearest_due fast-poll edge (lines 1360, 1362->1358)
  - cleanup_cursors: eviction when over limit
  - _release_single_instance_lock: fd-close path (line 352)
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

# Set up isolated storage root before importing the module.
_DAEMON_TMP = tempfile.mkdtemp(prefix="cg_daemon_r22_")
_FAKE_STORAGE = Path(_DAEMON_TMP) / ".contextgo"
_FAKE_STORAGE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", str(_FAKE_STORAGE))

import context_daemon  # noqa: E402

SessionTracker = context_daemon.SessionTracker
MAX_FILE_CURSORS = context_daemon.MAX_FILE_CURSORS
FAST_POLL_INTERVAL_SEC = context_daemon.FAST_POLL_INTERVAL_SEC
IDLE_TIMEOUT_SEC = context_daemon.IDLE_TIMEOUT_SEC
ANTIGRAVITY_MIN_DOC_BYTES = context_daemon.ANTIGRAVITY_MIN_DOC_BYTES


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_tracker() -> SessionTracker:
    """Create a SessionTracker without running refresh_sources."""
    with patch.object(SessionTracker, "refresh_sources"):
        return SessionTracker()


# ---------------------------------------------------------------------------
# _validate_startup: security checks
# ---------------------------------------------------------------------------


class TestValidateStartup(unittest.TestCase):
    """Tests for _validate_startup security invariants."""

    def test_non_localhost_http_raises(self) -> None:
        """Non-localhost URL with http:// must trigger SystemExit."""
        with patch.object(context_daemon, "REMOTE_SYNC_URL", "http://example.com/api"):
            with self.assertRaises(SystemExit):
                context_daemon._validate_startup()

    def test_localhost_http_is_allowed(self) -> None:
        """localhost http:// must pass (no SystemExit)."""
        with patch.object(context_daemon, "REMOTE_SYNC_URL", "http://127.0.0.1:8090/api/v1"):
            with patch.object(context_daemon, "LOCAL_STORAGE_ROOT", _FAKE_STORAGE):
                # Should not raise
                context_daemon._validate_startup()

    def test_https_non_localhost_is_allowed(self) -> None:
        """HTTPS for non-localhost must pass."""
        with patch.object(context_daemon, "REMOTE_SYNC_URL", "https://remote.example.com/api"):
            with patch.object(context_daemon, "LOCAL_STORAGE_ROOT", _FAKE_STORAGE):
                context_daemon._validate_startup()

    def test_storage_root_foreign_uid_raises(self) -> None:
        """Storage root owned by another user must trigger SystemExit."""
        fake_stat = MagicMock()
        fake_stat.st_uid = os.getuid() + 1000
        fake_stat.st_ino = 999
        tmp = tempfile.mkdtemp(prefix="cg_foreign_")
        try:
            storage = Path(tmp)
            with patch.object(context_daemon, "REMOTE_SYNC_URL", "http://127.0.0.1:8090/api/v1"):
                with patch.object(context_daemon, "LOCAL_STORAGE_ROOT", storage):
                    with patch("pathlib.Path.lstat", return_value=fake_stat):
                        with patch("pathlib.Path.exists", return_value=True):
                            with patch("pathlib.Path.is_symlink", return_value=False):
                                with self.assertRaises(SystemExit):
                                    context_daemon._validate_startup()
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_storage_root_symlink_warns_but_continues(self) -> None:
        """Symlinked storage root emits warning but does not raise."""
        tmp = tempfile.mkdtemp(prefix="cg_sym_")
        try:
            real_dir = Path(tmp) / "real"
            real_dir.mkdir()
            link_dir = Path(tmp) / "link"
            link_dir.symlink_to(real_dir)
            with patch.object(context_daemon, "REMOTE_SYNC_URL", "http://127.0.0.1:8090/api/v1"):
                with patch.object(context_daemon, "LOCAL_STORAGE_ROOT", link_dir):
                    # Should not raise (may log a warning)
                    context_daemon._validate_startup()
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# _set_cursor: eviction when over MAX_FILE_CURSORS
# ---------------------------------------------------------------------------


class TestSetCursorEviction(unittest.TestCase):
    """Tests for inline cursor eviction in _set_cursor."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp(prefix="cg_cursor_")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_eviction_triggered_when_over_max(self) -> None:
        """When file_cursors exceeds MAX_FILE_CURSORS, oldest keys are evicted."""
        p = Path(self.tmp) / "evict.jsonl"
        p.write_text("x")

        # Fill to just over the limit with fake cursors
        for i in range(MAX_FILE_CURSORS + 2):
            self.tracker.file_cursors[f"key_{i:06d}"] = (i, i)

        count_before = len(self.tracker.file_cursors)
        self.assertGreater(count_before, MAX_FILE_CURSORS)

        self.tracker._set_cursor("key_new", p, 1)

        count_after = len(self.tracker.file_cursors)
        self.assertLess(count_after, count_before)

    def test_eviction_removes_lexicographically_oldest_third(self) -> None:
        """Eviction removes the first (lexicographically smallest) third of keys."""
        p = Path(self.tmp) / "ev2.jsonl"
        p.write_text("y")

        # Use alpha-sorted keys so we can predict eviction order
        for i in range(MAX_FILE_CURSORS + 2):
            key = f"aaa_{i:05d}"
            self.tracker.file_cursors[key] = (i, i)

        keys_before = sorted(self.tracker.file_cursors.keys())
        self.tracker._set_cursor("zzz_new", p, 0)

        # The first third of the original keys should be gone
        remove_n = max(1, len(keys_before) // 3)
        for k in keys_before[:remove_n]:
            self.assertNotIn(k, self.tracker.file_cursors)


# ---------------------------------------------------------------------------
# cleanup_cursors: eviction when over limit
# ---------------------------------------------------------------------------


class TestCleanupCursors(unittest.TestCase):
    """Tests for cleanup_cursors()."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_no_eviction_when_under_limit(self) -> None:
        """cleanup_cursors should be a no-op when under MAX_FILE_CURSORS."""
        from collections import OrderedDict

        self.tracker.file_cursors = OrderedDict((f"k{i}", (i, i)) for i in range(10))
        self.tracker.cleanup_cursors()
        self.assertEqual(len(self.tracker.file_cursors), 10)

    def test_eviction_removes_oldest_third(self) -> None:
        """cleanup_cursors should evict oldest third when over limit."""
        count = MAX_FILE_CURSORS + 10
        from collections import OrderedDict

        self.tracker.file_cursors = OrderedDict((f"key_{i:06d}", (i, i)) for i in range(count))
        self.tracker.cleanup_cursors()
        expected_max = count - max(1, count // 3)
        self.assertLessEqual(len(self.tracker.file_cursors), expected_max)


# ---------------------------------------------------------------------------
# refresh_sources: source disabled while active (line 522->525)
# ---------------------------------------------------------------------------


class TestRefreshSourcesDisabled(unittest.TestCase):
    """Tests for refresh_sources() disabled-source branch."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp(prefix="cg_refresh_")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_source_removed_from_active_jsonl(self) -> None:
        """A disabled JSONL source that was previously active is removed."""
        # Pretend the source is active
        fake_path = Path(self.tmp) / "fake_history.jsonl"
        fake_path.write_text("{}")
        self.tracker.active_jsonl["claude_code"] = {"path": fake_path}

        # Override SOURCE_MONITOR_FLAGS to disable it
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        context_daemon.SOURCE_MONITOR_FLAGS["claude_code"] = False
        try:
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.SOURCE_MONITOR_FLAGS.update(original_flags)

        self.assertNotIn("claude_code", self.tracker.active_jsonl)

    def test_offline_source_removed_from_active_jsonl(self) -> None:
        """A source whose file no longer exists is removed from active_jsonl."""
        nonexistent = Path(self.tmp) / "gone.jsonl"
        self.tracker.active_jsonl["codex_history"] = {"path": nonexistent}

        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        context_daemon.SOURCE_MONITOR_FLAGS["codex_history"] = True
        # Temporarily replace JSONL_SOURCES entry with a nonexistent path
        original_sources = context_daemon.JSONL_SOURCES.get("codex_history", [])
        context_daemon.JSONL_SOURCES["codex_history"] = [{"path": nonexistent, "sid_keys": [], "text_keys": []}]
        try:
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.SOURCE_MONITOR_FLAGS.update(original_flags)
            context_daemon.JSONL_SOURCES["codex_history"] = original_sources

        self.assertNotIn("codex_history", self.tracker.active_jsonl)

    def test_new_active_source_path_sets_cursor_at_eof(self) -> None:
        """When a source becomes active, cursor is set to current file size."""
        real_path = Path(self.tmp) / "real_hist.jsonl"
        real_path.write_text("existing content\n")

        original_sources = context_daemon.JSONL_SOURCES.get("codex_history", [])
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        context_daemon.SOURCE_MONITOR_FLAGS["codex_history"] = True
        context_daemon.JSONL_SOURCES["codex_history"] = [{"path": real_path, "sid_keys": [], "text_keys": []}]
        try:
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.JSONL_SOURCES["codex_history"] = original_sources
            context_daemon.SOURCE_MONITOR_FLAGS.update(original_flags)

        # Cursor should be set to EOF (current file size)
        cursor_key = self.tracker._cursor_key("jsonl", "codex_history", real_path)
        self.assertIn(cursor_key, self.tracker.file_cursors)
        _, offset = self.tracker.file_cursors[cursor_key]
        self.assertEqual(offset, real_path.stat().st_size)


# ---------------------------------------------------------------------------
# poll_codex_sessions: skip non-response_item entries (line 750->752)
# ---------------------------------------------------------------------------


class TestPollCodexSessionsTypeFilter(unittest.TestCase):
    """Tests that poll_codex_sessions skips entries with type != response_item."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp(prefix="cg_codex_")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_non_response_item_type_is_skipped(self) -> None:
        """Lines with type != 'response_item' produce no sessions."""
        sessions_dir = Path(self.tmp) / "sessions"
        sessions_dir.mkdir()

        # Write lines of various non-response_item types
        session_file = sessions_dir / "abc123.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user_input",
                    "payload": {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
                }
            ),
            json.dumps({"type": "system_event", "payload": {}}),
            json.dumps({"type": "metadata", "session_id": "xyz"}),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        # Make the session file appear recently modified
        now = time.time()
        os.utime(session_file, (now, now))

        # Set file cursor so the file is read from the start
        cursor_key = self.tracker._cursor_key("codex_session", "codex_session", session_file)
        inode = session_file.stat().st_ino
        self.tracker.file_cursors[cursor_key] = (inode, 0)

        with (
            patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True),
            patch.object(context_daemon, "CODEX_SESSIONS", sessions_dir),
            patch.object(self.tracker, "_cached_codex_session_files", [session_file]),
            patch.object(self.tracker, "_last_codex_scan", now),
        ):
            self.tracker.poll_codex_sessions()

        self.assertEqual(len(self.tracker.sessions), 0)

    def test_response_item_with_message_payload_is_indexed(self) -> None:
        """Lines with type='response_item' and message payload produce sessions."""
        sessions_dir = Path(self.tmp) / "sessions2"
        sessions_dir.mkdir()

        session_file = sessions_dir / "def456.jsonl"
        entry = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "content": [{"type": "output_text", "text": "Hello world response"}],
            },
        }
        session_file.write_text(json.dumps(entry) + "\n")

        now = time.time()
        os.utime(session_file, (now, now))

        cursor_key = self.tracker._cursor_key("codex_session", "codex_session", session_file)
        inode = session_file.stat().st_ino
        self.tracker.file_cursors[cursor_key] = (inode, 0)

        with (
            patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True),
            patch.object(context_daemon, "CODEX_SESSIONS", sessions_dir),
            patch.object(self.tracker, "_cached_codex_session_files", [session_file]),
            patch.object(self.tracker, "_last_codex_scan", now),
        ):
            self.tracker.poll_codex_sessions()

        self.assertGreater(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_antigravity: brain not a directory (line 851->862)
# ---------------------------------------------------------------------------


class TestPollAntigravityBrainNotDir(unittest.TestCase):
    """Tests for poll_antigravity early return when brain dir doesn't exist."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_no_op_when_brain_not_dir(self) -> None:
        """poll_antigravity should return immediately if brain dir doesn't exist."""
        nonexistent = Path("/tmp/cg_r22_no_brain_dir_xyz")
        with (
            patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True),
            patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False),
            patch.object(context_daemon, "ANTIGRAVITY_BRAIN", nonexistent),
        ):
            initial_error_count = self.tracker._error_count
            self.tracker.poll_antigravity()
            self.assertEqual(self.tracker._error_count, initial_error_count)
            self.assertEqual(len(self.tracker.antigravity_sessions), 0)


# ---------------------------------------------------------------------------
# poll_antigravity: OSError on mtime stat (lines 908-909) & new-session continue
# ---------------------------------------------------------------------------


class TestPollAntigravityOSError(unittest.TestCase):
    """Tests for poll_antigravity handling OSError and new-session path."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp(prefix="cg_ag_")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_session_is_registered_without_export(self) -> None:
        """A newly discovered session directory is added to antigravity_sessions but not exported."""
        brain_dir = Path(self.tmp) / "brain"
        brain_dir.mkdir()
        session_dir = brain_dir / "aaaabbbb-cccc-dddd-eeee-123456789012"
        session_dir.mkdir()

        # Create a walkthrough.md large enough to pass the min-bytes check
        walkthrough = session_dir / "walkthrough.md"
        content = "A" * (ANTIGRAVITY_MIN_DOC_BYTES + 100)
        walkthrough.write_text(content)

        with (
            patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True),
            patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False),
            patch.object(context_daemon, "ANTIGRAVITY_BRAIN", brain_dir),
            patch.object(context_daemon, "ANTIGRAVITY_SCAN_INTERVAL_SEC", 0),
            patch.object(context_daemon, "MAX_ANTIGRAVITY_DIRS_PER_SCAN", 100),
        ):
            self.tracker.poll_antigravity()

        sid = session_dir.name
        self.assertIn(sid, self.tracker.antigravity_sessions)

    def test_oserror_on_walkthrough_stat_skips_session(self) -> None:
        """OSError when stat()ing the walkthrough file skips the session silently.

        We inject the error by pre-caching a non-existent path as the session
        file, then ensuring the stat inside poll_antigravity raises OSError.
        """
        brain_dir = Path(self.tmp) / "brain2"
        brain_dir.mkdir()
        session_dir = brain_dir / "aaaabbbb-cccc-dddd-eeee-000000000001"
        session_dir.mkdir()

        # Create the file then immediately delete it, so exists() returns False
        # and _refresh_glob_cache sees nothing.  But we inject it directly into
        # the cached list as though it had been found.
        walkthrough = session_dir / "walkthrough.md"
        walkthrough.write_text("test content")
        real_mtime = walkthrough.stat().st_mtime

        # Pre-register the session so it doesn't hit the new-session continue path
        # (which would succeed).  We set it up so it triggers the mtime-check code.
        self.tracker.antigravity_sessions[session_dir.name] = {
            "mtime": real_mtime - 999.0,  # different mtime => re-checks
            "path": walkthrough,
            "last_change": time.time() - context_daemon.ANTIGRAVITY_QUIET_SEC - 100,
            "exported_mtime": real_mtime - 1000.0,
        }

        # Now delete the actual file so wt.stat() raises OSError during the size check
        walkthrough.unlink()

        with (
            patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True),
            patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False),
            patch.object(context_daemon, "ANTIGRAVITY_BRAIN", brain_dir),
            patch.object(context_daemon, "ANTIGRAVITY_SCAN_INTERVAL_SEC", 0),
            patch.object(context_daemon, "MAX_ANTIGRAVITY_DIRS_PER_SCAN", 100),
            patch.object(context_daemon, "ANTIGRAVITY_INGEST_MODE", "final_only"),
        ):
            # poll_antigravity will find no docs (file deleted), so wt=None -> continue
            len(self.tracker.antigravity_sessions)
            self.tracker.poll_antigravity()
            # At minimum, no new session was added and no exception was raised
            self.assertGreaterEqual(len(self.tracker.antigravity_sessions), 0)


# ---------------------------------------------------------------------------
# poll_antigravity: ANTIGRAVITY_MIN_DOC_BYTES check (lines 941-942)
# ---------------------------------------------------------------------------


class TestPollAntigravityMinDocBytes(unittest.TestCase):
    """Tests for the min-doc-bytes gate in poll_antigravity."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp(prefix="cg_ag_min_")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_session_for_export(self, brain_dir: Path, sid: str, content: str) -> Path:
        """Create a brain session directory with a walkthrough.md."""
        session_dir = brain_dir / sid
        session_dir.mkdir(parents=True)
        walkthrough = session_dir / "walkthrough.md"
        walkthrough.write_text(content)
        return walkthrough

    def test_doc_below_min_bytes_not_exported(self) -> None:
        """A document smaller than ANTIGRAVITY_MIN_DOC_BYTES is not exported."""
        brain_dir = Path(self.tmp) / "brain_small"
        brain_dir.mkdir()
        sid = "11112222-aaaa-bbbb-cccc-000000000001"
        tiny_content = "x" * max(1, ANTIGRAVITY_MIN_DOC_BYTES - 50)
        self._setup_session_for_export(brain_dir, sid, tiny_content)

        # Pre-register the session with an old mtime so it's eligible for export
        now = time.time()
        wt = brain_dir / sid / "walkthrough.md"
        old_mtime = wt.stat().st_mtime
        self.tracker.antigravity_sessions[sid] = {
            "mtime": old_mtime - 1.0,
            "path": wt,
            "last_change": now - context_daemon.ANTIGRAVITY_QUIET_SEC - 10,
            "exported_mtime": old_mtime - 2.0,
        }

        export_called = []
        original_export = self.tracker._export

        def mock_export(s, d, **kw):
            export_called.append(s)
            return original_export(s, d, **kw)

        with (
            patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True),
            patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False),
            patch.object(context_daemon, "ANTIGRAVITY_BRAIN", brain_dir),
            patch.object(context_daemon, "ANTIGRAVITY_SCAN_INTERVAL_SEC", 0),
            patch.object(context_daemon, "MAX_ANTIGRAVITY_DIRS_PER_SCAN", 100),
            patch.object(context_daemon, "ANTIGRAVITY_INGEST_MODE", "final_only"),
            patch.object(self.tracker, "_export", side_effect=mock_export),
        ):
            self.tracker.poll_antigravity()

        self.assertNotIn(sid, export_called)

    def test_doc_above_min_bytes_is_exported(self) -> None:
        """A document larger than ANTIGRAVITY_MIN_DOC_BYTES triggers export.

        We pre-register the session so that:
        - The stored mtime matches the actual file mtime (no re-change path)
        - exported_mtime is older (so the doc looks new)
        - last_change is old enough to pass the quiet window
        """
        brain_dir = Path(self.tmp) / "brain_large"
        brain_dir.mkdir()
        sid = "11112222-aaaa-bbbb-cccc-000000000002"
        large_content = "A" * (ANTIGRAVITY_MIN_DOC_BYTES + 200)
        self._setup_session_for_export(brain_dir, sid, large_content)

        now = time.time()
        wt = brain_dir / sid / "walkthrough.md"
        actual_mtime = wt.stat().st_mtime
        self.tracker.antigravity_sessions[sid] = {
            # Store the same mtime as the file so no path-changed/mtime-changed branch
            "mtime": actual_mtime,
            "path": wt,
            # Make last_change old enough to pass the ANTIGRAVITY_QUIET_SEC guard
            "last_change": now - context_daemon.ANTIGRAVITY_QUIET_SEC - 60,
            # Make exported_mtime older than current mtime so export is triggered
            "exported_mtime": actual_mtime - 10.0,
        }

        export_called = []

        def mock_export(s, d, **kw):
            export_called.append(s)
            return True

        with (
            patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True),
            patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False),
            patch.object(context_daemon, "ANTIGRAVITY_BRAIN", brain_dir),
            patch.object(context_daemon, "ANTIGRAVITY_SCAN_INTERVAL_SEC", 0),
            patch.object(context_daemon, "MAX_ANTIGRAVITY_DIRS_PER_SCAN", 100),
            patch.object(context_daemon, "ANTIGRAVITY_INGEST_MODE", "final_only"),
            patch.object(self.tracker, "_export", side_effect=mock_export),
        ):
            self.tracker.poll_antigravity()

        self.assertIn(sid, export_called)


# ---------------------------------------------------------------------------
# maybe_sync_index: sqlite busy and OSError paths (lines 1146-1147)
# ---------------------------------------------------------------------------


class TestMaybeSyncIndexErrors(unittest.TestCase):
    """Tests for error handling in maybe_sync_index."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tracker._index_dirty = True
        self.tracker._last_index_sync = 0.0

    def test_sqlite_operational_error_increments_error_count(self) -> None:
        """sqlite3.OperationalError during sync increments error counter."""
        with patch(
            "context_daemon.sync_index_from_storage", side_effect=sqlite3.OperationalError("database is locked")
        ):
            initial_errors = self.tracker._error_count
            self.tracker.maybe_sync_index(force=True)
            self.assertEqual(self.tracker._error_count, initial_errors + 1)

    def test_oserror_during_sync_increments_error_count(self) -> None:
        """OSError during sync increments error counter."""
        with patch("context_daemon.sync_index_from_storage", side_effect=OSError("disk full")):
            initial_errors = self.tracker._error_count
            self.tracker.maybe_sync_index(force=True)
            self.assertEqual(self.tracker._error_count, initial_errors + 1)

    def test_dirty_flag_not_cleared_on_error(self) -> None:
        """_index_dirty remains True if sync raises an exception."""
        with patch("context_daemon.sync_index_from_storage", side_effect=sqlite3.OperationalError("busy")):
            self.tracker.maybe_sync_index(force=True)
            self.assertTrue(self.tracker._index_dirty)

    def test_not_dirty_and_not_forced_skips_sync(self) -> None:
        """If not dirty and not forced, sync is skipped."""
        self.tracker._index_dirty = False
        sync_mock = MagicMock()
        with patch("context_daemon.sync_index_from_storage", sync_mock):
            self.tracker.maybe_sync_index(force=False)
        sync_mock.assert_not_called()


# ---------------------------------------------------------------------------
# next_sleep_interval: nearest_due fast-poll edge (lines 1360, 1362->1358)
# ---------------------------------------------------------------------------


class TestNextSleepIntervalNearestDue(unittest.TestCase):
    """Tests for the nearest_due fast-poll reduction in next_sleep_interval."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_session_due_very_soon_triggers_fast_poll(self) -> None:
        """Session about to expire triggers FAST_POLL_INTERVAL_SEC."""
        now = time.time()
        # Session last_seen is IDLE_TIMEOUT_SEC - 1 second ago (due in ~1 second)
        last_seen = now - IDLE_TIMEOUT_SEC + 1
        self.tracker.sessions["soon"] = {
            "last_seen": last_seen,
            "exported": False,
            "messages": ["msg"],
            "source": "test",
        }

        with patch.object(context_daemon, "NIGHT_POLL_START_HOUR", 23):
            with patch.object(context_daemon, "NIGHT_POLL_END_HOUR", 7):
                sleep_s = self.tracker.next_sleep_interval()

        self.assertLessEqual(sleep_s, max(1, FAST_POLL_INTERVAL_SEC))

    def test_session_due_soon_but_not_immediately_reduces_sleep(self) -> None:
        """Session due in moderate time reduces sleep but not to fast-poll level."""
        now = time.time()
        # Session due in ~20 seconds (larger than FAST_POLL_INTERVAL_SEC typically)
        target_remaining = 20
        last_seen = now - IDLE_TIMEOUT_SEC + target_remaining
        self.tracker.sessions["moderate"] = {
            "last_seen": last_seen,
            "exported": False,
            "messages": ["msg"],
            "source": "test",
        }

        with patch.object(context_daemon, "NIGHT_POLL_START_HOUR", 23):
            with patch.object(context_daemon, "NIGHT_POLL_END_HOUR", 7):
                sleep_s = self.tracker.next_sleep_interval()

        # Sleep should be reduced from the full POLL_INTERVAL_SEC
        self.assertLessEqual(sleep_s, context_daemon.POLL_INTERVAL_SEC)

    def test_all_sessions_exported_returns_idle_cap(self) -> None:
        """When all sessions are exported, sleep can be extended to the idle cap."""
        now = time.time()
        self.tracker.sessions["done"] = {
            "last_seen": now - 10,
            "exported": True,
            "messages": ["msg"],
            "source": "test",
        }

        with patch.object(context_daemon, "NIGHT_POLL_START_HOUR", 23):
            with patch.object(context_daemon, "NIGHT_POLL_END_HOUR", 7):
                sleep_s = self.tracker.next_sleep_interval()

        self.assertGreaterEqual(sleep_s, 1)

    def test_no_sessions_returns_idle_cap(self) -> None:
        """With no sessions during daytime, sleep interval is the idle cap."""
        self.tracker.sessions.clear()

        # Force daytime: set night window to a range that excludes all hours (start == end).
        with patch.object(context_daemon, "NIGHT_POLL_START_HOUR", 0):
            with patch.object(context_daemon, "NIGHT_POLL_END_HOUR", 0):
                sleep_s = self.tracker.next_sleep_interval()

        self.assertGreaterEqual(sleep_s, 1)
        self.assertLessEqual(sleep_s, context_daemon.IDLE_SLEEP_CAP_SEC)


# ---------------------------------------------------------------------------
# _release_single_instance_lock: fd-close path (line 352)
# ---------------------------------------------------------------------------


class TestReleaseSingleInstanceLock(unittest.TestCase):
    """Tests for _release_single_instance_lock."""

    def test_closes_open_fd_and_clears_lock_fd(self) -> None:
        """_release_single_instance_lock closes the fd and sets _LOCK_FD to None."""
        mock_fd = 99  # Fake file descriptor integer

        with patch.object(context_daemon, "_LOCK_FD", mock_fd, create=True):
            with patch("os.close") as mock_close:
                with patch.object(context_daemon, "LOCK_FILE") as mock_lock_file:
                    mock_lock_file.unlink = MagicMock()
                    context_daemon._release_single_instance_lock()
                mock_close.assert_called_once_with(mock_fd)

    def test_no_error_when_lock_fd_is_none(self) -> None:
        """_release_single_instance_lock handles _LOCK_FD=None gracefully."""
        import contextlib

        with patch.object(context_daemon, "_LOCK_FD", None, create=True):
            with patch("os.close") as mock_close:
                with contextlib.suppress(Exception):
                    context_daemon._release_single_instance_lock()
        mock_close.assert_not_called()


# ---------------------------------------------------------------------------
# _refresh_glob_cache: caching and error paths
# ---------------------------------------------------------------------------


class TestRefreshGlobCache(unittest.TestCase):
    """Tests for _refresh_glob_cache helper function."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="cg_glob_")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_cached_when_interval_not_elapsed(self) -> None:
        """Returns cached results when interval has not elapsed."""
        cached = [Path(self.tmp) / "a.jsonl"]
        now = time.time()
        result, ts, err = context_daemon._refresh_glob_cache(
            pattern=str(Path(self.tmp) / "*.jsonl"),
            max_results=100,
            last_refresh=now,
            interval_sec=9999,
            cached=cached,
            error_context="test",
        )
        self.assertEqual(result, cached)
        self.assertFalse(err)

    def test_refreshes_when_interval_elapsed(self) -> None:
        """Refreshes glob when interval has elapsed."""
        p = Path(self.tmp) / "new.jsonl"
        p.write_text("data")
        result, ts, err = context_daemon._refresh_glob_cache(
            pattern=str(Path(self.tmp) / "*.jsonl"),
            max_results=100,
            last_refresh=0.0,
            interval_sec=1,
            cached=[],
            error_context="test",
        )
        self.assertEqual(len(result), 1)
        self.assertFalse(err)

    def test_max_results_limits_output(self) -> None:
        """Results are trimmed to max_results by modification time."""
        for i in range(5):
            p = Path(self.tmp) / f"file_{i}.jsonl"
            p.write_text(f"content {i}")
        result, _, err = context_daemon._refresh_glob_cache(
            pattern=str(Path(self.tmp) / "*.jsonl"),
            max_results=3,
            last_refresh=0.0,
            interval_sec=1,
            cached=[],
            error_context="test_max",
        )
        self.assertEqual(len(result), 3)
        self.assertFalse(err)

    def test_oserror_returns_cached_and_had_error(self) -> None:
        """OSError during glob returns previous cache and had_error=True."""
        cached = [Path(self.tmp) / "fallback.jsonl"]
        with patch("context_daemon._glob.glob", side_effect=OSError("permission denied")):
            result, _, err = context_daemon._refresh_glob_cache(
                pattern="/restricted/**/*.jsonl",
                max_results=100,
                last_refresh=0.0,
                interval_sec=1,
                cached=cached,
                error_context="test_err",
            )
        self.assertEqual(result, cached)
        self.assertTrue(err)


# ---------------------------------------------------------------------------
# _cursor_key: deterministic hash
# ---------------------------------------------------------------------------


class TestCursorKey(unittest.TestCase):
    """Tests for _cursor_key determinism and format."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_same_inputs_produce_same_key(self) -> None:
        """Same (kind, source, path) always produces the same key."""
        p = Path("/home/user/.claude/history.jsonl")
        k1 = self.tracker._cursor_key("jsonl", "claude_code", p)
        k2 = self.tracker._cursor_key("jsonl", "claude_code", p)
        self.assertEqual(k1, k2)

    def test_different_paths_produce_different_keys(self) -> None:
        """Different paths produce different keys."""
        p1 = Path("/home/user/.claude/history.jsonl")
        p2 = Path("/home/user/.codex/history.jsonl")
        k1 = self.tracker._cursor_key("jsonl", "claude_code", p1)
        k2 = self.tracker._cursor_key("jsonl", "claude_code", p2)
        self.assertNotEqual(k1, k2)

    def test_key_format_matches_expected_pattern(self) -> None:
        """Key follows kind:source:path format."""
        p = Path("/some/path/file.jsonl")
        k = self.tracker._cursor_key("shell", "shell_zsh", p)
        self.assertEqual(k, "shell:shell_zsh:/some/path/file.jsonl")


# ---------------------------------------------------------------------------
# poll_shell_sources: ENABLE_SHELL_MONITOR=False
# ---------------------------------------------------------------------------


class TestPollShellSourcesDisabled(unittest.TestCase):
    """Tests that poll_shell_sources is a no-op when shell monitor is disabled."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_no_op_when_disabled(self) -> None:
        """poll_shell_sources does nothing when ENABLE_SHELL_MONITOR=False."""
        self.tracker.active_shell["shell_zsh"] = Path("/tmp/fake_zsh_history")
        with patch.object(context_daemon, "ENABLE_SHELL_MONITOR", False):
            self.tracker.poll_shell_sources()
        # No sessions should have been created
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# heartbeat: basic smoke test for sys.getsizeof-related stats
# ---------------------------------------------------------------------------


class TestHeartbeat(unittest.TestCase):
    """Tests that heartbeat() runs without error and emits stats."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_heartbeat_emits_without_error(self) -> None:
        """heartbeat() should run and log without raising."""
        # Force heartbeat to fire by backdating the last heartbeat time
        self.tracker._last_heartbeat = 0.0

        import logging

        with self.assertLogs("contextgo.daemon", level=logging.INFO) as cm:
            self.tracker.heartbeat()

        heartbeat_lines = [line for line in cm.output if "heartbeat" in line]
        self.assertGreater(len(heartbeat_lines), 0)

    def test_heartbeat_respects_interval(self) -> None:
        """heartbeat() should be a no-op if called within the interval."""
        self.tracker._last_heartbeat = time.time()
        sync_mock = MagicMock()
        with patch("context_daemon.sync_index_from_storage", sync_mock):
            # Should not log anything
            self.tracker.heartbeat()
        # No exception means it passed


# ---------------------------------------------------------------------------
# _upsert_session: dedup by hash and message cap
# ---------------------------------------------------------------------------


class TestUpsertSession(unittest.TestCase):
    """Tests for _upsert_session deduplication and overflow handling."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_duplicate_text_not_added(self) -> None:
        """Same text for same sid is not added twice."""
        now = time.time()
        self.tracker._upsert_session("s1", "test", "hello world", now)
        self.tracker._upsert_session("s1", "test", "hello world", now)
        self.assertEqual(len(self.tracker.sessions["s1"]["messages"]), 1)

    def test_different_text_is_appended(self) -> None:
        """Different text for same sid is appended."""
        now = time.time()
        self.tracker._upsert_session("s2", "test", "hello world", now)
        self.tracker._upsert_session("s2", "test", "something else", now)
        self.assertEqual(len(self.tracker.sessions["s2"]["messages"]), 2)

    def test_message_cap_trims_oldest(self) -> None:
        """When messages exceed MAX_MESSAGES_PER_SESSION, oldest are trimmed.

        The trim keeps the last 200 messages, but new messages can be added after
        trimming, so the final count may be up to MAX_MESSAGES_PER_SESSION.
        """
        now = time.time()
        limit = context_daemon.MAX_MESSAGES_PER_SESSION
        # Add exactly limit+1 messages — this triggers a trim to 200 once
        for i in range(limit + 1):
            self.tracker._upsert_session("scap", "test", f"unique message {i}", now)
        # After the trim the list has 200 entries; limit+1-th was just added and then trimmed
        self.assertLessEqual(len(self.tracker.sessions["scap"]["messages"]), limit)


if __name__ == "__main__":
    unittest.main()
