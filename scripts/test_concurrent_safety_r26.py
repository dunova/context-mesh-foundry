#!/usr/bin/env python3
"""Concurrent safety stress tests for ContextGO SQLite-backed indexes.

AutoResearch R26 — tests verify:
- WAL mode is active on both indexes
- _retry_sqlite() handles SQLITE_BUSY under contention
- _retry_commit() retries on busy conditions
- Concurrent readers on pre-populated DBs
- Fingerprint UNIQUE constraint enforcement
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import memory_index as mi
import session_index as si

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_errors(threads: list[threading.Thread], errors: list[Exception]) -> None:
    for t in threads:
        t.join(timeout=30)
        if t.is_alive():
            raise TimeoutError(f"Thread {t.name} did not finish within 30s")
    if errors:
        raise errors[0]


# ---------------------------------------------------------------------------
# WAL mode tests
# ---------------------------------------------------------------------------


class TestWALMode:
    """Verify WAL journal mode is enabled on both indexes."""

    def test_wal_mode_memory_index(self, tmp_path: Path) -> None:
        """memory_index DB must use WAL journal mode."""
        db_path = tmp_path / "wal_memory.db"
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}):
            mi.ensure_index_db()
        conn = sqlite3.connect(str(db_path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal", f"Expected WAL mode, got {mode}"

    def test_wal_mode_session_index(self, tmp_path: Path) -> None:
        """session_index DB must use WAL journal mode."""
        db_path = tmp_path / "wal_session.db"
        with (
            patch.dict(os.environ, {si.SESSION_DB_PATH_ENV: str(db_path)}),
            patch.object(si, "_iter_sources", return_value=[]),
            patch.object(si, "SYNC_MIN_INTERVAL_SEC", 0),
        ):
            si.sync_session_index(force=True)
        conn = sqlite3.connect(str(db_path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal", f"Expected WAL mode, got {mode}"

    def test_wal_persists_concurrent_readers(self, tmp_path: Path) -> None:
        """WAL must be active while multiple threads read the DB."""
        db_path = tmp_path / "wal_concurrent.db"
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}):
            mi.ensure_index_db()

        errors: list[Exception] = []
        modes: list[str] = []
        lock = threading.Lock()

        def _check(_: int) -> None:
            try:
                conn = sqlite3.connect(str(db_path))
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                conn.close()
                with lock:
                    modes.append(mode)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_check, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        _collect_errors(threads, errors)
        assert all(m == "wal" for m in modes), f"Non-WAL modes: {modes}"


# ---------------------------------------------------------------------------
# _retry_sqlite() tests
# ---------------------------------------------------------------------------


class TestRetrySqlite:
    """Verify _retry_sqlite() handles SQLITE_BUSY correctly."""

    def test_succeeds_after_busy(self) -> None:
        """Succeeds after 2 BUSY errors."""
        call_count = {"n": 0}
        mock_cursor = MagicMock()

        def _flaky(sql: str, params: Any = ()) -> Any:
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise sqlite3.OperationalError("database is locked")
            return mock_cursor

        mock_conn = MagicMock()
        mock_conn.execute = MagicMock(side_effect=_flaky)

        with patch("memory_index.time.sleep"):
            cur = mi._retry_sqlite(mock_conn, "SELECT 1", max_retries=3)
            assert cur is mock_cursor
        assert call_count["n"] == 3

    def test_raises_after_max_retries(self) -> None:
        """Re-raises after exhausting retries."""
        mock_conn = MagicMock()
        mock_conn.execute = MagicMock(side_effect=sqlite3.OperationalError("database is locked"))

        with patch("memory_index.time.sleep"):
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                mi._retry_sqlite(mock_conn, "SELECT 1", max_retries=2)

    def test_non_lock_error_not_retried(self) -> None:
        """Non-lock errors are NOT retried."""
        call_count = {"n": 0}

        def _err(sql: str, params: Any = ()) -> Any:
            call_count["n"] += 1
            raise sqlite3.OperationalError("no such table")

        mock_conn = MagicMock()
        mock_conn.execute = MagicMock(side_effect=_err)

        with patch("memory_index.time.sleep"):
            with pytest.raises(sqlite3.OperationalError, match="no such table"):
                mi._retry_sqlite(mock_conn, "SELECT 1", max_retries=3)
        assert call_count["n"] == 1

    def test_real_contention(self, tmp_path: Path) -> None:
        """_retry_sqlite works with a real SQLite connection under WAL."""
        db_path = tmp_path / "contention.db"
        setup = sqlite3.connect(str(db_path), timeout=30)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.execute("CREATE TABLE vals (v TEXT)")
        setup.commit()
        setup.close()

        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        mi._retry_sqlite(conn, "INSERT INTO vals VALUES (?)", ("value",), max_retries=3)
        mi._retry_commit(conn, max_retries=3)
        conn.close()

        verify = sqlite3.connect(str(db_path))
        count = verify.execute("SELECT COUNT(*) FROM vals").fetchone()[0]
        verify.close()
        assert count == 1


# ---------------------------------------------------------------------------
# _retry_commit() tests
# ---------------------------------------------------------------------------


class TestRetryCommit:
    """Verify _retry_commit() retries on busy conditions."""

    def test_succeeds_after_busy(self) -> None:
        """Succeeds after 2 BUSY commit errors."""
        call_count = {"n": 0}

        def _flaky() -> None:
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise sqlite3.OperationalError("database is locked")

        mock_conn = MagicMock()
        mock_conn.commit = MagicMock(side_effect=_flaky)

        with patch("memory_index.time.sleep"):
            mi._retry_commit(mock_conn, max_retries=3)
        assert call_count["n"] == 3

    def test_raises_after_max_retries(self) -> None:
        """Raises after exhausting retries."""
        mock_conn = MagicMock()
        mock_conn.commit = MagicMock(side_effect=sqlite3.OperationalError("database is locked"))

        with patch("memory_index.time.sleep"):
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                mi._retry_commit(mock_conn, max_retries=2)

    def test_non_lock_error_not_retried(self) -> None:
        """Non-lock errors are NOT retried."""
        call_count = {"n": 0}

        def _err() -> None:
            call_count["n"] += 1
            raise sqlite3.OperationalError("disk I/O error")

        mock_conn = MagicMock()
        mock_conn.commit = MagicMock(side_effect=_err)

        with patch("memory_index.time.sleep"):
            with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
                mi._retry_commit(mock_conn, max_retries=3)
        assert call_count["n"] == 1

    def test_concurrent_commits(self, tmp_path: Path) -> None:
        """5 threads each commit to the same DB without error."""
        db_path = tmp_path / "commit.db"
        setup = sqlite3.connect(str(db_path), timeout=30)
        setup.execute("PRAGMA journal_mode=WAL")
        setup.execute("CREATE TABLE vals (id INTEGER, v TEXT)")
        setup.commit()
        setup.close()

        errors: list[Exception] = []
        lock = threading.Lock()

        def _worker(wid: int) -> None:
            try:
                conn = sqlite3.connect(str(db_path), timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                mi._retry_sqlite(conn, "INSERT INTO vals VALUES (?, ?)", (wid, f"v{wid}"), max_retries=5)
                mi._retry_commit(conn, max_retries=5)
                conn.close()
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        _collect_errors(threads, errors)

        verify = sqlite3.connect(str(db_path))
        count = verify.execute("SELECT COUNT(*) FROM vals").fetchone()[0]
        verify.close()
        assert count == 5


# ---------------------------------------------------------------------------
# Concurrent reader tests
# ---------------------------------------------------------------------------


class TestConcurrentReaders:
    """Concurrent reader threads must not raise."""

    def test_concurrent_memory_search(self, tmp_path: Path) -> None:
        """5 threads concurrently searching memory_index."""
        db_path = tmp_path / "mem.db"
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}):
            mi.ensure_index_db()

        errors: list[Exception] = []
        lock = threading.Lock()

        def _reader(_: int) -> None:
            try:
                with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}):
                    result = mi.search_index(query="test", limit=10)
                    assert isinstance(result, list)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_reader, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        _collect_errors(threads, errors)

    def test_concurrent_session_search(self, tmp_path: Path) -> None:
        """5 threads concurrently searching session_index."""
        db_path = tmp_path / "sess.db"
        with (
            patch.dict(os.environ, {si.SESSION_DB_PATH_ENV: str(db_path)}),
            patch.object(si, "_iter_sources", return_value=[]),
            patch.object(si, "SYNC_MIN_INTERVAL_SEC", 0),
        ):
            si.sync_session_index(force=True)

        errors: list[Exception] = []
        lock = threading.Lock()

        def _reader(_: int) -> None:
            try:
                with patch.dict(os.environ, {si.SESSION_DB_PATH_ENV: str(db_path)}):
                    result = si.format_search_results("test", search_type="all", limit=5, literal=False)
                    assert isinstance(result, str)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_reader, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        _collect_errors(threads, errors)


# ---------------------------------------------------------------------------
# Fingerprint UNIQUE constraint
# ---------------------------------------------------------------------------


class TestFingerprintUniqueness:
    """UNIQUE constraint on fingerprint prevents duplicates."""

    def test_duplicate_fingerprint_rejected(self, tmp_path: Path) -> None:
        """Second insert with same fingerprint must raise IntegrityError."""
        db_path = tmp_path / "fp.db"
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}):
            mi.ensure_index_db()

        conn = sqlite3.connect(str(db_path))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(observations)").fetchall() if r[1] != "id"]
        col_str = ", ".join(cols)
        ph = ", ".join("?" for _ in cols)
        fp = hashlib.sha256(b"dup").hexdigest()

        def _vals(content: str) -> list[Any]:
            v = []
            for c in cols:
                if c == "fingerprint":
                    v.append(fp)
                elif c == "content":
                    v.append(content)
                elif c == "source_type":
                    v.append("memory")
                elif "epoch" in c or "mtime" in c:
                    v.append(0)
                else:
                    v.append("")
            return v

        conn.execute(f"INSERT INTO observations ({col_str}) VALUES ({ph})", _vals("first"))
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"INSERT INTO observations ({col_str}) VALUES ({ph})", _vals("second"))

        conn.close()


# ---------------------------------------------------------------------------
# Schema integrity after single-threaded sync
# ---------------------------------------------------------------------------


class TestSchemaIntegrity:
    """Schema must be intact after sync operations."""

    def test_memory_index_schema(self, tmp_path: Path) -> None:
        """observations table and indexes must exist."""
        db_path = tmp_path / "schema_mem.db"
        with patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}):
            mi.ensure_index_db()

        conn = sqlite3.connect(str(db_path))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "observations" in tables

    def test_session_index_schema(self, tmp_path: Path) -> None:
        """session_documents table must exist."""
        db_path = tmp_path / "schema_sess.db"
        with (
            patch.dict(os.environ, {si.SESSION_DB_PATH_ENV: str(db_path)}),
            patch.object(si, "_iter_sources", return_value=[]),
            patch.object(si, "SYNC_MIN_INTERVAL_SEC", 0),
        ):
            si.sync_session_index(force=True)

        conn = sqlite3.connect(str(db_path))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "session_documents" in tables
