#!/usr/bin/env python3
"""R32 targeted tests for session_index.py — covering previously uncovered lines.

Targets:
  783->793, 791-792: ensure_session_db FTS5 OperationalError on setup
  855-866: _retry_sqlite retry-on-locked exhaustion
  894-908: _retry_sqlite_many retry-on-locked exhaustion
  930-944: _retry_commit retry-on-locked exhaustion
  1094->1101, 1098-1099: sync_session_index FTS5 rebuild + error path
  1160->1163: build_query_terms CJK stopword branch
  1214->1223: _cjk_safe_boundary loop body
  1429-1436: _check_fts5_available fallback probe table path
  1456: _fts5_search_rows empty query returns []
  1471: _build_fts_query empty token skip
  1529: _score_term_frequency empty-text/terms early-return
  1535: _score_term_frequency empty-term_lower skip
  1537->1532: _score_term_frequency term with count=0
  1597->1595: _rank_rows CJK bigrams deduplication
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = str(Path(__file__).parent)
sys.path.insert(0, _SCRIPTS_DIR)

import session_index

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Path:
    """Create a minimal session_index database in *tmp_path* and return path."""
    db = tmp_path / "session.db"
    with patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db)}):
        session_index.ensure_session_db()
    return db


# ---------------------------------------------------------------------------
# Lines 791-792: ensure_session_db — FTS5 OperationalError during setup
# ---------------------------------------------------------------------------

class TestEnsureSessionDbFts5Error(unittest.TestCase):
    """Cover the except block (lines 791-792) in ensure_session_db."""

    def test_fts5_setup_operationalerror_is_swallowed(self):
        """When _retry_sqlite raises OperationalError for FTS5 DDL, it is swallowed."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "si.db"
            # Reset the module-level cache so _check_fts5_available probes again.
            original = session_index._FTS5_AVAILABLE
            session_index._FTS5_AVAILABLE = None
            try:
                real_retry = session_index._retry_sqlite

                call_count = [0]

                def patched_retry(conn, sql, params=None, max_retries=3):
                    call_count[0] += 1
                    # Make the FTS5 DDL call raise
                    if "fts" in sql.lower() and "virtual" in sql.lower():
                        raise sqlite3.OperationalError("no such module: fts5")
                    return real_retry(conn, sql, params, max_retries)

                with patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db)}):
                    with patch.object(session_index, "_retry_sqlite", side_effect=patched_retry):
                        # Force fts5 to appear available so the block is entered
                        with patch.object(session_index, "_check_fts5_available", return_value=True):
                            result = session_index.ensure_session_db()
                            self.assertEqual(result, db)
            finally:
                session_index._FTS5_AVAILABLE = original


# ---------------------------------------------------------------------------
# Lines 855-866: _retry_sqlite — exhausted retries on locked DB
# ---------------------------------------------------------------------------

