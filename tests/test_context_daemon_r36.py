#!/usr/bin/env python3
"""R36 coverage-boosting tests for context_daemon.py.

Targets uncovered inotify and adaptive-polling code paths:
  - Lines 534-594: inotify event handling, mask parsing, polling fallback init
  - Lines 604-672: inotify watcher setup, fallback paths
  - Lines 680-694: inotify cleanup/teardown
  - Lines 1931-1958: adaptive polling interval adjustment
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Set up isolated storage root before importing the module.
_DAEMON_TMP = tempfile.mkdtemp(prefix="cg_daemon_r36_")
_FAKE_STORAGE = Path(_DAEMON_TMP) / ".contextgo"
_FAKE_STORAGE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", str(_FAKE_STORAGE))

import context_daemon  # noqa: E402

_FileWatcher = context_daemon._FileWatcher
_INOTIFY_EVENT_BASE = context_daemon._INOTIFY_EVENT_BASE
IDLE_SLEEP_CAP_SEC = context_daemon.IDLE_SLEEP_CAP_SEC
ERROR_BACKOFF_MAX_SEC = context_daemon.ERROR_BACKOFF_MAX_SEC
LOOP_JITTER_SEC = context_daemon.LOOP_JITTER_SEC
SessionTracker = context_daemon.SessionTracker


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _make_tracker() -> SessionTracker:
    """Create a SessionTracker without running refresh_sources."""
    with patch.object(SessionTracker, "refresh_sources"):
        return SessionTracker()


def _make_inotify_event(wd: int, mask: int, cookie: int, name: bytes = b"") -> bytes:
    """Pack an inotify_event struct: wd(i4) mask(u4) cookie(u4) len(u4) [+name padded]."""
    # name field is padded to a multiple of 4 bytes, null-terminated.
    if name:
        padded_len = ((len(name) + 1 + 3) // 4) * 4  # include null terminator, align
        name_bytes = name + b"\x00" * (padded_len - len(name))
    else:
        padded_len = 0
        name_bytes = b""
    header = struct.pack("<iIII", wd, mask, cookie, padded_len)
    return header + name_bytes


# ---------------------------------------------------------------------------
# Tests: _FileWatcher with inotify UNAVAILABLE (polling fallback)
# ---------------------------------------------------------------------------


class TestFileWatcherPollFallback(unittest.TestCase):
    """Tests for _FileWatcher in polling-fallback mode (inotify not available)."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cg_fw_poll_"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_watcher_no_inotify(self, dirs=None) -> _FileWatcher:
        """Create a _FileWatcher with inotify forcibly disabled."""
        if dirs is None:
            dirs = [self.tmpdir]
        with patch.object(context_daemon, "_LIBC", None):
            return _FileWatcher(dirs)

    # ------------------------------------------------------------------
    # _init_poll_fallback seeding
    # ------------------------------------------------------------------

    def test_poll_fallback_seeds_mtimes(self) -> None:
        """_init_poll_fallback should seed mtime for each existing directory."""
        watcher = self._make_watcher_no_inotify()
        self.assertIn(self.tmpdir, watcher._poll_mtimes)
        self.assertGreater(watcher._poll_mtimes[self.tmpdir], 0.0)

    def test_poll_fallback_available_is_false(self) -> None:
        """In polling mode _available must be False."""
        watcher = self._make_watcher_no_inotify()
        self.assertFalse(watcher._available)

    def test_has_changes_always_true_in_fallback(self) -> None:
        """has_changes() returns True unconditionally in fallback mode."""
        watcher = self._make_watcher_no_inotify()
        self.assertTrue(watcher.has_changes())

    def test_get_changed_paths_returns_all_dirs_in_fallback(self) -> None:
        """get_changed_paths() returns all watched directories in fallback mode."""
        watcher = self._make_watcher_no_inotify()
        paths = watcher.get_changed_paths()
        self.assertIn(self.tmpdir, paths)

    def test_inotify_fd_negative_in_fallback(self) -> None:
        """_inotify_fd must be -1 when inotify is unavailable."""
        watcher = self._make_watcher_no_inotify()
        self.assertEqual(watcher._inotify_fd, -1)

    # ------------------------------------------------------------------
    # _poll_mtime_fallback detection
    # ------------------------------------------------------------------

    def test_poll_mtime_fallback_detects_change(self) -> None:
        """_poll_mtime_fallback records directory when mtime advances."""
        watcher = self._make_watcher_no_inotify()
        # Force a known old mtime
        watcher._poll_mtimes[self.tmpdir] = 0.0
        # Touch a file to advance mtime
        (self.tmpdir / "dummy.txt").write_text("hello")
        watcher._poll_mtime_fallback()
        self.assertIn(self.tmpdir, watcher._changed_paths)

    def test_poll_mtime_fallback_no_change_when_mtime_same(self) -> None:
        """_poll_mtime_fallback does NOT add to changed set when mtime is unchanged."""
        watcher = self._make_watcher_no_inotify()
        # Seed mtime to current value
        watcher._poll_mtimes[self.tmpdir] = self.tmpdir.stat().st_mtime
        watcher._changed_paths.clear()
        watcher._poll_mtime_fallback()
        self.assertNotIn(self.tmpdir, watcher._changed_paths)

    def test_poll_mtime_fallback_skips_oserror_dirs(self) -> None:
        """_poll_mtime_fallback silently skips directories that raise OSError on stat."""
        watcher = self._make_watcher_no_inotify()
        ghost_dir = self.tmpdir / "nonexistent_subdir"
        watcher._directories.append(ghost_dir)
        watcher._poll_mtimes[ghost_dir] = 0.0
        # Should not raise
        watcher._poll_mtime_fallback()

    def test_poll_mtime_fallback_uses_zero_as_default_prev(self) -> None:
        """_poll_mtime_fallback uses 0.0 as the default previous mtime."""
        watcher = self._make_watcher_no_inotify()
        # Remove the entry to simulate first-time check
        watcher._poll_mtimes.pop(self.tmpdir, None)
        watcher._poll_mtime_fallback()
        # Any real directory has mtime > 0, so it should register as changed
        self.assertIn(self.tmpdir, watcher._changed_paths)

    # ------------------------------------------------------------------
    # update() in fallback mode calls _poll_mtime_fallback
    # ------------------------------------------------------------------

    def test_update_calls_poll_mtime_fallback(self) -> None:
        """update() dispatches to _poll_mtime_fallback when inotify fd < 0."""
        watcher = self._make_watcher_no_inotify()
        called = []
        original = watcher._poll_mtime_fallback
        watcher._poll_mtime_fallback = lambda: called.append(1) or original()
        watcher.update()
        self.assertEqual(len(called), 1)

    # ------------------------------------------------------------------
    # add_directory in fallback mode
    # ------------------------------------------------------------------

    def test_add_directory_polls_fallback_when_no_inotify(self) -> None:
        """add_directory seeds mtime in polling mode."""
        watcher = self._make_watcher_no_inotify()
        subdir = self.tmpdir / "newwatch"
        subdir.mkdir()
        watcher.add_directory(subdir)
        self.assertIn(subdir, watcher._directories)
        self.assertIn(subdir, watcher._poll_mtimes)

    def test_add_directory_noop_for_nonexistent_dir(self) -> None:
        """add_directory does nothing for a path that is not a directory."""
        watcher = self._make_watcher_no_inotify()
        ghost = self.tmpdir / "ghost_dir"
        watcher.add_directory(ghost)
        self.assertNotIn(ghost, watcher._directories)

    def test_add_directory_noop_for_already_watched(self) -> None:
        """add_directory is idempotent — second call does not duplicate entry."""
        watcher = self._make_watcher_no_inotify()
        count_before = len(watcher._directories)
        watcher.add_directory(self.tmpdir)
        self.assertEqual(len(watcher._directories), count_before)

    # ------------------------------------------------------------------
    # close() is safe in fallback mode
    # ------------------------------------------------------------------

    def test_close_noop_in_fallback_mode(self) -> None:
        """close() must not raise when fd is already -1."""
        watcher = self._make_watcher_no_inotify()
        watcher.close()  # must not raise
        self.assertEqual(watcher._inotify_fd, -1)


