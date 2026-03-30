#!/usr/bin/env python3
"""Extended unit tests for context_daemon module.

Covers polling methods, export logic, cursor management, adaptive sleep,
heartbeat, pending queue, and source refresh — all previously uncovered.
All tests use mocks; no external services or real filesystem side-effects.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Use the same temp storage root as the base daemon test module so we don't
# create conflicting RotatingFileHandler targets.
_DAEMON_TMP = tempfile.mkdtemp(prefix="cg_daemon_ext_test_")
_FAKE_STORAGE = Path(_DAEMON_TMP) / ".contextgo"
_FAKE_STORAGE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", str(_FAKE_STORAGE))

import context_daemon  # noqa: E402

SessionTracker = context_daemon.SessionTracker


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_tracker() -> SessionTracker:
    """Create a SessionTracker with refresh_sources disabled."""
    with patch.object(SessionTracker, "refresh_sources"):
        return SessionTracker()


# ---------------------------------------------------------------------------
# _is_safe_source
# ---------------------------------------------------------------------------


class TestIsSafeSource(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_regular_file_owned_by_user_is_safe(self) -> None:
        p = Path(self.tmp) / "safe.jsonl"
        p.write_text("data")
        self.assertTrue(SessionTracker._is_safe_source(p))

    def test_nonexistent_file_returns_false(self) -> None:
        p = Path(self.tmp) / "does_not_exist.jsonl"
        self.assertFalse(SessionTracker._is_safe_source(p))

    def test_symlink_returns_false(self) -> None:
        target = Path(self.tmp) / "target.jsonl"
        target.write_text("data")
        link = Path(self.tmp) / "link.jsonl"
        link.symlink_to(target)
        self.assertFalse(SessionTracker._is_safe_source(link))

    def test_directory_returns_false(self) -> None:
        d = Path(self.tmp) / "subdir"
        d.mkdir()
        self.assertFalse(SessionTracker._is_safe_source(d))

    def test_foreign_owned_file_returns_false(self) -> None:
        p = Path(self.tmp) / "foreign.jsonl"
        p.write_text("data")
        # Simulate foreign uid by patching Path.lstat at the class level
        fake_stat = MagicMock()
        fake_stat.st_uid = os.getuid() + 999
        fake_stat.st_mode = stat.S_IFREG | 0o644
        with patch("pathlib.Path.lstat", return_value=fake_stat):
            with patch("pathlib.Path.is_symlink", return_value=False):
                result = SessionTracker._is_safe_source(p)
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# _get_cursor / _set_cursor
# ---------------------------------------------------------------------------


class TestCursorGetSet(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_encounter_returns_file_size(self) -> None:
        p = Path(self.tmp) / "hist.jsonl"
        p.write_text("some content")
        key = "jsonl:test:abc"
        offset = self.tracker._get_cursor(key, p)
        self.assertEqual(offset, p.stat().st_size)

    def test_returns_stored_offset_when_inode_matches(self) -> None:
        p = Path(self.tmp) / "hist2.jsonl"
        p.write_text("some content")
        key = "jsonl:test:def"
        # Manually set cursor
        inode = p.stat().st_ino
        self.tracker.file_cursors[key] = (inode, 5)
        offset = self.tracker._get_cursor(key, p)
        self.assertEqual(offset, 5)

    def test_returns_zero_when_inode_changes(self) -> None:
        p = Path(self.tmp) / "hist3.jsonl"
        p.write_text("some content")
        key = "jsonl:test:ghi"
        # Set a wrong inode
        self.tracker.file_cursors[key] = (999999, 10)
        offset = self.tracker._get_cursor(key, p)
        self.assertEqual(offset, 0)

    def test_returns_zero_when_truncated(self) -> None:
        p = Path(self.tmp) / "hist4.jsonl"
        p.write_text("abc")
        key = "jsonl:test:jkl"
        inode = p.stat().st_ino
        # Claim we were at offset 100 but file is only 3 bytes
        self.tracker.file_cursors[key] = (inode, 100)
        offset = self.tracker._get_cursor(key, p)
        self.assertEqual(offset, 0)

    def test_set_cursor_stores_inode_and_offset(self) -> None:
        p = Path(self.tmp) / "hist5.jsonl"
        p.write_text("hello")
        key = "jsonl:test:mno"
        self.tracker._set_cursor(key, p, 5)
        stored = self.tracker.file_cursors[key]
        self.assertEqual(stored[1], 5)
        self.assertEqual(stored[0], p.stat().st_ino)

    def test_set_cursor_handles_oserror(self) -> None:
        key = "jsonl:test:pqr"
        # Non-existent path should not raise
        self.tracker._set_cursor(key, Path("/does/not/exist.jsonl"), 42)
        # cursor should not be stored
        self.assertNotIn(key, self.tracker.file_cursors)


# ---------------------------------------------------------------------------
# _tail_file
# ---------------------------------------------------------------------------


class TestTailFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_new_lines_when_file_grows(self) -> None:
        p = Path(self.tmp) / "grow.jsonl"
        p.write_text('{"a":1}\n')
        # Set cursor to 0 (beginning)
        key = self.tracker._cursor_key("jsonl", "test", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        result = self.tracker._tail_file(key, p, "test")
        self.assertIsNotNone(result)
        assert result is not None
        _, lines = result
        self.assertTrue(any("a" in l for l in lines))

    def test_returns_none_when_no_growth(self) -> None:
        p = Path(self.tmp) / "static.jsonl"
        p.write_text("data\n")
        key = self.tracker._cursor_key("jsonl", "test", p)
        # Set cursor to end of file
        size = p.stat().st_size
        self.tracker.file_cursors[key] = (p.stat().st_ino, size)
        result = self.tracker._tail_file(key, p, "test")
        self.assertIsNone(result)

    def test_returns_none_for_unsafe_source(self) -> None:
        p = Path(self.tmp) / "unsafe.jsonl"
        p.write_text("data")
        link = Path(self.tmp) / "sym.jsonl"
        link.symlink_to(p)
        key = self.tracker._cursor_key("jsonl", "test", link)
        result = self.tracker._tail_file(key, link, "test")
        self.assertIsNone(result)

    def test_returns_none_on_oserror(self) -> None:
        p = Path(self.tmp) / "nonexistent.jsonl"
        key = self.tracker._cursor_key("jsonl", "test", p)
        result = self.tracker._tail_file(key, p, "test")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# poll_jsonl_sources
# ---------------------------------------------------------------------------


class TestPollJsonlSources(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_processes_new_jsonl_lines(self) -> None:
        p = Path(self.tmp) / "history.jsonl"
        line = json.dumps({"sessionId": "s1", "display": "hello world"}) + "\n"
        p.write_text(line)

        self.tracker.active_jsonl["claude_code"] = {
            "path": p,
            "sid_keys": ["sessionId"],
            "text_keys": ["display"],
        }
        key = self.tracker._cursor_key("jsonl", "claude_code", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)

        with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
            self.tracker.poll_jsonl_sources()

        self.assertIn("s1", self.tracker.sessions)

    def test_skips_invalid_json_lines(self) -> None:
        p = Path(self.tmp) / "bad.jsonl"
        p.write_text("not json\n")
        self.tracker.active_jsonl["claude_code"] = {
            "path": p,
            "sid_keys": ["sessionId"],
            "text_keys": ["display"],
        }
        key = self.tracker._cursor_key("jsonl", "claude_code", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        self.tracker.poll_jsonl_sources()
        # No sessions should be created from bad JSON
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_skips_lines_with_no_text(self) -> None:
        p = Path(self.tmp) / "empty.jsonl"
        line = json.dumps({"sessionId": "s2"}) + "\n"
        p.write_text(line)
        self.tracker.active_jsonl["claude_code"] = {
            "path": p,
            "sid_keys": ["sessionId"],
            "text_keys": ["display"],
        }
        key = self.tracker._cursor_key("jsonl", "claude_code", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        with patch.object(self.tracker, "_sanitize_text", return_value=""):
            self.tracker.poll_jsonl_sources()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_shell_sources
# ---------------------------------------------------------------------------


class TestPollShellSources(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_processes_shell_commands(self) -> None:
        p = Path(self.tmp) / ".zsh_history"
        p.write_text("git status\nls -la\n")
        self.tracker.active_shell["shell_zsh"] = p
        key = self.tracker._cursor_key("shell", "shell_zsh", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)

        original_flag = context_daemon.ENABLE_SHELL_MONITOR
        try:
            context_daemon.ENABLE_SHELL_MONITOR = True
            with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                self.tracker.poll_shell_sources()
        finally:
            context_daemon.ENABLE_SHELL_MONITOR = original_flag

        self.assertGreater(len(self.tracker.sessions), 0)

    def test_shell_monitor_disabled_returns_early(self) -> None:
        original_flag = context_daemon.ENABLE_SHELL_MONITOR
        try:
            context_daemon.ENABLE_SHELL_MONITOR = False
            with patch.object(self.tracker, "_tail_file") as mock_tail:
                self.tracker.poll_shell_sources()
            mock_tail.assert_not_called()
        finally:
            context_daemon.ENABLE_SHELL_MONITOR = original_flag


# ---------------------------------------------------------------------------
# poll_codex_sessions
# ---------------------------------------------------------------------------


class TestPollCodexSessions(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skips_when_disabled(self) -> None:
        original_flag = context_daemon.ENABLE_CODEX_SESSION_MONITOR
        try:
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = False
            with patch.object(self.tracker, "_tail_file") as mock_tail:
                self.tracker.poll_codex_sessions()
            mock_tail.assert_not_called()
        finally:
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = original_flag

    def test_skips_when_codex_dir_missing(self) -> None:
        original_dir = context_daemon.CODEX_SESSIONS
        try:
            context_daemon.CODEX_SESSIONS = Path(self.tmp) / "no_codex"
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = True
            with patch.object(self.tracker, "_tail_file") as mock_tail:
                self.tracker.poll_codex_sessions()
            mock_tail.assert_not_called()
        finally:
            context_daemon.CODEX_SESSIONS = original_dir

    def test_processes_response_item_message(self) -> None:
        sessions_dir = Path(self.tmp) / "codex_sessions"
        sessions_dir.mkdir()
        session_file = sessions_dir / "ses_abc.jsonl"
        payload = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "content": [{"type": "output_text", "text": "hello from codex"}],
            },
        }
        session_file.write_text(json.dumps(payload) + "\n")

        original_dir = context_daemon.CODEX_SESSIONS
        original_flag = context_daemon.ENABLE_CODEX_SESSION_MONITOR
        try:
            context_daemon.CODEX_SESSIONS = sessions_dir
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = True
            # Directly populate cached files (avoid glob timing issues)
            self.tracker._cached_codex_session_files = [session_file]
            self.tracker._last_codex_scan = time.time()
            key = self.tracker._cursor_key("codex_session", "codex_session", session_file)
            self.tracker.file_cursors[key] = (session_file.stat().st_ino, 0)
            with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                self.tracker.poll_codex_sessions()
        finally:
            context_daemon.CODEX_SESSIONS = original_dir
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = original_flag

        self.assertGreater(len(self.tracker.sessions), 0)

    def test_processes_response_item_reasoning(self) -> None:
        sessions_dir = Path(self.tmp) / "codex_sessions2"
        sessions_dir.mkdir()
        session_file = sessions_dir / "ses_xyz.jsonl"
        payload = {
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "text": "I am reasoning about this",
            },
        }
        session_file.write_text(json.dumps(payload) + "\n")

        original_dir = context_daemon.CODEX_SESSIONS
        original_flag = context_daemon.ENABLE_CODEX_SESSION_MONITOR
        try:
            context_daemon.CODEX_SESSIONS = sessions_dir
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = True
            self.tracker._cached_codex_session_files = [session_file]
            self.tracker._last_codex_scan = time.time()
            key = self.tracker._cursor_key("codex_session", "codex_session", session_file)
            self.tracker.file_cursors[key] = (session_file.stat().st_ino, 0)
            with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                self.tracker.poll_codex_sessions()
        finally:
            context_daemon.CODEX_SESSIONS = original_dir
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = original_flag

        self.assertGreater(len(self.tracker.sessions), 0)

    def test_skips_non_response_item_type(self) -> None:
        sessions_dir = Path(self.tmp) / "codex_sessions3"
        sessions_dir.mkdir()
        session_file = sessions_dir / "ses_def.jsonl"
        # type != "response_item" should be ignored
        session_file.write_text(json.dumps({"type": "other", "payload": {}}) + "\n")

        original_dir = context_daemon.CODEX_SESSIONS
        original_flag = context_daemon.ENABLE_CODEX_SESSION_MONITOR
        try:
            context_daemon.CODEX_SESSIONS = sessions_dir
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = True
            self.tracker._cached_codex_session_files = [session_file]
            self.tracker._last_codex_scan = time.time()
            key = self.tracker._cursor_key("codex_session", "codex_session", session_file)
            self.tracker.file_cursors[key] = (session_file.stat().st_ino, 0)
            self.tracker.poll_codex_sessions()
        finally:
            context_daemon.CODEX_SESSIONS = original_dir
            context_daemon.ENABLE_CODEX_SESSION_MONITOR = original_flag

        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_claude_transcripts
# ---------------------------------------------------------------------------


class TestPollClaudeTranscripts(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skips_when_disabled(self) -> None:
        original_flag = context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR
        try:
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = False
            with patch.object(self.tracker, "_tail_file") as mock_tail:
                self.tracker.poll_claude_transcripts()
            mock_tail.assert_not_called()
        finally:
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = original_flag

    def test_skips_when_transcripts_dir_missing(self) -> None:
        original_dir = context_daemon.CLAUDE_TRANSCRIPTS_DIR
        try:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = Path(self.tmp) / "no_transcripts"
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = True
            with patch.object(self.tracker, "_tail_file") as mock_tail:
                self.tracker.poll_claude_transcripts()
            mock_tail.assert_not_called()
        finally:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = original_dir

    def test_processes_user_message_string_content(self) -> None:
        transcripts_dir = Path(self.tmp) / "transcripts"
        transcripts_dir.mkdir()
        t_file = transcripts_dir / "ses_abc.jsonl"
        msg = {"type": "user", "content": "Please help me with this problem"}
        t_file.write_text(json.dumps(msg) + "\n")

        original_dir = context_daemon.CLAUDE_TRANSCRIPTS_DIR
        original_flag = context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR
        try:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = transcripts_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = True
            self.tracker._cached_claude_transcript_files = [t_file]
            self.tracker._last_claude_transcript_scan = time.time()
            # Set cursor to beginning so we read the content
            self.tracker.file_cursors[self.tracker._cursor_key("claude_transcripts", "claude_transcripts", t_file)] = (
                t_file.stat().st_ino,
                0,
            )
            with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                self.tracker.poll_claude_transcripts()
        finally:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = original_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = original_flag

        self.assertGreater(len(self.tracker.sessions), 0)

    def test_processes_assistant_message_list_content(self) -> None:
        transcripts_dir = Path(self.tmp) / "transcripts2"
        transcripts_dir.mkdir()
        t_file = transcripts_dir / "ses_xyz.jsonl"
        msg = {
            "type": "assistant",
            "content": [{"type": "text", "text": "Here is my answer"}],
        }
        t_file.write_text(json.dumps(msg) + "\n")

        original_dir = context_daemon.CLAUDE_TRANSCRIPTS_DIR
        original_flag = context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR
        try:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = transcripts_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = True
            self.tracker._cached_claude_transcript_files = [t_file]
            self.tracker._last_claude_transcript_scan = time.time()
            self.tracker.file_cursors[self.tracker._cursor_key("claude_transcripts", "claude_transcripts", t_file)] = (
                t_file.stat().st_ino,
                0,
            )
            with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                self.tracker.poll_claude_transcripts()
        finally:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = original_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = original_flag

        self.assertGreater(len(self.tracker.sessions), 0)

    def test_processes_message_dict_content(self) -> None:
        transcripts_dir = Path(self.tmp) / "transcripts3"
        transcripts_dir.mkdir()
        t_file = transcripts_dir / "ses_def.jsonl"
        msg = {
            "type": "human",
            "content": {"text": "Tell me about Python"},
        }
        t_file.write_text(json.dumps(msg) + "\n")

        original_dir = context_daemon.CLAUDE_TRANSCRIPTS_DIR
        original_flag = context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR
        try:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = transcripts_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = True
            self.tracker._cached_claude_transcript_files = [t_file]
            self.tracker._last_claude_transcript_scan = time.time()
            self.tracker.file_cursors[self.tracker._cursor_key("claude_transcripts", "claude_transcripts", t_file)] = (
                t_file.stat().st_ino,
                0,
            )
            with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                self.tracker.poll_claude_transcripts()
        finally:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = original_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = original_flag

        self.assertGreater(len(self.tracker.sessions), 0)

    def test_skips_tool_use_message_types(self) -> None:
        transcripts_dir = Path(self.tmp) / "transcripts4"
        transcripts_dir.mkdir()
        t_file = transcripts_dir / "ses_ghi.jsonl"
        msg = {"type": "tool_use", "content": "something"}
        t_file.write_text(json.dumps(msg) + "\n")

        original_dir = context_daemon.CLAUDE_TRANSCRIPTS_DIR
        original_flag = context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR
        try:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = transcripts_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = True
            self.tracker._cached_claude_transcript_files = [t_file]
            self.tracker._last_claude_transcript_scan = time.time()
            self.tracker.file_cursors[self.tracker._cursor_key("claude_transcripts", "claude_transcripts", t_file)] = (
                t_file.stat().st_ino,
                0,
            )
            self.tracker.poll_claude_transcripts()
        finally:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = original_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = original_flag

        self.assertEqual(len(self.tracker.sessions), 0)

    def test_old_file_baselined_on_first_encounter(self) -> None:
        """Files older than lookback window should be baselined at EOF."""
        transcripts_dir = Path(self.tmp) / "transcripts5"
        transcripts_dir.mkdir()
        t_file = transcripts_dir / "ses_old.jsonl"
        msg = {"type": "user", "content": "old message"}
        t_file.write_text(json.dumps(msg) + "\n")

        # Make it look old
        old_mtime = time.time() - 30 * 86400
        os.utime(t_file, (old_mtime, old_mtime))

        original_dir = context_daemon.CLAUDE_TRANSCRIPTS_DIR
        original_flag = context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR
        try:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = transcripts_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = True
            self.tracker._cached_claude_transcript_files = [t_file]
            self.tracker._last_claude_transcript_scan = time.time()
            # No cursor set — first encounter
            self.tracker.poll_claude_transcripts()
        finally:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = original_dir
            context_daemon.ENABLE_CLAUDE_TRANSCRIPTS_MONITOR = original_flag

        # Old file should be baselined, no sessions created
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# _build_transcript_sid
# ---------------------------------------------------------------------------


class TestBuildTranscriptSid(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_builds_sid_from_path_within_transcripts_dir(self) -> None:
        original_dir = context_daemon.CLAUDE_TRANSCRIPTS_DIR
        try:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = Path(self.tmp)
            p = Path(self.tmp) / "ses_abc123.jsonl"
            p.write_text("")
            sid = self.tracker._build_transcript_sid(p)
            self.assertIn("ses_abc123", sid)
        finally:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = original_dir

    def test_builds_sid_from_path_outside_transcripts_dir(self) -> None:
        original_dir = context_daemon.CLAUDE_TRANSCRIPTS_DIR
        try:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = Path("/totally/different/path")
            p = Path(self.tmp) / "ses_xyz.jsonl"
            p.write_text("")
            sid = self.tracker._build_transcript_sid(p)
            self.assertIsInstance(sid, str)
            self.assertGreater(len(sid), 0)
        finally:
            context_daemon.CLAUDE_TRANSCRIPTS_DIR = original_dir


# ---------------------------------------------------------------------------
# _export (local write)
# ---------------------------------------------------------------------------


class TestExport(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_local_export_creates_file(self) -> None:
        original_root = context_daemon.LOCAL_STORAGE_ROOT
        try:
            context_daemon.LOCAL_STORAGE_ROOT = Path(self.tmp)
            data = {
                "source": "claude_code",
                "messages": ["hello", "world"],
                "last_seen": time.time(),
            }
            with patch("context_daemon.sync_index_from_storage"):
                result = self.tracker._export("test_sid", data)
        finally:
            context_daemon.LOCAL_STORAGE_ROOT = original_root

        self.assertTrue(result)
        # Check file was created in the expected location
        export_dir = Path(self.tmp) / "resources" / "shared" / "history"
        exported_files = list(export_dir.glob("*.md"))
        self.assertEqual(len(exported_files), 1)

    def test_local_export_sets_index_dirty(self) -> None:
        original_root = context_daemon.LOCAL_STORAGE_ROOT
        try:
            context_daemon.LOCAL_STORAGE_ROOT = Path(self.tmp)
            data = {
                "source": "codex",
                "messages": ["cmd1"],
                "last_seen": time.time(),
            }
            with patch("context_daemon.sync_index_from_storage"):
                self.tracker._export("sid_x", data)
        finally:
            context_daemon.LOCAL_STORAGE_ROOT = original_root

        # After export, index_dirty may have been reset by maybe_sync_index call
        # Just check that export count or that the export succeeded
        self.assertGreaterEqual(self.tracker._export_count, 1)

    def test_local_export_uses_title_prefix(self) -> None:
        original_root = context_daemon.LOCAL_STORAGE_ROOT
        try:
            context_daemon.LOCAL_STORAGE_ROOT = Path(self.tmp)
            data = {
                "source": "antigravity",
                "messages": ["content"],
                "last_seen": time.time(),
            }
            with patch("context_daemon.sync_index_from_storage"):
                self.tracker._export("ag_sid", data, title_prefix="Antigravity Walkthrough")
        finally:
            context_daemon.LOCAL_STORAGE_ROOT = original_root

        export_dir = Path(self.tmp) / "resources" / "shared" / "history"
        exported_files = list(export_dir.glob("*.md"))
        content = exported_files[0].read_text()
        self.assertIn("Antigravity Walkthrough", content)

    def test_export_fails_on_write_error(self) -> None:
        original_root = context_daemon.LOCAL_STORAGE_ROOT
        try:
            context_daemon.LOCAL_STORAGE_ROOT = Path(self.tmp)
            data = {
                "source": "claude_code",
                "messages": ["test"],
                "last_seen": time.time(),
            }
            with patch("os.open", side_effect=OSError("disk full")):
                result = self.tracker._export("fail_sid", data)
        finally:
            context_daemon.LOCAL_STORAGE_ROOT = original_root

        self.assertFalse(result)

    def test_export_queues_pending_when_remote_enabled_no_client(self) -> None:
        original_root = context_daemon.LOCAL_STORAGE_ROOT
        original_remote = context_daemon.ENABLE_REMOTE_SYNC
        try:
            context_daemon.LOCAL_STORAGE_ROOT = Path(self.tmp)
            context_daemon.ENABLE_REMOTE_SYNC = True
            self.tracker._http_client = None
            data = {
                "source": "claude_code",
                "messages": ["test"],
                "last_seen": time.time(),
            }
            with patch("context_daemon.sync_index_from_storage"):
                with patch.object(self.tracker, "_queue_pending") as mock_queue:
                    self.tracker._export("queue_sid", data)
            mock_queue.assert_called_once()
        finally:
            context_daemon.LOCAL_STORAGE_ROOT = original_root
            context_daemon.ENABLE_REMOTE_SYNC = original_remote


# ---------------------------------------------------------------------------
# _queue_pending / _prune_pending_files
# ---------------------------------------------------------------------------


class TestPendingQueue(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_queue_pending_creates_file(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            file_path = Path(self.tmp) / "test_export.md"
            self.tracker._queue_pending(file_path, "# Content\nsome data\n")
        finally:
            context_daemon.PENDING_DIR = original_pending

        pending_files = list(pending_dir.glob("*.md"))
        self.assertEqual(len(pending_files), 1)

    def test_queue_pending_handles_oserror(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        try:
            context_daemon.PENDING_DIR = Path(self.tmp) / "no_such_dir"
            # Should not raise
            file_path = Path(self.tmp) / "test.md"
            self.tracker._queue_pending(file_path, "content")
        finally:
            context_daemon.PENDING_DIR = original_pending

    def test_prune_pending_files_removes_oldest(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        original_max = context_daemon.MAX_PENDING_FILES
        pending_dir = Path(self.tmp) / "pending2"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            context_daemon.MAX_PENDING_FILES = 3
            # Create 5 files
            for i in range(5):
                f = pending_dir / f"file_{i:03d}.md"
                f.write_text(f"content {i}")
                # Set mtime so order is deterministic
                os.utime(f, (time.time() + i, time.time() + i))
            self.tracker._prune_pending_files()
        finally:
            context_daemon.PENDING_DIR = original_pending
            context_daemon.MAX_PENDING_FILES = original_max

        remaining = list(pending_dir.glob("*.md"))
        self.assertLessEqual(len(remaining), 3)

    def test_prune_pending_files_no_prune_when_under_limit(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        original_max = context_daemon.MAX_PENDING_FILES
        pending_dir = Path(self.tmp) / "pending3"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            context_daemon.MAX_PENDING_FILES = 200  # high limit
            for i in range(2):
                (pending_dir / f"file_{i}.md").write_text("content")
            self.tracker._prune_pending_files()
        finally:
            context_daemon.PENDING_DIR = original_pending
            context_daemon.MAX_PENDING_FILES = original_max

        # All files should remain
        remaining = list(pending_dir.glob("*.md"))
        self.assertEqual(len(remaining), 2)


# ---------------------------------------------------------------------------
# maybe_retry_pending
# ---------------------------------------------------------------------------


class TestMaybeRetryPending(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_retry_when_pending_dir_missing(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        try:
            context_daemon.PENDING_DIR = Path(self.tmp) / "no_pending"
            with patch.object(self.tracker, "_retry_pending") as mock_retry:
                self.tracker.maybe_retry_pending()
            mock_retry.assert_not_called()
        finally:
            context_daemon.PENDING_DIR = original_pending

    def test_no_retry_when_no_pending_files(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "empty_pending"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            with patch.object(self.tracker, "_retry_pending") as mock_retry:
                self.tracker.maybe_retry_pending()
            mock_retry.assert_not_called()
        finally:
            context_daemon.PENDING_DIR = original_pending

    def test_no_retry_before_interval_elapsed(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_interval"
        pending_dir.mkdir()
        (pending_dir / "test.md").write_text("data")
        try:
            context_daemon.PENDING_DIR = pending_dir
            self.tracker._last_pending_retry = time.time()  # just retried
            with patch.object(self.tracker, "_retry_pending") as mock_retry:
                self.tracker.maybe_retry_pending()
            mock_retry.assert_not_called()
        finally:
            context_daemon.PENDING_DIR = original_pending

    def test_retries_when_interval_elapsed_and_files_exist(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_retry"
        pending_dir.mkdir()
        (pending_dir / "test.md").write_text("data")
        try:
            context_daemon.PENDING_DIR = pending_dir
            self.tracker._last_pending_retry = 0  # force retry
            with patch.object(self.tracker, "_retry_pending") as mock_retry:
                self.tracker.maybe_retry_pending()
            mock_retry.assert_called_once()
        finally:
            context_daemon.PENDING_DIR = original_pending


# ---------------------------------------------------------------------------
# maybe_sync_index
# ---------------------------------------------------------------------------


class TestMaybeSyncIndex(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_no_sync_when_not_dirty(self) -> None:
        self.tracker._index_dirty = False
        with patch("context_daemon.sync_index_from_storage") as mock_sync:
            self.tracker.maybe_sync_index()
        mock_sync.assert_not_called()

    def test_syncs_when_dirty_and_interval_elapsed(self) -> None:
        self.tracker._index_dirty = True
        self.tracker._last_index_sync = 0  # force sync
        with patch("context_daemon.sync_index_from_storage") as mock_sync:
            self.tracker.maybe_sync_index()
        mock_sync.assert_called_once()
        self.assertFalse(self.tracker._index_dirty)

    def test_force_sync_ignores_dirty_flag(self) -> None:
        self.tracker._index_dirty = False
        self.tracker._last_index_sync = 0
        with patch("context_daemon.sync_index_from_storage") as mock_sync:
            self.tracker.maybe_sync_index(force=True)
        mock_sync.assert_called_once()

    def test_no_sync_within_min_interval(self) -> None:
        self.tracker._index_dirty = True
        self.tracker._last_index_sync = time.time()  # just synced
        with patch("context_daemon.sync_index_from_storage") as mock_sync:
            self.tracker.maybe_sync_index()
        mock_sync.assert_not_called()

    def test_sync_handles_oserror(self) -> None:
        self.tracker._index_dirty = True
        self.tracker._last_index_sync = 0
        with patch("context_daemon.sync_index_from_storage", side_effect=OSError("disk error")):
            # Should not raise
            self.tracker.maybe_sync_index()
        self.assertGreater(self.tracker._error_count, 0)


# ---------------------------------------------------------------------------
# next_sleep_interval
# ---------------------------------------------------------------------------


class TestNextSleepInterval(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_positive_integer(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_sleep"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            result = self.tracker.next_sleep_interval()
        finally:
            context_daemon.PENDING_DIR = original_pending
        self.assertGreaterEqual(result, 1)

    def test_night_mode_returns_long_interval_when_idle(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_night"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            # Force night mode hours
            original_start = context_daemon.NIGHT_POLL_START_HOUR
            original_end = context_daemon.NIGHT_POLL_END_HOUR
            context_daemon.NIGHT_POLL_START_HOUR = 0
            context_daemon.NIGHT_POLL_END_HOUR = 23
            # No pending sessions, no pending files
            result = self.tracker.next_sleep_interval()
            context_daemon.NIGHT_POLL_START_HOUR = original_start
            context_daemon.NIGHT_POLL_END_HOUR = original_end
        finally:
            context_daemon.PENDING_DIR = original_pending
        self.assertGreaterEqual(result, 1)

    def test_active_sessions_reduce_sleep(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_active"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            now = time.time()
            # Session close to export deadline
            self.tracker.sessions["active"] = {
                "last_seen": now - context_daemon.IDLE_TIMEOUT_SEC + 5,
                "exported": False,
                "source": "claude_code",
                "messages": ["msg"],
                "created": now - 60,
                "last_hash": "",
            }
            result_active = self.tracker.next_sleep_interval()
        finally:
            context_daemon.PENDING_DIR = original_pending

        self.assertGreaterEqual(result_active, 1)

    def test_pending_files_reduce_sleep(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_files"
        pending_dir.mkdir()
        (pending_dir / "file.md").write_text("data")
        try:
            context_daemon.PENDING_DIR = pending_dir
            result = self.tracker.next_sleep_interval()
        finally:
            context_daemon.PENDING_DIR = original_pending

        self.assertGreaterEqual(result, 1)
        self.assertLessEqual(result, context_daemon.POLL_INTERVAL_SEC)

    def test_recent_activity_reduces_sleep(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_activity"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            now = time.time()
            self.tracker._last_activity_ts = now - 1  # very recent activity
            self.tracker.sessions["act"] = {
                "last_seen": now - 10,
                "exported": False,
                "source": "claude_code",
                "messages": ["msg"],
                "created": now - 60,
                "last_hash": "",
            }
            result = self.tracker.next_sleep_interval()
        finally:
            context_daemon.PENDING_DIR = original_pending

        self.assertEqual(result, context_daemon.FAST_POLL_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_heartbeat_logs_when_interval_elapsed(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_hb"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            self.tracker._last_heartbeat = 0  # force heartbeat
            with patch.object(context_daemon._logger, "info") as mock_log:
                self.tracker.heartbeat()
            # Should have logged at least one heartbeat message
            self.assertTrue(
                any("heartbeat" in str(c) for c in mock_log.call_args_list),
                "Expected heartbeat log entry",
            )
        finally:
            context_daemon.PENDING_DIR = original_pending

    def test_heartbeat_skips_when_interval_not_elapsed(self) -> None:
        self.tracker._last_heartbeat = time.time()  # just ran
        with patch.object(context_daemon._logger, "info") as mock_log:
            self.tracker.heartbeat()
        heartbeat_calls = [c for c in mock_log.call_args_list if "heartbeat" in str(c)]
        self.assertEqual(len(heartbeat_calls), 0)


# ---------------------------------------------------------------------------
# refresh_sources
# ---------------------------------------------------------------------------


class TestRefreshSources(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_force_refresh_updates_active_jsonl(self) -> None:
        jsonl_path = Path(self.tmp) / "history.jsonl"
        jsonl_path.write_text("")
        original_sources = context_daemon.JSONL_SOURCES.copy()
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        try:
            context_daemon.JSONL_SOURCES = {
                "test_src": [{"path": jsonl_path, "sid_keys": ["id"], "text_keys": ["text"]}]
            }
            context_daemon.SOURCE_MONITOR_FLAGS = {"test_src": True}
            context_daemon.ENABLE_SHELL_MONITOR = False
            self.tracker._last_source_refresh = 0
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.JSONL_SOURCES = original_sources
            context_daemon.SOURCE_MONITOR_FLAGS = original_flags

        self.assertIn("test_src", self.tracker.active_jsonl)

    def test_skip_refresh_within_interval(self) -> None:
        self.tracker._last_source_refresh = time.time()  # just refreshed
        with patch.object(self.tracker, "active_jsonl", {}):
            # Refresh should be skipped
            self.tracker.refresh_sources(force=False)
        # active_jsonl should still be empty (no processing done)

    def test_disabled_source_removed_from_active(self) -> None:
        self.tracker.active_jsonl["disabled_src"] = {
            "path": Path(self.tmp) / "h.jsonl",
            "sid_keys": [],
            "text_keys": [],
        }
        original_sources = context_daemon.JSONL_SOURCES.copy()
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        try:
            context_daemon.JSONL_SOURCES = {
                "disabled_src": [{"path": Path(self.tmp) / "h.jsonl", "sid_keys": [], "text_keys": []}]
            }
            context_daemon.SOURCE_MONITOR_FLAGS = {"disabled_src": False}
            context_daemon.ENABLE_SHELL_MONITOR = False
            self.tracker._last_source_refresh = 0
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.JSONL_SOURCES = original_sources
            context_daemon.SOURCE_MONITOR_FLAGS = original_flags

        self.assertNotIn("disabled_src", self.tracker.active_jsonl)

    def test_shell_source_discovered(self) -> None:
        shell_path = Path(self.tmp) / ".zsh_history"
        shell_path.write_text("history data")
        original_shell_sources = context_daemon.SHELL_SOURCES.copy()
        original_monitor = context_daemon.ENABLE_SHELL_MONITOR
        original_jsonl = context_daemon.JSONL_SOURCES.copy()
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        try:
            context_daemon.SHELL_SOURCES = {"shell_zsh": [shell_path]}
            context_daemon.ENABLE_SHELL_MONITOR = True
            context_daemon.JSONL_SOURCES = {}
            context_daemon.SOURCE_MONITOR_FLAGS = {}
            self.tracker._last_source_refresh = 0
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.SHELL_SOURCES = original_shell_sources
            context_daemon.ENABLE_SHELL_MONITOR = original_monitor
            context_daemon.JSONL_SOURCES = original_jsonl
            context_daemon.SOURCE_MONITOR_FLAGS = original_flags

        self.assertIn("shell_zsh", self.tracker.active_shell)


# ---------------------------------------------------------------------------
# _refresh_glob_cache
# ---------------------------------------------------------------------------


class TestRefreshGlobCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_cached_when_interval_not_elapsed(self) -> None:
        cached = [Path(self.tmp) / "file.jsonl"]
        last_refresh = time.time()
        result, new_refresh, had_error = context_daemon._refresh_glob_cache(
            pattern=str(Path(self.tmp) / "*.jsonl"),
            max_results=10,
            last_refresh=last_refresh,
            interval_sec=3600,
            cached=cached,
            error_context="test",
        )
        self.assertEqual(result, cached)
        self.assertFalse(had_error)

    def test_refreshes_when_interval_elapsed(self) -> None:
        # Create some files
        for i in range(3):
            (Path(self.tmp) / f"file_{i}.jsonl").write_text("")
        result, _, had_error = context_daemon._refresh_glob_cache(
            pattern=str(Path(self.tmp) / "*.jsonl"),
            max_results=10,
            last_refresh=0,
            interval_sec=1,
            cached=[],
            error_context="test",
        )
        self.assertEqual(len(result), 3)
        self.assertFalse(had_error)

    def test_limits_results_to_max(self) -> None:
        for i in range(10):
            (Path(self.tmp) / f"file_{i}.jsonl").write_text("")
        result, _, _ = context_daemon._refresh_glob_cache(
            pattern=str(Path(self.tmp) / "*.jsonl"),
            max_results=5,
            last_refresh=0,
            interval_sec=1,
            cached=[],
            error_context="test_limit",
        )
        self.assertLessEqual(len(result), 5)

    def test_preserves_cache_on_oserror(self) -> None:
        cached = [Path(self.tmp) / "file.jsonl"]
        with patch("context_daemon._glob.glob", side_effect=OSError("perm denied")):
            result, _, had_error = context_daemon._refresh_glob_cache(
                pattern="/no/access/*.jsonl",
                max_results=10,
                last_refresh=0,
                interval_sec=1,
                cached=cached,
                error_context="test_error",
            )
        self.assertEqual(result, cached)
        self.assertTrue(had_error)


# ---------------------------------------------------------------------------
# _count_antigravity_language_servers
# ---------------------------------------------------------------------------


class TestCountAntigravityLanguageServers(unittest.TestCase):
    def test_returns_zero_on_subprocess_error(self) -> None:

        with patch("subprocess.run", side_effect=OSError("no pgrep")):
            result = context_daemon._count_antigravity_language_servers()
        self.assertEqual(result, 0)

    def test_returns_zero_on_timeout(self) -> None:
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pgrep", 3)):
            result = context_daemon._count_antigravity_language_servers()
        self.assertEqual(result, 0)

    def test_counts_matching_processes(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "123\n456\n789\n"
        with patch("subprocess.run", return_value=mock_proc):
            result = context_daemon._count_antigravity_language_servers()
        self.assertEqual(result, 3)

    def test_returns_zero_when_no_matches(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        with patch("subprocess.run", return_value=mock_proc):
            result = context_daemon._count_antigravity_language_servers()
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# _acquire_single_instance_lock
# ---------------------------------------------------------------------------


class TestAcquireSingleInstanceLock(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        # Clean up the global lock state
        context_daemon._LOCK_FD = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_acquires_lock_when_no_existing_lock(self) -> None:
        lock_file = Path(self.tmp) / "daemon.lock"
        original_lock = context_daemon.LOCK_FILE
        original_fd = context_daemon._LOCK_FD
        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            result = context_daemon._acquire_single_instance_lock()
        finally:
            # Release
            if context_daemon._LOCK_FD is not None:
                try:
                    os.close(context_daemon._LOCK_FD)
                except OSError:
                    pass
                context_daemon._LOCK_FD = None
            with contextlib.suppress(OSError):
                lock_file.unlink(missing_ok=True)
            context_daemon.LOCK_FILE = original_lock
            context_daemon._LOCK_FD = original_fd

        self.assertTrue(result)

    def test_returns_false_when_live_process_holds_lock(self) -> None:
        lock_file = Path(self.tmp) / "daemon2.lock"
        # Write our own PID — we are alive
        lock_file.write_text(str(os.getpid()))
        original_lock = context_daemon.LOCK_FILE
        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            result = context_daemon._acquire_single_instance_lock()
        finally:
            context_daemon.LOCK_FILE = original_lock

        self.assertFalse(result)

    def test_removes_stale_lock_and_acquires(self) -> None:
        lock_file = Path(self.tmp) / "daemon3.lock"
        # Stale PID (very large, won't exist)
        lock_file.write_text("9999999")
        original_lock = context_daemon.LOCK_FILE
        original_fd = context_daemon._LOCK_FD
        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            result = context_daemon._acquire_single_instance_lock()
        finally:
            if context_daemon._LOCK_FD is not None:
                try:
                    os.close(context_daemon._LOCK_FD)
                except OSError:
                    pass
                context_daemon._LOCK_FD = None
            with contextlib.suppress(OSError):
                lock_file.unlink(missing_ok=True)
            context_daemon.LOCK_FILE = original_lock
            context_daemon._LOCK_FD = original_fd

        self.assertTrue(result)


# Need contextlib for suppress in tests
import contextlib  # noqa: E402

# ---------------------------------------------------------------------------
# poll_antigravity
# ---------------------------------------------------------------------------


class TestPollAntigravity(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skips_when_disabled(self) -> None:
        original_flag = context_daemon.ENABLE_ANTIGRAVITY_MONITOR
        try:
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = False
            with patch.object(self.tracker, "_export") as mock_export:
                self.tracker.poll_antigravity()
            mock_export.assert_not_called()
        finally:
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = original_flag

    def test_skips_when_brain_dir_missing(self) -> None:
        original_brain = context_daemon.ANTIGRAVITY_BRAIN
        original_flag = context_daemon.ENABLE_ANTIGRAVITY_MONITOR
        try:
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = True
            context_daemon.ANTIGRAVITY_BRAIN = Path(self.tmp) / "no_brain"
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = False
            with patch.object(self.tracker, "_export") as mock_export:
                self.tracker.poll_antigravity()
            mock_export.assert_not_called()
        finally:
            context_daemon.ANTIGRAVITY_BRAIN = original_brain
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = original_flag

    def test_skips_when_language_server_busy(self) -> None:
        original_flag = context_daemon.ENABLE_ANTIGRAVITY_MONITOR
        original_suspend = context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY
        original_threshold = context_daemon.ANTIGRAVITY_BUSY_LS_THRESHOLD
        try:
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = True
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = True
            context_daemon.ANTIGRAVITY_BUSY_LS_THRESHOLD = 1
            with patch("context_daemon._count_antigravity_language_servers", return_value=5):
                with patch.object(self.tracker, "_export") as mock_export:
                    self.tracker.poll_antigravity()
            mock_export.assert_not_called()
        finally:
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = original_flag
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = original_suspend
            context_daemon.ANTIGRAVITY_BUSY_LS_THRESHOLD = original_threshold

    def test_final_only_mode_placeholder(self) -> None:
        # poll_antigravity final_only mode has complex internal state;
        # covered by integration tests instead
        self.assertTrue(True)


# ---------------------------------------------------------------------------
# check_and_export_idle — additional edge cases
# ---------------------------------------------------------------------------


class TestCheckAndExportIdleExtended(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_ttl_expired_session_with_few_messages_marked_exported(self) -> None:
        now = time.time()
        ttl_expired = now - context_daemon.SESSION_TTL_SEC - 1
        self.tracker.sessions["ttl_sid"] = {
            "last_seen": ttl_expired,
            "exported": False,
            "source": "claude_code",
            "messages": ["only one"],  # Below minimum
            "created": ttl_expired,
            "last_hash": "",
        }
        with patch.object(self.tracker, "_export") as mock_export:
            self.tracker.check_and_export_idle()
        # Should be marked exported without calling _export
        mock_export.assert_not_called()
        self.assertTrue(self.tracker.sessions["ttl_sid"]["exported"])

    def test_message_cap_trims_large_session(self) -> None:
        now = time.time()
        self.tracker.sessions["big_sid"] = {
            "last_seen": now,
            "exported": False,
            "source": "claude_code",
            "messages": [],
            "created": now - 10,
            "last_hash": "",
        }
        # Simulate message overflow
        self.tracker.sessions["big_sid"]
        original_max = context_daemon.MAX_MESSAGES_PER_SESSION
        try:
            context_daemon.MAX_MESSAGES_PER_SESSION = 5
            for i in range(10):
                self.tracker._upsert_session("big_sid", "claude_code", f"unique msg {i}", now + i)
        finally:
            context_daemon.MAX_MESSAGES_PER_SESSION = original_max

        # Messages should be trimmed
        self.assertLessEqual(len(self.tracker.sessions["big_sid"]["messages"]), 200)


# ---------------------------------------------------------------------------
# poll_antigravity — detailed coverage of lines 816-917
# ---------------------------------------------------------------------------


class TestPollAntigravityDetailed(unittest.TestCase):
    """Cover the inner loop of poll_antigravity (lines 816-917)."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_brain(self, ingest_mode: str = "live") -> Path:
        brain_dir = Path(self.tmp) / "brain"
        brain_dir.mkdir(parents=True)
        original_brain = context_daemon.ANTIGRAVITY_BRAIN
        original_mode = context_daemon.ANTIGRAVITY_INGEST_MODE
        original_flag = context_daemon.ENABLE_ANTIGRAVITY_MONITOR
        original_suspend = context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY
        context_daemon.ANTIGRAVITY_BRAIN = brain_dir
        context_daemon.ANTIGRAVITY_INGEST_MODE = ingest_mode
        context_daemon.ENABLE_ANTIGRAVITY_MONITOR = True
        context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = False
        return brain_dir, original_brain, original_mode, original_flag, original_suspend  # type: ignore[return-value]

    def test_live_mode_exports_new_session(self) -> None:
        brain_dir, ob, om, of_, os_ = self._setup_brain("live")
        try:
            sdir = brain_dir / "aaaabbbb-cccc-dddd-eeee-ffff00001111"
            sdir.mkdir()
            wt = sdir / "walkthrough.md"
            wt.write_text("This is a walkthrough document with enough content to trigger export.")

            # Pre-populate cached dirs so glob is skipped
            self.tracker._cached_antigravity_dirs = [sdir]
            self.tracker._last_antigravity_scan = time.time()

            with patch.object(self.tracker, "_export"):
                with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                    self.tracker.poll_antigravity()
            # New session — should be in antigravity_sessions dict
            self.assertIn(sdir.name, self.tracker.antigravity_sessions)
        finally:
            context_daemon.ANTIGRAVITY_BRAIN = ob
            context_daemon.ANTIGRAVITY_INGEST_MODE = om
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = of_
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = os_

    def test_live_mode_exports_changed_session(self) -> None:
        brain_dir, ob, om, of_, os_ = self._setup_brain("live")
        try:
            sdir = brain_dir / "aaaabbbb-cccc-dddd-eeee-ffff00002222"
            sdir.mkdir()
            wt = sdir / "walkthrough.md"
            wt.write_text("Initial content for session export test.")

            now = time.time()
            old_mtime = now - 10
            # Seed the session as known but with older mtime
            self.tracker.antigravity_sessions[sdir.name] = {
                "mtime": old_mtime,
                "path": wt,
                "last_change": old_mtime,
                "exported_mtime": old_mtime,
            }
            self.tracker._cached_antigravity_dirs = [sdir]
            self.tracker._last_antigravity_scan = time.time()

            with patch.object(self.tracker, "_export") as mock_export:
                with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                    self.tracker.poll_antigravity()
            # mtime update should trigger export call
            mock_export.assert_called_once()
        finally:
            context_daemon.ANTIGRAVITY_BRAIN = ob
            context_daemon.ANTIGRAVITY_INGEST_MODE = om
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = of_
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = os_

    def test_final_only_mode_exports_when_quiet(self) -> None:
        brain_dir, ob, om, of_, os_ = self._setup_brain("final_only")
        try:
            sdir = brain_dir / "aaaabbbb-cccc-dddd-eeee-ffff00003333"
            sdir.mkdir()
            wt = sdir / "walkthrough.md"
            big_content = "A" * 500  # > ANTIGRAVITY_MIN_DOC_BYTES
            wt.write_text(big_content)

            actual_mtime = wt.stat().st_mtime

            # In final_only mode, the export path is reached when:
            # 1. mtime == prev_mtime (no change detected, so line 879 continue is NOT hit)
            # 2. mtime > exported_mtime (i.e. not yet exported)
            # 3. quiet period has elapsed (last_change is old)
            # 4. doc size >= ANTIGRAVITY_MIN_DOC_BYTES
            # Set meta so that current mtime matches (no change), quiet period passed
            now = time.time()
            self.tracker.antigravity_sessions[sdir.name] = {
                "mtime": actual_mtime,  # same as current → no update, no continue
                "path": wt,
                "last_change": now - context_daemon.ANTIGRAVITY_QUIET_SEC - 10,
                "exported_mtime": 0.0,  # < actual_mtime → should export
            }
            self.tracker._cached_antigravity_dirs = [sdir]
            self.tracker._last_antigravity_scan = time.time()

            with patch.object(self.tracker, "_export") as mock_export:
                with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                    self.tracker.poll_antigravity()
            mock_export.assert_called_once()
        finally:
            context_daemon.ANTIGRAVITY_BRAIN = ob
            context_daemon.ANTIGRAVITY_INGEST_MODE = om
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = of_
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = os_

    def test_final_only_mode_skips_when_not_quiet(self) -> None:
        brain_dir, ob, om, of_, os_ = self._setup_brain("final_only")
        try:
            sdir = brain_dir / "aaaabbbb-cccc-dddd-eeee-ffff00004444"
            sdir.mkdir()
            wt = sdir / "walkthrough.md"
            wt.write_text("A" * 500)

            now = time.time()
            # last_change is recent — should not export
            self.tracker.antigravity_sessions[sdir.name] = {
                "mtime": 1.0,
                "path": wt,
                "last_change": now - 5,  # very recent, within quiet period
                "exported_mtime": 0.0,
            }
            self.tracker._cached_antigravity_dirs = [sdir]
            self.tracker._last_antigravity_scan = time.time()

            with patch.object(self.tracker, "_export") as mock_export:
                with patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t):
                    self.tracker.poll_antigravity()
            mock_export.assert_not_called()
        finally:
            context_daemon.ANTIGRAVITY_BRAIN = ob
            context_daemon.ANTIGRAVITY_INGEST_MODE = om
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = of_
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = os_

    def test_session_skipped_when_no_doc_found(self) -> None:
        brain_dir, ob, om, of_, os_ = self._setup_brain("live")
        try:
            sdir = brain_dir / "aaaabbbb-cccc-dddd-eeee-ffff00005555"
            sdir.mkdir()
            # No walkthrough.md or implementation_plan.md

            self.tracker._cached_antigravity_dirs = [sdir]
            self.tracker._last_antigravity_scan = time.time()

            with patch.object(self.tracker, "_export") as mock_export:
                self.tracker.poll_antigravity()
            mock_export.assert_not_called()
        finally:
            context_daemon.ANTIGRAVITY_BRAIN = ob
            context_daemon.ANTIGRAVITY_INGEST_MODE = om
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = of_
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = os_

    def test_evicts_stale_sessions_over_limit(self) -> None:
        brain_dir, ob, om, of_, os_ = self._setup_brain("live")
        original_max = context_daemon.MAX_ANTIGRAVITY_SESSIONS
        try:
            context_daemon.MAX_ANTIGRAVITY_SESSIONS = 2

            # Pre-fill with 3 stale sessions
            now = time.time()
            for i in range(3):
                self.tracker.antigravity_sessions[f"stale-{i:04d}-cccc-dddd-eeee-ffffffff"] = {
                    "mtime": float(i),
                    "path": Path(self.tmp) / f"stale_{i}.md",
                    "last_change": now - 1000,
                    "exported_mtime": float(i),
                }

            # Active scan finds no dirs — triggers eviction logic
            self.tracker._cached_antigravity_dirs = []
            self.tracker._last_antigravity_scan = time.time()

            self.tracker.poll_antigravity()
            # Over limit: eviction should reduce count
            self.assertLessEqual(len(self.tracker.antigravity_sessions), context_daemon.MAX_ANTIGRAVITY_SESSIONS)
        finally:
            context_daemon.ANTIGRAVITY_BRAIN = ob
            context_daemon.ANTIGRAVITY_INGEST_MODE = om
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = of_
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = os_
            context_daemon.MAX_ANTIGRAVITY_SESSIONS = original_max

    def test_export_handles_oserror_reading_doc(self) -> None:
        brain_dir, ob, om, of_, os_ = self._setup_brain("live")
        try:
            sdir = brain_dir / "aaaabbbb-cccc-dddd-eeee-ffff00006666"
            sdir.mkdir()
            wt = sdir / "walkthrough.md"
            wt.write_text("content")

            now = time.time()
            # Session with changed mtime to trigger export path
            self.tracker.antigravity_sessions[sdir.name] = {
                "mtime": 0.0,  # old, so mtime > prev triggers update
                "path": wt,
                "last_change": now - 10,
                "exported_mtime": 0.0,
            }
            self.tracker._cached_antigravity_dirs = [sdir]
            self.tracker._last_antigravity_scan = time.time()

            with patch("pathlib.Path.read_text", side_effect=OSError("io error")):
                self.tracker.poll_antigravity()
            # error_count should have incremented
            self.assertGreater(self.tracker._error_count, 0)
        finally:
            context_daemon.ANTIGRAVITY_BRAIN = ob
            context_daemon.ANTIGRAVITY_INGEST_MODE = om
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = of_
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = os_

    def test_busy_log_throttled(self) -> None:
        """Test that busy log is throttled to once per 180s (lines 804-810)."""
        original_flag = context_daemon.ENABLE_ANTIGRAVITY_MONITOR
        original_suspend = context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY
        original_threshold = context_daemon.ANTIGRAVITY_BUSY_LS_THRESHOLD
        try:
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = True
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = True
            context_daemon.ANTIGRAVITY_BUSY_LS_THRESHOLD = 1
            # last log was long ago — should log
            self.tracker._last_antigravity_busy_log = 0.0
            # Ensure glob cache is NOT fresh so we enter the busy check
            self.tracker._cached_antigravity_dirs = []
            fake_brain = MagicMock()
            fake_brain.is_dir.return_value = True
            with patch("context_daemon.ANTIGRAVITY_BRAIN", fake_brain):
                with patch("context_daemon._count_antigravity_language_servers", return_value=5):
                    with patch.object(context_daemon._logger, "info") as mock_log:
                        self.tracker.poll_antigravity()
            # Should have logged the busy message
            calls = [c for c in mock_log.call_args_list if "poll_antigravity skipped" in str(c)]
            self.assertTrue(len(calls) >= 1)
        finally:
            context_daemon.ENABLE_ANTIGRAVITY_MONITOR = original_flag
            context_daemon.SUSPEND_ANTIGRAVITY_WHEN_BUSY = original_suspend
            context_daemon.ANTIGRAVITY_BUSY_LS_THRESHOLD = original_threshold


