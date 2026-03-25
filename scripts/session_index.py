#!/usr/bin/env python3
"""Standalone local session index for Context Mesh Foundry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable

try:
    from context_config import env_int
    from memory_index import get_storage_root
    import context_native
except ImportError:  # pragma: no cover
    from .context_config import env_int  # type: ignore[import-not-found]
    from .memory_index import get_storage_root  # type: ignore[import-not-found]
    from . import context_native  # type: ignore[import-not-found]


SESSION_DB_PATH_ENV = "SESSION_INDEX_DB_PATH"
MAX_CONTENT_CHARS = env_int("CMF_SESSION_MAX_CONTENT_CHARS", "CONTEXT_MESH_SESSION_MAX_CONTENT_CHARS", default=24000, minimum=4000)
SYNC_MIN_INTERVAL_SEC = env_int("CMF_SESSION_SYNC_MIN_INTERVAL_SEC", "CONTEXT_MESH_SESSION_SYNC_MIN_INTERVAL_SEC", default=15, minimum=0)
SOURCE_CACHE_TTL_SEC = env_int("CONTEXT_MESH_SOURCE_CACHE_TTL_SEC", default=10, minimum=0)
EXPERIMENTAL_SEARCH_BACKEND = os.environ.get("CONTEXT_MESH_EXPERIMENTAL_SEARCH_BACKEND", "").strip().lower()
EXPERIMENTAL_SYNC_BACKEND = os.environ.get("CONTEXT_MESH_EXPERIMENTAL_SYNC_BACKEND", "").strip().lower()
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "what", "when", "where",
    "which", "who", "how", "please", "search", "session", "history", "continue", "find",
    "继续", "搜索", "终端", "方案", "项目", "历史", "会话", "相关", "那个", "这个",
}
SOURCE_WEIGHT = {
    "codex_session": 40,
    "claude_session": 40,
    "codex_history": 8,
    "claude_history": 8,
    "opencode_history": 6,
    "shell_zsh": 2,
    "shell_bash": 2,
}
_SOURCE_CACHE: dict[str, Any] = {"expires_at": 0.0, "items": [], "home": None}
NATIVE_NOISE_MARKERS = (
    "# agents.md instructions",
    "### available skills",
    "prompt engineer and agent skill optimizer",
    "current skill name:",
    "current description:",
    "the user explicitly asks for this skill",
    "query and upload to google notebooklm",
    "python -m pytest",
    "benchmarks/run.py",
    "function_call_output",
    "queue-operation",
    "chunk id:",
    "<instructions>",
    "skill.md",
)


def _normalize_file_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _is_noise_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if not compact:
        return True
    noise_markers = [
        "### Available skills",
        "You are Codex",
        "You are Claude",
        "file: /Users/dunova/.codex/skills/",
        "file: /Users/dunova/.agents/skills/",
        "file: /Users/dunova/.claude/skills/",
        "<environment_context>",
    ]
    if any(marker in compact for marker in noise_markers):
        return True
    if compact.count("SKILL.md") >= 3:
        return True
    return False


@dataclass
class SessionDocument:
    file_path: str
    source_type: str
    session_id: str
    title: str
    content: str
    created_at: str
    created_at_epoch: int
    file_mtime: int
    file_size: int


def _home() -> Path:
    return Path.home()


def get_session_db_path() -> Path:
    override = os.environ.get("CONTEXT_MESH_SESSION_INDEX_DB_PATH", "").strip() or os.environ.get(SESSION_DB_PATH_ENV, "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return get_storage_root() / "index" / "session_index.db"


def _iso_to_epoch(value: str | None, fallback: int) -> int:
    if not value:
        return fallback
    raw = str(value).strip()
    if not raw:
        return fallback
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except Exception:
        return fallback


def _collect_content_text(items: Any) -> list[str]:
    texts: list[str] = []
    if not isinstance(items, list):
        return texts
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"input_text", "output_text", "text"}:
            text = str(item.get("text") or "").strip()
            if text:
                texts.append(text)
    return texts


def _truncate(texts: Iterable[str], max_chars: int = MAX_CONTENT_CHARS) -> str:
    parts: list[str] = []
    total = 0
    for text in texts:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if not clean:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(clean) > remaining:
            parts.append(clean[:remaining])
            total = max_chars
            break
        parts.append(clean)
        total += len(clean) + 1
    return "\n".join(parts)


def _parse_codex_session(path: Path) -> SessionDocument | None:
    session_id = path.stem
    title = ""
    created_at = ""
    pieces: list[str] = []
    mtime = int(path.stat().st_mtime)
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                kind = obj.get("type")
                if kind == "session_meta":
                    payload = obj.get("payload") or {}
                    session_id = str(payload.get("id") or session_id)
                    title = str(payload.get("cwd") or title or "")
                    created_at = str(payload.get("timestamp") or created_at or obj.get("timestamp") or "")
                elif kind == "event_msg":
                    payload = obj.get("payload") or {}
                    if payload.get("type") == "user_message":
                        message = str(payload.get("message") or "").strip()
                        if message:
                            pieces.append(message)
                elif kind == "response_item":
                    payload = obj.get("payload") or {}
                    if payload.get("type") == "message" and payload.get("role") == "assistant":
                        for text in _collect_content_text(payload.get("content")):
                            if not _is_noise_text(text):
                                pieces.append(text)
    except Exception:
        return None

    content = _truncate(pieces)
    if not title:
        title = path.parent.as_posix()
    if not content:
        content = title
    return SessionDocument(
        file_path=str(path),
        source_type="codex_session",
        session_id=session_id,
        title=title[:300],
        content=content,
        created_at=created_at or datetime.fromtimestamp(mtime).isoformat(),
        created_at_epoch=_iso_to_epoch(created_at, mtime),
        file_mtime=mtime,
        file_size=path.stat().st_size,
    )


def _parse_claude_session(path: Path) -> SessionDocument | None:
    session_id = path.stem
    title = ""
    created_at = ""
    pieces: list[str] = []
    mtime = int(path.stat().st_mtime)
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                kind = obj.get("type")
                session_id = str(obj.get("sessionId") or session_id)
                if not title:
                    title = str(obj.get("cwd") or title or "")
                if not created_at:
                    created_at = str(obj.get("timestamp") or "")
                if kind == "user":
                    message = obj.get("message") or {}
                    content = message.get("content")
                    if isinstance(content, str) and content.strip() and not _is_noise_text(content):
                        pieces.append(content)
                elif kind == "assistant":
                    message = obj.get("message") or {}
                    for text in _collect_content_text(message.get("content")):
                        if not _is_noise_text(text):
                            pieces.append(text)
    except Exception:
        return None

    content = _truncate(pieces)
    if not title:
        title = path.parent.as_posix()
    if not content:
        content = title
    return SessionDocument(
        file_path=str(path),
        source_type="claude_session",
        session_id=session_id,
        title=title[:300],
        content=content,
        created_at=created_at or datetime.fromtimestamp(mtime).isoformat(),
        created_at_epoch=_iso_to_epoch(created_at, mtime),
        file_mtime=mtime,
        file_size=path.stat().st_size,
    )


def _parse_history_jsonl(path: Path, source_type: str) -> SessionDocument | None:
    mtime = int(path.stat().st_mtime)
    texts: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                for key in ("display", "text", "input", "prompt", "message"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        texts.append(value)
                        break
    except Exception:
        return None

    content = _truncate(texts)
    if not content:
        return None
    return SessionDocument(
        file_path=str(path),
        source_type=source_type,
        session_id=path.stem,
        title=path.name,
        content=content,
        created_at=datetime.fromtimestamp(mtime).isoformat(),
        created_at_epoch=mtime,
        file_mtime=mtime,
        file_size=path.stat().st_size,
    )


def _parse_shell_history(path: Path, source_type: str) -> SessionDocument | None:
    mtime = int(path.stat().st_mtime)
    texts: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                if line.startswith(": "):
                    _, _, command = line.partition(";")
                    if command.strip():
                        texts.append(command.strip())
                else:
                    texts.append(line)
    except Exception:
        return None
    content = _truncate(texts)
    if not content:
        return None
    return SessionDocument(
        file_path=str(path),
        source_type=source_type,
        session_id=path.stem,
        title=path.name,
        content=content,
        created_at=datetime.fromtimestamp(mtime).isoformat(),
        created_at_epoch=mtime,
        file_mtime=mtime,
        file_size=path.stat().st_size,
    )


def _iter_sources() -> list[tuple[str, Path]]:
    now = time.monotonic()
    cached_items = _SOURCE_CACHE.get("items") or []
    current_home = str(_home())
    cached_home = _SOURCE_CACHE.get("home")
    cache_valid = (
        SOURCE_CACHE_TTL_SEC > 0
        and _SOURCE_CACHE.get("expires_at", 0.0) > now
        and cached_items
        and cached_home == current_home
    )
    if cache_valid:
        return list(cached_items)

    native_backend = EXPERIMENTAL_SYNC_BACKEND
    if native_backend in {"rust", "go"}:
        try:
            result = context_native.run_native_scan(
                backend=native_backend,
                threads=4,
                json_output=True,
                release=(native_backend == "rust"),
                timeout=180,
            )
            if result.returncode == 0:
                items = context_native.inventory_items(result)
                if items:
                    if SOURCE_CACHE_TTL_SEC > 0:
                        _SOURCE_CACHE["items"] = list(items)
                        _SOURCE_CACHE["expires_at"] = now + SOURCE_CACHE_TTL_SEC
                        _SOURCE_CACHE["home"] = current_home
                    return items
        except Exception:
            pass

    home = Path(current_home)
    items: list[tuple[str, Path]] = []
    roots = [
        ("codex_session", home / ".codex" / "sessions"),
        ("claude_session", home / ".claude" / "projects"),
    ]
    for source_type, root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            items.append((source_type, path))

    flat_files = [
        ("codex_history", home / ".codex" / "history.jsonl"),
        ("claude_history", home / ".claude" / "history.jsonl"),
        ("opencode_history", home / ".local" / "state" / "opencode" / "prompt-history.jsonl"),
        ("shell_zsh", home / ".zsh_history"),
        ("shell_bash", home / ".bash_history"),
    ]
    for source_type, path in flat_files:
        if path.is_file():
            items.append((source_type, path))
    if SOURCE_CACHE_TTL_SEC > 0:
        _SOURCE_CACHE["items"] = list(items)
        _SOURCE_CACHE["expires_at"] = now + SOURCE_CACHE_TTL_SEC
        _SOURCE_CACHE["home"] = current_home
    return items


def _parse_source(source_type: str, path: Path) -> SessionDocument | None:
    if source_type == "codex_session":
        return _parse_codex_session(path)
    if source_type == "claude_session":
        return _parse_claude_session(path)
    if source_type.endswith("_history") and path.suffix == ".jsonl":
        return _parse_history_jsonl(path, source_type)
    if source_type.startswith("shell_"):
        return _parse_shell_history(path, source_type)
    return None


def ensure_session_db() -> Path:
    db_path = get_session_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_documents (
                file_path TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_at_epoch INTEGER NOT NULL,
                file_mtime INTEGER NOT NULL,
                file_size INTEGER NOT NULL,
                updated_at_epoch INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session_created ON session_documents(created_at_epoch DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session_source ON session_documents(source_type, created_at_epoch DESC)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM session_index_meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO session_index_meta(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def sync_session_index(force: bool = False) -> dict[str, int]:
    db_path = ensure_session_db()
    conn = sqlite3.connect(db_path)
    added = 0
    updated = 0
    removed = 0
    scanned = 0
    now_epoch = int(datetime.now().timestamp())
    seen_paths: set[str] = set()
    try:
        last_sync_raw = _meta_get(conn, "last_sync_epoch")
        last_sync_epoch = int(last_sync_raw or "0")
        if not force and last_sync_epoch and (now_epoch - last_sync_epoch) < SYNC_MIN_INTERVAL_SEC:
            total = conn.execute("SELECT COUNT(*) FROM session_documents").fetchone()[0]
            return {
                "scanned": 0,
                "added": 0,
                "updated": 0,
                "removed": 0,
                "skipped_recent": 1,
                "last_sync_epoch": last_sync_epoch,
                "total_sessions": int(total or 0),
            }

        for source_type, path in _iter_sources():
            scanned += 1
            canonical_path = _normalize_file_path(path)
            file_path = canonical_path
            seen_paths.add(file_path)
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            row = conn.execute(
                "SELECT file_mtime, file_size FROM session_documents WHERE file_path = ?",
                (canonical_path,),
            ).fetchone()
            if row and int(row[0]) == int(stat.st_mtime) and int(row[1]) == int(stat.st_size):
                continue
            doc = _parse_source(source_type, path)
            if not doc:
                continue
            conn.execute(
                """
                INSERT INTO session_documents(
                    file_path, source_type, session_id, title, content,
                    created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    source_type = excluded.source_type,
                    session_id = excluded.session_id,
                    title = excluded.title,
                    content = excluded.content,
                    created_at = excluded.created_at,
                    created_at_epoch = excluded.created_at_epoch,
                    file_mtime = excluded.file_mtime,
                    file_size = excluded.file_size,
                    updated_at_epoch = excluded.updated_at_epoch
                """,
                (
                    canonical_path,
                    doc.source_type,
                    doc.session_id,
                    doc.title,
                    doc.content,
                    doc.created_at,
                    doc.created_at_epoch,
                    doc.file_mtime,
                    doc.file_size,
                    now_epoch,
                )
            )
            if row:
                updated += 1
            else:
                added += 1

        stale = conn.execute("SELECT file_path FROM session_documents").fetchall()
        for (file_path,) in stale:
            if file_path not in seen_paths:
                conn.execute("DELETE FROM session_documents WHERE file_path = ?", (file_path,))
                removed += 1
        _meta_set(conn, "last_sync_epoch", str(now_epoch))
        conn.commit()
    finally:
        conn.close()
    return {
        "scanned": scanned,
        "added": added,
        "updated": updated,
        "removed": removed,
        "skipped_recent": 0,
        "last_sync_epoch": now_epoch,
    }


def build_query_terms(query: str) -> list[str]:
    raw = (query or "").strip()
    if not raw:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        clean = term.strip().strip("\"'")
        if not clean:
            return
        lower = clean.lower()
        if lower in seen or lower in STOPWORDS:
            return
        if len(clean) < 2:
            return
        seen.add(lower)
        terms.append(clean)

    date_match = re.fullmatch(r"\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*", raw)
    if date_match:
        y, m, d = date_match.groups()
        add(f"{y}-{int(m):02d}-{int(d):02d}")
        add(f"{y}{int(m):02d}{int(d):02d}")

    for token in re.findall(r"(?:~?/[A-Za-z0-9._/-]+)", raw):
        add(Path(token).name or token)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9._-]{2,40}", raw):
        if token.lower() not in STOPWORDS:
            add(token)
    for token in re.findall(r"[\u4e00-\u9fff]{2,12}", raw):
        add(token)
    if not terms:
        add(raw)
    return terms[:8]


def _build_snippet(text: str, terms: list[str], radius: int = 100) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return ""
    lower = compact.lower()
    idx = -1
    matched = ""
    for term in terms:
        pos = lower.find(term.lower())
        if pos >= 0 and (idx < 0 or pos < idx):
            idx = pos
            matched = term
    if idx < 0:
        return compact[: radius * 2]
    start = max(0, idx - radius)
    end = min(len(compact), idx + len(matched) + radius)
    return compact[start:end]


def _native_search_rows(query: str, limit: int = 10) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    backend = EXPERIMENTAL_SEARCH_BACKEND
    if backend not in {"rust", "go"}:
        return []
    try:
        result = context_native.run_native_scan(
            backend=backend,
            threads=4,
            query=query,
            json_output=True,
            release=(backend == "rust"),
            timeout=120,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    query_lower = query.lower().strip()
    for item in context_native.extract_matches(result):
        snippet = str(item.get("snippet", "") or "")
        snippet_lower = snippet.lower()
        if not snippet_lower:
            continue
        if any(marker in snippet_lower for marker in NATIVE_NOISE_MARKERS):
            continue
        if query_lower and query_lower not in snippet_lower:
            continue
        rows.append(
            {
                "source_type": item.get("source", "native_session"),
                "session_id": item.get("session_id", ""),
                "title": item.get("path", ""),
                "file_path": item.get("path", ""),
                "created_at": "",
                "created_at_epoch": 0,
                "snippet": snippet,
            }
        )
        if len(rows) >= max(1, min(limit, 100)):
            break
    return rows


def _fetch_session_docs_by_paths(conn: sqlite3.Connection, file_paths: Iterable[str]) -> dict[str, sqlite3.Row]:
    docs: dict[str, sqlite3.Row] = {}
    unique_paths: list[str] = []
    seen: set[str] = set()
    for raw_path in file_paths:
        if not raw_path:
            continue
        path_str = _normalize_file_path(Path(str(raw_path)))
        if path_str in seen:
            continue
        seen.add(path_str)
        unique_paths.append(path_str)
    if not unique_paths:
        return docs
    placeholders = ",".join("?" for _ in unique_paths)
    query = f"SELECT * FROM session_documents WHERE file_path IN ({placeholders})"
    for row in conn.execute(query, tuple(unique_paths)):
        docs[str(row["file_path"])] = row
    return docs


def _enrich_native_rows(rows: list[dict[str, Any]], conn: sqlite3.Connection, terms: list[str], limit: int) -> list[dict[str, Any]]:
    max_results = max(1, min(limit, 100))
    docs = _fetch_session_docs_by_paths(conn, (row.get("file_path") for row in rows if row.get("file_path")))
    enriched: list[dict[str, Any]] = []
    for row in rows:
        enriched_row = dict(row)
        file_path = _normalize_file_path(Path(str(row.get("file_path") or ""))) if row.get("file_path") else ""
        doc = docs.get(file_path)
        if doc:
            enriched_row["source_type"] = doc["source_type"]
            enriched_row["session_id"] = doc["session_id"]
            enriched_row["title"] = doc["title"]
            enriched_row["created_at"] = doc["created_at"]
            enriched_row["created_at_epoch"] = doc["created_at_epoch"]
            snippet_source = doc["content"]
        else:
            snippet_source = row.get("snippet") or ""
            enriched_row.setdefault("created_at", "")
            enriched_row.setdefault("created_at_epoch", 0)
        snippet = _build_snippet(snippet_source, terms)
        if not snippet:
            snippet = str(snippet_source or row.get("snippet") or "")
        enriched_row["snippet"] = snippet
        enriched.append(enriched_row)
        if len(enriched) >= max_results:
            break
    return enriched


def _search_rows(query: str, limit: int = 10, literal: bool = False) -> list[dict[str, Any]]:
    max_results = max(1, min(limit, 100))
    db_path = ensure_session_db()
    sync_session_index()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        terms = [query.strip()] if literal else build_query_terms(query)
        native_rows = _native_search_rows(query, limit=max_results)
        if native_rows:
            return _enrich_native_rows(native_rows, conn, terms, max_results)

        where_parts: list[str] = []
        args: list[Any] = []
        for term in terms:
            like_term = f"%{term.lower()}%"
            where_parts.append("(lower(title) LIKE ? OR lower(content) LIKE ? OR lower(file_path) LIKE ?)")
            args.extend([like_term, like_term, like_term])
        sql = """
            SELECT * FROM session_documents
            {where}
            ORDER BY created_at_epoch DESC
            LIMIT 200
        """.format(where=f"WHERE {' OR '.join(where_parts)}" if where_parts else "")
        rows = conn.execute(sql, args).fetchall()
        ranked: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            haystack = f"{row['title']}\n{row['content']}\n{row['file_path']}".lower()
            score = SOURCE_WEIGHT.get(str(row["source_type"]), 1)
            for term in terms:
                if term.lower() in haystack:
                    score += max(4, len(term) * len(term))
            if score <= 0:
                continue
            ranked.append((score, row))
        ranked.sort(key=lambda item: (item[0], item[1]["created_at_epoch"]), reverse=True)
        results: list[dict[str, Any]] = []
        for _, row in ranked[:max_results]:
            results.append(
                {
                    "source_type": row["source_type"],
                    "session_id": row["session_id"],
                    "title": row["title"],
                    "file_path": row["file_path"],
                    "created_at": row["created_at"],
                    "created_at_epoch": row["created_at_epoch"],
                    "snippet": _build_snippet(row["content"], terms),
                }
            )
        return results
    finally:
        conn.close()


def format_search_results(query: str, *, search_type: str = "all", limit: int = 10, literal: bool = False) -> str:
    results = _search_rows(query, limit=limit, literal=literal)
    if not results:
        return "No matches found in local session index."
    lines = [f"Found {len(results)} sessions (local index):"]
    for idx, row in enumerate(results, 1):
        lines.append(f"[{idx}] {row['created_at'][:10]} | {row['session_id']} | {row['source_type']}")
        lines.append(f"    {row['title']}")
        lines.append(f"    File: {row['file_path']}")
        lines.append(f"    > {row['snippet']}")
    return "\n".join(lines)


def health_payload() -> dict[str, Any]:
    sync_info = sync_session_index()
    db_path = ensure_session_db()
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM session_documents").fetchone()[0]
        latest = conn.execute("SELECT MAX(created_at_epoch) FROM session_documents").fetchone()[0]
        return {
            "session_index_db_exists": db_path.exists(),
            "session_index_db": str(db_path),
            "total_sessions": int(total or 0),
            "latest_epoch": int(latest or 0),
            "sync": sync_info,
        }
    finally:
        conn.close()