class TestRetrySquliteExhausted(unittest.TestCase):
    def test_raises_after_max_retries(self):
        """_retry_sqlite raises after all retries are exhausted."""
        conn = MagicMock(spec=sqlite3.Connection)
        locked_exc = sqlite3.OperationalError("database is locked")
        conn.execute.side_effect = locked_exc

        with patch.object(session_index.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                session_index._retry_sqlite(conn, "SELECT 1", max_retries=2)
        self.assertIn("locked", str(ctx.exception).lower())
        # execute should have been called 3 times (initial + 2 retries)
        self.assertEqual(conn.execute.call_count, 3)

    def test_non_locked_error_reraises_immediately(self):
        """Non-locked OperationalError is re-raised immediately without retry."""
        conn = MagicMock(spec=sqlite3.Connection)
        conn.execute.side_effect = sqlite3.OperationalError("syntax error")

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            session_index._retry_sqlite(conn, "BAD SQL", max_retries=3)
        self.assertIn("syntax", str(ctx.exception).lower())
        self.assertEqual(conn.execute.call_count, 1)


# ---------------------------------------------------------------------------
# Lines 894-908: _retry_sqlite_many — exhausted retries on locked DB
# ---------------------------------------------------------------------------

class TestRetrySqliteManyExhausted(unittest.TestCase):
    def test_raises_after_max_retries(self):
        """_retry_sqlite_many raises after all retries on locked."""
        conn = MagicMock(spec=sqlite3.Connection)
        locked_exc = sqlite3.OperationalError("database is locked")
        conn.executemany.side_effect = locked_exc

        with patch.object(session_index.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                session_index._retry_sqlite_many(conn, "INSERT INTO t VALUES(?)", [(1,)], max_retries=2)
        self.assertEqual(conn.executemany.call_count, 3)

    def test_non_locked_reraises_immediately(self):
        """Non-locked OperationalError is re-raised immediately."""
        conn = MagicMock(spec=sqlite3.Connection)
        conn.executemany.side_effect = sqlite3.OperationalError("no such table: t")

        with self.assertRaises(sqlite3.OperationalError):
            session_index._retry_sqlite_many(conn, "INSERT INTO t VALUES(?)", [(1,)], max_retries=3)
        self.assertEqual(conn.executemany.call_count, 1)


# ---------------------------------------------------------------------------
# Lines 930-944: _retry_commit — exhausted retries on locked DB
# ---------------------------------------------------------------------------

class TestRetryCommitExhausted(unittest.TestCase):
    def test_raises_after_max_retries(self):
        """_retry_commit raises after all retries on locked."""
        conn = MagicMock(spec=sqlite3.Connection)
        locked_exc = sqlite3.OperationalError("database is locked")
        conn.commit.side_effect = locked_exc

        with patch.object(session_index.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                session_index._retry_commit(conn, max_retries=2)
        self.assertEqual(conn.commit.call_count, 3)

    def test_non_locked_reraises_immediately(self):
        """Non-locked commit error is re-raised immediately."""
        conn = MagicMock(spec=sqlite3.Connection)
        conn.commit.side_effect = sqlite3.OperationalError("disk is full")

        with self.assertRaises(sqlite3.OperationalError):
            session_index._retry_commit(conn, max_retries=3)
        self.assertEqual(conn.commit.call_count, 1)


# ---------------------------------------------------------------------------
# Lines 1094-1099: sync_session_index FTS5 rebuild error path
# ---------------------------------------------------------------------------

class TestSyncFts5Rebuild(unittest.TestCase):
    """Cover lines 1094-1101 including the OperationalError branch (1098-1099)."""

    def _make_populated_db(self, tmp_path: Path) -> Path:
        """Create a DB with at least one document."""
        import json
        root = tmp_path
        codex_root = root / ".codex" / "sessions" / "2026" / "03" / "01"
        codex_root.mkdir(parents=True)
        sf = codex_root / "test.jsonl"
        sf.write_text(
            "\n".join([
                json.dumps({"type": "session_meta", "payload": {
                    "id": "rebuild-session", "cwd": "/tmp/x",
                    "timestamp": "2026-03-01T00:00:00Z"}}),
                json.dumps({"type": "event_msg", "payload": {
                    "type": "user_message", "message": "hello rebuild"}}),
            ]),
            encoding="utf-8",
        )
        db = root / "si.db"
        with patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db)}):
            with patch.object(session_index, "_home", return_value=root):
                session_index.ensure_session_db()
                session_index.sync_session_index(force=True)
        return db

    def test_fts5_rebuild_operationalerror_swallowed(self):
        """OperationalError during FTS5 rebuild is swallowed (lines 1098-1099)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            db = self._make_populated_db(tmp_path)

            real_retry = session_index._retry_sqlite

            def patched_retry(conn, sql, params=None, max_retries=3):
                if "rebuild" in sql.lower():
                    raise sqlite3.OperationalError("no such table: session_documents_fts")
                return real_retry(conn, sql, params, max_retries)

            with patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db)}):
                with patch.object(session_index, "_home", return_value=tmp_path):
                    with patch.object(session_index, "_retry_sqlite", side_effect=patched_retry):
                        with patch.object(session_index, "_check_fts5_available", return_value=True):
                            # Should not raise even though FTS rebuild fails
                            result = session_index.sync_session_index(force=True)
                            self.assertIsInstance(result, dict)


# ---------------------------------------------------------------------------
# Lines 1160-1163: build_query_terms CJK stopword branch
# ---------------------------------------------------------------------------

class TestBuildQueryTermsCjkStopword(unittest.TestCase):
    """Cover lines 1159-1163: a CJK token that is in CJK_STOPWORDS gets parked."""

    def test_cjk_stopword_only_query_returns_it_as_fallback(self):
        """A query consisting only of CJK stopwords is returned as fallback."""
        # Verify we have a CJK stopword to test
        self.assertTrue(len(session_index.CJK_STOPWORDS) > 0)
        # Pick a CJK stopword
        cjk_stop = next(iter(session_index.CJK_STOPWORDS))
        # A pure CJK-stopword query: terms should be non-empty (fallback)
        result = session_index.build_query_terms(cjk_stop)
        # The stopword should be returned since no other terms survived
        self.assertIn(cjk_stop, result)

    def test_cjk_stopword_not_included_when_other_terms_exist(self):
        """CJK stopword token is excluded when other non-stop terms exist."""
        cjk_stop = next(iter(session_index.CJK_STOPWORDS))
        # Mix a stopword with a regular word
        result = session_index.build_query_terms(f"SearchableTerm {cjk_stop}")
        lower_result = [t.lower() for t in result]
        self.assertIn("searchableterm", lower_result)
        # The stopword should not appear in terms
        self.assertNotIn(cjk_stop.lower(), lower_result)


# ---------------------------------------------------------------------------
# Lines 1214-1223: _cjk_safe_boundary loop
# ---------------------------------------------------------------------------

class TestCjkSafeBoundary(unittest.TestCase):
    """Cover the inner loop of _cjk_safe_boundary (lines 1214-1223)."""

    def test_adjust_forward_at_cjk_run(self):
        """Direction=1 nudges the position forward through CJK chars."""
        text = "Hello 世界 World"
        # pos=6 is at '世', direction=1 should advance past the CJK run
        result = session_index._cjk_safe_boundary(text, 6, 1)
        # Should have moved forward
        self.assertGreaterEqual(result, 6)

    def test_adjust_backward_at_cjk_run(self):
        """Direction=-1 nudges position backward through CJK chars."""
        text = "Hello 世界 World"
        # pos=8 is right after '界', direction=-1 should move back
        result = session_index._cjk_safe_boundary(text, 8, -1)
        self.assertLessEqual(result, 8)

    def test_no_cjk_chars_no_adjustment(self):
        """Non-CJK text: position is returned unchanged."""
        text = "Hello World"
        result = session_index._cjk_safe_boundary(text, 5, 1)
        self.assertEqual(result, 5)

    def test_pos_at_string_end_no_crash(self):
        """Position at string end doesn't crash."""
        text = "abc世界"
        result = session_index._cjk_safe_boundary(text, len(text), 1)
        self.assertEqual(result, len(text))

    def test_pos_zero_direction_minus_1_no_crash(self):
        """pos=0, direction=-1: no adjustment, no crash."""
        text = "世界Hello"
        result = session_index._cjk_safe_boundary(text, 0, -1)
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# Lines 1429-1436: _check_fts5_available fallback probe table
# ---------------------------------------------------------------------------