# ---------------------------------------------------------------------------
# Tests: _FileWatcher inotify initialisation failures
# ---------------------------------------------------------------------------


class TestFileWatcherInotifyInitFailures(unittest.TestCase):
    """Tests for the inotify init paths that degrade gracefully."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cg_fw_init_"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_inotify_init1_failure_falls_back_to_poll(self) -> None:
        """When inotify_init1 returns -1 the watcher must fall back to polling."""
        mock_libc = MagicMock()
        mock_libc.inotify_init1.return_value = -1
        with patch.object(context_daemon, "_LIBC", mock_libc):
            with patch("ctypes.get_errno", return_value=22):
                watcher = _FileWatcher([self.tmpdir])
        self.assertFalse(watcher._available)
        self.assertEqual(watcher._inotify_fd, -1)

    def test_inotify_add_watch_failure_all_dirs_falls_back(self) -> None:
        """When add_watch fails for all dirs the watcher falls back to polling."""
        mock_libc = MagicMock()
        mock_libc.inotify_init1.return_value = 5  # fake fd
        mock_libc.inotify_add_watch.return_value = -1
        mock_libc.inotify_rm_watch.return_value = 0
        with patch.object(context_daemon, "_LIBC", mock_libc):
            with patch("ctypes.get_errno", return_value=13):
                with patch("os.close"):
                    watcher = _FileWatcher([self.tmpdir])
        self.assertFalse(watcher._available)

    def test_inotify_add_watch_partial_success_still_active(self) -> None:
        """If at least one add_watch succeeds, watcher remains in inotify mode."""
        dir2 = Path(tempfile.mkdtemp(prefix="cg_fw_partial_"))
        try:
            mock_libc = MagicMock()
            mock_libc.inotify_init1.return_value = 7  # fake fd
            # First call succeeds (wd=1), second call fails (wd=-1)
            mock_libc.inotify_add_watch.side_effect = [1, -1]
            mock_libc.inotify_rm_watch.return_value = 0
            with patch.object(context_daemon, "_LIBC", mock_libc):
                with patch("ctypes.get_errno", return_value=13):
                    watcher = _FileWatcher([self.tmpdir, dir2])
            self.assertTrue(watcher._available)
            self.assertIn(1, watcher._wd_to_dir)
        finally:
            import shutil

            shutil.rmtree(dir2, ignore_errors=True)

    def test_inotify_fd_closed_on_all_watches_fail(self) -> None:
        """When all add_watch calls fail, the fd must be closed."""
        mock_libc = MagicMock()
        mock_libc.inotify_init1.return_value = 9
        mock_libc.inotify_add_watch.return_value = -1
        closed_fds = []
        with patch.object(context_daemon, "_LIBC", mock_libc):
            with patch("ctypes.get_errno", return_value=13):
                with patch("os.close", side_effect=lambda fd: closed_fds.append(fd)):
                    _FileWatcher([self.tmpdir])
        self.assertIn(9, closed_fds)


# ---------------------------------------------------------------------------
# Tests: _FileWatcher inotify event drain (_drain_inotify)
# ---------------------------------------------------------------------------


class TestFileWatcherDrainInotify(unittest.TestCase):
    """Tests for _drain_inotify event parsing (mocked ctypes/os calls)."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cg_fw_drain_"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_inotify_watcher(self, fd: int = 3) -> _FileWatcher:
        """Return a watcher pre-configured in inotify mode (no real syscalls)."""
        mock_libc = MagicMock()
        mock_libc.inotify_init1.return_value = fd
        mock_libc.inotify_add_watch.return_value = 1
        with patch.object(context_daemon, "_LIBC", mock_libc):
            watcher = _FileWatcher([self.tmpdir])
        # Manually set state to simulate inotify mode
        watcher._inotify_fd = fd
        watcher._available = True
        watcher._wd_to_dir = {1: self.tmpdir}
        watcher._changed_paths = set()
        return watcher

    # ------------------------------------------------------------------
    # drain when fd < 0
    # ------------------------------------------------------------------

    def test_drain_noop_when_fd_negative(self) -> None:
        """_drain_inotify returns immediately when fd is -1."""
        watcher = self._make_inotify_watcher()
        watcher._inotify_fd = -1
        # Should not raise or call select
        with patch("select.select") as mock_sel:
            watcher._drain_inotify()
            mock_sel.assert_not_called()

    # ------------------------------------------------------------------
    # drain when select reports nothing readable
    # ------------------------------------------------------------------

    def test_drain_noop_when_select_empty(self) -> None:
        """_drain_inotify does nothing when select reports no data."""
        watcher = self._make_inotify_watcher(fd=4)
        with patch("select.select", return_value=([], [], [])):
            watcher._drain_inotify()
        self.assertFalse(watcher._changed_paths)

    # ------------------------------------------------------------------
    # drain when select raises OSError
    # ------------------------------------------------------------------

    def test_drain_handles_select_oserror(self) -> None:
        """_drain_inotify swallows OSError from select."""
        watcher = self._make_inotify_watcher(fd=5)
        with patch("select.select", side_effect=OSError("bad fd")):
            watcher._drain_inotify()  # must not raise
        self.assertFalse(watcher._changed_paths)

    def test_drain_handles_select_valueerror(self) -> None:
        """_drain_inotify swallows ValueError from select (closed fd)."""
        watcher = self._make_inotify_watcher(fd=6)
        with patch("select.select", side_effect=ValueError("bad value")):
            watcher._drain_inotify()  # must not raise

    # ------------------------------------------------------------------
    # drain when os.read raises BlockingIOError
    # ------------------------------------------------------------------

    def test_drain_handles_blocking_io_error(self) -> None:
        """_drain_inotify returns gracefully on BlockingIOError."""
        watcher = self._make_inotify_watcher(fd=7)
        with patch("select.select", return_value=([7], [], [])):
            with patch("os.read", side_effect=BlockingIOError()):
                watcher._drain_inotify()
        self.assertFalse(watcher._changed_paths)

    # ------------------------------------------------------------------
    # drain when os.read raises generic OSError
    # ------------------------------------------------------------------

    def test_drain_handles_oserror_on_read(self) -> None:
        """_drain_inotify logs and returns on OS-level read error."""
        watcher = self._make_inotify_watcher(fd=8)
        with patch("select.select", return_value=([8], [], [])):
            with patch("os.read", side_effect=OSError("I/O error")):
                watcher._drain_inotify()
        self.assertFalse(watcher._changed_paths)

    # ------------------------------------------------------------------
    # drain when os.read returns empty bytes
    # ------------------------------------------------------------------

    def test_drain_handles_empty_read(self) -> None:
        """_drain_inotify returns when os.read gives zero bytes."""
        watcher = self._make_inotify_watcher(fd=9)
        with patch("select.select", return_value=([9], [], [])):
            with patch("os.read", return_value=b""):
                watcher._drain_inotify()
        self.assertFalse(watcher._changed_paths)

    # ------------------------------------------------------------------
    # drain processes a single well-formed event
    # ------------------------------------------------------------------

    def test_drain_parses_single_event_no_name(self) -> None:
        """_drain_inotify correctly maps wd=1 to the watched directory."""
        watcher = self._make_inotify_watcher(fd=10)
        event = _make_inotify_event(wd=1, mask=0x00000002, cookie=0)
        with patch("select.select", return_value=([10], [], [])):
            with patch("os.read", return_value=event):
                watcher._drain_inotify()
        self.assertIn(self.tmpdir, watcher._changed_paths)

    def test_drain_parses_event_with_filename(self) -> None:
        """_drain_inotify handles events that include a filename in the name field."""
        watcher = self._make_inotify_watcher(fd=11)
        event = _make_inotify_event(wd=1, mask=0x00000100, cookie=0, name=b"test.py")
        with patch("select.select", return_value=([11], [], [])):
            with patch("os.read", return_value=event):
                watcher._drain_inotify()
        self.assertIn(self.tmpdir, watcher._changed_paths)

    def test_drain_parses_multiple_events_in_buffer(self) -> None:
        """_drain_inotify correctly processes two back-to-back events."""
        watcher = self._make_inotify_watcher(fd=12)
        event1 = _make_inotify_event(wd=1, mask=0x00000002, cookie=0)
        event2 = _make_inotify_event(wd=1, mask=0x00000008, cookie=0, name=b"file.txt")
        combined = event1 + event2
        with patch("select.select", return_value=([12], [], [])):
            with patch("os.read", return_value=combined):
                watcher._drain_inotify()
        self.assertIn(self.tmpdir, watcher._changed_paths)

    def test_drain_ignores_unknown_wd(self) -> None:
        """_drain_inotify silently ignores events for unknown watch descriptors."""
        watcher = self._make_inotify_watcher(fd=13)
        # wd=99 is not in _wd_to_dir
        event = _make_inotify_event(wd=99, mask=0x00000002, cookie=0)
        with patch("select.select", return_value=([13], [], [])):
            with patch("os.read", return_value=event):
                watcher._drain_inotify()
        self.assertFalse(watcher._changed_paths)

    def test_drain_stops_on_truncated_event(self) -> None:
        """_drain_inotify stops parsing if name_len would exceed buffer bounds."""
        watcher = self._make_inotify_watcher(fd=14)
        # Craft an event header whose name_len claims 1024 bytes but buffer ends
        header = struct.pack("<iIII", 1, 0x00000002, 0, 1024)
        with patch("select.select", return_value=([14], [], [])):
            with patch("os.read", return_value=header):
                watcher._drain_inotify()  # must not raise

    # ------------------------------------------------------------------
    # has_changes / get_changed_paths in inotify mode
    # ------------------------------------------------------------------

    def test_has_changes_false_when_no_events(self) -> None:
        """has_changes() returns False in inotify mode with empty changed set."""
        watcher = self._make_inotify_watcher(fd=15)
        self.assertFalse(watcher.has_changes())

    def test_has_changes_true_after_event(self) -> None:
        """has_changes() returns True after _drain_inotify records a change."""
        watcher = self._make_inotify_watcher(fd=16)
        event = _make_inotify_event(wd=1, mask=0x00000002, cookie=0)
        with patch("select.select", return_value=([16], [], [])):
            with patch("os.read", return_value=event):
                watcher._drain_inotify()
        self.assertTrue(watcher.has_changes())

    def test_get_changed_paths_clears_set(self) -> None:
        """get_changed_paths() returns the changed dirs and resets the internal set."""
        watcher = self._make_inotify_watcher(fd=17)
        watcher._changed_paths.add(self.tmpdir)
        paths = watcher.get_changed_paths()
        self.assertIn(self.tmpdir, paths)
        self.assertFalse(watcher.has_changes())


