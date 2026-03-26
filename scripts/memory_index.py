#!/usr/bin/env python3
"""Unified local memory index for ContextGO."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

try:
    from context_config import storage_root
except ImportError:  # pragma: no cover
    from .context_config import storage_root  # type: ignore[import-not-found]


PRIVATE_BLOCK_RE = re.compile(r"<private>[\s\S]*?</private>", re.IGNORECASE)
PRIVATE_TAG_RE = re.compile(r"</?private>", re.IGNORECASE)


def strip_private_blocks(text: str) -> str:
    if not text:
        return ""
    without_block = PRIVATE_BLOCK_RE.sub("", text)
    return PRIVATE_TAG_RE.sub("", without_block).strip()


def get_storage_root() -> Path:
    return storage_root()


def get_index_db_path() -> Path:
    """Return the path to the memory index SQLite database."""
    override = os.environ.get("MEMORY_INDEX_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return get_storage_root() / "index" / "memory_index.db"


def _history_dirs() -> list[Path]:
    root = get_storage_root()
    return [
        root / "resources" / "shared" / "history",
        root / "resources" / "shared" / "conversations",
    ]


def _to_epoch(ts: str, fallback: int) -> int:
    if not ts:
        return fallback
    try:
        return int(datetime.fromisoformat(ts).timestamp())
    except (ValueError, OverflowError):
        return fallback


@dataclass
class Observation:
    fingerprint: str
    source_type: str
    session_id: str
    title: str
    content: str
    tags_json: str
    file_path: str
    created_at: str
    created_at_epoch: int


def _parse_markdown(path: Path) -> Observation | None:
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
            continue
        if s.lower().startswith("tags:"):
            tags = [x.strip() for x in s.split(":", 1)[1].split(",") if x.strip()]
            continue
        if s.lower().startswith("date:"):
            created_at = s.split(":", 1)[1].strip()
            continue
        if s.lower() == "## content":
            in_content = True
            continue
        if in_content:
            body_lines.append(ln)

    if not body_lines:
        body_lines = lines
    content = strip_private_blocks("\n".join(body_lines)).strip()
    title = strip_private_blocks(title).strip()
    if not title:
        title = path.stem
    if not content:
        return None

    mtime_epoch = int(path.stat().st_mtime)
    created_at_epoch = _to_epoch(created_at, mtime_epoch)
    if not created_at:
        created_at = datetime.fromtimestamp(created_at_epoch).isoformat()

    source_type = "conversation" if "/conversations/" in str(path).replace("\\", "/") else "history"
    session_id = path.stem.split("_")[-1] if "_" in path.stem else path.stem

    fingerprint = hashlib.sha256(
        f"{source_type}|{title}|{content}|{created_at_epoch}".encode("utf-8")
    ).hexdigest()

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


def ensure_index_db() -> Path:
    db_path = get_index_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                source_type TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_at_epoch INTEGER NOT NULL,
                updated_at_epoch INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_created ON observations(created_at_epoch DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_source ON observations(source_type, created_at_epoch DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id, created_at_epoch DESC)")
        conn.commit()
    finally:
        conn.close()
    return db_path


def sync_index_from_storage() -> dict[str, int]:
    db_path = ensure_index_db()
    conn = sqlite3.connect(db_path)
    added = 0
    updated = 0
    removed = 0
    scanned = 0
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
                if not obs:
                    continue
                same_path_rows = conn.execute(
                    "SELECT id, fingerprint FROM observations WHERE file_path = ? ORDER BY updated_at_epoch DESC, id DESC",
                    (obs.file_path,),
                ).fetchall()
                if same_path_rows:
                    keep_id = int(same_path_rows[0][0])
                    # clean historical duplicates for same path
                    for dup in same_path_rows[1:]:
                        conn.execute("DELETE FROM observations WHERE id = ?", (int(dup[0]),))
                        removed += 1
                    keep_fp = str(same_path_rows[0][1])
                    if keep_fp != obs.fingerprint:
                        conn.execute(
                            """
                            UPDATE observations
                            SET fingerprint = ?, source_type = ?, session_id = ?, title = ?, content = ?,
                                tags_json = ?, created_at = ?, created_at_epoch = ?, updated_at_epoch = ?
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
                            fingerprint, source_type, session_id, title, content, tags_json,
                            file_path, created_at, created_at_epoch, updated_at_epoch
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

        stale = conn.execute(
            """
            SELECT id, file_path FROM observations
            WHERE source_type IN ('history', 'conversation')
            """
        ).fetchall()
        for rid, path in stale:
            if path not in seen_local_paths:
                conn.execute("DELETE FROM observations WHERE id = ?", (rid,))
                removed += 1
        conn.commit()
    finally:
        conn.close()
    return {"scanned": scanned, "added": added, "updated": updated, "removed": removed}


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    tags: list[str] = []
    try:
        loaded = json.loads(row["tags_json"] or "[]")
        if isinstance(loaded, list):
            tags = [str(x) for x in loaded]
    except (json.JSONDecodeError, ValueError):
        tags = []
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

        # Build WHERE clause from hardcoded predicate strings; user-supplied
        # values flow exclusively through bind parameters (args).
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT * FROM observations {where_clause} ORDER BY created_at_epoch DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(limit, 200)), max(0, offset)])
        rows = conn.execute(sql, args).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def timeline_index(anchor_id: int, depth_before: int = 3, depth_after: int = 3) -> list[dict[str, Any]]:
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
            """
            SELECT * FROM observations
            WHERE created_at_epoch <= ?
            ORDER BY created_at_epoch DESC
            LIMIT ?
            """,
            (anchor["created_at_epoch"], max(1, depth_before + 1)),
        ).fetchall()
        after_rows = conn.execute(
            """
            SELECT * FROM observations
            WHERE created_at_epoch > ?
            ORDER BY created_at_epoch ASC
            LIMIT ?
            """,
            (anchor["created_at_epoch"], max(0, depth_after)),
        ).fetchall()
        merged = list(reversed(before_rows)) + list(after_rows)
        return [_row_to_dict(r) for r in merged]
    finally:
        conn.close()


