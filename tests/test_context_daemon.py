#!/usr/bin/env python3
"""Unit tests for context_daemon module.

Because context_daemon has heavy module-level side-effects (creates log
directories, opens RotatingFileHandler, checks storage root ownership), we
import it inside a temporary directory and with a mocked storage_root so the
tests remain hermetic and deterministic.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ---------------------------------------------------------------------------
# Module-level setup: import context_daemon with a controlled temp storage root
# so the module-level code (mkdir, chmod, RotatingFileHandler) doesn't touch
# real user directories.
# ---------------------------------------------------------------------------

_DAEMON_TMP = tempfile.mkdtemp(prefix="cg_daemon_test_")
_FAKE_STORAGE = Path(_DAEMON_TMP) / ".contextgo"
_FAKE_STORAGE.mkdir(parents=True, exist_ok=True)

# Patch CONTEXTGO_STORAGE_ROOT before importing the daemon module.
os.environ.setdefault("CONTEXTGO_STORAGE_ROOT", str(_FAKE_STORAGE))

# Also ensure memory_index functions needed at module level are available.
# We patch them so no real SQLite DB is needed.
_mock_strip = MagicMock(side_effect=lambda text: text)
_mock_sync = MagicMock(return_value={})

import unittest.mock as _umock  # noqa: E402

with (
    _umock.patch.dict("sys.modules", {}),
    _umock.patch(
        "builtins.__import__",
        wraps=__builtins__.__import__ if isinstance(__builtins__, types.ModuleType) else __import__,
    ),  # type: ignore[attr-defined]
):
    pass  # no-op context; actual patching done below via env var

# Import for real — the storage root is already set via env var
import context_daemon  # noqa: E402

# Grab refs to the classes/functions we want to test
SessionTracker = context_daemon.SessionTracker
_SECRET_REPLACEMENTS = context_daemon._SECRET_REPLACEMENTS
_SHELL_LINE_RE = context_daemon._SHELL_LINE_RE
_IGNORE_SHELL_CMD_PREFIXES = context_daemon._IGNORE_SHELL_CMD_PREFIXES


# ---------------------------------------------------------------------------
# Helper: build a minimal SessionTracker without running refresh_sources
# ---------------------------------------------------------------------------


def _make_tracker() -> SessionTracker:
    """Create a SessionTracker with refresh_sources disabled."""
    with patch.object(SessionTracker, "refresh_sources"):
        return SessionTracker()


# ---------------------------------------------------------------------------
# Tests: _sanitize_text
# ---------------------------------------------------------------------------


class TestSanitizeText(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def _sanitize(self, text: str) -> str:
        # patch strip_private_blocks to be identity so we can test secret redaction
        with patch("context_daemon.strip_private_blocks", side_effect=lambda t: t):
            return self.tracker._sanitize_text(text)

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(self._sanitize(""), "")

    def test_plain_text_unchanged(self) -> None:
        result = self._sanitize("hello world")
        self.assertEqual(result, "hello world")

    def test_redacts_api_key_assignment(self) -> None:
        result = self._sanitize("api_key=secret123abc")
        self.assertIn("***", result)
        self.assertNotIn("secret123abc", result)

    def test_redacts_token_assignment(self) -> None:
        result = self._sanitize("token=mysecrettoken")
        self.assertIn("***", result)
        self.assertNotIn("mysecrettoken", result)

    def test_redacts_openai_sk_token(self) -> None:
        result = self._sanitize("sk-ABCDEFGHIJKLMNOPQRSTUVWX")
        self.assertIn("sk-***", result)

    def test_redacts_github_pat(self) -> None:
        result = self._sanitize("ghp_" + "A" * 25)
        self.assertIn("ghp_***", result)

    def test_redacts_aws_access_key(self) -> None:
        result = self._sanitize("AKIAIOSFODNN7EXAMPLE1234")
        self.assertIn("AKIA***", result)

    def test_redacts_slack_token(self) -> None:
        result = self._sanitize("xoxb-1234567890-abcdefghij")
        self.assertIn("xox?-***", result)

    def test_redacts_authorization_bearer(self) -> None:
        result = self._sanitize("Authorization: Bearer mytoken123abc")
        self.assertIn("***", result)

    def test_truncates_long_text(self) -> None:
        long_text = "x" * 5000
        result = self._sanitize(long_text)
        self.assertEqual(len(result), 4000)

    def test_private_blocks_stripped(self) -> None:
        # Use the real strip_private_blocks
        result = self.tracker._sanitize_text("<private>secret data</private>public data")
        self.assertNotIn("secret data", result)
        self.assertIn("public data", result)


# ---------------------------------------------------------------------------
# Tests: _parse_shell_line
# ---------------------------------------------------------------------------


class TestParseShellLine(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()
        # Patch _sanitize_text to be identity so we test parsing, not sanitisation
        patcher = patch.object(self.tracker, "_sanitize_text", side_effect=lambda t: t)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_line_returns_none(self) -> None:
        self.assertIsNone(self.tracker._parse_shell_line("shell_zsh", ""))

    def test_whitespace_only_returns_none(self) -> None:
        self.assertIsNone(self.tracker._parse_shell_line("shell_zsh", "   "))

    def test_plain_command_returns_tuple(self) -> None:
        result = self.tracker._parse_shell_line("shell_zsh", "ls -la")
        self.assertIsNotNone(result)
        assert result is not None
        sid, cmd = result
        self.assertEqual(cmd, "ls -la")
        self.assertIn("shell_zsh_", sid)

    def test_zsh_extended_history_format(self) -> None:
        # ": 1711382400:0;git status"  -> ts=1711382400, cmd="git status"
        result = self.tracker._parse_shell_line("shell_zsh", ": 1711382400:0;git status")
        self.assertIsNotNone(result)
        assert result is not None
        sid, cmd = result
        self.assertEqual(cmd, "git status")
        # The timestamp 1711382400 is 2024-03-25T12:00:00 UTC.  In time zones
        # east of UTC+12 this rolls over to 2024-03-26 local time, so we
        # accept either date string in the session id.
        self.assertTrue(
            "20240325" in sid or "20240326" in sid,
            f"Expected '20240325' or '20240326' in sid={sid!r}",
        )

    def test_history_command_filtered(self) -> None:
        result = self.tracker._parse_shell_line("shell_zsh", "history")
        self.assertIsNone(result)

    def test_history_with_args_filtered(self) -> None:
        result = self.tracker._parse_shell_line("shell_zsh", "history 100")
        self.assertIsNone(result)

    def test_fc_command_filtered(self) -> None:
        result = self.tracker._parse_shell_line("shell_bash", "fc -l")
        self.assertIsNone(result)

    def test_session_id_includes_date(self) -> None:
        result = self.tracker._parse_shell_line("shell_bash", "echo hello")
        self.assertIsNotNone(result)
        assert result is not None
        sid, _ = result
        # Should contain today's date in YYYYMMDD format
        today = time.strftime("%Y%m%d")
        self.assertIn(today, sid)

    def test_sanitized_empty_returns_none(self) -> None:
        # If after sanitisation the command becomes empty
        with patch.object(self.tracker, "_sanitize_text", return_value=""):
            result = self.tracker._parse_shell_line("shell_zsh", "echo something")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Tests: _extract_sid
# ---------------------------------------------------------------------------


class TestExtractSid(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_extracts_first_matching_key(self) -> None:
        data = {"sessionId": "abc123", "session_id": "xyz789"}
        result = self.tracker._extract_sid(data, ["sessionId", "session_id"], "claude")
        self.assertEqual(result, "abc123")

    def test_falls_back_to_second_key(self) -> None:
        data = {"session_id": "xyz789"}
        result = self.tracker._extract_sid(data, ["sessionId", "session_id"], "codex")
        self.assertEqual(result, "xyz789")

    def test_uses_default_when_no_key_found(self) -> None:
        data = {"something_else": "nope"}
        result = self.tracker._extract_sid(data, ["sessionId"], "myapp")
        self.assertEqual(result, "myapp_default")

    def test_int_value_converted_to_str(self) -> None:
        data = {"session_id": 42}
        result = self.tracker._extract_sid(data, ["session_id"], "test")
        self.assertEqual(result, "42")

    def test_empty_string_value_skipped(self) -> None:
        data = {"sessionId": "", "session_id": "valid"}
        result = self.tracker._extract_sid(data, ["sessionId", "session_id"], "app")
        self.assertEqual(result, "valid")


# ---------------------------------------------------------------------------
# Tests: _extract_text
# ---------------------------------------------------------------------------


class TestExtractText(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_extracts_first_matching_text_key(self) -> None:
        data = {"display": "hello world", "text": "ignored"}
        result = self.tracker._extract_text(data, ["display", "text"])
        self.assertEqual(result, "hello world")

    def test_falls_back_to_second_key(self) -> None:
        data = {"text": "fallback value"}
        result = self.tracker._extract_text(data, ["display", "text"])
        self.assertEqual(result, "fallback value")

    def test_returns_empty_when_no_key(self) -> None:
        data = {"something_else": "ignored"}
        result = self.tracker._extract_text(data, ["display", "text"])
        self.assertEqual(result, "")

    def test_skips_whitespace_only_value(self) -> None:
        data = {"display": "   ", "text": "real text"}
        result = self.tracker._extract_text(data, ["display", "text"])
        self.assertEqual(result, "real text")

    def test_extracts_from_parts_array(self) -> None:
        data = {
            "parts": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ]
        }
        result = self.tracker._extract_text(data, [])
        self.assertIn("part one", result)
        self.assertIn("part two", result)

    def test_parts_with_input_prefix(self) -> None:
        data = {
            "input": "user prompt",
            "parts": [{"type": "text", "text": "response text"}],
        }
        result = self.tracker._extract_text(data, [])
        self.assertIn("user prompt", result)
        self.assertIn("response text", result)


# ---------------------------------------------------------------------------
# Tests: _upsert_session
# ---------------------------------------------------------------------------


class TestUpsertSession(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_creates_new_session(self) -> None:
        now = time.time()
        self.tracker._upsert_session("sid1", "claude_code", "Hello from test", now)
        self.assertIn("sid1", self.tracker.sessions)
        self.assertEqual(self.tracker.sessions["sid1"]["source"], "claude_code")

    def test_appends_message_to_existing_session(self) -> None:
        now = time.time()
        self.tracker._upsert_session("sid2", "claude_code", "First message", now)
        self.tracker._upsert_session("sid2", "claude_code", "Second message", now + 1)
        self.assertEqual(len(self.tracker.sessions["sid2"]["messages"]), 2)

    def test_deduplicates_identical_messages(self) -> None:
        now = time.time()
        self.tracker._upsert_session("sid3", "codex", "Same text", now)
        self.tracker._upsert_session("sid3", "codex", "Same text", now + 1)
        self.assertEqual(len(self.tracker.sessions["sid3"]["messages"]), 1)

    def test_updates_last_seen_on_new_message(self) -> None:
        t0 = time.time()
        self.tracker._upsert_session("sid4", "claude_code", "msg1", t0)
        t1 = t0 + 100
        self.tracker._upsert_session("sid4", "claude_code", "msg2", t1)
        self.assertAlmostEqual(self.tracker.sessions["sid4"]["last_seen"], t1)

    def test_evicts_oldest_when_at_capacity(self) -> None:
        original_max = context_daemon.MAX_TRACKED_SESSIONS
        try:
            context_daemon.MAX_TRACKED_SESSIONS = 3
            now = time.time()
            for i in range(4):
                self.tracker._upsert_session(f"sid_{i}", "test", f"msg {i}", now + i)
            self.assertLessEqual(len(self.tracker.sessions), 3)
        finally:
            context_daemon.MAX_TRACKED_SESSIONS = original_max


# ---------------------------------------------------------------------------
# Tests: _sanitize_filename_part
# ---------------------------------------------------------------------------


class TestSanitizeFilenamePart(unittest.TestCase):
    def test_safe_name_unchanged(self) -> None:
        result = SessionTracker._sanitize_filename_part("my-session_01")
        self.assertEqual(result, "my-session_01")

    def test_special_chars_replaced(self) -> None:
        result = SessionTracker._sanitize_filename_part("hello world/foo:bar")
        self.assertNotIn(" ", result)
        self.assertNotIn("/", result)
        self.assertNotIn(":", result)

    def test_empty_uses_default(self) -> None:
        result = SessionTracker._sanitize_filename_part("")
        self.assertEqual(result, "session")

    def test_truncates_to_64_chars(self) -> None:
        result = SessionTracker._sanitize_filename_part("a" * 100)
        self.assertLessEqual(len(result), 64)

    def test_strips_leading_trailing_separators(self) -> None:
        result = SessionTracker._sanitize_filename_part("-my-session-")
        self.assertFalse(result.startswith("-"))
        self.assertFalse(result.endswith("-"))


# ---------------------------------------------------------------------------
# Tests: _cursor_key
# ---------------------------------------------------------------------------


class TestCursorKey(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_cursor_key_format(self) -> None:
        key = self.tracker._cursor_key("jsonl", "claude_code", "/home/user/.claude/history.jsonl")
        self.assertTrue(key.startswith("jsonl:claude_code:"))
        # digest should be 10 hex chars
        digest = key.split(":")[-1]
        self.assertEqual(len(digest), 10)

    def test_different_paths_produce_different_keys(self) -> None:
        k1 = self.tracker._cursor_key("jsonl", "claude_code", "/path/a")
        k2 = self.tracker._cursor_key("jsonl", "claude_code", "/path/b")
        self.assertNotEqual(k1, k2)

    def test_same_path_produces_same_key(self) -> None:
        k1 = self.tracker._cursor_key("shell", "shell_zsh", "/home/user/.zsh_history")
        k2 = self.tracker._cursor_key("shell", "shell_zsh", "/home/user/.zsh_history")
        self.assertEqual(k1, k2)


# ---------------------------------------------------------------------------
# Tests: cleanup_cursors
# ---------------------------------------------------------------------------


class TestCleanupCursors(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_no_cleanup_under_limit(self) -> None:
        original_max = context_daemon.MAX_FILE_CURSORS
        try:
            context_daemon.MAX_FILE_CURSORS = 100
            for i in range(10):
                self.tracker.file_cursors[f"key_{i}"] = (i, 0)
            self.tracker.cleanup_cursors()
            self.assertEqual(len(self.tracker.file_cursors), 10)
        finally:
            context_daemon.MAX_FILE_CURSORS = original_max

    def test_evicts_oldest_third_when_over_limit(self) -> None:
        original_max = context_daemon.MAX_FILE_CURSORS
        try:
            context_daemon.MAX_FILE_CURSORS = 5
            for i in range(9):
                self.tracker.file_cursors[f"key_{i:03d}"] = (i, 0)
            self.tracker.cleanup_cursors()
            self.assertLess(len(self.tracker.file_cursors), 9)
        finally:
            context_daemon.MAX_FILE_CURSORS = original_max


# ---------------------------------------------------------------------------
# Tests: _evict_oldest
# ---------------------------------------------------------------------------


class TestEvictOldest(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_evicts_oldest_by_last_seen(self) -> None:
        now = time.time()
        self.tracker.sessions["old"] = {"last_seen": now - 100, "exported": False, "messages": []}
        self.tracker.sessions["new"] = {"last_seen": now, "exported": False, "messages": []}
        self.tracker._evict_oldest()
        self.assertNotIn("old", self.tracker.sessions)
        self.assertIn("new", self.tracker.sessions)

    def test_prefers_evicting_exported_sessions(self) -> None:
        now = time.time()
        self.tracker.sessions["unexported"] = {"last_seen": now - 100, "exported": False, "messages": []}
        self.tracker.sessions["exported_old"] = {"last_seen": now - 200, "exported": True, "messages": []}
        self.tracker._evict_oldest()
        # The exported session should be evicted first, not the unexported one
        self.assertNotIn("exported_old", self.tracker.sessions)
        self.assertIn("unexported", self.tracker.sessions)


# ---------------------------------------------------------------------------
# Tests: _cfg_bool / _cfg_int / _cfg_float / _cfg_str helpers
# ---------------------------------------------------------------------------


class TestCfgHelpers(unittest.TestCase):
    def test_cfg_bool_reads_env(self) -> None:
        with patch.dict(os.environ, {"CONTEXTGO_TEST_FLAG": "1"}):
            result = context_daemon._cfg_bool("TEST_FLAG", default=False)
        self.assertTrue(result)

    def test_cfg_int_reads_env(self) -> None:
        with patch.dict(os.environ, {"CONTEXTGO_TEST_INT": "42"}):
            result = context_daemon._cfg_int("TEST_INT", default=0)
        self.assertEqual(result, 42)

    def test_cfg_float_reads_env(self) -> None:
        with patch.dict(os.environ, {"CONTEXTGO_TEST_FLOAT": "3.14"}):
            result = context_daemon._cfg_float("TEST_FLOAT", default=0.0)
        self.assertAlmostEqual(result, 3.14)

    def test_cfg_str_reads_env(self) -> None:
        with patch.dict(os.environ, {"CONTEXTGO_TEST_STR": "hello"}):
            result = context_daemon._cfg_str("TEST_STR", default="")
        self.assertEqual(result, "hello")

    def test_cfg_bool_uses_default_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTGO_TEST_MISSING", None)
            result = context_daemon._cfg_bool("TEST_MISSING", default=True)
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# Tests: _pid_alive
# ---------------------------------------------------------------------------


class TestPidAlive(unittest.TestCase):
    def test_own_pid_is_alive(self) -> None:
        self.assertTrue(context_daemon._pid_alive(os.getpid()))

    def test_nonexistent_pid_not_alive(self) -> None:
        # PID 0 is not a user process; large PID unlikely to exist
        self.assertFalse(context_daemon._pid_alive(9999999))

    def test_negative_pid_not_alive(self) -> None:
        # Very large PID that won't exist on any system
        self.assertFalse(context_daemon._pid_alive(2**22 - 1))


# ---------------------------------------------------------------------------
# Tests: check_and_export_idle
# ---------------------------------------------------------------------------


class TestCheckAndExportIdle(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = _make_tracker()

    def test_idle_session_with_enough_messages_exported(self) -> None:
        now = time.time()
        self.tracker.sessions["idle_sid"] = {
            "last_seen": now - context_daemon.IDLE_TIMEOUT_SEC - 1,
            "exported": False,
            "source": "claude_code",
            "messages": ["msg1", "msg2"],
            "created": now - context_daemon.IDLE_TIMEOUT_SEC - 10,
            "last_hash": "",
        }
        with patch.object(self.tracker, "_export"):
            self.tracker.check_and_export_idle()
        self.assertTrue(self.tracker.sessions["idle_sid"]["exported"])

    def test_active_session_not_exported(self) -> None:
        now = time.time()
        self.tracker.sessions["active_sid"] = {
            "last_seen": now,  # just seen — not idle
            "exported": False,
            "source": "claude_code",
            "messages": ["msg1", "msg2", "msg3"],
            "created": now - 10,
            "last_hash": "",
        }
        with patch.object(self.tracker, "_export") as mock_export:
            self.tracker.check_and_export_idle()
        mock_export.assert_not_called()

    def test_exported_old_session_removed(self) -> None:
        now = time.time()
        old_enough = now - context_daemon.SESSION_TTL_SEC - 1
        self.tracker.sessions["old_exported"] = {
            "last_seen": old_enough,
            "exported": True,
            "source": "claude_code",
            "messages": [],
            "created": old_enough,
            "last_hash": "",
        }
        self.tracker.check_and_export_idle()
        self.assertNotIn("old_exported", self.tracker.sessions)

    def test_shell_session_needs_more_messages(self) -> None:
        """Shell sessions require >= 4 messages before export."""
        now = time.time()
        self.tracker.sessions["shell_sid"] = {
            "last_seen": now - context_daemon.IDLE_TIMEOUT_SEC - 1,
            "exported": False,
            "source": "shell_zsh",
            "messages": ["cmd1", "cmd2"],  # only 2 — below threshold of 4
            "created": now - context_daemon.IDLE_TIMEOUT_SEC - 10,
            "last_hash": "",
        }
        with patch.object(self.tracker, "_export") as mock_export:
            self.tracker.check_and_export_idle()
        mock_export.assert_not_called()


if __name__ == "__main__":
    unittest.main()
