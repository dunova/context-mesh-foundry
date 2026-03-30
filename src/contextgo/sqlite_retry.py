#!/usr/bin/env python3
"""Shared SQLite retry-on-busy helpers for ContextGO."""

from __future__ import annotations

import random
import sqlite3
import time
from typing import Any

__all__ = [
    "SQLITE_RETRY_DELAYS",
    "retry_sqlite",
    "retry_sqlite_many",
    "retry_commit",
]

SQLITE_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.5, 2.0)


def retry_sqlite(
    conn: sqlite3.Connection,
    sql: str,
    params: Any = None,
    max_retries: int = 3,
    *,
    _logger: Any = None,
) -> sqlite3.Cursor:
    """Execute *sql* on *conn* with retry-on-busy logic.

    Retries up to *max_retries* times with exponential back-off (0.1 / 0.5 / 2 s)
    when SQLite raises ``OperationalError: database is locked``.  All other
    errors are re-raised immediately.

    Args:
        conn: An open :class:`sqlite3.Connection`.
        sql: SQL statement to execute.
        params: Optional bind parameters (sequence or mapping).
        max_retries: Maximum number of retry attempts (default 3).
        _logger: Optional :class:`logging.Logger` for retry-warning messages.

    Returns:
        The :class:`sqlite3.Cursor` returned by the final successful execute.

    Raises:
        sqlite3.OperationalError: When the database remains locked after all
            retries are exhausted, or for any non-lock operational error.
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(max_retries + 1):
        try:
            if params is not None:
                return conn.execute(sql, params)
            return conn.execute(sql)
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_exc = exc
            if attempt < max_retries:
                delay = SQLITE_RETRY_DELAYS[min(attempt, len(SQLITE_RETRY_DELAYS) - 1)]
                delay = delay * (1 + random.uniform(-0.1, 0.1))
                if _logger is not None:
                    _logger.warning(
                        "retry_sqlite: database locked, retrying in %.1fs (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                time.sleep(delay)
    if last_exc is None:
        raise RuntimeError("retry loop exited without exception")
    raise last_exc


def retry_sqlite_many(
    conn: sqlite3.Connection,
    sql: str,
    params_seq: Any,
    max_retries: int = 3,
    *,
    _logger: Any = None,
) -> sqlite3.Cursor:
    """Like :func:`retry_sqlite` but calls ``executemany`` instead of ``execute``.

    Materialises iterators so retries don't see an exhausted sequence.

    Args:
        conn: An open :class:`sqlite3.Connection`.
        sql: SQL statement to execute against each row in *params_seq*.
        params_seq: Iterable of parameter sequences or mappings.
        max_retries: Maximum number of retry attempts (default 3).
        _logger: Optional :class:`logging.Logger` for retry-warning messages.

    Returns:
        The :class:`sqlite3.Cursor` returned by the final successful executemany.

    Raises:
        sqlite3.OperationalError: When the database remains locked after all
            retries are exhausted, or for any non-lock operational error.
    """
    if not isinstance(params_seq, list):
        params_seq = list(params_seq)
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(max_retries + 1):
        try:
            return conn.executemany(sql, params_seq)
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_exc = exc
            if attempt < max_retries:
                delay = SQLITE_RETRY_DELAYS[min(attempt, len(SQLITE_RETRY_DELAYS) - 1)]
                delay = delay * (1 + random.uniform(-0.1, 0.1))
                if _logger is not None:
                    _logger.warning(
                        "retry_sqlite_many: database locked, retrying in %.1fs (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                time.sleep(delay)
    if last_exc is None:
        raise RuntimeError("retry loop exited without exception")
    raise last_exc


def retry_commit(
    conn: sqlite3.Connection,
    max_retries: int = 3,
    *,
    _logger: Any = None,
) -> None:
    """Commit *conn* with retry-on-busy logic.

    Retries up to *max_retries* times with exponential back-off (0.1 / 0.5 / 2 s)
    when SQLite raises ``OperationalError: database is locked``.

    Args:
        conn: An open :class:`sqlite3.Connection`.
        max_retries: Maximum number of retry attempts (default 3).
        _logger: Optional :class:`logging.Logger` for retry-warning messages.

    Raises:
        sqlite3.OperationalError: When the database remains locked after all
            retries are exhausted, or for any non-lock operational error.
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(max_retries + 1):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            last_exc = exc
            if attempt < max_retries:
                delay = SQLITE_RETRY_DELAYS[min(attempt, len(SQLITE_RETRY_DELAYS) - 1)]
                delay = delay * (1 + random.uniform(-0.1, 0.1))
                if _logger is not None:
                    _logger.warning(
                        "retry_commit: database locked, retrying in %.1fs (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                time.sleep(delay)
    if last_exc is None:
        raise RuntimeError("retry loop exited without exception")
    raise last_exc