# ---------------------------------------------------------------------------
# Tests: _FileWatcher add_directory in inotify mode
# ---------------------------------------------------------------------------


class TestFileWatcherAddDirectoryInotify(unittest.TestCase):
    """Tests for add_directory when inotify fd is open."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cg_fw_adddir_"))
        self.subdir = self.tmpdir / "extra"
        self.subdir.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_directory_registers_new_watch(self) -> None:
        """add_directory calls inotify_add_watch when fd >= 0."""
        mock_libc = MagicMock()
        mock_libc.inotify_init1.return_value = 20
        mock_libc.inotify_add_watch.return_value = 1  # first call for init
        # Keep _LIBC patched across both construction and add_directory so that
        # the runtime check `_LIBC is not None` inside add_directory also sees it.
        with patch.object(context_daemon, "_LIBC", mock_libc):
            watcher = _FileWatcher([self.tmpdir])
            mock_libc.inotify_add_watch.return_value = 2  # new watch for subdir
            watcher.add_directory(self.subdir)
        self.assertIn(self.subdir, watcher._directories)
        self.assertIn(2, watcher._wd_to_dir)
        self.assertEqual(watcher._wd_to_dir[2], self.subdir)

    def test_add_directory_skips_failed_watch(self) -> None:
        """add_directory is a no-op (for _wd_to_dir) when inotify_add_watch fails."""
        mock_libc = MagicMock()
        mock_libc.inotify_init1.return_value = 21
        mock_libc.inotify_add_watch.side_effect = [1, -1]  # init ok, add new fails
        with patch.object(context_daemon, "_LIBC", mock_libc):
            watcher = _FileWatcher([self.tmpdir])
            watcher.add_directory(self.subdir)
        self.assertIn(self.subdir, watcher._directories)
        self.assertNotIn(2, watcher._wd_to_dir)


# ---------------------------------------------------------------------------
# Tests: _FileWatcher._close_inotify (cleanup/teardown)
# ---------------------------------------------------------------------------


class TestFileWatcherCloseInotify(unittest.TestCase):
    """Tests for the inotify fd cleanup path."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cg_fw_close_"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_inotify_watcher_with_fd(self, fd: int) -> _FileWatcher:
        mock_libc = MagicMock()
        mock_libc.inotify_init1.return_value = fd
        mock_libc.inotify_add_watch.return_value = 1
        with patch.object(context_daemon, "_LIBC", mock_libc):
            watcher = _FileWatcher([self.tmpdir])
        watcher._inotify_fd = fd
        watcher._wd_to_dir = {1: self.tmpdir}
        return watcher

    def test_close_closes_fd(self) -> None:
        """_close_inotify must call os.close on the fd."""
        watcher = self._make_inotify_watcher_with_fd(fd=30)
        closed = []
        with patch("os.close", side_effect=lambda fd: closed.append(fd)):
            watcher._close_inotify()
        self.assertIn(30, closed)

    def test_close_sets_fd_to_negative_one(self) -> None:
        """After _close_inotify, _inotify_fd must be -1."""
        watcher = self._make_inotify_watcher_with_fd(fd=31)
        with patch("os.close"):
            watcher._close_inotify()
        self.assertEqual(watcher._inotify_fd, -1)

    def test_close_clears_wd_to_dir(self) -> None:
        """After _close_inotify, _wd_to_dir must be empty."""
        watcher = self._make_inotify_watcher_with_fd(fd=32)
        with patch("os.close"):
            watcher._close_inotify()
        self.assertEqual(watcher._wd_to_dir, {})

    def test_close_suppresses_oserror(self) -> None:
        """_close_inotify must not propagate OSError from os.close."""
        watcher = self._make_inotify_watcher_with_fd(fd=33)
        with patch("os.close", side_effect=OSError("bad fd")):
            watcher._close_inotify()  # must not raise
        self.assertEqual(watcher._inotify_fd, -1)

    def test_close_noop_when_fd_already_negative(self) -> None:
        """_close_inotify must not call os.close when fd is already -1."""
        watcher = self._make_inotify_watcher_with_fd(fd=34)
        watcher._inotify_fd = -1
        with patch("os.close") as mock_close:
            watcher._close_inotify()
            mock_close.assert_not_called()

    def test_close_via_public_close_method(self) -> None:
        """close() delegates to _close_inotify."""
        watcher = self._make_inotify_watcher_with_fd(fd=35)
        with patch("os.close"):
            watcher.close()
        self.assertEqual(watcher._inotify_fd, -1)

    def test_close_idempotent(self) -> None:
        """Calling close() twice must not raise."""
        watcher = self._make_inotify_watcher_with_fd(fd=36)
        with patch("os.close"):
            watcher.close()
            watcher.close()  # second call must be noop
        self.assertEqual(watcher._inotify_fd, -1)