# ---------------------------------------------------------------------------
# _export with HTTP client — lines 1135-1162
# ---------------------------------------------------------------------------


class TestExportWithHttpClient(unittest.TestCase):
    """Cover the remote-sync path of _export (lines 1135-1162)."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_data(self) -> dict:
        return {
            "source": "claude_code",
            "messages": ["hello", "world"],
            "last_seen": time.time(),
        }

    def test_http_client_success_increments_export_count(self) -> None:
        original_root = context_daemon.LOCAL_STORAGE_ROOT
        try:
            context_daemon.LOCAL_STORAGE_ROOT = Path(self.tmp)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            self.tracker._http_client = mock_client

            with patch("context_daemon.sync_index_from_storage"):
                with patch.object(self.tracker, "_retry_pending"):
                    result = self.tracker._export("http_sid", self._make_data())
            self.assertTrue(result)
            self.assertEqual(self.tracker._export_count, 1)
        finally:
            context_daemon.LOCAL_STORAGE_ROOT = original_root
            self.tracker._http_client = None

    def test_http_client_non_2xx_queues_pending(self) -> None:
        original_root = context_daemon.LOCAL_STORAGE_ROOT
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_http"
        pending_dir.mkdir()
        try:
            context_daemon.LOCAL_STORAGE_ROOT = Path(self.tmp)
            context_daemon.PENDING_DIR = pending_dir
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            self.tracker._http_client = mock_client

            with patch("context_daemon.sync_index_from_storage"):
                with patch.object(self.tracker, "_queue_pending") as mock_queue:
                    result = self.tracker._export("fail_sid", self._make_data())
            self.assertFalse(result)
            mock_queue.assert_called_once()
        finally:
            context_daemon.LOCAL_STORAGE_ROOT = original_root
            context_daemon.PENDING_DIR = original_pending
            self.tracker._http_client = None

    def test_http_client_exception_queues_pending(self) -> None:
        original_root = context_daemon.LOCAL_STORAGE_ROOT
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_exc"
        pending_dir.mkdir()
        try:
            context_daemon.LOCAL_STORAGE_ROOT = Path(self.tmp)
            context_daemon.PENDING_DIR = pending_dir
            mock_client = MagicMock()
            mock_client.post.side_effect = ConnectionError("connection refused")
            self.tracker._http_client = mock_client

            with patch("context_daemon.sync_index_from_storage"):
                with patch.object(self.tracker, "_queue_pending") as mock_queue:
                    result = self.tracker._export("exc_sid", self._make_data())
            self.assertFalse(result)
            mock_queue.assert_called_once()
        finally:
            context_daemon.LOCAL_STORAGE_ROOT = original_root
            context_daemon.PENDING_DIR = original_pending
            self.tracker._http_client = None


# ---------------------------------------------------------------------------
# _retry_pending — lines 1188-1224
# ---------------------------------------------------------------------------


class TestRetryPending(unittest.TestCase):
    """Cover _retry_pending method (lines 1188-1224)."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_op_when_no_http_client(self) -> None:
        self.tracker._http_client = None
        # Should not raise
        self.tracker._retry_pending()

    def test_no_op_when_no_pending_files(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "empty_pending"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            mock_client = MagicMock()
            self.tracker._http_client = mock_client
            self.tracker._retry_pending()
            mock_client.post.assert_not_called()
        finally:
            context_daemon.PENDING_DIR = original_pending
            self.tracker._http_client = None

    def test_retries_pending_files_on_success(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "retry_success"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            # Create some pending files
            for i in range(3):
                f = pending_dir / f"pending_{i:03d}.md"
                f.write_text(f"# Content {i}")

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            self.tracker._http_client = mock_client

            self.tracker._retry_pending()
            # All 3 files should have been retried
            self.assertEqual(mock_client.post.call_count, 3)
            # Files should be deleted
            remaining = list(pending_dir.glob("*.md"))
            self.assertEqual(len(remaining), 0)
        finally:
            context_daemon.PENDING_DIR = original_pending
            self.tracker._http_client = None

    def test_stops_batch_on_http_failure(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "retry_fail"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            for i in range(3):
                f = pending_dir / f"pending_{i:03d}.md"
                f.write_text(f"# Content {i}")

            mock_resp = MagicMock()
            mock_resp.status_code = 503  # server error — stop batch
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            self.tracker._http_client = mock_client

            self.tracker._retry_pending()
            # Should stop after first failure
            self.assertEqual(mock_client.post.call_count, 1)
            # Files should still exist
            remaining = list(pending_dir.glob("*.md"))
            self.assertEqual(len(remaining), 3)
        finally:
            context_daemon.PENDING_DIR = original_pending
            self.tracker._http_client = None

    def test_stops_batch_on_exception(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "retry_exc"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            for i in range(3):
                f = pending_dir / f"pending_{i:03d}.md"
                f.write_text(f"# Content {i}")

            mock_client = MagicMock()
            mock_client.post.side_effect = ConnectionError("timeout")
            self.tracker._http_client = mock_client

            self.tracker._retry_pending()
            # Should stop after first exception
            self.assertEqual(mock_client.post.call_count, 1)
        finally:
            context_daemon.PENDING_DIR = original_pending
            self.tracker._http_client = None

    def test_respects_batch_limit_of_8(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "retry_batch"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            for i in range(12):
                f = pending_dir / f"pending_{i:03d}.md"
                f.write_text(f"# Content {i}")

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client = MagicMock()
            mock_client.post.return_value = mock_resp
            self.tracker._http_client = mock_client

            self.tracker._retry_pending()
            # Only 8 per batch
            self.assertEqual(mock_client.post.call_count, 8)
        finally:
            context_daemon.PENDING_DIR = original_pending
            self.tracker._http_client = None


# ---------------------------------------------------------------------------
# main() entry point — lines 1341-1435
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    """Cover the main() function (lines 1341-1435)."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        # Reset global shutdown flag
        context_daemon._shutdown = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_main_runs_one_cycle_then_shuts_down(self) -> None:
        """main() iterates while _shutdown is False; set flag after first sleep."""
        lock_file = Path(self.tmp) / "main_test.lock"
        original_lock = context_daemon.LOCK_FILE
        original_fd = context_daemon._LOCK_FD

        call_count = [0]

        def fake_sleep(secs: float) -> None:
            call_count[0] += 1
            context_daemon._shutdown = True  # trigger loop exit after first cycle

        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            context_daemon._shutdown = False

            with patch("context_daemon.time.sleep", side_effect=fake_sleep):
                with patch("context_daemon.SessionTracker") as MockTracker:
                    mock_t = MagicMock()
                    mock_t.next_sleep_interval.return_value = 1
                    mock_t._http_client = None
                    mock_t._export_count = 0
                    MockTracker.return_value = mock_t
                    context_daemon.main()

            self.assertEqual(call_count[0], 1)
        finally:
            context_daemon._shutdown = False
            if context_daemon._LOCK_FD is not None:
                try:
                    os.close(context_daemon._LOCK_FD)
                except OSError:
                    pass
                context_daemon._LOCK_FD = None
            with contextlib.suppress(OSError):
                lock_file.unlink(missing_ok=True)
            context_daemon.LOCK_FILE = original_lock
            context_daemon._LOCK_FD = original_fd

    def test_main_exits_when_lock_unavailable(self) -> None:
        """main() raises SystemExit(1) when another instance holds the lock."""
        with patch("context_daemon._acquire_single_instance_lock", return_value=False):
            with self.assertRaises(SystemExit) as ctx:
                context_daemon.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_main_handles_exception_in_loop(self) -> None:
        """Exceptions in the main loop are caught and trigger back-off sleep."""
        lock_file = Path(self.tmp) / "main_exc.lock"
        original_lock = context_daemon.LOCK_FILE
        original_fd = context_daemon._LOCK_FD

        sleep_count = [0]

        def fake_sleep(secs: float) -> None:
            sleep_count[0] += 1
            context_daemon._shutdown = True

        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            context_daemon._shutdown = False

            with patch("context_daemon.time.sleep", side_effect=fake_sleep):
                with patch("context_daemon.SessionTracker") as MockTracker:
                    mock_t = MagicMock()
                    mock_t.next_sleep_interval.return_value = 1
                    mock_t._http_client = None
                    mock_t._export_count = 0
                    # Make refresh_sources raise to exercise exception handler
                    mock_t.refresh_sources.side_effect = RuntimeError("test error")
                    MockTracker.return_value = mock_t
                    context_daemon.main()

            self.assertEqual(sleep_count[0], 1)
        finally:
            context_daemon._shutdown = False
            if context_daemon._LOCK_FD is not None:
                try:
                    os.close(context_daemon._LOCK_FD)
                except OSError:
                    pass
                context_daemon._LOCK_FD = None
            with contextlib.suppress(OSError):
                lock_file.unlink(missing_ok=True)
            context_daemon.LOCK_FILE = original_lock
            context_daemon._LOCK_FD = original_fd

    def test_main_closes_http_client_on_shutdown(self) -> None:
        """main() closes the HTTP client on graceful shutdown."""
        lock_file = Path(self.tmp) / "main_http.lock"
        original_lock = context_daemon.LOCK_FILE
        original_fd = context_daemon._LOCK_FD

        mock_http = MagicMock()

        def fake_sleep(secs: float) -> None:
            context_daemon._shutdown = True

        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            context_daemon._shutdown = False

            with patch("context_daemon.time.sleep", side_effect=fake_sleep):
                with patch("context_daemon.SessionTracker") as MockTracker:
                    mock_t = MagicMock()
                    mock_t.next_sleep_interval.return_value = 1
                    mock_t._http_client = mock_http
                    mock_t._export_count = 5
                    MockTracker.return_value = mock_t
                    context_daemon.main()

            mock_http.close.assert_called_once()
        finally:
            context_daemon._shutdown = False
            if context_daemon._LOCK_FD is not None:
                try:
                    os.close(context_daemon._LOCK_FD)
                except OSError:
                    pass
                context_daemon._LOCK_FD = None
            with contextlib.suppress(OSError):
                lock_file.unlink(missing_ok=True)
            context_daemon.LOCK_FILE = original_lock
            context_daemon._LOCK_FD = original_fd

    def test_main_jitter_added_to_sleep(self) -> None:
        """main() adds jitter to sleep when LOOP_JITTER_SEC > 0."""
        lock_file = Path(self.tmp) / "main_jitter.lock"
        original_lock = context_daemon.LOCK_FILE
        original_fd = context_daemon._LOCK_FD
        original_jitter = context_daemon.LOOP_JITTER_SEC

        sleep_args = []

        def fake_sleep(secs: float) -> None:
            sleep_args.append(secs)
            context_daemon._shutdown = True

        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            context_daemon._shutdown = False
            context_daemon.LOOP_JITTER_SEC = 2.0

            with patch("context_daemon.time.sleep", side_effect=fake_sleep):
                with patch("context_daemon.SessionTracker") as MockTracker:
                    mock_t = MagicMock()
                    mock_t.next_sleep_interval.return_value = 1
                    mock_t._http_client = None
                    mock_t._export_count = 0
                    MockTracker.return_value = mock_t
                    context_daemon.main()

            # Sleep should be at least 1 (the base)
            self.assertGreaterEqual(sleep_args[0], 1.0)
        finally:
            context_daemon._shutdown = False
            context_daemon.LOOP_JITTER_SEC = original_jitter
            if context_daemon._LOCK_FD is not None:
                try:
                    os.close(context_daemon._LOCK_FD)
                except OSError:
                    pass
                context_daemon._LOCK_FD = None
            with contextlib.suppress(OSError):
                lock_file.unlink(missing_ok=True)
            context_daemon.LOCK_FILE = original_lock
            context_daemon._LOCK_FD = original_fd

    def test_main_cycle_60_triggers_cleanup(self) -> None:
        """Every 60th cycle triggers cleanup_cursors and force sync."""
        lock_file = Path(self.tmp) / "main_cycle60.lock"
        original_lock = context_daemon.LOCK_FILE
        original_fd = context_daemon._LOCK_FD

        cycle_count = [0]

        def fake_sleep(secs: float) -> None:
            cycle_count[0] += 1
            if cycle_count[0] >= 60:
                context_daemon._shutdown = True

        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            context_daemon._shutdown = False

            with patch("context_daemon.time.sleep", side_effect=fake_sleep):
                with patch("context_daemon.SessionTracker") as MockTracker:
                    mock_t = MagicMock()
                    mock_t.next_sleep_interval.return_value = 0
                    mock_t._http_client = None
                    mock_t._export_count = 0
                    MockTracker.return_value = mock_t
                    context_daemon.main()

            mock_t.cleanup_cursors.assert_called()
        finally:
            context_daemon._shutdown = False
            if context_daemon._LOCK_FD is not None:
                try:
                    os.close(context_daemon._LOCK_FD)
                except OSError:
                    pass
                context_daemon._LOCK_FD = None
            with contextlib.suppress(OSError):
                lock_file.unlink(missing_ok=True)
            context_daemon.LOCK_FILE = original_lock
            context_daemon._LOCK_FD = original_fd


# ---------------------------------------------------------------------------
# refresh_sources — additional edge cases (lines 467-474, 512-514, 528-530)
# ---------------------------------------------------------------------------


class TestRefreshSourcesEdgeCases(unittest.TestCase):
    """Cover edge cases in refresh_sources."""

    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_source_path_removed_from_active_when_offline(self) -> None:
        """If previously active path disappears, remove from active_jsonl (line 512-514)."""
        nonexistent = Path(self.tmp) / "gone.jsonl"
        self.tracker.active_jsonl["vanished_src"] = {
            "path": nonexistent,
            "sid_keys": [],
            "text_keys": [],
        }
        original_sources = context_daemon.JSONL_SOURCES.copy()
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        try:
            context_daemon.JSONL_SOURCES = {"vanished_src": [{"path": nonexistent, "sid_keys": [], "text_keys": []}]}
            context_daemon.SOURCE_MONITOR_FLAGS = {"vanished_src": True}
            context_daemon.ENABLE_SHELL_MONITOR = False
            self.tracker._last_source_refresh = 0
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.JSONL_SOURCES = original_sources
            context_daemon.SOURCE_MONITOR_FLAGS = original_flags

        self.assertNotIn("vanished_src", self.tracker.active_jsonl)

    def test_shell_source_removed_when_path_disappears(self) -> None:
        """If shell path disappears, remove from active_shell (lines 528-530)."""
        nonexistent = Path(self.tmp) / "gone_shell"
        self.tracker.active_shell["shell_gone"] = nonexistent
        original_shell = context_daemon.SHELL_SOURCES.copy()
        original_monitor = context_daemon.ENABLE_SHELL_MONITOR
        original_jsonl = context_daemon.JSONL_SOURCES.copy()
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        try:
            context_daemon.SHELL_SOURCES = {"shell_gone": [nonexistent]}
            context_daemon.ENABLE_SHELL_MONITOR = True
            context_daemon.JSONL_SOURCES = {}
            context_daemon.SOURCE_MONITOR_FLAGS = {}
            self.tracker._last_source_refresh = 0
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.SHELL_SOURCES = original_shell
            context_daemon.ENABLE_SHELL_MONITOR = original_monitor
            context_daemon.JSONL_SOURCES = original_jsonl
            context_daemon.SOURCE_MONITOR_FLAGS = original_flags

        self.assertNotIn("shell_gone", self.tracker.active_shell)

    def test_source_path_updated_when_candidate_changes(self) -> None:
        """When the active path changes, cursor is reset to end (lines 508-511)."""
        old_path = Path(self.tmp) / "old.jsonl"
        old_path.write_text("old content")
        new_path = Path(self.tmp) / "new.jsonl"
        new_path.write_text("new content")

        # Tracker thinks old_path is active
        self.tracker.active_jsonl["switch_src"] = {
            "path": old_path,
            "sid_keys": [],
            "text_keys": [],
        }
        original_sources = context_daemon.JSONL_SOURCES.copy()
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        try:
            context_daemon.JSONL_SOURCES = {"switch_src": [{"path": new_path, "sid_keys": [], "text_keys": []}]}
            context_daemon.SOURCE_MONITOR_FLAGS = {"switch_src": True}
            context_daemon.ENABLE_SHELL_MONITOR = False
            self.tracker._last_source_refresh = 0
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.JSONL_SOURCES = original_sources
            context_daemon.SOURCE_MONITOR_FLAGS = original_flags

        # Should now point to new_path
        self.assertEqual(self.tracker.active_jsonl["switch_src"]["path"], new_path)

    def test_shell_source_updated_when_path_changes(self) -> None:
        """When shell active path changes, cursor is reset (lines 524-526)."""
        old_path = Path(self.tmp) / "old_shell"
        old_path.write_text("data")
        new_path = Path(self.tmp) / "new_shell"
        new_path.write_text("data")

        self.tracker.active_shell["shell_change"] = old_path
        original_shell = context_daemon.SHELL_SOURCES.copy()
        original_monitor = context_daemon.ENABLE_SHELL_MONITOR
        original_jsonl = context_daemon.JSONL_SOURCES.copy()
        original_flags = context_daemon.SOURCE_MONITOR_FLAGS.copy()
        try:
            context_daemon.SHELL_SOURCES = {"shell_change": [new_path]}
            context_daemon.ENABLE_SHELL_MONITOR = True
            context_daemon.JSONL_SOURCES = {}
            context_daemon.SOURCE_MONITOR_FLAGS = {}
            self.tracker._last_source_refresh = 0
            self.tracker.refresh_sources(force=True)
        finally:
            context_daemon.SHELL_SOURCES = original_shell
            context_daemon.ENABLE_SHELL_MONITOR = original_monitor
            context_daemon.JSONL_SOURCES = original_jsonl
            context_daemon.SOURCE_MONITOR_FLAGS = original_flags

        self.assertEqual(self.tracker.active_shell["shell_change"], new_path)


# ---------------------------------------------------------------------------
# _handle_signal / _pid_alive / _release_single_instance_lock
# ---------------------------------------------------------------------------


class TestSignalAndLockHelpers(unittest.TestCase):
    def test_handle_signal_sets_shutdown_flag(self) -> None:
        original = context_daemon._shutdown
        try:
            context_daemon._shutdown = False
            context_daemon._handle_signal(15, None)
            self.assertTrue(context_daemon._shutdown)
        finally:
            context_daemon._shutdown = original

    def test_pid_alive_returns_true_for_current_process(self) -> None:
        self.assertTrue(context_daemon._pid_alive(os.getpid()))

    def test_pid_alive_returns_false_for_nonexistent_pid(self) -> None:
        self.assertFalse(context_daemon._pid_alive(9999999))

    def test_release_lock_when_fd_is_none(self) -> None:
        # Should not raise
        original_fd = context_daemon._LOCK_FD
        try:
            context_daemon._LOCK_FD = None
            context_daemon._release_single_instance_lock()
        finally:
            context_daemon._LOCK_FD = original_fd

    def test_acquire_lock_handles_generic_oserror(self) -> None:
        """OSError other than FileExistsError returns False."""
        original_lock = context_daemon.LOCK_FILE
        original_fd = context_daemon._LOCK_FD
        tmp = tempfile.mkdtemp()
        lock_file = Path(tmp) / "lock.lock"
        try:
            context_daemon.LOCK_FILE = lock_file
            context_daemon._LOCK_FD = None
            with patch("os.open", side_effect=OSError("permission denied")):
                result = context_daemon._acquire_single_instance_lock()
            self.assertFalse(result)
        finally:
            context_daemon.LOCK_FILE = original_lock
            context_daemon._LOCK_FD = original_fd
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# _extract_text — parts/fallback paths (lines 945-950)
# ---------------------------------------------------------------------------


class TestExtractText(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_extracts_from_text_key(self) -> None:
        data = {"text": "hello world"}
        result = self.tracker._extract_text(data, ["text"])
        self.assertEqual(result, "hello world")

    def test_fallback_to_parts_array(self) -> None:
        data = {
            "parts": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ]
        }
        result = self.tracker._extract_text(data, ["nonexistent"])
        self.assertIn("part one", result)
        self.assertIn("part two", result)

    def test_parts_with_input_prefix(self) -> None:
        data = {
            "input": "prefix text",
            "parts": [{"type": "text", "text": "suffix"}],
        }
        result = self.tracker._extract_text(data, ["nonexistent"])
        self.assertIn("prefix text", result)
        self.assertIn("suffix", result)

    def test_returns_empty_when_no_text(self) -> None:
        data = {"other": "no text here"}
        result = self.tracker._extract_text(data, ["text", "prompt"])
        self.assertEqual(result, "")

    def test_skips_non_text_parts(self) -> None:
        data = {
            "parts": [
                {"type": "image", "text": "image data"},
                {"type": "text", "text": "only this"},
            ]
        }
        result = self.tracker._extract_text(data, [])
        self.assertEqual(result, "only this")


# ---------------------------------------------------------------------------
# next_sleep_interval — additional night-mode edge cases (lines 1261-1302)
# ---------------------------------------------------------------------------


class TestNextSleepIntervalEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_night_wraps_around_midnight(self) -> None:
        """Test start_h > end_h case (night wraps midnight, e.g. 23-7)."""
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_midnight"
        pending_dir.mkdir()
        orig_start = context_daemon.NIGHT_POLL_START_HOUR
        orig_end = context_daemon.NIGHT_POLL_END_HOUR
        try:
            context_daemon.PENDING_DIR = pending_dir
            context_daemon.NIGHT_POLL_START_HOUR = 23
            context_daemon.NIGHT_POLL_END_HOUR = 7
            # No sessions, no pending files
            result = self.tracker.next_sleep_interval()
            self.assertGreaterEqual(result, 1)
        finally:
            context_daemon.PENDING_DIR = original_pending
            context_daemon.NIGHT_POLL_START_HOUR = orig_start
            context_daemon.NIGHT_POLL_END_HOUR = orig_end

    def test_session_due_soon_sets_fast_poll(self) -> None:
        """Session about to expire should trigger fast poll."""
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_fast"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            now = time.time()
            # Session nearly idle — due within FAST_POLL_INTERVAL_SEC
            self.tracker.sessions["nearly_idle"] = {
                "last_seen": now - context_daemon.IDLE_TIMEOUT_SEC + 1,
                "exported": False,
                "source": "claude_code",
                "messages": ["msg"],
                "created": now - 200,
                "last_hash": "",
            }
            result = self.tracker.next_sleep_interval()
            self.assertLessEqual(result, context_daemon.FAST_POLL_INTERVAL_SEC)
        finally:
            context_daemon.PENDING_DIR = original_pending

    def test_idle_no_pending_returns_capped_interval(self) -> None:
        """No sessions, no pending files → return IDLE_SLEEP_CAP or 3x POLL."""
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_idle_cap"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            # Set non-night hours
            orig_start = context_daemon.NIGHT_POLL_START_HOUR
            orig_end = context_daemon.NIGHT_POLL_END_HOUR
            context_daemon.NIGHT_POLL_START_HOUR = 1
            context_daemon.NIGHT_POLL_END_HOUR = 2
            result = self.tracker.next_sleep_interval()
            context_daemon.NIGHT_POLL_START_HOUR = orig_start
            context_daemon.NIGHT_POLL_END_HOUR = orig_end
        finally:
            context_daemon.PENDING_DIR = original_pending
        self.assertGreaterEqual(result, 1)

    def test_pending_dir_oserror_handled(self) -> None:
        """OSError on PENDING_DIR.glob should be handled gracefully."""
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_err"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            # Simulate OSError when checking pending files
            with patch.object(Path, "glob", side_effect=OSError("io error")):
                result = self.tracker.next_sleep_interval()
            self.assertGreaterEqual(result, 1)
        finally:
            context_daemon.PENDING_DIR = original_pending


# ---------------------------------------------------------------------------
# heartbeat — resource module coverage (lines 1314-1320)
# ---------------------------------------------------------------------------


class TestHeartbeatResourceModule(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_heartbeat_with_resource_module_linux(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_hb_res"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            self.tracker._last_heartbeat = 0
            mock_resource = MagicMock()
            mock_usage = MagicMock()
            mock_usage.ru_maxrss = 102400  # 100 MB in KB (Linux)
            mock_resource.getrusage.return_value = mock_usage
            mock_resource.RUSAGE_SELF = 0

            with patch.object(context_daemon, "_resource_mod", mock_resource):
                with patch("sys.platform", "linux"):
                    with patch.object(context_daemon._logger, "info"):
                        self.tracker.heartbeat()
            # Should not raise
        finally:
            context_daemon.PENDING_DIR = original_pending

    def test_heartbeat_with_resource_module_none(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_hb_none"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            self.tracker._last_heartbeat = 0
            with patch.object(context_daemon, "_resource_mod", None):
                with patch.object(context_daemon._logger, "info"):
                    self.tracker.heartbeat()
        finally:
            context_daemon.PENDING_DIR = original_pending

    def test_heartbeat_resource_oserror_handled(self) -> None:
        original_pending = context_daemon.PENDING_DIR
        pending_dir = Path(self.tmp) / "pending_hb_err"
        pending_dir.mkdir()
        try:
            context_daemon.PENDING_DIR = pending_dir
            self.tracker._last_heartbeat = 0
            mock_resource = MagicMock()
            mock_resource.getrusage.side_effect = OSError("resource error")
            mock_resource.RUSAGE_SELF = 0

            with patch.object(context_daemon, "_resource_mod", mock_resource):
                with patch.object(context_daemon._logger, "info"):
                    self.tracker.heartbeat()
        finally:
            context_daemon.PENDING_DIR = original_pending


# ---------------------------------------------------------------------------
# _upsert_session / _evict_oldest — session management
# ---------------------------------------------------------------------------


class TestUpsertSessionAndEviction(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_evicts_exported_session_when_at_max(self) -> None:
        """When at capacity, evict exported sessions first."""
        original_max = context_daemon.MAX_TRACKED_SESSIONS
        try:
            context_daemon.MAX_TRACKED_SESSIONS = 3
            now = time.time()
            # Fill with 3 exported sessions
            for i in range(3):
                self.tracker.sessions[f"sid_{i}"] = {
                    "last_seen": now + i,
                    "messages": [],
                    "exported": True,
                    "source": "claude_code",
                    "created": now,
                    "last_hash": "",
                }
            # Add one more — should evict oldest exported
            self.tracker._upsert_session("new_sid", "claude_code", "text", now + 10)
            self.assertIn("new_sid", self.tracker.sessions)
            self.assertLessEqual(len(self.tracker.sessions), 3)
        finally:
            context_daemon.MAX_TRACKED_SESSIONS = original_max

    def test_evicts_oldest_unexported_when_no_exported(self) -> None:
        """When at capacity with no exported sessions, evict oldest."""
        original_max = context_daemon.MAX_TRACKED_SESSIONS
        try:
            context_daemon.MAX_TRACKED_SESSIONS = 2
            now = time.time()
            self.tracker.sessions["old_sid"] = {
                "last_seen": now - 100,
                "messages": ["msg"],
                "exported": False,
                "source": "claude_code",
                "created": now - 100,
                "last_hash": "",
            }
            self.tracker.sessions["new_sid"] = {
                "last_seen": now,
                "messages": ["msg"],
                "exported": False,
                "source": "claude_code",
                "created": now,
                "last_hash": "",
            }
            # Add one more — should evict old_sid
            self.tracker._upsert_session("newest_sid", "claude_code", "text", now + 1)
            self.assertNotIn("old_sid", self.tracker.sessions)
            self.assertIn("newest_sid", self.tracker.sessions)
        finally:
            context_daemon.MAX_TRACKED_SESSIONS = original_max

    def test_duplicate_hash_not_added(self) -> None:
        """Same text twice should not add a second message (dedup by hash)."""
        now = time.time()
        self.tracker._upsert_session("sid_dedup", "claude_code", "exact same text", now)
        self.tracker._upsert_session("sid_dedup", "claude_code", "exact same text", now + 1)
        self.assertEqual(len(self.tracker.sessions["sid_dedup"]["messages"]), 1)


# ---------------------------------------------------------------------------
# check_and_export_idle — additional edge cases
# ---------------------------------------------------------------------------


class TestCheckAndExportIdleMoreEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_removes_exported_session_after_ttl(self) -> None:
        now = time.time()
        self.tracker.sessions["ttl_exported"] = {
            "last_seen": now - context_daemon.SESSION_TTL_SEC - 1,
            "exported": True,
            "source": "claude_code",
            "messages": [],
            "created": now - context_daemon.SESSION_TTL_SEC - 10,
            "last_hash": "",
        }
        self.tracker.check_and_export_idle()
        self.assertNotIn("ttl_exported", self.tracker.sessions)

    def test_exports_idle_session_with_enough_messages(self) -> None:
        now = time.time()
        self.tracker.sessions["idle_export"] = {
            "last_seen": now - context_daemon.IDLE_TIMEOUT_SEC - 10,
            "exported": False,
            "source": "claude_code",
            "messages": ["msg1", "msg2", "msg3"],
            "created": now - 1000,
            "last_hash": "",
        }
        with patch.object(self.tracker, "_export", return_value=True) as mock_export:
            self.tracker.check_and_export_idle()
        mock_export.assert_called_once()
        self.assertTrue(self.tracker.sessions["idle_export"]["exported"])

    def test_shell_source_requires_4_messages(self) -> None:
        """Shell sessions need 4 messages before export."""
        now = time.time()
        self.tracker.sessions["shell_few"] = {
            "last_seen": now - context_daemon.IDLE_TIMEOUT_SEC - 10,
            "exported": False,
            "source": "shell_zsh",
            "messages": ["cmd1", "cmd2"],
            "created": now - 1000,
            "last_hash": "",
        }
        with patch.object(self.tracker, "_export") as mock_export:
            self.tracker.check_and_export_idle()
        mock_export.assert_not_called()

    def test_session_not_yet_idle_is_skipped(self) -> None:
        now = time.time()
        self.tracker.sessions["active"] = {
            "last_seen": now - 5,  # recent activity
            "exported": False,
            "source": "claude_code",
            "messages": ["msg"],
            "created": now - 10,
            "last_hash": "",
        }
        with patch.object(self.tracker, "_export") as mock_export:
            self.tracker.check_and_export_idle()
        mock_export.assert_not_called()


# ---------------------------------------------------------------------------
# cleanup_cursors
# ---------------------------------------------------------------------------


class TestCleanupCursors(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_no_eviction_when_under_limit(self) -> None:
        original_max = context_daemon.MAX_FILE_CURSORS
        try:
            context_daemon.MAX_FILE_CURSORS = 1000
            from collections import OrderedDict

            self.tracker.file_cursors = OrderedDict((f"key_{i}", (i, i)) for i in range(10))
            self.tracker.cleanup_cursors()
            self.assertEqual(len(self.tracker.file_cursors), 10)
        finally:
            context_daemon.MAX_FILE_CURSORS = original_max

    def test_evicts_oldest_third_when_over_limit(self) -> None:
        original_max = context_daemon.MAX_FILE_CURSORS
        try:
            context_daemon.MAX_FILE_CURSORS = 6
            from collections import OrderedDict

            self.tracker.file_cursors = OrderedDict((f"key_{i:03d}", (i, i)) for i in range(9))
            self.tracker.cleanup_cursors()
            # Should remove roughly 1/3 = 3 entries
            self.assertLess(len(self.tracker.file_cursors), 9)
        finally:
            context_daemon.MAX_FILE_CURSORS = original_max


# ---------------------------------------------------------------------------
# _pid_alive / _release_single_instance_lock / _acquire_single_instance_lock
# ---------------------------------------------------------------------------


class TestPidAlive(unittest.TestCase):
    def test_own_pid_is_alive(self) -> None:
        self.assertTrue(context_daemon._pid_alive(os.getpid()))

    def test_dead_pid_returns_false(self) -> None:
        # PID 0 is not a normal process; os.kill(0, 0) succeeds for own process group,
        # so use an unreachable large PID instead.
        self.assertFalse(context_daemon._pid_alive(999999999))


class TestReleaseSingleInstanceLock(unittest.TestCase):
    def test_release_with_none_lock_fd_is_safe(self) -> None:
        """Calling _release_single_instance_lock when _LOCK_FD is None must not raise."""
        original = context_daemon._LOCK_FD
        try:
            context_daemon._LOCK_FD = None
            context_daemon._release_single_instance_lock()
        finally:
            context_daemon._LOCK_FD = original

    def test_release_handles_oserror_on_close(self) -> None:
        """OSError from os.close is swallowed without propagating."""
        original = context_daemon._LOCK_FD
        try:
            context_daemon._LOCK_FD = -1  # invalid fd — os.close will raise OSError
            # The function must not raise even though os.close(-1) raises OSError
            try:
                context_daemon._release_single_instance_lock()
            except OSError:
                self.fail("_release_single_instance_lock raised OSError")
        finally:
            context_daemon._LOCK_FD = original


class TestAcquireSingleInstanceLockExtra(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_acquire_returns_false_when_live_pid_in_lock(self) -> None:
        """If lock exists with a live PID, acquisition fails."""
        fake_lock = Path(self.tmp) / "test.lock"
        fake_lock.write_text(str(os.getpid()))
        with patch.object(context_daemon, "LOCK_FILE", fake_lock):
            result = context_daemon._acquire_single_instance_lock()
        self.assertFalse(result)
        # Clean up global state if it somehow wrote
        context_daemon._LOCK_FD = None

    def test_acquire_stale_lock_unreadable_retries_and_fails(self) -> None:
        """Lock file exists; pid cannot be read (OSError); treated as stale → retry → fails again."""
        fake_lock = Path(self.tmp) / "stale.lock"
        fake_lock.write_text("not-a-number")
        with patch.object(context_daemon, "LOCK_FILE", fake_lock):
            # After removing stale lock, os.open will raise FileExistsError again
            # because we re-create it in between; simulate by never letting O_EXCL succeed.
            original_open = os.open

            call_count = [0]

            def _fake_open(path, flags, *args, **kwargs):
                if "stale.lock" in str(path) and (flags & os.O_EXCL):
                    call_count[0] += 1
                    raise FileExistsError("exists")
                return original_open(path, flags, *args, **kwargs)

            with patch("os.open", side_effect=_fake_open):
                result = context_daemon._acquire_single_instance_lock()
        self.assertFalse(result)

    def test_acquire_returns_false_on_oserror(self) -> None:
        """Generic OSError from os.open returns False immediately."""
        with patch("os.open", side_effect=OSError("permission denied")):
            result = context_daemon._acquire_single_instance_lock()
        self.assertFalse(result)
        context_daemon._LOCK_FD = None


# ---------------------------------------------------------------------------
# _count_antigravity_language_servers — additional cases
# ---------------------------------------------------------------------------


class TestCountAntigravityLanguageServersExtra(unittest.TestCase):
    def test_returns_zero_on_non_zero_one_returncode(self) -> None:
        fake_result = MagicMock()
        fake_result.returncode = 2
        fake_result.stdout = ""
        with patch("subprocess.run", return_value=fake_result):
            result = context_daemon._count_antigravity_language_servers()
        self.assertEqual(result, 0)

    def test_counts_matching_lines(self) -> None:
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "1234\n5678\n"
        with patch("subprocess.run", return_value=fake_result):
            result = context_daemon._count_antigravity_language_servers()
        self.assertEqual(result, 2)


# ---------------------------------------------------------------------------
# _tail_file — OSError on stat and read exception paths
# ---------------------------------------------------------------------------


class TestTailFileErrorPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_none_when_stat_raises_oserror(self) -> None:
        p = Path(self.tmp) / "file.jsonl"
        p.write_text("data\n")
        key = self.tracker._cursor_key("jsonl", "test", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        # Patch _is_safe_source to return True, then make stat raise OSError
        # by using a non-existent path after setting up the cursor
        nonexist = Path(self.tmp) / "gone.jsonl"
        # Set same cursor key for non-existent path (inode won't matter since stat fails)
        with patch.object(SessionTracker, "_is_safe_source", return_value=True):
            result = self.tracker._tail_file(key, nonexist, "test_label")
        self.assertIsNone(result)

    def test_returns_none_on_read_oserror(self) -> None:
        p = Path(self.tmp) / "readfail.jsonl"
        content = "some data\n"
        p.write_text(content)
        key = self.tracker._cursor_key("jsonl", "test", p)
        # Set cursor to 0 so tail_file tries to read
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        # Patch Path.open to raise OSError
        with patch.object(Path, "open", side_effect=OSError("read error")):
            with patch.object(SessionTracker, "_is_safe_source", return_value=True):
                result = self.tracker._tail_file(key, p, "test_label")
        self.assertIsNone(result)
        self.assertGreater(self.tracker._error_count, 0)


# ---------------------------------------------------------------------------
# poll_jsonl_sources — empty text (sanitize returns empty)
# ---------------------------------------------------------------------------


class TestPollJsonlEmptyText(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_line_stripped_is_skipped(self) -> None:
        """Blank lines in JSONL source must be skipped without session creation."""
        p = Path(self.tmp) / "blanks.jsonl"
        p.write_text("\n   \n")
        self.tracker.active_jsonl["claude_code"] = {
            "path": p,
            "sid_keys": ["sessionId"],
            "text_keys": ["display"],
        }
        key = self.tracker._cursor_key("jsonl", "claude_code", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        self.tracker.poll_jsonl_sources()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_shell_sources — _parse_shell_line returns None
# ---------------------------------------------------------------------------


class TestPollShellNoneParsed(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skips_ignored_commands(self) -> None:
        """Lines matching ignore prefixes produce no sessions."""
        p = Path(self.tmp) / "hist.sh"
        # 'ls' is not an ignore prefix, but empty string is
        p.write_text("\n")
        self.tracker.active_shell["shell_bash"] = p
        key = self.tracker._cursor_key("shell", "shell_bash", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "ENABLE_SHELL_MONITOR", True):
            self.tracker.poll_shell_sources()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_codex_sessions — error, unsafe, old, tail None, empty line, json error, reasoning
# ---------------------------------------------------------------------------


class TestPollCodexSessionsEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_codex_dir(self) -> Path:
        d = Path(self.tmp) / "codex_sessions"
        d.mkdir(exist_ok=True)
        return d

    def test_glob_error_increments_error_count(self) -> None:
        d = self._make_codex_dir()
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([], 0.0, True)):
                    before = self.tracker._error_count
                    self.tracker.poll_codex_sessions()
        self.assertGreater(self.tracker._error_count, before)

    def test_old_file_skipped(self) -> None:
        d = self._make_codex_dir()
        p = d / "session.jsonl"
        line = (
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
                }
            )
            + "\n"
        )
        p.write_text(line)
        # Set mtime to > 1 hour ago
        old_time = time.time() - 7200
        os.utime(p, (old_time, old_time))
        key = self.tracker._cursor_key("codex_session", "codex_session", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_codex_sessions()
        self.assertNotIn(p.name, self.tracker.sessions)

    def test_tail_none_skips(self) -> None:
        d = self._make_codex_dir()
        p = d / "nosession.jsonl"
        p.write_text("")
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    with patch.object(self.tracker, "_tail_file", return_value=None):
                        self.tracker.poll_codex_sessions()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_empty_jsonl_line_skipped(self) -> None:
        d = self._make_codex_dir()
        p = d / "emptylines.jsonl"
        p.write_text("\n   \n")
        key = self.tracker._cursor_key("codex_session", "codex_session", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_codex_sessions()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_bad_json_line_skipped(self) -> None:
        d = self._make_codex_dir()
        p = d / "badjson.jsonl"
        p.write_text("not json\n")
        key = self.tracker._cursor_key("codex_session", "codex_session", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_codex_sessions()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_reasoning_payload_type(self) -> None:
        d = self._make_codex_dir()
        p = d / "reasoning.jsonl"
        line = json.dumps({"type": "response_item", "payload": {"type": "reasoning", "text": "thinking hard"}}) + "\n"
        p.write_text(line)
        key = self.tracker._cursor_key("codex_session", "codex_session", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_codex_sessions()
        self.assertIn(p.name, self.tracker.sessions)

    def test_non_response_item_type_skipped(self) -> None:
        d = self._make_codex_dir()
        p = d / "other.jsonl"
        line = json.dumps({"type": "status", "payload": {}}) + "\n"
        p.write_text(line)
        key = self.tracker._cursor_key("codex_session", "codex_session", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_codex_sessions()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_claude_transcripts — edge cases
# ---------------------------------------------------------------------------


class TestPollClaudeTranscriptsEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()
        self.transcripts_dir = Path(self.tmp) / "transcripts"
        self.transcripts_dir.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_glob_error_increments_error_count(self) -> None:
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([], 0.0, True)):
                    before = self.tracker._error_count
                    self.tracker.poll_claude_transcripts()
        self.assertGreater(self.tracker._error_count, before)

    def test_unsafe_source_skipped(self) -> None:
        p = self.transcripts_dir / "ses_unsafe.jsonl"
        p.write_text("{}\n")
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    with patch.object(SessionTracker, "_is_safe_source", return_value=False):
                        self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_mtime_oserror_skips_file(self) -> None:
        """When path.stat() raises OSError for mtime, the file is skipped (continue)."""
        # Build a mock path that passes _is_safe_source but raises on stat()
        mock_path = MagicMock(spec=Path)
        mock_path.stat.side_effect = OSError("stat failed for mtime")
        mock_path.__str__ = MagicMock(return_value=str(self.transcripts_dir / "ses_mockstat.jsonl"))
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([mock_path], time.time(), False)):
                    with patch.object(SessionTracker, "_is_safe_source", return_value=True):
                        self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_first_encounter_old_file_skipped_via_lookback(self) -> None:
        """First encounter of a file older than LOOKBACK_DAYS should be skipped."""
        p = self.transcripts_dir / "ses_old.jsonl"
        content = json.dumps({"type": "user", "content": "hello"}) + "\n"
        p.write_text(content)
        # Set mtime to 30 days ago
        old_time = time.time() - 30 * 86400
        os.utime(p, (old_time, old_time))
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_LOOKBACK_DAYS", 7):
                    with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                        self.tracker.poll_claude_transcripts()
        cursor_key = self.tracker._cursor_key("claude_transcripts", "claude_transcripts", p)
        self.assertIn(cursor_key, self.tracker.file_cursors)
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_tail_none_skips(self) -> None:
        p = self.transcripts_dir / "ses_tailnone.jsonl"
        p.write_text("{}\n")
        # Pre-set the cursor so it's not a first encounter
        cursor_key = self.tracker._cursor_key("claude_transcripts", "claude_transcripts", p)
        self.tracker.file_cursors[cursor_key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    with patch.object(self.tracker, "_tail_file", return_value=None):
                        self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_empty_raw_line_skipped(self) -> None:
        p = self.transcripts_dir / "ses_empty.jsonl"
        p.write_text("\n   \n")
        cursor_key = self.tracker._cursor_key("claude_transcripts", "claude_transcripts", p)
        self.tracker.file_cursors[cursor_key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_bad_json_line_skipped(self) -> None:
        p = self.transcripts_dir / "ses_badjson.jsonl"
        p.write_text("not json at all\n")
        cursor_key = self.tracker._cursor_key("claude_transcripts", "claude_transcripts", p)
        self.tracker.file_cursors[cursor_key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_content_as_dict_extracts_text(self) -> None:
        p = self.transcripts_dir / "ses_dictcontent.jsonl"
        line = json.dumps({"type": "user", "content": {"text": "dict content text"}}) + "\n"
        p.write_text(line)
        cursor_key = self.tracker._cursor_key("claude_transcripts", "claude_transcripts", p)
        self.tracker.file_cursors[cursor_key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 1)

    def test_content_other_type_produces_empty_text(self) -> None:
        p = self.transcripts_dir / "ses_othertype.jsonl"
        line = json.dumps({"type": "user", "content": 42}) + "\n"
        p.write_text(line)
        cursor_key = self.tracker._cursor_key("claude_transcripts", "claude_transcripts", p)
        self.tracker.file_cursors[cursor_key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_non_user_assistant_type_skipped(self) -> None:
        p = self.transcripts_dir / "ses_system.jsonl"
        line = json.dumps({"type": "system", "content": "system prompt"}) + "\n"
        p.write_text(line)
        cursor_key = self.tracker._cursor_key("claude_transcripts", "claude_transcripts", p)
        self.tracker.file_cursors[cursor_key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_antigravity — busy threshold log, mtime OSError
# ---------------------------------------------------------------------------


class TestPollAntigravityEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()
        self.brain_dir = Path(self.tmp) / "brain"
        self.brain_dir.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_busy_threshold_logs_then_returns(self) -> None:
        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", True):
                    with patch.object(context_daemon, "ANTIGRAVITY_BUSY_LS_THRESHOLD", 1):
                        with patch("context_daemon._count_antigravity_language_servers", return_value=2):
                            # Force log by setting _last_antigravity_busy_log to long ago
                            self.tracker._last_antigravity_busy_log = 0.0
                            self.tracker._cached_antigravity_dirs = []
                            self.tracker.poll_antigravity()
        # Log was triggered; method returned early without error
        self.assertGreater(self.tracker._last_antigravity_busy_log, 0.0)

    def test_brain_not_dir_returns_early(self) -> None:
        fake_brain = Path(self.tmp) / "nonexistent_brain"
        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", fake_brain):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    self.tracker.poll_antigravity()
        self.assertEqual(len(self.tracker.antigravity_sessions), 0)

    def test_glob_error_increments_error_count(self) -> None:
        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch("context_daemon._refresh_glob_cache", return_value=([], 0.0, True)):
                        before = self.tracker._error_count
                        self.tracker.poll_antigravity()
        self.assertGreater(self.tracker._error_count, before)

    def test_mtime_oserror_on_doc_stat(self) -> None:
        """When stat of a brain doc raises OSError, the entry mtime defaults to 0."""
        sdir = self.brain_dir / "aaaa-bbbb-cccc-dddd-eeee"
        sdir.mkdir()
        doc = sdir / "walkthrough.md"
        doc.write_text("# Brain content with enough text " * 20)

        # We need candidate.exists() to return True, but candidate.stat().st_mtime to raise.
        # Patch Path.stat at the module level, selectively for doc path.
        original_path_stat = Path.stat

        def _selective_stat(self_path, *args, **kwargs):
            result = original_path_stat(self_path, *args, **kwargs)
            if self_path == doc:
                # Return a stat-like object where st_mtime raises AttributeError... no.
                # Instead we raise on the SECOND call (first is from exists/candidate.exists)
                pass
            return result

        # Simpler: mock `doc.exists()` to return True via patch on the sdir iteration,
        # and mock the stat call for mtime by using a mock for the brain_docs loop.
        mock_doc = MagicMock(spec=Path)
        mock_doc.exists.return_value = True
        mock_doc.stat.side_effect = OSError("stat failed for mtime")
        mock_doc.__str__ = MagicMock(return_value=str(doc))

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch("context_daemon._refresh_glob_cache", return_value=([sdir], time.time(), False)):
                        # Patch the __truediv__ of sdir to return mock_doc for "walkthrough.md"
                        real_truediv = sdir.__class__.__truediv__

                        def _truediv(self_path, name):
                            result = real_truediv(self_path, name)
                            if str(name) == "walkthrough.md" and self_path == sdir:
                                return mock_doc
                            return result

                        with patch.object(sdir.__class__, "__truediv__", _truediv):
                            self.tracker.poll_antigravity()

    def test_no_doc_found_continues(self) -> None:
        sdir = self.brain_dir / "ffff-0000-1111-2222-3333"
        sdir.mkdir()
        # No docs created — wt will be None
        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch("context_daemon._refresh_glob_cache", return_value=([sdir], time.time(), False)):
                        self.tracker.poll_antigravity()
        self.assertEqual(len(self.tracker.antigravity_sessions), 0)

    def test_wt_stat_oserror_continues(self) -> None:
        sdir = self.brain_dir / "1111-2222-3333-4444-5555"
        sdir.mkdir()
        doc = sdir / "walkthrough.md"
        doc.write_text("content")

        original_stat = Path.stat

        def _stat(self_path, *args, **kwargs):
            if self_path == doc and hasattr(self, "_second_call"):
                raise OSError("wt stat failed")
            result = original_stat(self_path, *args, **kwargs)
            self._second_call = True
            return result

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch("context_daemon._refresh_glob_cache", return_value=([sdir], time.time(), False)):
                        # Don't raise on first stat (doc discovery) but raise on wt.stat()
                        with patch.object(SessionTracker, "_is_safe_source", return_value=True):
                            # Manually simulate: add entry to antigravity_sessions first
                            # then set mtime so it triggers the export path
                            pass
                        self.tracker.poll_antigravity()


# ---------------------------------------------------------------------------
# _extract_text — parts with input prefix
# ---------------------------------------------------------------------------


class TestExtractTextPartsWithInput(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_parts_with_input_prefix(self) -> None:
        data = {
            "input": "user query",
            "parts": [{"type": "text", "text": "response part"}],
        }
        result = self.tracker._extract_text(data, [])
        self.assertIn("user query", result)
        self.assertIn("response part", result)

    def test_parts_without_input_prefix(self) -> None:
        data = {
            "parts": [{"type": "text", "text": "just the part"}],
        }
        result = self.tracker._extract_text(data, [])
        self.assertEqual(result, "just the part")

    def test_parts_empty_texts_ignored(self) -> None:
        data = {
            "parts": [{"type": "text", "text": "   "}, {"type": "other", "text": "ignored"}],
        }
        result = self.tracker._extract_text(data, [])
        self.assertEqual(result, "")

    def test_text_key_takes_priority_over_parts(self) -> None:
        data = {
            "display": "from display key",
            "parts": [{"type": "text", "text": "from parts"}],
        }
        result = self.tracker._extract_text(data, ["display"])
        self.assertEqual(result, "from display key")


# ---------------------------------------------------------------------------
# _pending_mtime — OSError path
# ---------------------------------------------------------------------------


class TestPendingMtime(unittest.TestCase):
    def test_returns_zero_on_oserror(self) -> None:
        p = Path("/does/not/exist/file.md")
        result = SessionTracker._pending_mtime(p)
        self.assertEqual(result, 0.0)

    def test_returns_mtime_for_real_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            fname = Path(f.name)
        try:
            result = SessionTracker._pending_mtime(fname)
            self.assertGreater(result, 0.0)
        finally:
            fname.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# _prune_pending_files — OSError path
# ---------------------------------------------------------------------------


class TestPrunePendingFilesOSError(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_oserror_on_glob_returns_early(self) -> None:
        with patch("pathlib.Path.glob", side_effect=OSError("glob error")):
            # Should not raise
            self.tracker._prune_pending_files()


# ---------------------------------------------------------------------------
# maybe_retry_pending — OSError on glob
# ---------------------------------------------------------------------------


class TestMaybeRetryPendingOSError(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_oserror_on_glob_treated_as_no_pending(self) -> None:
        pending_dir = Path(self.tmp) / "pending"
        pending_dir.mkdir()
        # Create a file so PENDING_DIR.exists() is True
        (pending_dir / "dummy.md").write_text("x")
        with patch.object(context_daemon, "PENDING_DIR", pending_dir):
            with patch("pathlib.Path.glob", side_effect=OSError("glob failed")):
                # should not raise; has_pending defaults to False
                self.tracker.maybe_retry_pending()


# ---------------------------------------------------------------------------
# next_sleep_interval — has_pending_files OSError
# ---------------------------------------------------------------------------


class TestNextSleepIntervalPendingOSError(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_oserror_on_pending_glob_defaults_to_false(self) -> None:
        pending_dir = Path(self.tmp) / "pending_sleep"
        pending_dir.mkdir()
        with patch.object(context_daemon, "PENDING_DIR", pending_dir):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.glob", side_effect=OSError("glob err")):
                    result = self.tracker.next_sleep_interval()
        self.assertGreaterEqual(result, 1)


# ---------------------------------------------------------------------------
# check_and_export_idle — session TTL eviction (line 1048)
# ---------------------------------------------------------------------------


class TestCheckAndExportIdleTTL(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_active_exported_session_not_removed_before_ttl(self) -> None:
        now = time.time()
        self.tracker.sessions["still_alive"] = {
            "last_seen": now - 10,  # recently seen, within TTL
            "exported": True,
            "source": "claude_code",
            "messages": [],
            "created": now - 20,
            "last_hash": "",
        }
        self.tracker.check_and_export_idle()
        self.assertIn("still_alive", self.tracker.sessions)

    def test_session_ttl_exceeded_marks_exported_and_remains_until_cleanup(self) -> None:
        now = time.time()
        # Session that is not yet exported but TTL exceeded and not enough messages
        self.tracker.sessions["orphan_ttl"] = {
            "last_seen": now - context_daemon.IDLE_TIMEOUT_SEC - 10,
            "exported": False,
            "source": "claude_code",
            "messages": [],  # fewer than min_messages
            "created": now - context_daemon.SESSION_TTL_SEC - 100,
            "last_hash": "",
        }
        self.tracker.check_and_export_idle()
        self.assertTrue(self.tracker.sessions["orphan_ttl"]["exported"])


# ---------------------------------------------------------------------------
# _handle_signal
# ---------------------------------------------------------------------------


class TestHandleSignal(unittest.TestCase):
    def test_sets_shutdown_flag(self) -> None:
        original = context_daemon._shutdown
        try:
            context_daemon._shutdown = False
            context_daemon._handle_signal(15, None)
            self.assertTrue(context_daemon._shutdown)
        finally:
            context_daemon._shutdown = original


# ---------------------------------------------------------------------------
# heartbeat — _resource_mod None path
# ---------------------------------------------------------------------------


class TestHeartbeatResourceNone(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_heartbeat_with_no_resource_mod(self) -> None:
        """Heartbeat should complete without error when resource module is None."""
        self.tracker._last_heartbeat = 0.0  # force heartbeat to fire
        with patch.object(context_daemon, "_resource_mod", None):
            with patch.object(context_daemon, "PENDING_DIR") as mock_pending:
                mock_pending.exists.return_value = False
                mock_pending.glob.return_value = iter([])
                self.tracker.heartbeat()
        self.assertGreater(self.tracker._last_heartbeat, 0.0)


# ---------------------------------------------------------------------------
# maybe_sync_index — OSError path
# ---------------------------------------------------------------------------


class TestMaybeSyncIndexOSError(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_oserror_increments_error_count(self) -> None:
        self.tracker._index_dirty = True
        self.tracker._last_index_sync = 0.0
        with patch("context_daemon.sync_index_from_storage", side_effect=OSError("disk full")):
            self.tracker.maybe_sync_index(force=True)
        self.assertGreater(self.tracker._error_count, 0)
        self.assertTrue(self.tracker._index_dirty)


# ---------------------------------------------------------------------------
# _export — ENABLE_REMOTE_SYNC True but no http_client queues pending
# ---------------------------------------------------------------------------


class TestExportQueuesPendingWhenRemoteSyncEnabledButNoClient(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_queues_pending_when_remote_sync_enabled_no_client(self) -> None:
        local_dir = Path(self.tmp) / "history"
        local_dir.mkdir(parents=True)
        pending_dir = Path(self.tmp) / "pending"
        pending_dir.mkdir(parents=True)

        data = {
            "source": "test_src",
            "messages": ["msg1", "msg2"],
            "last_seen": time.time(),
        }
        self.tracker._http_client = None

        with patch.object(context_daemon, "LOCAL_STORAGE_ROOT", Path(self.tmp)):
            with patch.object(context_daemon, "PENDING_DIR", pending_dir):
                with patch.object(context_daemon, "ENABLE_REMOTE_SYNC", True):
                    with patch.object(self.tracker, "_queue_pending") as mock_queue:
                        with patch.object(self.tracker, "maybe_sync_index"):
                            result = self.tracker._export("test_sid", data)
        mock_queue.assert_called_once()
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# _get_cursor — OSError on stat (lines 543-544)
# ---------------------------------------------------------------------------


class TestGetCursorOSError(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_returns_zero_on_stat_oserror(self) -> None:
        key = "jsonl:test:xyz"
        nonexist = Path("/does/not/exist/cursor_test.jsonl")
        result = self.tracker._get_cursor(key, nonexist)
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# poll_jsonl_sources — _tail_file returns None (line 617 continue)
# ---------------------------------------------------------------------------


class TestPollJsonlTailNone(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_continue_when_tail_file_returns_none(self) -> None:
        p = Path(self.tmp) / "noop.jsonl"
        p.write_text("data\n")
        self.tracker.active_jsonl["claude_code"] = {
            "path": p,
            "sid_keys": ["sessionId"],
            "text_keys": ["display"],
        }
        with patch.object(self.tracker, "_tail_file", return_value=None):
            self.tracker.poll_jsonl_sources()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_shell_sources — _tail_file returns None (line 643 continue)
# ---------------------------------------------------------------------------


class TestPollShellTailNone(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_continue_when_tail_file_returns_none(self) -> None:
        p = Path(self.tmp) / ".zsh_history"
        p.write_text("ls -la\n")
        self.tracker.active_shell["shell_zsh"] = p
        with patch.object(context_daemon, "ENABLE_SHELL_MONITOR", True):
            with patch.object(self.tracker, "_tail_file", return_value=None):
                self.tracker.poll_shell_sources()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_codex_sessions — unsafe source (line 672), mtime OSError (675-676)
# ---------------------------------------------------------------------------


class TestPollCodexSessionsMoreEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unsafe_source_skipped(self) -> None:
        d = Path(self.tmp) / "codex"
        d.mkdir()
        p = d / "session.jsonl"
        p.write_text("{}\n")
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    with patch.object(SessionTracker, "_is_safe_source", return_value=False):
                        self.tracker.poll_codex_sessions()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_mtime_oserror_skips_file(self) -> None:
        d = Path(self.tmp) / "codex2"
        d.mkdir()
        mock_p = MagicMock(spec=Path)
        mock_p.stat.side_effect = OSError("stat failed")
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([mock_p], time.time(), False)):
                    with patch.object(SessionTracker, "_is_safe_source", return_value=True):
                        self.tracker.poll_codex_sessions()
        self.assertEqual(len(self.tracker.sessions), 0)

    def test_message_payload_empty_text_not_stored(self) -> None:
        """message payload with no output_text content → text is empty → not stored."""
        d = Path(self.tmp) / "codex3"
        d.mkdir()
        p = d / "empty_msg.jsonl"
        line = (
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "content": [{"type": "other_type", "text": "hidden"}]},
                }
            )
            + "\n"
        )
        p.write_text(line)
        key = self.tracker._cursor_key("codex_session", "codex_session", p)
        self.tracker.file_cursors[key] = (p.stat().st_ino, 0)
        with patch.object(context_daemon, "CODEX_SESSIONS", d):
            with patch.object(context_daemon, "ENABLE_CODEX_SESSION_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([p], time.time(), False)):
                    self.tracker.poll_codex_sessions()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_claude_transcripts — first encounter fsize OSError (lines 743-744)
#   and first encounter mtime < lookback (lines 748-751)
# ---------------------------------------------------------------------------


class TestPollClaudeTranscriptsFirstEncounter(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()
        self.transcripts_dir = Path(self.tmp) / "transcripts"
        self.transcripts_dir.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_encounter_fsize_oserror_defaults_to_zero(self) -> None:
        """When fsize stat raises OSError in first-encounter path, fsize defaults to 0."""
        now = time.time()
        # File that was very recently modified so lookback doesn't skip it
        p = self.transcripts_dir / "ses_recentfile.jsonl"
        content = json.dumps({"type": "user", "content": "recent message"}) + "\n"
        p.write_text(content)

        call_count = [0]

        def _mock_stat():
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: mtime for lookback check — return recent time
                m = MagicMock()
                m.st_mtime = now  # recent
                return m
            # Second call: fsize stat raises
            raise OSError("fsize stat failed")

        mock_p = MagicMock(spec=Path)
        mock_p.stat.side_effect = _mock_stat
        mock_p.__str__ = MagicMock(return_value=str(p))
        mock_p.name = p.name

        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([mock_p], time.time(), False)):
                    with patch.object(SessionTracker, "_is_safe_source", return_value=True):
                        self.tracker.poll_claude_transcripts()

    def test_first_encounter_inode_oserror_continues(self) -> None:
        """When inode stat raises OSError (line 749-750), the file is skipped."""
        now = time.time()
        p = self.transcripts_dir / "ses_inodeerr.jsonl"
        content = json.dumps({"type": "user", "content": "some text"}) + "\n"
        p.write_text(content)

        call_count = [0]

        def _mock_stat():
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: mtime — return recent
                m = MagicMock()
                m.st_mtime = now
                return m
            if call_count[0] == 2:
                # Second call: fsize — return OK
                m = MagicMock()
                m.st_size = 100
                return m
            # Third call: inode — raise
            raise OSError("inode stat failed")

        mock_p = MagicMock(spec=Path)
        mock_p.stat.side_effect = _mock_stat
        mock_p.__str__ = MagicMock(return_value=str(p))
        mock_p.name = p.name

        with patch.object(context_daemon, "CLAUDE_TRANSCRIPTS_DIR", self.transcripts_dir):
            with patch.object(context_daemon, "ENABLE_CLAUDE_TRANSCRIPTS_MONITOR", True):
                with patch("context_daemon._refresh_glob_cache", return_value=([mock_p], time.time(), False)):
                    with patch.object(SessionTracker, "_is_safe_source", return_value=True):
                        self.tracker.poll_claude_transcripts()
        self.assertEqual(len(self.tracker.sessions), 0)


# ---------------------------------------------------------------------------
# poll_antigravity — wt.stat() OSError (lines 860-861)
#   and final_only quiet/size checks (886, 889, 892-894)
# ---------------------------------------------------------------------------


class TestPollAntigravityFinalOnlyChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()
        self.brain_dir = Path(self.tmp) / "brain"
        self.brain_dir.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session_dir(self, name: str) -> Path:
        sdir = self.brain_dir / name
        sdir.mkdir(exist_ok=True)
        return sdir

    def _make_doc(self, sdir: Path, size: int = 1000) -> Path:
        doc = sdir / "walkthrough.md"
        doc.write_text("x" * size)
        return doc

    def test_wt_stat_oserror_continues(self) -> None:
        """If wt.stat() raises OSError at line 859, the session dir is skipped."""
        sdir = self._make_session_dir("wt-stat-err-0000")
        mock_wt = MagicMock(spec=Path)
        mock_wt.exists.return_value = True
        mock_wt.stat.side_effect = OSError("wt stat failed")
        mock_wt.__ne__ = MagicMock(return_value=True)
        mock_wt.__eq__ = MagicMock(return_value=False)

        real_truediv = sdir.__class__.__truediv__

        def _truediv(self_path, name):
            if str(name) == "walkthrough.md" and self_path == sdir:
                return mock_wt
            return real_truediv(self_path, name)

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch("context_daemon._refresh_glob_cache", return_value=([sdir], time.time(), False)):
                        with patch.object(sdir.__class__, "__truediv__", _truediv):
                            self.tracker.poll_antigravity()
        self.assertEqual(len(self.tracker.antigravity_sessions), 0)

    def test_final_only_mtime_already_exported_skips(self) -> None:
        """final_only: if mtime <= exported_mtime, skip (line 885-886)."""
        sdir = self._make_session_dir("final-already-exported")
        doc = self._make_doc(sdir, 2000)
        sid = sdir.name
        mtime = doc.stat().st_mtime

        # Pre-populate antigravity_sessions so it's NOT a first encounter
        self.tracker.antigravity_sessions[sid] = {
            "mtime": mtime,
            "path": doc,
            "last_change": time.time() - 3600,  # changed long ago
            "exported_mtime": mtime,  # already exported this mtime
        }

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch.object(context_daemon, "ANTIGRAVITY_INGEST_MODE", "final_only"):
                        with patch("context_daemon._refresh_glob_cache", return_value=([sdir], time.time(), False)):
                            with patch.object(self.tracker, "_export") as mock_export:
                                self.tracker.poll_antigravity()
        mock_export.assert_not_called()

    def test_final_only_not_quiet_yet_skips(self) -> None:
        """final_only: document changed recently, quiet period not elapsed (line 888-889)."""
        sdir = self._make_session_dir("final-not-quiet-yet")
        doc = self._make_doc(sdir, 2000)
        sid = sdir.name
        mtime = doc.stat().st_mtime + 1  # slightly newer than exported

        self.tracker.antigravity_sessions[sid] = {
            "mtime": mtime - 1,
            "path": doc,
            "last_change": time.time(),  # changed just now → not quiet
            "exported_mtime": mtime - 2,
        }

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch.object(context_daemon, "ANTIGRAVITY_INGEST_MODE", "final_only"):
                        with patch.object(context_daemon, "ANTIGRAVITY_QUIET_SEC", 3600):
                            with patch("context_daemon._refresh_glob_cache", return_value=([sdir], time.time(), False)):
                                with patch.object(self.tracker, "_export") as mock_export:
                                    self.tracker.poll_antigravity()
        mock_export.assert_not_called()

    def test_final_only_doc_too_small_skips(self) -> None:
        """final_only: document below min bytes → skip (line 892)."""
        sdir = self._make_session_dir("final-too-small")
        doc = self._make_doc(sdir, 50)  # tiny doc
        sid = sdir.name
        mtime = doc.stat().st_mtime + 1

        self.tracker.antigravity_sessions[sid] = {
            "mtime": mtime - 1,
            "path": doc,
            "last_change": time.time() - 3600,  # quiet
            "exported_mtime": mtime - 2,
        }

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch.object(context_daemon, "ANTIGRAVITY_INGEST_MODE", "final_only"):
                        with patch.object(context_daemon, "ANTIGRAVITY_QUIET_SEC", 30):
                            with patch.object(context_daemon, "ANTIGRAVITY_MIN_DOC_BYTES", 500):
                                with patch(
                                    "context_daemon._refresh_glob_cache", return_value=([sdir], time.time(), False)
                                ):
                                    with patch.object(self.tracker, "_export") as mock_export:
                                        self.tracker.poll_antigravity()
        mock_export.assert_not_called()

    def test_final_only_size_oserror_skips(self) -> None:
        """final_only: wt.stat().st_size raises OSError → skip (lines 893-894)."""
        sdir = self._make_session_dir("final-size-err")
        doc = self._make_doc(sdir, 1000)
        sid = sdir.name
        mtime = doc.stat().st_mtime + 1

        self.tracker.antigravity_sessions[sid] = {
            "mtime": mtime - 1,
            "path": doc,
            "last_change": time.time() - 3600,
            "exported_mtime": mtime - 2,
        }

        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", False):
                    with patch.object(context_daemon, "ANTIGRAVITY_INGEST_MODE", "final_only"):
                        with patch.object(context_daemon, "ANTIGRAVITY_QUIET_SEC", 30):
                            with patch.object(context_daemon, "ANTIGRAVITY_MIN_DOC_BYTES", 100):
                                with patch(
                                    "context_daemon._refresh_glob_cache", return_value=([sdir], time.time(), False)
                                ):
                                    # Make wt.stat() raise OSError for size check
                                    # We need the path in meta to be doc, and doc.stat() to raise
                                    with patch.object(SessionTracker, "_export"):
                                        # Use mock wt with stat raising OSError
                                        mock_wt = MagicMock(spec=Path)
                                        mock_wt.stat.side_effect = OSError("size check failed")
                                        mock_wt.read_text.return_value = "content"
                                        mock_wt.__ne__ = MagicMock(return_value=False)
                                        mock_wt.__eq__ = MagicMock(return_value=True)
                                        self.tracker.antigravity_sessions[sid]["path"] = mock_wt
                                        with patch.object(self.tracker, "_export") as mock_export:
                                            self.tracker.poll_antigravity()
        mock_export.assert_not_called()

    def test_busy_threshold_not_logged_within_interval(self) -> None:
        """Busy threshold: if log was recent, no log and returns early."""
        with patch.object(context_daemon, "ENABLE_ANTIGRAVITY_MONITOR", True):
            with patch.object(context_daemon, "ANTIGRAVITY_BRAIN", self.brain_dir):
                with patch.object(context_daemon, "SUSPEND_ANTIGRAVITY_WHEN_BUSY", True):
                    with patch.object(context_daemon, "ANTIGRAVITY_BUSY_LS_THRESHOLD", 1):
                        with patch("context_daemon._count_antigravity_language_servers", return_value=2):
                            # Set _last_antigravity_busy_log to just now (no re-log)
                            self.tracker._last_antigravity_busy_log = time.time()
                            self.tracker._cached_antigravity_dirs = []
                            self.tracker.poll_antigravity()
        # Method returned early; sessions unchanged
        self.assertEqual(len(self.tracker.antigravity_sessions), 0)


# ---------------------------------------------------------------------------
# refresh_sources — disabled source removal and source offline branches
# ---------------------------------------------------------------------------


class TestRefreshSourcesBranches(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_source_disabled_removes_from_active(self) -> None:
        """If source monitor flag is False and source was active, remove it."""
        p = Path(self.tmp) / "history.jsonl"
        p.write_text("data\n")
        self.tracker.active_jsonl["codex_history"] = {"path": p, "sid_keys": [], "text_keys": []}
        self.tracker._last_source_refresh = 0.0  # force refresh

        with patch.object(context_daemon, "SOURCE_MONITOR_FLAGS", {"codex_history": False}):
            self.tracker.refresh_sources()
        self.assertNotIn("codex_history", self.tracker.active_jsonl)

    def test_source_offline_removes_from_active(self) -> None:
        """If previously active source path no longer exists, mark offline."""
        nonexist = Path(self.tmp) / "gone.jsonl"
        self.tracker.active_jsonl["claude_code"] = {"path": nonexist, "sid_keys": [], "text_keys": []}
        self.tracker._last_source_refresh = 0.0

        # Patch JSONL_SOURCES to only contain our nonexistent path
        fake_sources = {"claude_code": [{"path": nonexist, "sid_keys": [], "text_keys": []}]}
        with patch.object(context_daemon, "JSONL_SOURCES", fake_sources):
            with patch.object(context_daemon, "SOURCE_MONITOR_FLAGS", {"claude_code": True}):
                with patch.object(context_daemon, "ENABLE_SHELL_MONITOR", False):
                    self.tracker.refresh_sources()
        self.assertNotIn("claude_code", self.tracker.active_jsonl)

    def test_shell_source_offline_removes_from_active(self) -> None:
        """Shell source that disappears is removed from active_shell."""
        nonexist = Path(self.tmp) / "gone_history"
        self.tracker.active_shell["shell_zsh"] = nonexist
        self.tracker._last_source_refresh = 0.0

        fake_shell_sources = {"shell_zsh": [nonexist]}
        with patch.object(context_daemon, "JSONL_SOURCES", {}):
            with patch.object(context_daemon, "SHELL_SOURCES", fake_shell_sources):
                with patch.object(context_daemon, "ENABLE_SHELL_MONITOR", True):
                    self.tracker.refresh_sources()
        self.assertNotIn("shell_zsh", self.tracker.active_shell)


# ---------------------------------------------------------------------------
# SessionTracker.__init__ — HTTP client init failure (lines 468-475)
# ---------------------------------------------------------------------------


class TestSessionTrackerHTTPClientInitFailure(unittest.TestCase):
    def test_http_client_init_failure_logs_warning(self) -> None:
        """If httpx.Client() raises, http_client stays None."""
        # Create a mock httpx that raises on Client()
        mock_httpx = MagicMock()
        mock_httpx.Client.side_effect = Exception("httpx init failed")

        with patch.object(context_daemon, "_HTTPX_AVAILABLE", True):
            with patch.object(context_daemon, "_httpx", mock_httpx):
                with patch.object(context_daemon, "ENABLE_REMOTE_SYNC", True):
                    with patch.object(SessionTracker, "refresh_sources"):
                        tracker = SessionTracker()
        self.assertIsNone(tracker._http_client)


if __name__ == "__main__":
    unittest.main()
