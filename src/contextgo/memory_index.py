#!/usr/bin/env python3
"""Unified local memory index for ContextGO.

Provides SQLite-backed storage, full-text search, timeline navigation,
and import/export of memory observations collected from local session files.
"""

from __future__ import annotations

__all__ = [
    "Observation",
    "ensure_index_db",
    "export_observations_payload",
    "get_index_db_path",
    "get_observations_by_ids",
    "import_observations_payload",
    "index_stats",
    "search_index",
    "strip_private_blocks",
    "sync_index_from_storage",
    "timeline_index",
    "_retry_sqlite",
    "_retry_sqlite_many",
    "_retry_commit",
]

import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from sqlite_retry import retry_commit as _retry_commit
    from sqlite_retry import retry_sqlite as _retry_sqlite
    from sqlite_retry import retry_sqlite_many as _retry_sqlite_many
except ImportError:  # pragma: no cover
    from .sqlite_retry import retry_commit as _retry_commit  # type: ignore[import-not-found]
    from .sqlite_retry import retry_sqlite as _retry_sqlite
    from .sqlite_retry import retry_sqlite_many as _retry_sqlite_many

# ---------------------------------------------------------------------------
# In-process search result cache (TTL-based)
# ---------------------------------------------------------------------------
# Cache TTL in seconds.  Set MEMORY_INDEX_SEARCH_CACHE_TTL=0 to disable.
try:
    _SEARCH_CACHE_TTL: int = int(os.environ.get("MEMORY_INDEX_SEARCH_CACHE_TTL", "5") or "5")
except (ValueError, TypeError):
    _SEARCH_CACHE_TTL: int = 5

# Maximum number of entries in the in-process search cache.
_SEARCH_CACHE_MAX_SIZE: int = 256

# Mapping of cache_key -> (expiry_epoch_float, results)
_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# Module-level set of db paths for which the schema has already been applied.
_SCHEMA_INITIALIZED: set[str] = set()

try:
    from context_config import storage_root
except ImportError:  # pragma: no cover
    from .context_config import storage_root  # type: ignore[import-not-found]


# SQL constants

