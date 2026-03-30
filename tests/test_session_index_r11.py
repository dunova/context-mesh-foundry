#!/usr/bin/env python3
"""R11 extended tests for session_index module — targeting uncovered lines."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_index


class TestIsNoiseText(unittest.TestCase):
    def test_empty_string_is_noise(self) -> None:
        self.assertTrue(session_index._is_noise_text(""))

    def test_whitespace_only_is_noise(self) -> None:
        self.assertTrue(session_index._is_noise_text("   \t\n  "))

    def test_skill_md_repeated_is_noise(self) -> None:
        self.assertTrue(session_index._is_noise_text("SKILL.md SKILL.md SKILL.md here"))

    def test_normal_text_not_noise(self) -> None:
        self.assertFalse(session_index._is_noise_text("NotebookLM integration completed successfully."))


class TestCollectContentText(unittest.TestCase):
    def test_extracts_input_text(self) -> None:
        items = [{"type": "input_text", "text": "hello world"}]
        result = session_index._collect_content_text(items)
        self.assertEqual(result, ["hello world"])

    def test_extracts_output_text(self) -> None:
        items = [{"type": "output_text", "text": "response text"}]
        result = session_index._collect_content_text(items)
        self.assertEqual(result, ["response text"])

    def test_extracts_text_type(self) -> None:
        items = [{"type": "text", "text": "plain text"}]
        result = session_index._collect_content_text(items)
        self.assertEqual(result, ["plain text"])

    def test_skips_unknown_types(self) -> None:
        items = [{"type": "image", "url": "http://example.com"}]
        result = session_index._collect_content_text(items)
        self.assertEqual(result, [])

    def test_skips_empty_text(self) -> None:
        items = [{"type": "text", "text": ""}]
        result = session_index._collect_content_text(items)
        self.assertEqual(result, [])

    def test_not_list_returns_empty(self) -> None:
        result = session_index._collect_content_text("not a list")
        self.assertEqual(result, [])

    def test_skips_non_dict_items(self) -> None:
        items = ["string", 42, {"type": "text", "text": "valid"}]
        result = session_index._collect_content_text(items)
        self.assertEqual(result, ["valid"])


class TestTruncate(unittest.TestCase):
    def test_truncates_at_max_chars(self) -> None:
        texts = ["a" * 100, "b" * 100]
        result = session_index._truncate(texts, max_chars=50)
        self.assertEqual(len(result), 50)

    def test_empty_texts_filtered(self) -> None:
        result = session_index._truncate(["", "  ", "valid"], max_chars=1000)
        self.assertIn("valid", result)

    def test_joins_multiple_texts(self) -> None:
        result = session_index._truncate(["foo", "bar"], max_chars=1000)
        self.assertIn("foo", result)
        self.assertIn("bar", result)


class TestCompactSnippet(unittest.TestCase):
    def test_short_text_unchanged(self) -> None:
        result = session_index._compact_snippet("hello", max_chars=120)
        self.assertEqual(result, "hello")

    def test_long_text_truncated_with_ellipsis(self) -> None:
        result = session_index._compact_snippet("a" * 200, max_chars=50)
        self.assertTrue(result.endswith("\u2026"))
        self.assertLessEqual(len(result), 50)

    def test_collapses_whitespace(self) -> None:
        result = session_index._compact_snippet("hello   world  test", max_chars=120)
        self.assertEqual(result, "hello world test")


class TestIsoToEpoch(unittest.TestCase):
    def test_parses_valid_iso(self) -> None:
        epoch = session_index._iso_to_epoch("2026-03-25T00:00:00Z", fallback=0)
        self.assertGreater(epoch, 0)

    def test_returns_fallback_for_empty(self) -> None:
        self.assertEqual(session_index._iso_to_epoch("", fallback=42), 42)

    def test_returns_fallback_for_invalid(self) -> None:
        self.assertEqual(session_index._iso_to_epoch("not-a-date", fallback=99), 99)

    def test_returns_fallback_for_none(self) -> None:
        self.assertEqual(session_index._iso_to_epoch(None, fallback=77), 77)


class TestNormalizeFilePath(unittest.TestCase):
    def test_returns_string(self) -> None:
        result = session_index._normalize_file_path(Path("/tmp/test.jsonl"))
        self.assertIsInstance(result, str)

    def test_resolves_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "file.jsonl"
            path.touch()
            result = session_index._normalize_file_path(path)
            self.assertEqual(result, str(path.resolve()))


class TestMetaGetSet(unittest.TestCase):
    def test_meta_get_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                try:
                    result = session_index._meta_get(conn, "nonexistent_key")
                    self.assertIsNone(result)
                finally:
                    conn.close()

    def test_meta_set_and_get_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                try:
                    session_index._meta_set(conn, "test_key", "test_value")
                    conn.commit()
                    result = session_index._meta_get(conn, "test_key")
                    self.assertEqual(result, "test_value")
                finally:
                    conn.close()

    def test_meta_set_updates_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                try:
                    session_index._meta_set(conn, "key", "old_value")
                    session_index._meta_set(conn, "key", "new_value")
                    conn.commit()
                    result = session_index._meta_get(conn, "key")
                    self.assertEqual(result, "new_value")
                finally:
                    conn.close()


class TestGetSessionDbPath(unittest.TestCase):
    def test_uses_env_override(self) -> None:
        with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: "/tmp/custom.db"}, clear=False):
            result = session_index.get_session_db_path()
            self.assertEqual(str(result), "/tmp/custom.db")

    def test_uses_storage_root_when_no_env(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != session_index.SESSION_DB_PATH_ENV}
        with mock.patch.dict(os.environ, env_copy, clear=True):
            result = session_index.get_session_db_path()
            self.assertIsInstance(result, Path)
            self.assertTrue(str(result).endswith("session_index.db"))


class TestParseClaudeSession(unittest.TestCase):
    def test_parses_claude_session_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "claude_session.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "user",
                                "sessionId": "claude-abc",
                                "cwd": "/tmp/claude-project",
                                "timestamp": "2026-03-25T10:00:00Z",
                                "message": {"content": "Claude session user message content"},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            doc = session_index._parse_claude_session(path)
            self.assertIsNotNone(doc)
            assert doc is not None
            self.assertEqual(doc.session_id, "claude-abc")
            self.assertIn("Claude session user message content", doc.content)

    def test_parses_claude_assistant_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "claude_session.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "assistant",
                                "sessionId": "claude-def",
                                "cwd": "/tmp/proj",
                                "timestamp": "2026-03-25T10:00:00Z",
                                "message": {"content": [{"type": "text", "text": "Assistant response text"}]},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            doc = session_index._parse_claude_session(path)
            self.assertIsNotNone(doc)
            assert doc is not None
            self.assertIn("Assistant response text", doc.content)

    def test_returns_none_for_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "claude_err.jsonl"
            path.touch()
            with mock.patch.object(session_index, "_iter_jsonl_objects", side_effect=OSError("cannot read")):
                doc = session_index._parse_claude_session(path)
        self.assertIsNone(doc)


class TestParseHistoryJsonl(unittest.TestCase):
    def test_parses_display_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            path.write_text(
                json.dumps({"display": "history display text"}) + "\n",
                encoding="utf-8",
            )
            doc = session_index._parse_history_jsonl(path, "codex_history")
            self.assertIsNotNone(doc)
            assert doc is not None
            self.assertIn("history display text", doc.content)

    def test_parses_text_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            path.write_text(
                json.dumps({"text": "history text field"}) + "\n",
                encoding="utf-8",
            )
            doc = session_index._parse_history_jsonl(path, "codex_history")
            self.assertIsNotNone(doc)
            assert doc is not None
            self.assertIn("history text field", doc.content)

    def test_returns_none_for_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            doc = session_index._parse_history_jsonl(path, "codex_history")
            self.assertIsNone(doc)


class TestParseShellHistory(unittest.TestCase):
    def test_parses_zsh_history_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".zsh_history"
            path.write_text(
                ": 1700000000:0;git status\n: 1700000001:0;echo hello\n",
                encoding="utf-8",
            )
            doc = session_index._parse_shell_history(path, "shell_zsh")
            self.assertIsNotNone(doc)
            assert doc is not None
            self.assertIn("git status", doc.content)
            self.assertIn("echo hello", doc.content)

    def test_parses_plain_bash_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".bash_history"
            path.write_text("ls -la\npwd\n", encoding="utf-8")
            doc = session_index._parse_shell_history(path, "shell_bash")
            self.assertIsNotNone(doc)
            assert doc is not None
            self.assertIn("ls -la", doc.content)

    def test_returns_none_for_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".bash_history"
            path.write_text("", encoding="utf-8")
            doc = session_index._parse_shell_history(path, "shell_bash")
            self.assertIsNone(doc)


class TestParseSource(unittest.TestCase):
    def test_dispatches_codex_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "s1", "cwd": "/tmp/x", "timestamp": "2026-03-25T00:00:00Z"},
                    }
                )
                + "\n"
                + json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "test message"}})
                + "\n",
                encoding="utf-8",
            )
            doc = session_index._parse_source("codex_session", path)
            self.assertIsNotNone(doc)

    def test_dispatches_shell_zsh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".zsh_history"
            path.write_text("ls -la\n", encoding="utf-8")
            doc = session_index._parse_source("shell_zsh", path)
            self.assertIsNotNone(doc)

    def test_dispatches_codex_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            path.write_text(json.dumps({"display": "some command"}) + "\n", encoding="utf-8")
            doc = session_index._parse_source("codex_history", path)
            self.assertIsNotNone(doc)

    def test_returns_none_for_unknown_source_type(self) -> None:
        result = session_index._parse_source("unknown_type", Path("/tmp/file.txt"))
        self.assertIsNone(result)


class TestUpdateSourceCache(unittest.TestCase):
    def test_updates_cache_when_ttl_positive(self) -> None:
        items = [("codex_session", Path("/tmp/a.jsonl"))]
        now = time.monotonic()
        with mock.patch.object(session_index, "SOURCE_CACHE_TTL_SEC", 60):
            session_index._update_source_cache(items, now, "/home/user")
        self.assertEqual(session_index._SOURCE_CACHE["items"], items)
        self.assertEqual(session_index._SOURCE_CACHE["home"], "/home/user")

    def test_no_cache_when_ttl_zero(self) -> None:
        # Reset cache first
        session_index._SOURCE_CACHE["items"] = []
        session_index._SOURCE_CACHE["expires_at"] = 0.0
        items = [("codex_session", Path("/tmp/b.jsonl"))]
        now = time.monotonic()
        with mock.patch.object(session_index, "SOURCE_CACHE_TTL_SEC", 0):
            session_index._update_source_cache(items, now, "/home/user")
        # When TTL is 0, cache is not updated
        self.assertEqual(session_index._SOURCE_CACHE["items"], [])


class TestIterSourcesCache(unittest.TestCase):
    def test_returns_cached_items_when_fresh(self) -> None:
        cached_items = [("codex_session", Path("/tmp/cached.jsonl"))]
        now = time.monotonic()
        session_index._SOURCE_CACHE["items"] = cached_items
        session_index._SOURCE_CACHE["expires_at"] = now + 100.0
        session_index._SOURCE_CACHE["home"] = str(session_index._home())
        with mock.patch.object(session_index, "SOURCE_CACHE_TTL_SEC", 60):
            with mock.patch.object(session_index, "EXPERIMENTAL_SYNC_BACKEND", ""):
                result = session_index._iter_sources()
        self.assertEqual(result, cached_items)

    def test_discovers_shell_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            zsh_history = home / ".zsh_history"
            zsh_history.write_text("git status\n", encoding="utf-8")
            # Clear cache
            session_index._SOURCE_CACHE["expires_at"] = 0.0
            with mock.patch.object(session_index, "_home", return_value=home):
                with mock.patch.object(session_index, "EXPERIMENTAL_SYNC_BACKEND", ""):
                    result = session_index._iter_sources()
            source_types = [st for st, _ in result]
            self.assertIn("shell_zsh", source_types)


class TestFetchRows(unittest.TestCase):
    def test_returns_rows_matching_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute(
                        """INSERT INTO session_documents(
                            file_path, source_type, session_id, title, content,
                            created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            "/tmp/test.jsonl",
                            "codex_session",
                            "s1",
                            "/tmp/project",
                            "NotebookLM integration content",
                            "2026-03-25T00:00:00Z",
                            1700000000,
                            100,
                            200,
                            1700000000,
                        ),
                    )
                    conn.commit()
                    rows = session_index._fetch_rows(conn, ["NotebookLM"])
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["session_id"], "s1")
                finally:
                    conn.close()

    def test_returns_all_docs_when_no_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute(
                        """INSERT INTO session_documents(
                            file_path, source_type, session_id, title, content,
                            created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            "/tmp/test2.jsonl",
                            "codex_session",
                            "s2",
                            "/tmp/project2",
                            "some content",
                            "2026-03-25T00:00:00Z",
                            1700000000,
                            100,
                            200,
                            1700000000,
                        ),
                    )
                    conn.commit()
                    rows = session_index._fetch_rows(conn, [])
                    self.assertGreaterEqual(len(rows), 1)
                finally:
                    conn.close()


class TestRankRows(unittest.TestCase):
    def test_scores_matching_rows_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute(
                        """INSERT INTO session_documents(
                            file_path, source_type, session_id, title, content,
                            created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            "/tmp/rank_test.jsonl",
                            "codex_session",
                            "rank-session",
                            "/tmp/project",
                            "NotebookLM integration result",
                            "2026-03-25T00:00:00Z",
                            1700000000,
                            100,
                            200,
                            1700000000,
                        ),
                    )
                    conn.commit()
                    rows = session_index._fetch_rows(conn, ["NotebookLM"])
                    ranked = session_index._rank_rows(rows, ["NotebookLM"])
                    self.assertGreater(len(ranked), 0)
                    self.assertGreater(ranked[0][0], 0)
                finally:
                    conn.close()

    def test_skips_cwd_title_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            cwd_str = str(Path.cwd().resolve())
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute(
                        """INSERT INTO session_documents(
                            file_path, source_type, session_id, title, content,
                            created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            "/tmp/cwd_test.jsonl",
                            "codex_session",
                            "cwd-session",
                            cwd_str,
                            "NotebookLM content in cwd",
                            "2026-03-25T00:00:00Z",
                            1700000000,
                            100,
                            200,
                            1700000000,
                        ),
                    )
                    conn.commit()
                    rows = session_index._fetch_rows(conn, ["NotebookLM"])
                    ranked_skip = session_index._rank_rows(rows, ["NotebookLM"], skip_cwd_title=True)
                    # The cwd-titled row should be filtered out
                    for _, row in ranked_skip:
                        self.assertNotEqual(row["title"], cwd_str)
                finally:
                    conn.close()


class TestSyncSessionIndexRemovesStale(unittest.TestCase):
    def test_removes_stale_db_entries_when_source_not_discovered(self) -> None:
        """Index entries whose files are no longer discovered are pruned on sync."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
            codex_root.mkdir(parents=True)
            session_file = codex_root / "stale_session.jsonl"
            session_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "stale-session",
                                    "cwd": "/tmp/stale",
                                    "timestamp": "2026-03-25T00:00:00Z",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "user_message", "message": "stale session content r11"},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            db_path = root / "session_index.db"
            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False),
            ):
                # First sync — adds the session
                first = session_index.sync_session_index(force=True)
                self.assertEqual(first["added"], 1)
                canonical = session_index._normalize_file_path(session_file)
                # Second sync with empty sources (simulating file was removed from discovery)
                with mock.patch.object(session_index, "_iter_sources", return_value=[]):
                    session_index.sync_session_index(force=True)
            # The previously indexed path should now be removed
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT file_path FROM session_documents WHERE file_path = ?", (canonical,)
                ).fetchall()
                self.assertEqual(len(rows), 0)
            finally:
                conn.close()


