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
]

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from context_config import storage_root
except ImportError:  # pragma: no cover
    from .context_config import storage_root  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Text sanitisation
# ---------------------------------------------------------------------------


def strip_private_blocks(text: str) -> str:
    """Remove ``<private>…</private>`` blocks and stray tags from *text*."""
    if not text:
        return ""
    without_block = _PRIVATE_BLOCK_RE.sub("", text)
    return _PRIVATE_TAG_RE.sub("", without_block).strip()


def _sanitize_text(text: str) -> str:
    """Strip private blocks and redact known secret patterns."""
    out = strip_private_blocks(text or "")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("***REDACTED***", out)
    return out.strip()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_index_db_path() -> Path:
    """Return the path to the memory index SQLite database.

    Honour ``MEMORY_INDEX_DB_PATH`` when set; otherwise place the database
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


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------


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

    mtime_epoch = int(path.stat().st_mtime)
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


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

_DDL = """
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

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_obs_created ON observations(created_at_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_obs_source  ON observations(source_type, created_at_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id, created_at_epoch DESC)",
]


def ensure_index_db() -> Path:
    """Ensure the SQLite index database and its schema exist.

    Creates all parent directories as needed.  Returns the resolved path to
    the database file.
    """
    db_path = get_index_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_DDL)
        for idx_sql in _INDEXES:
            conn.execute(idx_sql)
        conn.commit()
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def sync_index_from_storage() -> dict[str, int]:
    """Scan local history directories and reconcile the index database.

    Returns a summary dict with keys ``scanned``, ``added``, ``updated``,
    and ``removed``.
    """
    db_path = ensure_index_db()
    conn = sqlite3.connect(db_path)
    added = updated = removed = scanned = 0
    now_epoch = int(datetime.now().timestamp())
    seen_local_paths: set[str] = set()

    try:
        for base in _history_dirs():
            if not base.exists():
                continue
            for file_path in sorted(base.glob("*.md")):
                scanned += 1
                seen_local_paths.add(str(file_path))
                obs = _parse_markdown(file_path)
                if obs is None:
                    continue

                # Reconcile by file path first to avoid duplicate rows.
                same_path_rows = conn.execute(
                    "SELECT id, fingerprint FROM observations"
                    " WHERE file_path = ?"
                    " ORDER BY updated_at_epoch DESC, id DESC",
                    (obs.file_path,),
                ).fetchall()

                if same_path_rows:
                    keep_id = int(same_path_rows[0][0])
                    for dup in same_path_rows[1:]:
                        conn.execute("DELETE FROM observations WHERE id = ?", (int(dup[0]),))
                        removed += 1
                    if str(same_path_rows[0][1]) != obs.fingerprint:
                        conn.execute(
                            """
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
                            """,
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
                        conn.execute(
                            "UPDATE observations SET updated_at_epoch = ? WHERE id = ?",
                            (now_epoch, keep_id),
                        )
                    continue

                # Reconcile by fingerprint to handle renames.
                row = conn.execute(
                    "SELECT id, file_path FROM observations WHERE fingerprint = ?",
                    (obs.fingerprint,),
                ).fetchone()
                if row:
                    if row[1] != obs.file_path:
                        conn.execute(
                            "UPDATE observations SET file_path = ?, updated_at_epoch = ? WHERE id = ?",
                            (obs.file_path, now_epoch, row[0]),
                        )
                        updated += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO observations(
                            fingerprint, source_type, session_id, title, content,
                            tags_json, file_path, created_at, created_at_epoch,
                            updated_at_epoch
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
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
        stale = conn.execute(
            "SELECT id, file_path FROM observations WHERE source_type IN ('history', 'conversation')"
        ).fetchall()
        for rid, fpath in stale:
            if fpath not in seen_local_paths:
                conn.execute("DELETE FROM observations WHERE id = ?", (rid,))
                removed += 1

        conn.commit()
    finally:
        conn.close()

    return {"scanned": scanned, "added": added, "updated": updated, "removed": removed}


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


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
    is assembled only from static predicate strings.

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
    db_path = ensure_index_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        where: list[str] = []
        args: list[Any] = []
        q = strip_private_blocks(query).strip()
        if q:
            where.append("(lower(title) LIKE ? OR lower(content) LIKE ? OR lower(tags_json) LIKE ?)")
            like_q = f"%{q.lower()}%"
            args.extend([like_q, like_q, like_q])
        if source_type and source_type != "all":
            where.append("source_type = ?")
            args.append(source_type)
        if date_start_epoch is not None:
            where.append("created_at_epoch >= ?")
            args.append(date_start_epoch)
        if date_end_epoch is not None:
            where.append("created_at_epoch <= ?")
            args.append(date_end_epoch)

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT * FROM observations {where_clause} ORDER BY created_at_epoch DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(limit, 200)), max(0, offset)])
        rows = conn.execute(sql, args).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        anchor = conn.execute(
            "SELECT id, created_at_epoch FROM observations WHERE id = ?",
            (anchor_id,),
        ).fetchone()
        if not anchor:
            return []

        before_rows = conn.execute(
            "SELECT * FROM observations WHERE created_at_epoch <= ? ORDER BY created_at_epoch DESC LIMIT ?",
            (anchor["created_at_epoch"], max(1, depth_before + 1)),
        ).fetchall()
        after_rows = conn.execute(
            "SELECT * FROM observations WHERE created_at_epoch > ? ORDER BY created_at_epoch ASC LIMIT ?",
            (anchor["created_at_epoch"], max(0, depth_after)),
        ).fetchall()

        merged = list(reversed(before_rows)) + list(after_rows)
        return [_row_to_dict(r) for r in merged]
    finally:
        conn.close()


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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cleaned = [int(x) for x in ids[: max(1, min(limit, 300))]]
        qmarks = ",".join("?" for _ in cleaned)
        rows = conn.execute(
            f"SELECT * FROM observations WHERE id IN ({qmarks}) ORDER BY created_at_epoch DESC",
            cleaned,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def index_stats() -> dict[str, Any]:
    """Return aggregate statistics for the index database.

    Returns:
        Dict with ``db_path``, ``total_observations``, and ``latest_epoch``.
    """
    db_path = ensure_index_db()
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        newest = conn.execute("SELECT MAX(created_at_epoch) FROM observations").fetchone()[0]
        return {
            "db_path": str(db_path),
            "total_observations": int(total or 0),
            "latest_epoch": int(newest or 0),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


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
    rows: list[dict[str, Any]] = []
    offset = 0
    page = 200

    while len(rows) < target:
        batch = search_index(
            query=query,
            limit=min(page, target - len(rows)),
            offset=offset,
            source_type=source_type,
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += len(batch)

    return {
        "exported_at": datetime.now().isoformat(),
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
    if raw_path.startswith("/") or raw_path.startswith("~"):
        raw_path = "import://local-path-redacted"

    title = _sanitize_text(str(raw.get("title") or "imported memory"))[:240]
    content = _sanitize_text(str(raw.get("content") or ""))
    created_at_epoch = int(raw.get("created_at_epoch") or int(datetime.now().timestamp()))

    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint and content:
        src = raw.get("source_type") or "import"
        sid = raw.get("session_id") or "imported"
        fingerprint = hashlib.sha256(f"{src}|{sid}|{title}|{content}|{created_at_epoch}".encode()).hexdigest()

    return {
        "fingerprint": fingerprint,
        "source_type": str(raw.get("source_type") or "import"),
        "session_id": str(raw.get("session_id") or "imported"),
        "title": title,
        "content": content,
        "tags_json": json.dumps(clean_tags, ensure_ascii=False),
        "file_path": raw_path,
        "created_at": str(raw.get("created_at") or datetime.now().isoformat()),
        "created_at_epoch": created_at_epoch,
    }


def import_observations_payload(
    payload: dict[str, Any],
    *,
    sync_from_storage: bool = True,
) -> dict[str, Any]:
    """Import observations from an export payload into the local index.

    Skips records that already exist (matched by fingerprint) and records
    with no usable content.

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
    conn = sqlite3.connect(db_path)
    inserted = 0
    skipped = 0
    now_epoch = int(datetime.now().timestamp())

    try:
        for raw in observations:
            if not isinstance(raw, dict):
                continue
            obs = _normalize_import_observation(raw)
            if not obs["fingerprint"] or not obs["content"].strip():
                skipped += 1
                continue
            exists = conn.execute(
                "SELECT id FROM observations WHERE fingerprint = ?",
                (obs["fingerprint"],),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO observations(
                    fingerprint, source_type, session_id, title, content,
                    tags_json, file_path, created_at, created_at_epoch,
                    updated_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()

    if sync_from_storage:
        sync_index_from_storage()

    return {"inserted": inserted, "skipped": skipped, "db_path": str(db_path)}