_DDL_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint      TEXT    UNIQUE NOT NULL,
    source_type      TEXT    NOT NULL,
    session_id       TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    tags_json        TEXT    NOT NULL,
    file_path        TEXT    NOT NULL,
    created_at       TEXT    NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    updated_at_epoch INTEGER NOT NULL
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_obs_created  ON observations(created_at_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_obs_source   ON observations(source_type, created_at_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_obs_session  ON observations(session_id, created_at_epoch DESC)",
    # Accelerates _SQL_FIND_BY_PATH (sync reconciliation) and file_path LIKE filters.
    "CREATE INDEX IF NOT EXISTS idx_obs_filepath ON observations(file_path)",
    # Accelerates _SQL_FIND_BY_FP (fingerprint dedup check).
    # fingerprint already has a UNIQUE constraint but an explicit index name makes
    # query plans clearer and is required for the IF NOT EXISTS guard on schema upgrades.
    "CREATE INDEX IF NOT EXISTS idx_obs_fp       ON observations(fingerprint)",
    # Accelerates combined source_type + epoch range scans that include updated_at.
    "CREATE INDEX IF NOT EXISTS idx_obs_updated  ON observations(updated_at_epoch DESC)",
]

# FTS5 virtual table DDL (content table mirrors the observations base table).
_DDL_FTS5 = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5("
    "title, content, tags_json, "
    "content=observations, content_rowid=rowid"
    ")"
)

# SQL to (re)populate the FTS5 index from the base table.
_SQL_FTS5_REBUILD = "INSERT INTO observations_fts(observations_fts) VALUES ('rebuild')"

# SQL to insert / delete a single row in the FTS5 shadow tables.
_SQL_FTS5_INSERT = "INSERT INTO observations_fts(rowid, title, content, tags_json) VALUES (?, ?, ?, ?)"
_SQL_FTS5_DELETE = "INSERT INTO observations_fts(observations_fts, rowid) VALUES ('delete', ?)"

_SQL_INSERT_OBS = """
    INSERT INTO observations(
        fingerprint, source_type, session_id, title, content,
        tags_json, file_path, created_at, created_at_epoch,
        updated_at_epoch
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_UPDATE_OBS_FULL = """
    UPDATE observations
    SET fingerprint      = ?,
        source_type      = ?,
        session_id       = ?,
        title            = ?,
        content          = ?,
        tags_json        = ?,
        created_at       = ?,
        created_at_epoch = ?,
        updated_at_epoch = ?
    WHERE id = ?
"""

_SQL_TOUCH_OBS = "UPDATE observations SET updated_at_epoch = ? WHERE id = ?"
_SQL_UPDATE_PATH = "UPDATE observations SET file_path = ?, updated_at_epoch = ? WHERE id = ?"
_SQL_DELETE_OBS = "DELETE FROM observations WHERE id = ?"

_SQL_FIND_BY_PATH = (
    "SELECT id, fingerprint FROM observations WHERE file_path = ? ORDER BY updated_at_epoch DESC, id DESC"
)
_SQL_FIND_BY_FP = "SELECT id, file_path FROM observations WHERE fingerprint = ?"
_SQL_FIND_BY_FPS = "SELECT fingerprint FROM observations WHERE fingerprint IN ({})"
_SQL_STALE_LOCAL = "SELECT id, file_path FROM observations WHERE source_type IN ('history', 'conversation')"
_SQL_COUNT = "SELECT COUNT(*) FROM observations"
_SQL_MAX_EPOCH = "SELECT MAX(created_at_epoch) FROM observations"
_SQL_FETCH_BY_IDS = "SELECT * FROM observations WHERE id IN ({}) ORDER BY created_at_epoch DESC"
_SQL_ANCHOR = "SELECT id, created_at_epoch FROM observations WHERE id = ?"
_SQL_BEFORE = "SELECT * FROM observations WHERE created_at_epoch <= ? ORDER BY created_at_epoch DESC LIMIT ?"
_SQL_AFTER = "SELECT * FROM observations WHERE created_at_epoch > ? ORDER BY created_at_epoch ASC LIMIT ?"


# Regex helpers

_PRIVATE_BLOCK_RE = re.compile(r"<private>[\s\S]*?</private>", re.IGNORECASE)
_PRIVATE_TAG_RE = re.compile(r"</?private>", re.IGNORECASE)

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


# Text sanitisation


def strip_private_blocks(text: str) -> str:
    """Remove ``<private>…</private>`` blocks and stray tags from *text*."""
    if not text:
        return ""
    return _PRIVATE_TAG_RE.sub("", _PRIVATE_BLOCK_RE.sub("", text)).strip()


def _sanitize_text(text: str) -> str:
    """Strip private blocks and redact known secret patterns."""
    out = strip_private_blocks(text or "")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("***REDACTED***", out)
    return out.strip()


# Path helpers


def get_index_db_path() -> Path:
    """Return the path to the memory index SQLite database.

    Honours ``MEMORY_INDEX_DB_PATH`` when set; otherwise places the database
    under the configured storage root.
    """
    override = os.environ.get("MEMORY_INDEX_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return storage_root() / "index" / "memory_index.db"


def _history_dirs() -> list[Path]:
    """Return the canonical directories that contain local history files."""
    root = storage_root()
    return [
        root / "resources" / "shared" / "history",
        root / "resources" / "shared" / "conversations",
    ]


# Internal utilities


def _to_epoch(ts: str, fallback: int) -> int:
    """Parse an ISO-8601 timestamp to a Unix epoch integer.

    Returns *fallback* when *ts* is empty or cannot be parsed.
    """
    if not ts:
        return fallback
    try:
        return int(datetime.fromisoformat(ts).timestamp())
    except (ValueError, OverflowError):
        return fallback


@contextmanager
def _open_db(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection with WAL mode and ensure it is closed."""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA cache_size=-8000")
        conn.execute("PRAGMA mmap_size=268435456")
        conn.execute("PRAGMA temp_store=MEMORY")
        yield conn
    finally:
        if conn is not None:
            conn.close()


# FTS5 availability cache: db_path_str -> bool
_FTS5_AVAILABLE_CACHE: dict[str, bool] = {}

# Special characters in the FTS5 query syntax that must be escaped or stripped.
_FTS5_SPECIAL_RE = re.compile(r'["\(\)\[\]:^*]')


def _fts5_available(conn: sqlite3.Connection, db_path: Path) -> bool:
    """Return True if the observations_fts virtual table exists and is usable.

    The result is cached per database path so the table-existence check is
    only executed once per process lifetime per database file.

    Args:
        conn: An open :class:`sqlite3.Connection` to the target database.
        db_path: Filesystem path of the database (used as the cache key).

    Returns:
        ``True`` when FTS5 is available; ``False`` otherwise.
    """
    key = str(db_path)
    if key in _FTS5_AVAILABLE_CACHE:
        return _FTS5_AVAILABLE_CACHE[key]
    try:
        conn.execute("SELECT 1 FROM observations_fts LIMIT 0")
        _FTS5_AVAILABLE_CACHE[key] = True
    except sqlite3.OperationalError:
        _FTS5_AVAILABLE_CACHE[key] = False
    return _FTS5_AVAILABLE_CACHE[key]


def _escape_fts5_query(query: str) -> str:
    """Convert a free-text query string into a safe FTS5 MATCH expression.

    Multi-word queries are converted to AND-connected quoted phrases so that
    each individual token must appear in the document.  FTS5 special
    characters (parentheses, quotes, colons, carets, asterisks) are stripped
    to prevent syntax errors.  The function handles CJK text correctly
    because SQLite's unicode61 tokeniser handles Unicode codepoints natively;
    no special escaping is needed for CJK characters.

    Args:
        query: Raw user-supplied search string.

    Returns:
        A string suitable for use as the right-hand operand of an FTS5 MATCH
        expression, or an empty string when the query is blank after cleaning.

    Examples:
        >>> _escape_fts5_query("hello world")
        '"hello" AND "world"'
        >>> _escape_fts5_query("fast search")
        '"fast" AND "search"'
    """
    cleaned = _FTS5_SPECIAL_RE.sub(" ", query).strip()
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return ""
    # Wrap each token in double-quotes so it is treated as a literal phrase.
    return " AND ".join(f'"{t}"' for t in tokens)


# Domain model


@dataclass
class Observation:
    """A single indexed memory observation."""

    fingerprint: str
    source_type: str
    session_id: str
    title: str
    content: str
    tags_json: str
    file_path: str
    created_at: str
    created_at_epoch: int


# Markdown parsing


def _parse_markdown(path: Path) -> Observation | None:
    """Parse a Markdown file into an :class:`Observation`.

    Returns ``None`` when the file is empty, unreadable, or produces no
    usable content after sanitisation.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    if not text.strip():
        return None

    lines = text.splitlines()
    title = ""
    tags: list[str] = []
    created_at = ""
    body_lines: list[str] = []
    in_content = False

    for ln in lines:
        s = ln.strip()
        if s.startswith("# ") and not title:
            title = s[2:].strip()
        elif s.lower().startswith("tags:"):
            tags = [x.strip() for x in s.split(":", 1)[1].split(",") if x.strip()]
        elif s.lower().startswith("date:"):
            created_at = s.split(":", 1)[1].strip()
        elif s.lower() == "## content":
            in_content = True
        elif in_content:
            body_lines.append(ln)

    if not body_lines:
        body_lines = lines

    content = strip_private_blocks("\n".join(body_lines)).strip()
    title = strip_private_blocks(title).strip() or path.stem
    if not content:
        return None

    try:
        mtime_epoch = int(path.stat().st_mtime)
    except OSError:
        return None
    created_at_epoch = _to_epoch(created_at, mtime_epoch)
    if not created_at:
        created_at = datetime.fromtimestamp(created_at_epoch).isoformat()

    path_str = str(path).replace("\\", "/")
    source_type = "conversation" if "/conversations/" in path_str else "history"
    session_id = path.stem.split("_")[-1] if "_" in path.stem else path.stem

    fingerprint = hashlib.sha256(f"{source_type}|{title}|{content}|{created_at_epoch}".encode()).hexdigest()

    return Observation(
        fingerprint=fingerprint,
        source_type=source_type,
        session_id=session_id,
        title=title[:240],
        content=content,
        tags_json=json.dumps(tags, ensure_ascii=False),
        file_path=str(path),
        created_at=created_at,
        created_at_epoch=created_at_epoch,
    )


# Database schema


def ensure_index_db() -> Path:
    """Ensure the SQLite index database and its schema exist.

    Creates all parent directories as needed.  Also creates the FTS5 virtual
    table ``observations_fts`` and populates it from the base table when it
    did not previously exist.  Returns the resolved path to the database file.
    """
    db_path = get_index_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _db_key = str(db_path)
    if _db_key in _SCHEMA_INITIALIZED:
        return db_path
    with _open_db(db_path) as conn:
        _retry_sqlite(conn, _DDL_OBSERVATIONS)
        for idx_sql in _DDL_INDEXES:
            _retry_sqlite(conn, idx_sql)
        # Create FTS5 virtual table if it does not already exist, then
        # populate it from the base table.  The 'rebuild' command is
        # idempotent: it truncates and repopulates the FTS index from the
        # content= base table, so it is safe to call on every startup.
        try:
            fts_existed = _fts5_available(conn, db_path)
            _retry_sqlite(conn, _DDL_FTS5)
            if not fts_existed:
                # Newly created — rebuild to populate from existing rows.
                _retry_sqlite(conn, _SQL_FTS5_REBUILD)
                # Invalidate the cache entry so the next call re-checks.
                _FTS5_AVAILABLE_CACHE.pop(_db_key, None)
        except sqlite3.OperationalError:
            # FTS5 extension not available in this SQLite build; continue
            # without it — LIKE-based search will be used as the fallback.
            _FTS5_AVAILABLE_CACHE[_db_key] = False
        _retry_commit(conn)
    _SCHEMA_INITIALIZED.add(_db_key)
    return db_path


# Sync


def sync_index_from_storage() -> dict[str, int]:
    """Scan local history directories and reconcile the index database.

    Returns a summary dict with keys ``scanned``, ``added``, ``updated``,
    and ``removed``.
    """
    db_path = ensure_index_db()
    added = updated = removed = scanned = 0
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    seen_local_paths: set[str] = set()

    with _open_db(db_path) as conn:
        for base in _history_dirs():
            if not base.exists():
                continue
            for file_path in sorted(base.glob("*.md")):
                scanned += 1
                seen_local_paths.add(str(file_path))
                obs = _parse_markdown(file_path)
                if obs is None:
                    continue

                same_path_rows = _retry_sqlite(conn, _SQL_FIND_BY_PATH, (obs.file_path,)).fetchall()

                if same_path_rows:
                    keep_id = int(same_path_rows[0]["id"])
                    for dup in same_path_rows[1:]:
                        _retry_sqlite(conn, _SQL_DELETE_OBS, (int(dup["id"]),))
                        removed += 1
                    if str(same_path_rows[0]["fingerprint"]) != obs.fingerprint:
                        _retry_sqlite(
                            conn,
                            _SQL_UPDATE_OBS_FULL,
                            (
                                obs.fingerprint,
                                obs.source_type,
                                obs.session_id,
                                obs.title,
                                obs.content,
                                obs.tags_json,
                                obs.created_at,
                                obs.created_at_epoch,
                                now_epoch,
                                keep_id,
                            ),
                        )
                        updated += 1
                    else:
                        _retry_sqlite(conn, _SQL_TOUCH_OBS, (now_epoch, keep_id))
                    continue

                # Reconcile by fingerprint to handle renames.
                row = _retry_sqlite(conn, _SQL_FIND_BY_FP, (obs.fingerprint,)).fetchone()
                if row:
                    if row["file_path"] != obs.file_path:
                        _retry_sqlite(conn, _SQL_UPDATE_PATH, (obs.file_path, now_epoch, row["id"]))
                        updated += 1
                else:
                    _retry_sqlite(
                        conn,
                        _SQL_INSERT_OBS,
                        (
                            obs.fingerprint,
                            obs.source_type,
                            obs.session_id,
                            obs.title,
                            obs.content,
                            obs.tags_json,
                            obs.file_path,
                            obs.created_at,
                            obs.created_at_epoch,
                            now_epoch,
                        ),
                    )
                    added += 1

        # Remove stale rows whose backing files have been deleted.
        for row in _retry_sqlite(conn, _SQL_STALE_LOCAL).fetchall():
            if row["file_path"] not in seen_local_paths:
                _retry_sqlite(conn, _SQL_DELETE_OBS, (row["id"],))
                removed += 1

        # Rebuild the FTS5 index after bulk DML so the content table stays
        # consistent.  The 'rebuild' command is transactionally safe under WAL.
        if added or updated or removed:
            try:
                if _fts5_available(conn, db_path):
                    conn.execute(_SQL_FTS5_REBUILD)
            except sqlite3.OperationalError as fts_exc:
                import logging as _logging

                _logging.getLogger(__name__).warning("FTS5 rebuild failed: %s", fts_exc)

        _retry_commit(conn)

    return {"scanned": scanned, "added": added, "updated": updated, "removed": removed}


# Query helpers


def _obs_where_clause(query: str, source_type: str) -> tuple[str, list[Any]]:
    """Build a WHERE clause and bind args for observation queries.

    Uses ``COLLATE NOCASE`` for case-insensitive matching, which avoids
    wrapping columns in ``lower()`` and allows SQLite to use column indexes
    when the collation matches the index definition.
    """
    where: list[str] = []
    args: list[Any] = []
    q = strip_private_blocks(query).strip()
    if q:
        # COLLATE NOCASE lets SQLite skip the per-row lower() call and
        # potentially leverage an index with the same collation.
        like_q = f"%{q.lower()}%"
        where.append(
            "(title LIKE ? COLLATE NOCASE OR content LIKE ? COLLATE NOCASE OR tags_json LIKE ? COLLATE NOCASE)"
        )
        args.extend([like_q, like_q, like_q])
    if source_type and source_type != "all":
        where.append("source_type = ?")
        args.append(source_type)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    return where_clause, args


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite row to a plain dictionary with a decoded tags list."""
    tags: list[str] = []
    try:
        loaded = json.loads(row["tags_json"] or "[]")
        if isinstance(loaded, list):
            tags = [str(x) for x in loaded]
    except (json.JSONDecodeError, ValueError):
        pass
    return {
        "id": row["id"],
        "source_type": row["source_type"],
        "session_id": row["session_id"],
        "title": row["title"],
        "content": row["content"],
        "tags": tags,
        "file_path": row["file_path"],
        "created_at": row["created_at"],
        "created_at_epoch": row["created_at_epoch"],
        "fingerprint": row["fingerprint"],
    }


def search_index(
    query: str,
    limit: int = 20,
    offset: int = 0,
    source_type: str = "all",
    date_start_epoch: int | None = None,
    date_end_epoch: int | None = None,
) -> list[dict[str, Any]]:
    """Search the index and return matching observations as dicts.

    All user-supplied values flow through bind parameters; the WHERE clause
    is assembled only from static predicate strings.  Results are cached
    in-process for ``_SEARCH_CACHE_TTL`` seconds so that repeated identical
    queries within a single CLI invocation avoid redundant I/O.

    Args:
        query: Free-text query matched against title, content, and tags.
        limit: Maximum number of results (clamped to 1–200).
        offset: Zero-based result offset for pagination.
        source_type: Filter by source type, or ``"all"`` for no filter.
        date_start_epoch: Inclusive lower bound on ``created_at_epoch``.
        date_end_epoch: Inclusive upper bound on ``created_at_epoch``.

    Returns:
        List of observation dicts ordered by ``created_at_epoch`` descending.
    """
    clamped_limit = max(1, min(limit, 200))
    clamped_offset = max(0, offset)

    db_path = ensure_index_db()

    # Build a stable cache key that includes the DB path so results from
    # different databases (e.g. per-test temp DBs) never cross-contaminate.
    cache_key = json.dumps(
        [str(db_path), query, clamped_limit, clamped_offset, source_type, date_start_epoch, date_end_epoch],
        ensure_ascii=False,
    )
    if _SEARCH_CACHE_TTL > 0:
        now = time.monotonic()
        cached = _SEARCH_CACHE.get(cache_key)
        if cached is not None and cached[0] > now:
            return list(cached[1])

    with _open_db(db_path) as conn:
        results = _search_with_fts5_or_like(
            conn,
            db_path,
            query=query,
            source_type=source_type,
            date_start_epoch=date_start_epoch,
            date_end_epoch=date_end_epoch,
            limit=clamped_limit,
            offset=clamped_offset,
        )

    if _SEARCH_CACHE_TTL > 0:
        # Evict oldest half when cache exceeds max size.
        if len(_SEARCH_CACHE) > _SEARCH_CACHE_MAX_SIZE:
            evict_count = len(_SEARCH_CACHE) // 2
            for _k in list(_SEARCH_CACHE.keys())[:evict_count]:
                _SEARCH_CACHE.pop(_k, None)
        _SEARCH_CACHE[cache_key] = (time.monotonic() + _SEARCH_CACHE_TTL, results)

    return results


def _search_with_fts5_or_like(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    query: str,
    source_type: str,
    date_start_epoch: int | None,
    date_end_epoch: int | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Execute a search against the index, preferring FTS5 over LIKE.

    When *query* is non-empty and the ``observations_fts`` virtual table is
    present, the function issues an FTS5 MATCH query and ranks results by
    BM25 score (best match first).  If the FTS5 query fails for any reason
    (e.g. an unsupported query syntax despite escaping, or the extension
    being unavailable at query time), it transparently falls back to the
    legacy ``LIKE '%term%'`` path via :func:`_obs_where_clause`.

    When *query* is empty the function always uses the LIKE path (which
    simply omits the text predicate) because FTS5 MATCH requires a non-empty
    search term.

    Date range filters are applied identically in both paths.

    Args:
        conn: An open :class:`sqlite3.Connection`.
        db_path: Filesystem path of the database (used for the FTS5 cache).
        query: Raw user search string.
        source_type: Source type filter, or ``"all"`` for no filter.
        date_start_epoch: Inclusive lower bound on ``created_at_epoch``.
        date_end_epoch: Inclusive upper bound on ``created_at_epoch``.
        limit: Maximum rows to return.
        offset: Zero-based pagination offset.

    Returns:
        List of observation dicts.
    """
    q = strip_private_blocks(query).strip()

    # FTS5 path: attempted only when a non-empty query is provided and FTS5 is
    # available.
    if q and _fts5_available(conn, db_path):
        fts_expr = _escape_fts5_query(q)
        if fts_expr:
            try:
                return _execute_fts5_search(
                    conn,
                    fts_expr=fts_expr,
                    source_type=source_type,
                    date_start_epoch=date_start_epoch,
                    date_end_epoch=date_end_epoch,
                    limit=limit,
                    offset=offset,
                )
            except sqlite3.OperationalError:
                # FTS5 query failed — fall through to LIKE path.
                pass

    # LIKE fallback path (always used when query is empty or FTS5 unavailable).
    where_clause, args = _obs_where_clause(query, source_type)
    if date_start_epoch is not None:
        where_clause = (where_clause + " AND" if where_clause else "WHERE") + " created_at_epoch >= ?"
        args.append(date_start_epoch)
    if date_end_epoch is not None:
        where_clause = (where_clause + " AND" if where_clause else "WHERE") + " created_at_epoch <= ?"
        args.append(date_end_epoch)
    sql = f"SELECT * FROM observations {where_clause} ORDER BY created_at_epoch DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def _execute_fts5_search(
    conn: sqlite3.Connection,
    *,
    fts_expr: str,
    source_type: str,
    date_start_epoch: int | None,
    date_end_epoch: int | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Run an FTS5 MATCH query joined back to the observations table.

    Results are ordered by BM25 relevance (ascending value = better match)
    with ``created_at_epoch DESC`` as a tie-breaker.

    Args:
        conn: An open :class:`sqlite3.Connection`.
        fts_expr: Pre-escaped FTS5 MATCH expression.
        source_type: Source type filter, or ``"all"`` for no filter.
        date_start_epoch: Inclusive lower bound on ``created_at_epoch``.
        date_end_epoch: Inclusive upper bound on ``created_at_epoch``.
        limit: Maximum rows to return.
        offset: Zero-based pagination offset.

    Returns:
        List of observation dicts ordered by relevance then recency.
    """
    extra_where: list[str] = []
    extra_args: list[Any] = []

    if source_type and source_type != "all":
        extra_where.append("o.source_type = ?")
        extra_args.append(source_type)
    if date_start_epoch is not None:
        extra_where.append("o.created_at_epoch >= ?")
        extra_args.append(date_start_epoch)
    if date_end_epoch is not None:
        extra_where.append("o.created_at_epoch <= ?")
        extra_args.append(date_end_epoch)

    and_clause = (" AND " + " AND ".join(extra_where)) if extra_where else ""

    sql = (
        "SELECT o.* FROM observations o "
        "JOIN observations_fts f ON f.rowid = o.rowid "
        f"WHERE f.observations_fts MATCH ?{and_clause} "
        "ORDER BY bm25(observations_fts) ASC, o.created_at_epoch DESC "
        "LIMIT ? OFFSET ?"
    )
    args: list[Any] = [fts_expr, *extra_args, limit, offset]
    return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def timeline_index(
    anchor_id: int,
    depth_before: int = 3,
    depth_after: int = 3,
) -> list[dict[str, Any]]:
    """Return observations surrounding *anchor_id* in chronological order.

    Args:
        anchor_id: Database ID of the central observation.
        depth_before: Number of observations to include before the anchor.
        depth_after: Number of observations to include after the anchor.

    Returns:
        List of observation dicts in ascending chronological order, or an
        empty list when *anchor_id* does not exist.
    """
    db_path = ensure_index_db()
    with _open_db(db_path) as conn:
        anchor = conn.execute(_SQL_ANCHOR, (anchor_id,)).fetchone()
        if not anchor:
            return []

        before_rows = conn.execute(
            _SQL_BEFORE,
            (anchor["created_at_epoch"], max(1, depth_before + 1)),
        ).fetchall()
        after_rows = conn.execute(
            _SQL_AFTER,
            (anchor["created_at_epoch"], max(0, depth_after)),
        ).fetchall()

        return [_row_to_dict(r) for r in list(reversed(before_rows)) + list(after_rows)]


def get_observations_by_ids(
    ids: list[int],
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch observations by their primary-key IDs.

    Args:
        ids: List of integer observation IDs.
        limit: Maximum number of IDs to query (clamped to 1–300).

    Returns:
        List of matching observation dicts ordered by ``created_at_epoch``
        descending.
    """
    if not ids:
        return []
    db_path = ensure_index_db()
    cleaned = [int(x) for x in ids[: max(1, min(limit, 300))]]
    qmarks = ",".join("?" for _ in cleaned)
    with _open_db(db_path) as conn:
        rows = conn.execute(
            _SQL_FETCH_BY_IDS.format(qmarks),
            cleaned,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def index_stats() -> dict[str, Any]:
    """Return aggregate statistics for the index database.

    Returns:
        Dict with ``db_path``, ``total_observations``, and ``latest_epoch``.
    """
    db_path = ensure_index_db()
    with _open_db(db_path) as conn:
        total = conn.execute(_SQL_COUNT).fetchone()[0]
        newest = conn.execute(_SQL_MAX_EPOCH).fetchone()[0]
    return {
        "db_name": db_path.name,
        "total_observations": int(total or 0),
        "latest_epoch": int(newest or 0),
    }


# Export / Import


def export_observations_payload(
    query: str = "",
    *,
    limit: int = 5000,
    source_type: str = "all",
) -> dict[str, Any]:
    """Sync the index and return a serialisable export payload.

    Args:
        query: Optional free-text filter applied during export.
        limit: Maximum number of observations to include (clamped to 1–50 000).
        source_type: Source type filter, or ``"all"`` for no filter.

    Returns:
        Dict with ``exported_at``, ``query``, ``source_type``, ``sync``,
        ``total_observations``, and ``observations``.
    """
    sync_info = sync_index_from_storage()
    target = max(1, min(int(limit), 50_000))

    # Fetch all matching rows in a single paginated pass using one connection.
    db_path = ensure_index_db()
    rows: list[dict[str, Any]] = []
    page = 200

    with _open_db(db_path) as conn:
        where_clause, args = _obs_where_clause(query, source_type)

        offset = 0
        while len(rows) < target:
            batch_limit = min(page, target - len(rows))
            sql = f"SELECT * FROM observations {where_clause} ORDER BY created_at_epoch DESC LIMIT ? OFFSET ?"
            batch = conn.execute(sql, [*args, batch_limit, offset]).fetchall()
            if not batch:
                break
            rows.extend(_row_to_dict(r) for r in batch)
            if len(batch) < page:
                break
            offset += len(batch)

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "source_type": source_type,
        "sync": sync_info,
        "total_observations": len(rows),
        "observations": rows,
    }


def _normalize_import_observation(raw: dict[str, Any]) -> dict[str, Any]:
    """Sanitise and normalise a single raw import record.

    Strips private blocks and known secret patterns from all text fields,
    rejects absolute local paths, and derives a deterministic fingerprint
    when none is supplied.
    """
    raw_tags = raw.get("tags") or []
    if not isinstance(raw_tags, list):
        raw_tags = [raw_tags]
    clean_tags = [cleaned for tag in raw_tags if (cleaned := _sanitize_text(str(tag))[:80])]

    raw_path = _sanitize_text(str(raw.get("file_path") or "import://json"))[:300]
    if raw_path.startswith(("/", "~", "\\")) or (len(raw_path) >= 2 and raw_path[1] == ":"):
        raw_path = "import://local-path-redacted"

    title = _sanitize_text(str(raw.get("title") or "imported memory"))[:240]
    content = _sanitize_text(str(raw.get("content") or ""))
    created_at_epoch = int(raw.get("created_at_epoch") or int(datetime.now(timezone.utc).timestamp()))

    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint and content:
        src = raw.get("source_type") or "import"
        fingerprint = hashlib.sha256(f"{src}|{title}|{content}|{created_at_epoch}".encode()).hexdigest()

    return {
        "fingerprint": fingerprint,
        "source_type": str(raw.get("source_type") or "import"),
        "session_id": str(raw.get("session_id") or "imported"),
        "title": title,
        "content": content,
        "tags_json": json.dumps(clean_tags, ensure_ascii=False),
        "file_path": raw_path,
        "created_at": str(raw.get("created_at") or datetime.now(timezone.utc).isoformat()),
        "created_at_epoch": created_at_epoch,
    }


def import_observations_payload(
    payload: dict[str, Any],
    *,
    sync_from_storage: bool = True,
) -> dict[str, Any]:
    """Import observations from an export payload into the local index.

    Skips records that already exist (matched by fingerprint) and records
    with no usable content.  Duplicate detection is done in a single batch
    query rather than per-row lookups.

    Args:
        payload: Dict containing an ``observations`` list, as produced by
            :func:`export_observations_payload`.
        sync_from_storage: When ``True``, trigger a storage sync after
            inserting imported records.

    Returns:
        Dict with ``inserted``, ``skipped``, and ``db_path``.

    Raises:
        ValueError: When ``payload["observations"]`` is not a list.
    """
    observations = payload.get("observations") or []
    if not isinstance(observations, list):
        raise ValueError("invalid payload: observations must be a list")

    db_path = ensure_index_db()
    inserted = 0
    skipped = 0
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    # Normalise all incoming records and drop those with no usable content.
    candidates: list[dict[str, Any]] = []
    for raw in observations:
        if not isinstance(raw, dict):
            continue
        obs = _normalize_import_observation(raw)
        if not obs["fingerprint"] or not obs["content"].strip():
            skipped += 1
        else:
            candidates.append(obs)

    if candidates:
        with _open_db(db_path) as conn:
            # Batch-check which fingerprints already exist.
            # Chunk into groups of 900 to stay under SQLite's 999 bind-param limit.
            fps = [c["fingerprint"] for c in candidates]
            existing: set[str] = set()
            _CHUNK = 900
            for _i in range(0, len(fps), _CHUNK):
                _chunk = fps[_i : _i + _CHUNK]
                qmarks = ",".join("?" for _ in _chunk)
                existing.update(
                    row["fingerprint"]
                    for row in _retry_sqlite(conn, _SQL_FIND_BY_FPS.format(qmarks), _chunk).fetchall()
                )

            to_insert: list[tuple[Any, ...]] = []
            for obs in candidates:
                if obs["fingerprint"] in existing:
                    skipped += 1
                    continue
                to_insert.append(
                    (
                        obs["fingerprint"],
                        obs["source_type"],
                        obs["session_id"],
                        obs["title"],
                        obs["content"],
                        obs["tags_json"],
                        obs["file_path"],
                        obs["created_at"],
                        obs["created_at_epoch"],
                        now_epoch,
                    )
                )

            if to_insert:
                _retry_sqlite_many(conn, _SQL_INSERT_OBS, to_insert)
                inserted = len(to_insert)
                # Keep FTS5 index consistent after bulk inserts.
                try:
                    if _fts5_available(conn, db_path):
                        conn.execute(_SQL_FTS5_REBUILD)
                except sqlite3.OperationalError:
                    pass  # FTS5 not available; LIKE fallback will be used.

            _retry_commit(conn)

    if sync_from_storage:
        sync_index_from_storage()

    return {"inserted": inserted, "skipped": skipped, "db_path": str(db_path)}