class TestSyncSchemaVersionChange(unittest.TestCase):
    def test_schema_version_change_forces_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_root = root / ".codex" / "sessions" / "2026" / "03" / "25"
            codex_root.mkdir(parents=True)
            (codex_root / "session.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {
                                    "id": "schema-test",
                                    "cwd": "/tmp/x",
                                    "timestamp": "2026-03-25T00:00:00Z",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "user_message", "message": "test content for schema"},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            db_path = root / "session_index.db"
            with (
                mock.patch.object(session_index, "_home", return_value=root),
                mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False),
            ):
                first = session_index.sync_session_index(force=True)
                self.assertEqual(first["added"], 1)

                # Simulate schema version change
                with mock.patch.object(session_index, "SESSION_INDEX_SCHEMA_VERSION", "old-version"):
                    second = session_index.sync_session_index(force=False)
                    # Should force reindex due to schema version mismatch
                    self.assertGreaterEqual(second["added"], 1)


class TestBuildSnippetFallbacks(unittest.TestCase):
    def test_returns_empty_for_empty_text(self) -> None:
        result = session_index._build_snippet("", ["term"])
        self.assertEqual(result, "")

    def test_falls_back_to_first_chars_when_no_match(self) -> None:
        text = "abcdefghijklmnopqrstuvwxyz" * 10
        result = session_index._build_snippet(text, ["xyz_no_match"])
        self.assertTrue(len(result) > 0)

    def test_finds_summary_marker(self) -> None:
        text = "Some preamble text. " + "变更概览：very important change." + " More text."
        result = session_index._build_snippet(text, ["no_match_term"])
        self.assertIn("变更概览", result)

    def test_prefers_conclusion_window_over_early_match(self) -> None:
        text = "NotebookLM early mention. " + "x" * 500 + " 最终交付：NotebookLM final decision confirmed."
        result = session_index._build_snippet(text, ["NotebookLM"])
        # Should prefer the later occurrence near "最终交付"
        self.assertIn("最终交付", result)