def get_observations_by_ids(ids: list[int], limit: int = 100) -> list[dict[str, Any]]:
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


def export_observations_payload(
    query: str = "",
    *,
    limit: int = 5000,
    source_type: str = "all",
) -> dict[str, Any]:
    sync_info = sync_index_from_storage()
    target = max(1, min(int(limit), 50000))
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


SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def _sanitize_import_text(text: str) -> str:
    out = strip_private_blocks(text or "")
    for pat in SECRET_PATTERNS:
        out = pat.sub("***REDACTED***", out)
    return out.strip()


def _normalize_import_observation(raw: dict[str, Any]) -> dict[str, Any]:
    raw_tags = raw.get("tags") or []
    if not isinstance(raw_tags, list):
        raw_tags = [raw_tags]
    clean_tags = []
    for tag in raw_tags:
        cleaned = _sanitize_import_text(str(tag))
        if cleaned:
            clean_tags.append(cleaned[:80])

    raw_path = _sanitize_import_text(str(raw.get("file_path") or "import://json"))[:300]
    if raw_path.startswith("/") or raw_path.startswith("~"):
        raw_path = "import://local-path-redacted"

    title = _sanitize_import_text(str(raw.get("title") or "imported memory"))[:240]
    content = _sanitize_import_text(str(raw.get("content") or ""))
    created_at_epoch = int(raw.get("created_at_epoch") or int(datetime.now().timestamp()))
    fingerprint = str(raw.get("fingerprint") or "").strip()
    if not fingerprint and content:
        fingerprint = hashlib.sha256(
            f"{raw.get('source_type') or 'import'}|{raw.get('session_id') or 'imported'}|{title}|{content}|{created_at_epoch}".encode(
                "utf-8"
            )
        ).hexdigest()
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
    observations = payload.get("observations") or []
    if not isinstance(observations, list):
        raise ValueError("invalid payload: observations must be list")

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
                    fingerprint, source_type, session_id, title, content, tags_json,
                    file_path, created_at, created_at_epoch, updated_at_epoch
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

    return {
        "inserted": inserted,
        "skipped": skipped,
        "db_path": str(db_path),
    }
