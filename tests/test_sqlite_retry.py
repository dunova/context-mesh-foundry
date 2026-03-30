"""Tests for scripts/sqlite_retry.py — shared SQLite retry helpers."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from sqlite_retry import (
    SQLITE_RETRY_DELAYS,
    retry_commit,
    retry_sqlite,
    retry_sqlite_many,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _locked_exc() -> sqlite3.OperationalError:
    """Return an OperationalError that looks like a real 'database is locked'."""
    return sqlite3.OperationalError("database is locked")


def _other_exc() -> sqlite3.OperationalError:
    """Return an OperationalError that is NOT a lock error."""
    return sqlite3.OperationalError("no such table: foo")


def _make_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with a simple test table."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# retry_sqlite
# ---------------------------------------------------------------------------


class TestRetrySqlite(unittest.TestCase):
    @patch("sqlite_retry.time.sleep")
    def test_retry_sqlite_success(self, mock_sleep: MagicMock) -> None:
        """execute succeeds on first try — no sleep, returns a Cursor."""
        conn = _make_conn()
        cursor = retry_sqlite(conn, "SELECT 1")
        self.assertIsNotNone(cursor)
        mock_sleep.assert_not_called()

    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.random", return_value=0.5)
    def test_retry_sqlite_locked_then_success(self, mock_uniform: MagicMock, mock_sleep: MagicMock) -> None:
        """First call raises 'database is locked', second call succeeds."""
        good_cursor = MagicMock(spec=sqlite3.Cursor)
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.execute.side_effect = [_locked_exc(), good_cursor]

        result = retry_sqlite(mock_conn, "INSERT INTO t VALUES (1)")

        self.assertIs(result, good_cursor)
        self.assertEqual(mock_conn.execute.call_count, 2)
        mock_sleep.assert_called_once_with(SQLITE_RETRY_DELAYS[0])

    @patch("sqlite_retry.time.sleep")
    def test_retry_sqlite_all_retries_exhausted(self, mock_sleep: MagicMock) -> None:
        """All retries fail — re-raises the last OperationalError."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.execute.side_effect = _locked_exc()

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            retry_sqlite(mock_conn, "SELECT 1", max_retries=2)

        self.assertIn("database is locked", str(ctx.exception).lower())
        # 1 initial attempt + 2 retries = 3 total calls
        self.assertEqual(mock_conn.execute.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("sqlite_retry.time.sleep")
    def test_retry_sqlite_non_lock_error(self, mock_sleep: MagicMock) -> None:
        """Non-lock OperationalError is re-raised immediately without any retry."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.execute.side_effect = _other_exc()

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            retry_sqlite(mock_conn, "SELECT * FROM foo")

        self.assertIn("no such table", str(ctx.exception))
        mock_conn.execute.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("sqlite_retry.time.sleep")
    def test_retry_sqlite_with_params(self, mock_sleep: MagicMock) -> None:
        """Params are forwarded to conn.execute when provided."""
        conn = _make_conn()
        cursor = retry_sqlite(conn, "INSERT INTO t VALUES (?)", (42,))
        self.assertIsNotNone(cursor)
        row = conn.execute("SELECT v FROM t").fetchone()
        self.assertEqual(row[0], 42)
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# retry_sqlite_many
# ---------------------------------------------------------------------------


class TestRetrySqliteMany(unittest.TestCase):
    @patch("sqlite_retry.time.sleep")
    def test_retry_sqlite_many_success(self, mock_sleep: MagicMock) -> None:
        """executemany succeeds on first try."""
        conn = _make_conn()
        cursor = retry_sqlite_many(conn, "INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        self.assertIsNotNone(cursor)
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        self.assertEqual(count, 3)
        mock_sleep.assert_not_called()

    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.random", return_value=0.5)
    def test_retry_sqlite_many_locked(self, mock_uniform: MagicMock, mock_sleep: MagicMock) -> None:
        """executemany retries on lock and succeeds on second attempt."""
        good_cursor = MagicMock(spec=sqlite3.Cursor)
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.executemany.side_effect = [_locked_exc(), good_cursor]

        result = retry_sqlite_many(mock_conn, "INSERT INTO t VALUES (?)", [(1,)])

        self.assertIs(result, good_cursor)
        self.assertEqual(mock_conn.executemany.call_count, 2)
        mock_sleep.assert_called_once_with(SQLITE_RETRY_DELAYS[0])

    @patch("sqlite_retry.time.sleep")
    def test_retry_sqlite_many_non_lock_error(self, mock_sleep: MagicMock) -> None:
        """Non-lock OperationalError in executemany is re-raised immediately."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.executemany.side_effect = _other_exc()

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            retry_sqlite_many(mock_conn, "INSERT INTO t VALUES (?)", [(1,)])

        self.assertIn("no such table", str(ctx.exception))
        mock_conn.executemany.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("sqlite_retry.time.sleep")
    def test_retry_sqlite_many_all_retries_exhausted(self, mock_sleep: MagicMock) -> None:
        """All executemany retries fail — re-raises the last OperationalError."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.executemany.side_effect = _locked_exc()

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            retry_sqlite_many(mock_conn, "INSERT INTO t VALUES (?)", [(1,)], max_retries=2)

        self.assertIn("database is locked", str(ctx.exception).lower())
        self.assertEqual(mock_conn.executemany.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.random", return_value=0.5)
    def test_retry_sqlite_many_logger_warning(self, mock_uniform: MagicMock, mock_sleep: MagicMock) -> None:
        """_logger.warning is called on each retry for retry_sqlite_many."""
        good_cursor = MagicMock(spec=sqlite3.Cursor)
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.executemany.side_effect = [_locked_exc(), good_cursor]

        mock_logger = MagicMock()
        retry_sqlite_many(mock_conn, "INSERT INTO t VALUES (?)", [(1,)], _logger=mock_logger)

        mock_logger.warning.assert_called_once_with(
            "retry_sqlite_many: database locked, retrying in %.2fs (attempt %d/%d)",
            SQLITE_RETRY_DELAYS[0],
            1,
            5,
        )

    @patch("sqlite_retry.time.sleep")
    def test_retry_sqlite_many_materializes_iterator(self, mock_sleep: MagicMock) -> None:
        """Iterator params_seq is materialized to a list before the first attempt."""
        rows_yielded: list[tuple[int]] = []

        def _gen() -> object:
            for i in range(3):
                rows_yielded.append((i,))
                yield (i,)

        good_cursor = MagicMock(spec=sqlite3.Cursor)
        mock_conn = MagicMock(spec=sqlite3.Connection)
        # Fail once so a retry happens; the generator must still supply all rows.
        mock_conn.executemany.side_effect = [_locked_exc(), good_cursor]

        retry_sqlite_many(mock_conn, "INSERT INTO t VALUES (?)", _gen())

        # Generator was exhausted exactly once (materialized before retries).
        self.assertEqual(len(rows_yielded), 3)
        # Both calls receive the same list (materialized copy).
        first_call_params = mock_conn.executemany.call_args_list[0][0][1]
        second_call_params = mock_conn.executemany.call_args_list[1][0][1]
        self.assertIsInstance(first_call_params, list)
        self.assertEqual(first_call_params, second_call_params)


# ---------------------------------------------------------------------------
# retry_commit
# ---------------------------------------------------------------------------


class TestRetryCommit(unittest.TestCase):
    @patch("sqlite_retry.time.sleep")
    def test_retry_commit_success(self, mock_sleep: MagicMock) -> None:
        """commit() works on first try — no sleep, returns None."""
        conn = _make_conn()
        result = retry_commit(conn)
        self.assertIsNone(result)
        mock_sleep.assert_not_called()

    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.random", return_value=0.5)
    def test_retry_commit_locked(self, mock_uniform: MagicMock, mock_sleep: MagicMock) -> None:
        """commit() retries on lock and succeeds on second attempt."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.commit.side_effect = [_locked_exc(), None]

        retry_commit(mock_conn)

        self.assertEqual(mock_conn.commit.call_count, 2)
        mock_sleep.assert_called_once_with(SQLITE_RETRY_DELAYS[0])

    @patch("sqlite_retry.time.sleep")
    def test_retry_commit_non_lock_error(self, mock_sleep: MagicMock) -> None:
        """Non-lock OperationalError in commit() is re-raised immediately."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.commit.side_effect = _other_exc()

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            retry_commit(mock_conn)

        self.assertIn("no such table", str(ctx.exception))
        mock_conn.commit.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.random", return_value=0.5)
    def test_retry_commit_logger_warning(self, mock_uniform: MagicMock, mock_sleep: MagicMock) -> None:
        """_logger.warning is called on each retry for retry_commit."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.commit.side_effect = [_locked_exc(), None]

        mock_logger = MagicMock()
        retry_commit(mock_conn, _logger=mock_logger)

        mock_logger.warning.assert_called_once_with(
            "retry_commit: database locked, retrying in %.2fs (attempt %d/%d)",
            SQLITE_RETRY_DELAYS[0],
            1,
            5,
        )

    @patch("sqlite_retry.time.sleep")
    def test_retry_commit_exhausted(self, mock_sleep: MagicMock) -> None:
        """All commit() retries fail — re-raises the last OperationalError."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.commit.side_effect = _locked_exc()

        with self.assertRaises(sqlite3.OperationalError) as ctx:
            retry_commit(mock_conn, max_retries=2)

        self.assertIn("database is locked", str(ctx.exception).lower())
        # 1 initial + 2 retries = 3 total calls
        self.assertEqual(mock_conn.commit.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)


# ---------------------------------------------------------------------------
# Logger warning integration
# ---------------------------------------------------------------------------


class TestRetryLoggerWarning(unittest.TestCase):
    @patch("sqlite_retry.time.sleep")
    @patch("sqlite_retry.random.random", return_value=0.5)
    def test_retry_logger_warning(self, mock_uniform: MagicMock, mock_sleep: MagicMock) -> None:
        """_logger.warning is called once per retry for retry_sqlite."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        good_cursor = MagicMock(spec=sqlite3.Cursor)
        mock_conn.execute.side_effect = [_locked_exc(), _locked_exc(), good_cursor]

        mock_logger = MagicMock()
        retry_sqlite(mock_conn, "SELECT 1", max_retries=3, _logger=mock_logger)

        # Two lock errors -> two warning calls
        self.assertEqual(mock_logger.warning.call_count, 2)
        # First warning must reference attempt 1/3
        first_call = mock_logger.warning.call_args_list[0]
        self.assertEqual(
            first_call,
            call(
                "retry_sqlite: database locked, retrying in %.2fs (attempt %d/%d)",
                SQLITE_RETRY_DELAYS[0],
                1,
                3,
            ),
        )
        # Second warning must reference attempt 2/3
        second_call = mock_logger.warning.call_args_list[1]
        self.assertEqual(
            second_call,
            call(
                "retry_sqlite: database locked, retrying in %.2fs (attempt %d/%d)",
                SQLITE_RETRY_DELAYS[1],
                2,
                3,
            ),
        )

    @patch("sqlite_retry.time.sleep")
    def test_retry_logger_none_no_error(self, mock_sleep: MagicMock) -> None:
        """No exception is raised when _logger is None and a retry occurs."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        good_cursor = MagicMock(spec=sqlite3.Cursor)
        mock_conn.execute.side_effect = [_locked_exc(), good_cursor]

        # Should not raise even though _logger is None (the default)
        result = retry_sqlite(mock_conn, "SELECT 1")
        self.assertIs(result, good_cursor)


if __name__ == "__main__":
    unittest.main()