class TestFormatSearchResultsEmpty(unittest.TestCase):
    def test_returns_no_matches_message(self) -> None:
        with mock.patch.object(session_index, "_search_rows", return_value=[]):
            result = session_index.format_search_results("nonexistent_query_xyz")
        self.assertEqual(result, "No matches found in local session index.")


class TestHealthPayload(unittest.TestCase):
    def test_health_payload_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "session_index.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                with mock.patch.object(session_index, "_iter_sources", return_value=[]):
                    payload = session_index.health_payload()
        self.assertIn("session_index_db_exists", payload)
        self.assertIn("total_sessions", payload)
        self.assertIn("latest_epoch", payload)
        self.assertIn("sync", payload)
        self.assertTrue(payload["session_index_db_exists"])


class TestNativeSearchRowsEmptyQuery(unittest.TestCase):
    def test_returns_empty_for_empty_query(self) -> None:
        result = session_index._native_search_rows("", limit=10)
        self.assertEqual(result, [])

    def test_returns_empty_when_backend_not_configured(self) -> None:
        with mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", ""):
            result = session_index._native_search_rows("NotebookLM", limit=10)
        self.assertEqual(result, [])


class TestNativeSearchRowsNativeFailure(unittest.TestCase):
    def test_returns_empty_on_os_error(self) -> None:
        mock_cn = mock.MagicMock()
        mock_cn.run_native_scan.side_effect = OSError("no binary")
        with (
            mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "go"),
            mock.patch.object(session_index, "_get_context_native", return_value=mock_cn),
        ):
            result = session_index._native_search_rows("NotebookLM", limit=5)
        self.assertEqual(result, [])

    def test_returns_empty_on_nonzero_returncode(self) -> None:
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_cn = mock.MagicMock()
        mock_cn.run_native_scan.return_value = mock_result
        with (
            mock.patch.object(session_index, "EXPERIMENTAL_SEARCH_BACKEND", "go"),
            mock.patch.object(session_index, "_get_context_native", return_value=mock_cn),
        ):
            result = session_index._native_search_rows("NotebookLM", limit=5)
        self.assertEqual(result, [])