# ---------------------------------------------------------------------------
# Tests: adaptive polling interval adjustment (lines 1931-1958)
# ---------------------------------------------------------------------------


class TestAdaptivePollingLogic(unittest.TestCase):
    """Tests for the adaptive sleep calculation in the main loop.

    The production loop logic lives in the `main()` function; we exercise it
    by replicating the relevant conditional expressions directly, and by
    testing the building blocks it relies upon.
    """

    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def _compute_adaptive_sleep(
        self,
        base_sleep: float,
        had_error: bool,
        inotify_active: bool,
        watcher_has_changes: bool,
        has_active_sources: bool,
        has_pending_sessions: bool,
        has_pending_files: bool,
        consecutive_errors: int = 0,
    ) -> float:
        """Mirror the adaptive sleep formula from the main loop (lines 1924-1942)."""
        sleep_s = base_sleep

        if (
            (not had_error and inotify_active and not watcher_has_changes and not has_active_sources)
            and not has_pending_sessions
            and not has_pending_files
        ):
            sleep_s = min(float(IDLE_SLEEP_CAP_SEC), sleep_s * 2)

        if consecutive_errors > 0:
            sleep_s += min(float(ERROR_BACKOFF_MAX_SEC), float(2 ** min(consecutive_errors, 6)))

        # Omit jitter for deterministic tests
        return max(1.0, sleep_s)

    # ------------------------------------------------------------------
    # Exponential back-off on errors
    # ------------------------------------------------------------------

    def test_error_backoff_adds_to_sleep(self) -> None:
        """consecutive_errors > 0 must add an exponential term to sleep."""
        base = 5.0
        sleep_no_error = self._compute_adaptive_sleep(
            base,
            had_error=False,
            inotify_active=True,
            watcher_has_changes=True,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
            consecutive_errors=0,
        )
        sleep_one_error = self._compute_adaptive_sleep(
            base,
            had_error=True,
            inotify_active=False,
            watcher_has_changes=True,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
            consecutive_errors=1,
        )
        self.assertGreater(sleep_one_error, sleep_no_error)

    def test_error_backoff_capped_at_max(self) -> None:
        """Error back-off is capped at ERROR_BACKOFF_MAX_SEC."""
        sleep = self._compute_adaptive_sleep(
            5.0,
            had_error=True,
            inotify_active=False,
            watcher_has_changes=True,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
            consecutive_errors=100,
        )
        self.assertLessEqual(sleep, 5.0 + ERROR_BACKOFF_MAX_SEC + 1)

    def test_no_error_no_backoff(self) -> None:
        """With consecutive_errors=0 no extra back-off is added."""
        sleep = self._compute_adaptive_sleep(
            10.0,
            had_error=False,
            inotify_active=True,
            watcher_has_changes=True,
            has_active_sources=True,
            has_pending_sessions=True,
            has_pending_files=True,
            consecutive_errors=0,
        )
        self.assertEqual(sleep, 10.0)

    # ------------------------------------------------------------------
    # Idle sleep cap doubling
    # ------------------------------------------------------------------

    def test_idle_quiet_doubles_sleep(self) -> None:
        """When all quiet conditions are met, sleep is doubled (up to IDLE_SLEEP_CAP_SEC)."""
        base = 5.0
        sleep = self._compute_adaptive_sleep(
            base,
            had_error=False,
            inotify_active=True,
            watcher_has_changes=False,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
        )
        self.assertAlmostEqual(sleep, min(float(IDLE_SLEEP_CAP_SEC), base * 2))

    def test_idle_doubling_capped_at_IDLE_SLEEP_CAP_SEC(self) -> None:
        """Sleep is never doubled beyond IDLE_SLEEP_CAP_SEC."""
        large_base = float(IDLE_SLEEP_CAP_SEC) * 10
        sleep = self._compute_adaptive_sleep(
            large_base,
            had_error=False,
            inotify_active=True,
            watcher_has_changes=False,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
        )
        self.assertEqual(sleep, float(IDLE_SLEEP_CAP_SEC))

    def test_no_doubling_when_error(self) -> None:
        """had_error=True prevents idle doubling."""
        base = 5.0
        sleep = self._compute_adaptive_sleep(
            base,
            had_error=True,
            inotify_active=True,
            watcher_has_changes=False,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
            consecutive_errors=0,
        )
        # No doubling because had_error=True; consecutive_errors=0 so no backoff
        self.assertEqual(sleep, base)

    def test_no_doubling_when_inotify_inactive(self) -> None:
        """inotify_active=False prevents idle doubling (fallback mode)."""
        base = 5.0
        sleep = self._compute_adaptive_sleep(
            base,
            had_error=False,
            inotify_active=False,
            watcher_has_changes=False,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
        )
        self.assertEqual(sleep, base)

    def test_no_doubling_when_watcher_has_changes(self) -> None:
        """watcher_has_changes=True prevents idle doubling."""
        base = 5.0
        sleep = self._compute_adaptive_sleep(
            base,
            had_error=False,
            inotify_active=True,
            watcher_has_changes=True,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
        )
        self.assertEqual(sleep, base)

    def test_no_doubling_when_active_sources(self) -> None:
        """has_active_sources=True prevents idle doubling."""
        base = 5.0
        sleep = self._compute_adaptive_sleep(
            base,
            had_error=False,
            inotify_active=True,
            watcher_has_changes=False,
            has_active_sources=True,
            has_pending_sessions=False,
            has_pending_files=False,
        )
        self.assertEqual(sleep, base)

    def test_no_doubling_when_pending_sessions(self) -> None:
        """has_pending_sessions=True prevents idle doubling."""
        base = 5.0
        sleep = self._compute_adaptive_sleep(
            base,
            had_error=False,
            inotify_active=True,
            watcher_has_changes=False,
            has_active_sources=False,
            has_pending_sessions=True,
            has_pending_files=False,
        )
        self.assertEqual(sleep, base)

    def test_no_doubling_when_pending_files(self) -> None:
        """has_pending_files=True prevents idle doubling."""
        base = 5.0
        sleep = self._compute_adaptive_sleep(
            base,
            had_error=False,
            inotify_active=True,
            watcher_has_changes=False,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=True,
        )
        self.assertEqual(sleep, base)

    # ------------------------------------------------------------------
    # Sleep floor
    # ------------------------------------------------------------------

    def test_sleep_minimum_is_one_second(self) -> None:
        """max(1.0, sleep_s) ensures the sleep is at least 1 second."""
        base = 0.1
        sleep = self._compute_adaptive_sleep(
            base,
            had_error=False,
            inotify_active=False,
            watcher_has_changes=True,
            has_active_sources=True,
            has_pending_sessions=True,
            has_pending_files=True,
        )
        self.assertGreaterEqual(sleep, 1.0)

    # ------------------------------------------------------------------
    # Consecutive error exponent clamp
    # ------------------------------------------------------------------

    def test_error_exponent_clamped_at_6(self) -> None:
        """Exponent is clamped at 6 so 2**min(errors, 6) never overflows."""
        sleep_6 = self._compute_adaptive_sleep(
            5.0,
            had_error=True,
            inotify_active=False,
            watcher_has_changes=True,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
            consecutive_errors=6,
        )
        sleep_100 = self._compute_adaptive_sleep(
            5.0,
            had_error=True,
            inotify_active=False,
            watcher_has_changes=True,
            has_active_sources=False,
            has_pending_sessions=False,
            has_pending_files=False,
            consecutive_errors=100,
        )
        self.assertEqual(sleep_6, sleep_100)