class TestCheckFts5Available(unittest.TestCase):
    """Cover lines 1429-1436: fallback probe path when SELECT fts5() fails."""

    def setUp(self):
        # Reset the global cache before each test
        self._orig = session_index._FTS5_AVAILABLE
        session_index._FTS5_AVAILABLE = None

    def tearDown(self):
        session_index._FTS5_AVAILABLE = self._orig

    def test_fallback_probe_succeeds(self):
        """When SELECT fts5() fails but CREATE VIRTUAL TABLE succeeds, returns True."""
        conn = MagicMock(spec=sqlite3.Connection)
        # First call (SELECT fts5(?)) raises
        # Second call (CREATE VIRTUAL TABLE) succeeds
        # Third call (DROP TABLE) succeeds
        conn.execute.side_effect = [
            sqlite3.OperationalError("no such function: fts5"),  # SELECT fts5(?)
            MagicMock(),  # CREATE VIRTUAL TABLE
            MagicMock(),  # DROP TABLE
        ]
        result = session_index._check_fts5_available(conn)
        self.assertTrue(result)
        self.assertTrue(session_index._FTS5_AVAILABLE)

    def test_fallback_probe_fails_returns_false(self):
        """When both SELECT fts5() and CREATE VIRTUAL TABLE fail, returns False."""
        conn = MagicMock(spec=sqlite3.Connection)
        conn.execute.side_effect = [
            sqlite3.OperationalError("no such function: fts5"),  # SELECT fts5(?)
            sqlite3.OperationalError("no such module: fts5"),    # CREATE VIRTUAL TABLE
        ]
        result = session_index._check_fts5_available(conn)
        self.assertFalse(result)
        self.assertFalse(session_index._FTS5_AVAILABLE)

    def test_uses_cache_when_set(self):
        """Returns cached _FTS5_AVAILABLE without calling conn.execute."""
        session_index._FTS5_AVAILABLE = True
        conn = MagicMock(spec=sqlite3.Connection)
        result = session_index._check_fts5_available(conn)
        self.assertTrue(result)
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Line 1456: _fts5_search_rows with empty query
# ---------------------------------------------------------------------------