class TestFetchSessionDocsByPathsEmpty(unittest.TestCase):
    def test_returns_empty_dict_for_no_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    result = session_index._fetch_session_docs_by_paths(conn, [])
                    self.assertEqual(result, {})
                finally:
                    conn.close()

    def test_skips_empty_path_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    result = session_index._fetch_session_docs_by_paths(conn, ["", "  ", ""])
                    self.assertEqual(result, {})
                finally:
                    conn.close()


class TestEnrichNativeRowsNoDoc(unittest.TestCase):
    def test_uses_snippet_when_no_doc_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with mock.patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db_path)}, clear=False):
                session_index.ensure_session_db()
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    rows = [
                        {
                            "file_path": "/tmp/nonexistent.jsonl",
                            "snippet": "fallback snippet text",
                            "source_type": "native_session",
                            "session_id": "unknown",
                            "title": "/tmp/nonexistent.jsonl",
                        }
                    ]
                    enriched = session_index._enrich_native_rows(rows, conn, ["fallback"], limit=5)
                    self.assertEqual(len(enriched), 1)
                    self.assertIn("fallback", enriched[0]["snippet"])
                finally:
                    conn.close()


class TestIterJsonlObjects(unittest.TestCase):
    def test_yields_valid_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            path.write_text(
                '{"key": "value1"}\n{"key": "value2"}\n',
                encoding="utf-8",
            )
            objects = list(session_index._iter_jsonl_objects(path))
            self.assertEqual(len(objects), 2)

    def test_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            path.write_text(
                '{"key": "value"}\n\n\n',
                encoding="utf-8",
            )
            objects = list(session_index._iter_jsonl_objects(path))
            self.assertEqual(len(objects), 1)

    def test_skips_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            path.write_text(
                '{"key": "value"}\nnot valid json\n{"key2": "value2"}\n',
                encoding="utf-8",
            )
            objects = list(session_index._iter_jsonl_objects(path))
            self.assertEqual(len(objects), 2)


class TestMakeFlatDoc(unittest.TestCase):
    def test_returns_none_for_empty_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            path.touch()
            doc = session_index._make_flat_doc(path, "test_type", [], int(path.stat().st_mtime))
            self.assertIsNone(doc)

    def test_builds_doc_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            path.touch()
            doc = session_index._make_flat_doc(path, "test_type", ["line1", "line2"], int(path.stat().st_mtime))
            self.assertIsNotNone(doc)
            assert doc is not None
            self.assertIn("line1", doc.content)


class TestLooksLikePathOnlyContent(unittest.TestCase):
    def test_empty_title_returns_false(self) -> None:
        self.assertFalse(session_index._looks_like_path_only_content("", "/tmp/path"))

    def test_title_content_mismatch_returns_false(self) -> None:
        self.assertFalse(session_index._looks_like_path_only_content("/tmp/a", "/tmp/b"))

    def test_path_with_period_returns_false(self) -> None:
        self.assertFalse(session_index._looks_like_path_only_content("/tmp/test.py", "/tmp/test.py"))


if __name__ == "__main__":
    unittest.main()