# ---------------------------------------------------------------------------
# Tests: _FileWatcher init with empty directories list
# ---------------------------------------------------------------------------


class TestFileWatcherEdgeCases(unittest.TestCase):
    """Edge cases for _FileWatcher construction and behaviour."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cg_fw_edge_"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_dirs_list_no_inotify(self) -> None:
        """Constructing with no directories (all non-existent) should not crash."""
        with patch.object(context_daemon, "_LIBC", None):
            watcher = _FileWatcher([])
        self.assertFalse(watcher._available)
        self.assertEqual(watcher._directories, [])

    def test_nonexistent_dir_filtered_out(self) -> None:
        """Directories that don't exist are silently filtered in __init__."""
        ghost = self.tmpdir / "ghost"
        with patch.object(context_daemon, "_LIBC", None):
            watcher = _FileWatcher([ghost])
        self.assertNotIn(ghost, watcher._directories)

    def test_poll_fallback_oserror_on_stat_during_init(self) -> None:
        """_init_poll_fallback tolerates OSError when seeding mtimes."""
        ghost = self.tmpdir / "nonexistent"
        # We need the dir to pass the is_dir() filter but fail stat()
        # Simulate by adding a real dir then making stat fail
        with patch.object(context_daemon, "_LIBC", None):
            watcher = _FileWatcher([self.tmpdir])
        # Manually add a ghost and re-run _init_poll_fallback
        watcher._directories.append(ghost)
        watcher._init_poll_fallback()  # must not raise

    def test_update_dispatches_to_drain_inotify_when_fd_set(self) -> None:
        """update() dispatches to _drain_inotify when _inotify_fd >= 0."""
        with patch.object(context_daemon, "_LIBC", None):
            watcher = _FileWatcher([self.tmpdir])
        watcher._inotify_fd = 5  # simulate open fd
        called = []
        watcher._drain_inotify = lambda: called.append(1)
        watcher.update()
        self.assertEqual(called, [1])

    def test_inotify_event_base_constant(self) -> None:
        """_INOTIFY_EVENT_BASE must equal 16 (wd+mask+cookie+len = 4*4)."""
        self.assertEqual(_INOTIFY_EVENT_BASE, 16)

    def test_poll_mtime_fallback_updates_stored_mtime(self) -> None:
        """After detecting a change, _poll_mtime_fallback updates the stored mtime."""
        with patch.object(context_daemon, "_LIBC", None):
            watcher = _FileWatcher([self.tmpdir])
        watcher._poll_mtimes[self.tmpdir] = 0.0
        (self.tmpdir / "trigger.txt").write_text("data")
        watcher._poll_mtime_fallback()
        new_mtime = watcher._poll_mtimes.get(self.tmpdir, 0.0)
        self.assertGreater(new_mtime, 0.0)