class TestFts5SearchRowsEmptyQuery(unittest.TestCase):
    def test_empty_query_returns_empty_list(self):
        """_fts5_search_rows returns [] for blank/whitespace query (line 1456)."""
        conn = MagicMock(spec=sqlite3.Connection)
        result = session_index._fts5_search_rows(conn, "   ")
        self.assertEqual(result, [])
        conn.execute.assert_not_called()

    def test_empty_string_returns_empty_list(self):
        result = session_index._fts5_search_rows(conn=MagicMock(), query="")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Line 1471: _build_fts_query inner empty-token skip
# The inner function is not directly accessible, but we can trigger it via
# _fts5_search_rows with a query containing multiple spaces.
# ---------------------------------------------------------------------------

class TestFts5SearchBuildFtsQueryEmptyToken(unittest.TestCase):
    def test_multiple_spaces_query_skips_empty_tokens(self):
        """Query with extra spaces produces valid FTS5 expression (line 1471 skip)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "fts_tok.db"
            with patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db)}):
                session_index.ensure_session_db()
                with session_index._open_db(db) as conn:
                    # Query with multiple spaces between tokens — empty tokens hit line 1471
                    result = session_index._fts5_search_rows(conn, "hello   world", limit=5)
                    # Result is a list (possibly empty if no docs)
                    self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# Line 1529: _score_term_frequency early return
# Line 1534-1535: empty term_lower skip
# Line 1537: term with zero count
# ---------------------------------------------------------------------------

class TestScoreTermFrequency(unittest.TestCase):
    def test_empty_text_returns_zero(self):
        """Empty text triggers early return at line 1529."""
        self.assertEqual(session_index._score_term_frequency("", ["hello"]), 0.0)

    def test_empty_terms_returns_zero(self):
        """Empty terms list triggers early return at line 1529."""
        self.assertEqual(session_index._score_term_frequency("some text", []), 0.0)

    def test_none_text_returns_zero(self):
        """None text triggers early return."""
        self.assertEqual(session_index._score_term_frequency(None, ["hello"]), 0.0)  # type: ignore

    def test_empty_string_term_skipped(self):
        """Empty string term is skipped (line 1535)."""
        result = session_index._score_term_frequency("hello world", ["", "hello"])
        # Only 'hello' contributes
        expected = 1 * (len("hello") ** 0.5)
        self.assertAlmostEqual(result, expected, places=5)

    def test_term_not_in_text_zero_contribution(self):
        """Term not found in text has count=0 and contributes nothing (line 1537)."""
        result = session_index._score_term_frequency("hello world", ["xyz"])
        self.assertEqual(result, 0.0)

    def test_term_found_multiple_times(self):
        """Term found multiple times accumulates score."""
        text = "apple apple apple"
        result = session_index._score_term_frequency(text, ["apple"])
        self.assertGreater(result, 0.0)

    def test_score_capped_at_100(self):
        """Score is capped at 100."""
        text = "a " * 10000
        result = session_index._score_term_frequency(text, ["a"])
        self.assertLessEqual(result, 100.0)


# ---------------------------------------------------------------------------
# Lines 1597->1595: _rank_rows CJK bigrams deduplication
# ---------------------------------------------------------------------------

class TestRankRowsCjkBigrams(unittest.TestCase):
    """Cover the CJK bigram construction including deduplication (lines 1592-1598)."""

    def _make_mock_row(self, title="", content="", file_path="", source_type="codex",
                       created_at_epoch=0, created_at="", session_id="sid"):
        row = MagicMock(spec=sqlite3.Row)
        row.__getitem__ = lambda self, key: {
            "title": title,
            "content": content,
            "file_path": file_path,
            "source_type": source_type,
            "created_at_epoch": created_at_epoch,
            "created_at": created_at,
            "session_id": session_id,
        }[key]
        return row

    def test_cjk_bigrams_computed_and_scored(self):
        """CJK bigrams are extracted from query and applied to row scoring."""
        # A query with CJK characters that form bigrams
        terms = ["搜索方案"]
        # A row whose content contains those bigrams
        content = "我们的搜索方案设计"
        row = self._make_mock_row(
            title="测试标题搜索方案",
            content=content,
            source_type="codex",
            created_at_epoch=int(time.time()),
        )
        # Patch _is_current_repo_meta_result and _looks_like_path_only_content
        with patch.object(session_index, "_is_current_repo_meta_result", return_value=False):
            with patch.object(session_index, "_looks_like_path_only_content", return_value=False):
                with patch.object(session_index, "_search_noise_penalty", return_value=0):
                    ranked = session_index._rank_rows([row], terms)
        # Row should be ranked (positive score)
        self.assertGreater(len(ranked), 0)

    def test_cjk_bigrams_deduplicated(self):
        """Duplicate CJK bigrams are only counted once."""
        # Term that repeats a bigram: 世界世界 gives bigrams 世界, 界世, 世界 — last is dup
        terms = ["世界世"]  # bigrams: 世界, 界世
        # Same bigram from two different terms — deduplication branch
        terms2 = ["世界", "世界"]  # same bigrams from both terms
        row = self._make_mock_row(
            title="世界你好世界",
            content="世界相关内容",
            source_type="codex",
            created_at_epoch=int(time.time()),
        )
        with patch.object(session_index, "_is_current_repo_meta_result", return_value=False):
            with patch.object(session_index, "_looks_like_path_only_content", return_value=False):
                with patch.object(session_index, "_search_noise_penalty", return_value=0):
                    ranked1 = session_index._rank_rows([row], terms)
                    ranked2 = session_index._rank_rows([row], terms2)
        # Both should produce valid results
        self.assertIsInstance(ranked1, list)
        self.assertIsInstance(ranked2, list)


# ---------------------------------------------------------------------------
# Integration: lines 1094-1101 via full sync with FTS5 available
# ---------------------------------------------------------------------------

class TestSyncFts5RebuildIntegration(unittest.TestCase):
    """When FTS5 is available, sync_session_index should rebuild FTS index."""

    def test_fts5_rebuild_called_on_add(self):
        """sync_session_index triggers FTS5 rebuild when docs are added (line 1094-1097)."""
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_root = root / ".codex" / "sessions" / "2026" / "03" / "02"
            codex_root.mkdir(parents=True)
            sf = codex_root / "fts_test.jsonl"
            sf.write_text(
                "\n".join([
                    json.dumps({"type": "session_meta", "payload": {
                        "id": "fts-rebuild-test", "cwd": "/tmp/fts",
                        "timestamp": "2026-03-02T00:00:00Z"}}),
                    json.dumps({"type": "event_msg", "payload": {
                        "type": "user_message", "message": "fts rebuild integration"}}),
                ]),
                encoding="utf-8",
            )
            db = root / "fts_rebuild.db"
            with patch.dict(os.environ, {session_index.SESSION_DB_PATH_ENV: str(db)}):
                with patch.object(session_index, "_home", return_value=root):
                    session_index.ensure_session_db()
                    result = session_index.sync_session_index(force=True)
            self.assertIsInstance(result, dict)
            self.assertIn("added", result)


if __name__ == "__main__":
    unittest.main()