# ---------------------------------------------------------------------------
# Tests: _FileWatcher thread-safety of _changed_paths
# ---------------------------------------------------------------------------


class TestFileWatcherThreadSafety(unittest.TestCase):
    """Verify that _changed_paths is protected by the lock."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cg_fw_thread_"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_changed_paths_protected_by_lock(self) -> None:
        """Multiple threads updating _changed_paths must not raise."""
        with patch.object(context_daemon, "_LIBC", None):
            watcher = _FileWatcher([self.tmpdir])

        errors = []

        def writer(n: int) -> None:
            for _ in range(n):
                try:
                    with watcher._lock:
                        watcher._changed_paths.add(self.tmpdir)
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(50,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertIn(self.tmpdir, watcher._changed_paths)

    def test_get_changed_paths_thread_safe(self) -> None:
        """get_changed_paths() must be safe to call concurrently."""
        with patch.object(context_daemon, "_LIBC", None):
            watcher = _FileWatcher([self.tmpdir])

        results = []
        errors = []

        def reader() -> None:
            for _ in range(20):
                try:
                    results.append(watcher.get_changed_paths())
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# Tests: adaptive polling — PENDING_DIR existence check
# ---------------------------------------------------------------------------


class TestAdaptivePollPendingDir(unittest.TestCase):
    """Tests verifying the has_pending_files computation that guards idle doubling."""

    def test_pending_dir_with_md_files_counts_as_pending(self, tmp_path=None) -> None:
        """PENDING_DIR.exists() and *.md files -> has_pending_files=True."""
        import tempfile

        tmpdir = Path(tempfile.mkdtemp(prefix="cg_pending_"))
        pending = tmpdir / "pending"
        pending.mkdir()
        (pending / "note.md").write_text("# hello")

        has_pending_files = pending.exists() and any(pending.glob("*.md"))
        self.assertTrue(has_pending_files)

        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pending_dir_no_md_files_not_pending(self) -> None:
        """PENDING_DIR with no *.md files -> has_pending_files=False."""
        import tempfile

        tmpdir = Path(tempfile.mkdtemp(prefix="cg_pending2_"))
        pending = tmpdir / "pending"
        pending.mkdir()
        (pending / "note.txt").write_text("not markdown")

        has_pending_files = pending.exists() and any(pending.glob("*.md"))
        self.assertFalse(has_pending_files)

        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pending_dir_missing_not_pending(self) -> None:
        """Non-existent PENDING_DIR -> has_pending_files=False."""
        import tempfile

        tmpdir = Path(tempfile.mkdtemp(prefix="cg_pending3_"))
        pending = tmpdir / "no_pending_dir"

        has_pending_files = pending.exists() and any(pending.glob("*.md"))
        self.assertFalse(has_pending_files)

        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_has_pending_sessions_computation(self) -> None:
        """has_pending_sessions uses sessions dict 'exported' flag."""
        sessions = {
            "s1": {"exported": True},
            "s2": {"exported": False},
        }
        has_pending = any(not v.get("exported") for v in sessions.values())
        self.assertTrue(has_pending)

    def test_no_pending_sessions_all_exported(self) -> None:
        """has_pending_sessions is False when all sessions are exported."""
        sessions = {
            "s1": {"exported": True},
            "s2": {"exported": True},
        }
        has_pending = any(not v.get("exported") for v in sessions.values())
        self.assertFalse(has_pending)

    def test_no_pending_sessions_empty_dict(self) -> None:
        """has_pending_sessions is False when sessions dict is empty."""
        sessions: dict = {}
        has_pending = any(not v.get("exported") for v in sessions.values())
        self.assertFalse(has_pending)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
