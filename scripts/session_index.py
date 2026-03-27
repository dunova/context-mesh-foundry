#!/usr/bin/env python3
"""Standalone local session index for ContextGO.

Indexes Codex, Claude, and shell session files into a SQLite database and
provides ranked full-text search over their content.  All persistent state
lives under the storage root (default ``~/.contextgo``); no hardcoded paths.

Public API (stable):
    get_session_db_path() -> Path
    ensure_session_db() -> Path
    sync_session_index(force: bool = False) -> dict[str, int]
    build_query_terms(query: str) -> list[str]
    format_search_results(query, *, search_type, limit, literal) -> str
    health_payload() -> dict[str, Any]
    SESSION_DB_PATH_ENV  -- env-var name for DB path override
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import context_native
    from context_config import env_int, storage_root
except ImportError:  # pragma: no cover
    from . import context_native  # type: ignore[import-not-found]
    from .context_config import env_int, storage_root  # type: ignore[import-not-found]


# Configuration

#: Env-var name for overriding the default DB path.
SESSION_DB_PATH_ENV = "CONTEXTGO_SESSION_INDEX_DB_PATH"

MAX_CONTENT_CHARS: int = env_int("CONTEXTGO_SESSION_MAX_CONTENT_CHARS", default=24000, minimum=4000)
SYNC_MIN_INTERVAL_SEC: int = env_int("CONTEXTGO_SESSION_SYNC_MIN_INTERVAL_SEC", default=15, minimum=0)
SOURCE_CACHE_TTL_SEC: int = env_int("CONTEXTGO_SOURCE_CACHE_TTL_SEC", default=10, minimum=0)
EXPERIMENTAL_SEARCH_BACKEND: str = os.environ.get("CONTEXTGO_EXPERIMENTAL_SEARCH_BACKEND", "").strip().lower()
EXPERIMENTAL_SYNC_BACKEND: str = os.environ.get("CONTEXTGO_EXPERIMENTAL_SYNC_BACKEND", "").strip().lower()

#: Bump this string to force a full re-index on next sync.
SESSION_INDEX_SCHEMA_VERSION = "2026-03-26-search-noise-v5"

#: Number of upsert rows per SQLite transaction batch during sync.
_BATCH_COMMIT_SIZE: int = env_int("CONTEXTGO_INDEX_BATCH_SIZE", default=100, minimum=10)


# SQL Constants

_DDL_SESSION_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS session_documents (
    file_path        TEXT PRIMARY KEY,
    source_type      TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    title            TEXT NOT NULL,
    content          TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    file_mtime       INTEGER NOT NULL,
    file_size        INTEGER NOT NULL,
    updated_at_epoch INTEGER NOT NULL
)
"""

_DDL_SESSION_META = """
CREATE TABLE IF NOT EXISTS session_index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_session_created ON session_documents(created_at_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_session_source  ON session_documents(source_type, created_at_epoch DESC)",
]

_SQL_META_GET = "SELECT value FROM session_index_meta WHERE key = ?"
_SQL_META_SET = """
    INSERT INTO session_index_meta(key, value) VALUES(?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value
"""
_SQL_CHECK_CHANGED = "SELECT file_mtime, file_size FROM session_documents WHERE file_path = ?"
_SQL_UPSERT_DOC = """
    INSERT INTO session_documents(
        file_path, source_type, session_id, title, content,
        created_at, created_at_epoch, file_mtime, file_size, updated_at_epoch
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(file_path) DO UPDATE SET
        source_type      = excluded.source_type,
        session_id       = excluded.session_id,
        title            = excluded.title,
        content          = excluded.content,
        created_at       = excluded.created_at,
        created_at_epoch = excluded.created_at_epoch,
        file_mtime       = excluded.file_mtime,
        file_size        = excluded.file_size,
        updated_at_epoch = excluded.updated_at_epoch
"""
_SQL_DELETE_DOC = "DELETE FROM session_documents WHERE file_path = ?"
_SQL_ALL_PATHS = "SELECT file_path FROM session_documents"
_SQL_COUNT_DOCS = "SELECT COUNT(*) FROM session_documents"
_SQL_MAX_EPOCH = "SELECT MAX(created_at_epoch) FROM session_documents"


# Noise configuration


def _load_noise_config() -> dict[str, list[str]]:
    """Load noise-filter marker tables from ``config/noise_markers.json``.

    The config file is resolved relative to this script's parent directory so
    the path works both in-repo and after pip-install.  Falls back to empty
    lists when the config file is absent.
    """
    config_path = Path(__file__).parent.parent / "config" / "noise_markers.json"
    if config_path.exists():
        with open(config_path) as fh:
            data = json.load(fh)
        return {
            "search_noise_markers": list(data.get("search_noise_markers", [])),
            "native_noise_markers": list(data.get("native_noise_markers", [])),
            "text_noise_markers": list(data.get("text_noise_markers", [])),
            "text_noise_lower_markers": list(data.get("text_noise_lower_markers", [])),
            "noise_prefixes": list(data.get("noise_prefixes", [])),
        }
    return {
        "search_noise_markers": [],
        "native_noise_markers": [],
        "text_noise_markers": [],
        "text_noise_lower_markers": [],
        "noise_prefixes": [],
    }


# Loaded once at import time; all marker constants are derived from this dict.
_NOISE_CONFIG: dict[str, list[str]] = _load_noise_config()

# Markers sourced from config/noise_markers.json.
# Run ``scripts/check_noise_sync.py`` to verify sync with the Rust/Go backends.
SEARCH_NOISE_MARKERS: tuple[str, ...] = tuple(_NOISE_CONFIG["search_noise_markers"])
NATIVE_NOISE_MARKERS: tuple[str, ...] = tuple(_NOISE_CONFIG["native_noise_markers"])
_NOISE_TEXT_MARKERS: tuple[str, ...] = tuple(_NOISE_CONFIG["text_noise_markers"])
_NOISE_TEXT_LOWER_MARKERS: tuple[str, ...] = tuple(_NOISE_CONFIG["text_noise_lower_markers"])

STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "what",
        "when",
        "where",
        "which",
        "who",
        "how",
        "please",
        "search",
        "session",
        "history",
        "continue",
        "find",
        # Chinese stopwords
        "继续",
        "搜索",
        "终端",
        "方案",
        "项目",
        "历史",
        "会话",
        "相关",
        "那个",
        "这个",
    }
)

SOURCE_WEIGHT: dict[str, int] = {
    "codex_session": 40,
    "claude_session": 40,
    "codex_history": 8,
    "claude_history": 8,
    "opencode_history": 6,
    "shell_zsh": 2,
    "shell_bash": 2,
}

# In-process cache for source-file discovery results.
_SOURCE_CACHE: dict[str, Any] = {"expires_at": 0.0, "items": [], "home": None}

# Pre-compiled whitespace normalizer used throughout this module.
_WHITESPACE_RE = re.compile(r"\s+")

_SNIPPET_MAX_CHARS = 120


# Helpers


def _home() -> Path:
    """Return the current user's home directory.

    Isolated as a function so tests can monkeypatch it without affecting
    ``Path.home`` globally.
    """
    return Path.home()


def _normalize_file_path(path: Path) -> str:
    """Return the resolved, absolute string form of *path*.

    Falls back to the un-resolved string if ``Path.resolve`` raises.
    """
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _iso_to_epoch(value: str | None, fallback: int) -> int:
    """Parse an ISO 8601 datetime string to a Unix epoch integer.

    Returns *fallback* if *value* is empty or unparseable.
    """
    if not value:
        return fallback
    raw = str(value).strip()
    if not raw:
        return fallback
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except (ValueError, OverflowError):
        return fallback


def _collect_content_text(items: Any) -> list[str]:
    """Extract user/assistant text blocks from a JSON content array."""
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
    """Join *texts* into a single string, capped at *max_chars* total characters."""
    parts: list[str] = []
    total = 0
    for text in texts:
        clean = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
        if not clean:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(clean) > remaining:
            parts.append(clean[:remaining])
            break
        parts.append(clean)
        total += len(clean) + 1
    return "\n".join(parts)


def _compact_snippet(text: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """Collapse internal whitespace and truncate *text* to *max_chars* with an ellipsis."""
    clean = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "\u2026"


# Noise Filtering


def _is_noise_text(text: str) -> bool:
    """Return ``True`` if *text* should be excluded from the session index."""
    compact = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    if not compact:
        return True
    if any(marker in compact for marker in _NOISE_TEXT_MARKERS):
        return True
    if compact.count("SKILL.md") >= 3:
        return True
    compact_lower = compact.lower()
    if any(marker in compact_lower for marker in _NOISE_TEXT_LOWER_MARKERS):
        return True
    if "已预热" in compact and "样本定位" in compact:
        return True
    return "主链不再是瓶颈" in compact and "native 搜索结果质量" in compact


def _search_noise_penalty(*parts: str) -> int:
    """Compute a numeric noise penalty for a candidate search result.

    Higher penalties push results further down the ranking.
    """
    haystack = "\n".join(str(part or "") for part in parts).lower()
    penalty = 0

    marker_hits = sum(1 for marker in SEARCH_NOISE_MARKERS if marker in haystack)
    if marker_hits:
        penalty += min(120, marker_hits * 60)

    if "/skills/" in haystack or "skills-repo" in haystack:
        penalty += 120
    if "guardian_truncated" in haystack:
        penalty += 60
    if "chunk id:" in haystack or "wall time:" in haystack:
        penalty += 120

    lines = [line.strip() for line in haystack.splitlines() if line.strip()]
    short_token_lines = sum(
        1 for line in lines if len(line) <= 40 and " " not in line and line.count("/") < 2 and line.count("-") <= 3
    )
    if short_token_lines >= 8:
        penalty += 200

    if "drwx" in haystack or "rwxr-xr-x" in haystack or "\ntotal " in haystack:
        penalty += 200

    meta_terms = ("notebooklm", "search", "session_index", "native-scan")
    if all(term in haystack for term in meta_terms):
        penalty += 240
    if ("我先" in haystack or "我继续" in haystack) and ("native-scan" in haystack or "session_index" in haystack):
        penalty += 240

    return penalty


def _is_current_repo_meta_result(title: str, content: str, file_path: str) -> bool:  # noqa: ARG001
    """Return ``True`` if this result is meta-commentary about the current repo."""
    current_repo = str(Path.cwd().resolve())
    if title != current_repo:
        return False
    compact = _WHITESPACE_RE.sub(" ", str(content or "")).strip()
    if not compact:
        return True
    meta_markers = (
        "写集仅限",
        "改动文件：",
        "改动文件:",
        "**改动文件**",
        "核心变化：",
        "核心变化:",
        "建议验证命令：",
        "建议验证命令:",
        "职责只限测试",
        "测试集使用",
        "全平台对话测试集",
        "artifacts/testsets/dataset_",
        "仓库：",
        "你负责",
        "变更概览",
        "改动概览",
        "我先",
        "我继续",
        "我现在",
        "已收到任务",
        "已变更概览",
        "search NotebookLM",
        "native-scan",
        "session_index",
    )
    return any(marker in compact for marker in meta_markers)


def _looks_like_path_only_content(title: str, content: str) -> bool:
    """Return ``True`` if the document content is nothing but a filesystem path."""
    title_clean = _WHITESPACE_RE.sub(" ", str(title or "")).strip()
    content_clean = _WHITESPACE_RE.sub(" ", str(content or "")).strip()
    if not title_clean or not content_clean:
        return False
    if title_clean != content_clean:
        return False
    return "/" in content_clean and not any(ch in content_clean for ch in ("。", "，", ".", ":"))


# Document Model


@dataclass
class SessionDocument:
    """In-memory representation of a single indexed session file."""

    file_path: str
    source_type: str
    session_id: str
    title: str
    content: str
    created_at: str
    created_at_epoch: int
    file_mtime: int
    file_size: int


# Document Parsers


def _finish_session_doc(
    path: Path,
    source_type: str,
    session_id: str,
    title: str,
    created_at: str,
    pieces: list[str],
    mtime: int,
) -> SessionDocument:
    """Build a SessionDocument from already-parsed fields."""
    content = _truncate(pieces)
    if not title:
        title = path.parent.as_posix()
    if not content:
        content = title
    return SessionDocument(
        file_path=str(path),
        source_type=source_type,
        session_id=session_id,
        title=title[:300],
        content=content,
        created_at=created_at or datetime.fromtimestamp(mtime).isoformat(),
        created_at_epoch=_iso_to_epoch(created_at, mtime),
        file_mtime=mtime,
        file_size=path.stat().st_size,
    )


def _parse_codex_session(path: Path) -> SessionDocument | None:
    """Parse a Codex JSONL session file into a ``SessionDocument``.

    Returns ``None`` if the file cannot be read or yields no usable content.
    """
    session_id = path.stem
    title = ""
    created_at = ""
    pieces: list[str] = []
    mtime = int(path.stat().st_mtime)

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
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
                        if message and not _is_noise_text(message):
                            pieces.append(message)
                elif kind == "response_item":
                    payload = obj.get("payload") or {}
                    if payload.get("type") == "message" and payload.get("role") == "assistant":
                        for text in _collect_content_text(payload.get("content")):
                            if not _is_noise_text(text):
                                pieces.append(text)
    except (OSError, UnicodeDecodeError, ValueError):
        return None

    return _finish_session_doc(path, "codex_session", session_id, title, created_at, pieces, mtime)


def _parse_claude_session(path: Path) -> SessionDocument | None:
    """Parse a Claude JSONL session file into a ``SessionDocument``.

    Returns ``None`` if the file cannot be read or yields no usable content.
    """
    session_id = path.stem
    title = ""
    created_at = ""
    pieces: list[str] = []
    mtime = int(path.stat().st_mtime)

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                kind = obj.get("type")
                session_id = str(obj.get("sessionId") or session_id)
                if not title:
                    title = str(obj.get("cwd") or title or "")
                if not created_at:
                    created_at = str(obj.get("timestamp") or "")

                if kind == "user":
                    message = obj.get("message") or {}
                    raw_content = message.get("content")
                    if isinstance(raw_content, str) and raw_content.strip() and not _is_noise_text(raw_content):
                        pieces.append(raw_content)
                elif kind == "assistant":
                    message = obj.get("message") or {}
                    for text in _collect_content_text(message.get("content")):
                        if not _is_noise_text(text):
                            pieces.append(text)
    except (OSError, UnicodeDecodeError, ValueError):
        return None

    return _finish_session_doc(path, "claude_session", session_id, title, created_at, pieces, mtime)


def _parse_history_jsonl(path: Path, source_type: str) -> SessionDocument | None:
    """Parse a flat JSONL history file into a ``SessionDocument``.

    Returns ``None`` if no usable content is found.
    """
    mtime = int(path.stat().st_mtime)
    texts: list[str] = []

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue
                for key in ("display", "text", "input", "prompt", "message"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        texts.append(value)
                        break
    except (OSError, UnicodeDecodeError, ValueError):
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
    """Parse a shell history file (zsh or bash) into a ``SessionDocument``.

    Returns ``None`` if no usable content is found.
    """
    mtime = int(path.stat().st_mtime)
    texts: list[str] = []

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if line.startswith(": "):
                    _, _, command = line.partition(";")
                    if command.strip():
                        texts.append(command.strip())
                else:
                    texts.append(line)
    except (OSError, UnicodeDecodeError, ValueError):
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


def _parse_source(source_type: str, path: Path) -> SessionDocument | None:
    """Dispatch a source file to the appropriate parser."""
    if source_type == "codex_session":
        return _parse_codex_session(path)
    if source_type == "claude_session":
        return _parse_claude_session(path)
    if source_type.endswith("_history") and path.suffix == ".jsonl":
        return _parse_history_jsonl(path, source_type)
    if source_type.startswith("shell_"):
        return _parse_shell_history(path, source_type)
    return None


# Source Discovery


def _iter_sources() -> list[tuple[str, Path]]:
    """Return a list of ``(source_type, path)`` pairs for all discoverable sources.

    Results are cached for ``SOURCE_CACHE_TTL_SEC`` seconds to avoid repeated
    filesystem traversals.  Falls back to Python discovery when the native
    backend is unavailable or returns an error.
    """
    now = time.monotonic()
    current_home = str(_home())
    if (
        SOURCE_CACHE_TTL_SEC > 0
        and _SOURCE_CACHE.get("expires_at", 0.0) > now
        and _SOURCE_CACHE.get("items")
        and _SOURCE_CACHE.get("home") == current_home
    ):
        return list(_SOURCE_CACHE["items"])

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
                items: list[tuple[str, Path]] = context_native.inventory_items(result)
                if items:
                    _update_source_cache(items, now, current_home)
                    return items
        except (OSError, RuntimeError):
            pass

    home = Path(current_home)
    discovered: list[tuple[str, Path]] = []

    for source_type, root in [
        ("codex_session", home / ".codex" / "sessions"),
        ("codex_session", home / ".codex" / "archived_sessions"),
        ("claude_session", home / ".claude" / "projects"),
    ]:
        if root.is_dir():
            for path in root.rglob("*.jsonl"):
                discovered.append((source_type, path))

    for source_type, path in [
        ("codex_history", home / ".codex" / "history.jsonl"),
        ("claude_history", home / ".claude" / "history.jsonl"),
        ("opencode_history", home / ".local" / "state" / "opencode" / "prompt-history.jsonl"),
        ("shell_zsh", home / ".zsh_history"),
        ("shell_bash", home / ".bash_history"),
    ]:
        if path.is_file():
            discovered.append((source_type, path))

    _update_source_cache(discovered, now, current_home)
    return discovered


def _update_source_cache(items: list[tuple[str, Path]], now: float, home: str) -> None:
    """Write discovery results into the in-process source cache."""
    if SOURCE_CACHE_TTL_SEC > 0:
        _SOURCE_CACHE["items"] = list(items)
        _SOURCE_CACHE["expires_at"] = now + SOURCE_CACHE_TTL_SEC
        _SOURCE_CACHE["home"] = home


# Database Schema and Initialization


def get_session_db_path() -> Path:
    """Return the path to the session index SQLite database.

    Checks ``CONTEXTGO_SESSION_INDEX_DB_PATH`` first; falls back to the
    storage root.
    """
    override = os.environ.get(SESSION_DB_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return storage_root() / "index" / "session_index.db"


def ensure_session_db() -> Path:
    """Create the session index database and schema if they do not exist.

    Returns the path to the database file.
    """
    db_path = get_session_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _open_db(db_path) as conn:
        conn.execute(_DDL_SESSION_DOCUMENTS)
        for ddl in _DDL_INDEXES:
            conn.execute(ddl)
        conn.execute(_DDL_SESSION_META)
        conn.commit()
    return db_path


@contextmanager
def _open_db(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection and ensure it is closed on exit."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    """Retrieve a value from the ``session_index_meta`` table, or ``None``."""
    row = conn.execute(_SQL_META_GET, (key,)).fetchone()
    return str(row[0]) if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a key/value pair into the ``session_index_meta`` table."""
    conn.execute(_SQL_META_SET, (key, value))


# Index Synchronisation


def sync_session_index(force: bool = False) -> dict[str, int]:
    """Scan source files and upsert changed documents into the session index.

    Change detection is mtime + size based.  A full re-index is triggered
    automatically when ``SESSION_INDEX_SCHEMA_VERSION`` changes or when
    *force* is ``True``.

    Returns a stats dict with keys:
    ``scanned``, ``added``, ``updated``, ``removed``,
    ``skipped_recent``, ``last_sync_epoch``, ``total_sessions``.
    """
    db_path = ensure_session_db()
    added = updated = removed = scanned = batch_pending = 0
    now_epoch = int(datetime.now().timestamp())
    seen_paths: set[str] = set()

    with _open_db(db_path) as conn:
        current_version = _meta_get(conn, "schema_version")
        if current_version != SESSION_INDEX_SCHEMA_VERSION:
            conn.execute("DELETE FROM session_documents")
            _meta_set(conn, "schema_version", SESSION_INDEX_SCHEMA_VERSION)
            conn.commit()
            force = True

        last_sync_raw = _meta_get(conn, "last_sync_epoch")
        last_sync_epoch = int(last_sync_raw or "0")
        if not force and last_sync_epoch and (now_epoch - last_sync_epoch) < SYNC_MIN_INTERVAL_SEC:
            total = conn.execute(_SQL_COUNT_DOCS).fetchone()[0]
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
            seen_paths.add(canonical_path)

            try:
                stat = path.stat()
            except FileNotFoundError:
                continue

            row = conn.execute(_SQL_CHECK_CHANGED, (canonical_path,)).fetchone()
            if row and int(row[0]) == int(stat.st_mtime) and int(row[1]) == int(stat.st_size):
                continue

            doc = _parse_source(source_type, path)
            if not doc:
                continue

            conn.execute(
                _SQL_UPSERT_DOC,
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
                ),
            )
            updated += 1 if row else 0
            added += 0 if row else 1

            batch_pending += 1
            if batch_pending >= _BATCH_COMMIT_SIZE:
                conn.commit()
                batch_pending = 0

        # Remove index entries whose source files no longer exist.
        for (file_path,) in conn.execute(_SQL_ALL_PATHS).fetchall():
            if file_path not in seen_paths:
                conn.execute(_SQL_DELETE_DOC, (file_path,))
                removed += 1
                batch_pending += 1
                if batch_pending >= _BATCH_COMMIT_SIZE:
                    conn.commit()
                    batch_pending = 0

        _meta_set(conn, "last_sync_epoch", str(now_epoch))
        conn.commit()
        total = conn.execute(_SQL_COUNT_DOCS).fetchone()[0]

    return {
        "scanned": scanned,
        "added": added,
        "updated": updated,
        "removed": removed,
        "skipped_recent": 0,
        "last_sync_epoch": now_epoch,
        "total_sessions": int(total or 0),
    }


# Search and Ranking


def build_query_terms(query: str) -> list[str]:
    """Decompose a natural-language query into ranked search terms.

    Processing order:
    1. ISO date expressions -> normalised ``YYYY-MM-DD`` and ``YYYYMMDD``.
    2. Filesystem path tokens -> file/directory basename.
    3. ASCII identifier tokens (3-40 chars, not in STOPWORDS).
    4. Chinese CJK token sequences (2-12 chars) with prefix/suffix extraction.
    5. Fallback: the raw query string if nothing else matched.

    Returns at most 8 terms, deduplicated and stopword-filtered.
    """
    raw = (query or "").strip()
    if not raw:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        clean = term.strip().strip("\"'")
        if not clean or len(clean) < 2:
            return
        lower = clean.lower()
        if lower in seen or lower in STOPWORDS:
            return
        seen.add(lower)
        terms.append(clean)

    date_match = re.fullmatch(r"\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*", raw)
    if date_match:
        y, m, d = date_match.groups()
        _add(f"{y}-{int(m):02d}-{int(d):02d}")
        _add(f"{y}{int(m):02d}{int(d):02d}")

    for token in re.findall(r"(?:~?/[A-Za-z0-9._/-]+)", raw):
        _add(Path(token).name or token)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9._-]{2,40}", raw):
        if token.lower() not in STOPWORDS:
            _add(token)
    for token in re.findall(r"[\u4e00-\u9fff]{2,12}", raw):
        _add(token)
        normalized = token.lstrip("的了将把从向在对与和及并或再先后")
        if normalized != token or len(normalized) >= 6:
            if len(normalized) >= 2:
                _add(normalized[:2])
                _add(normalized[-2:])
            if len(normalized) >= 4:
                _add(normalized[:4])
                _add(normalized[-4:])

    if not terms:
        _add(raw)
    return terms[:8]


def _build_snippet(text: str, terms: list[str], radius: int = 80) -> str:
    """Extract a context window around the best term match in *text*.

    Falls back to known summary section headings or the first 2*radius
    characters when no term matches.
    """
    compact = _WHITESPACE_RE.sub(" ", text or "").strip()
    if not compact:
        return ""
    lower = compact.lower()
    idx = -1
    matched = ""
    best_score: int | None = None
    conclusion_markers = ("最终", "结论", "交付", "已完成", "核心")

    for term in terms:
        term_lower = term.lower()
        start = 0
        while True:
            pos = lower.find(term_lower, start)
            if pos < 0:
                break
            window = compact[max(0, pos - 120) : min(len(compact), pos + len(term) + 120)]
            score = pos
            if any(marker in window for marker in conclusion_markers):
                score -= 5000
            if pos > len(compact) // 2:
                score -= 500
            if best_score is None or score < best_score:
                best_score = score
                idx = pos
                matched = term
            start = pos + len(term_lower)

    if idx < 0:
        for marker in ("最终交付", "变更概览", "核心变化", "改动文件", "建议验证", "结论", "Summary"):
            pos = compact.find(marker)
            if pos >= 0:
                start = max(0, pos - radius // 2)
                end = min(len(compact), pos + len(marker) + radius + radius // 2)
                return compact[start:end]
        return compact[: radius * 2]

    start = max(0, idx - radius)
    end = min(len(compact), idx + len(matched) + radius)
    return compact[start:end]


def _native_search_rows(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Run a query against the native (Rust/Go) backend.

    Returns an empty list when the backend is not configured or fails.
    """
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
    except (OSError, RuntimeError):
        return []

    if result.returncode != 0:
        return []

    max_results = max(1, min(limit, 100))
    query_lower = query.lower().strip()
    rows: list[dict[str, Any]] = []

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
        if len(rows) >= max_results:
            break

    return rows


def _fetch_session_docs_by_paths(conn: sqlite3.Connection, file_paths: Iterable[str]) -> dict[str, sqlite3.Row]:
    """Batch-fetch ``session_documents`` rows by a collection of file paths."""
    unique_paths: list[str] = []
    seen: set[str] = set()
    for raw_path in file_paths:
        if not raw_path:
            continue
        path_str = _normalize_file_path(Path(str(raw_path)))
        if path_str not in seen:
            seen.add(path_str)
            unique_paths.append(path_str)

    if not unique_paths:
        return {}

    placeholders = ",".join("?" for _ in unique_paths)
    sql = f"SELECT * FROM session_documents WHERE file_path IN ({placeholders})"
    return {str(row["file_path"]): row for row in conn.execute(sql, tuple(unique_paths))}


def _enrich_native_rows(
    rows: list[dict[str, Any]],
    conn: sqlite3.Connection,
    terms: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Augment native-backend rows with metadata from the local SQLite index."""
    max_results = max(1, min(limit, 100))
    docs = _fetch_session_docs_by_paths(conn, (row.get("file_path") for row in rows if row.get("file_path")))
    enriched: list[dict[str, Any]] = []

    for row in rows:
        enriched_row = dict(row)
        raw_fp = row.get("file_path") or ""
        file_path = _normalize_file_path(Path(str(raw_fp))) if raw_fp else ""
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
        enriched_row["snippet"] = snippet or str(snippet_source or row.get("snippet") or "")
        enriched.append(enriched_row)

        if len(enriched) >= max_results:
            break

    return enriched


def _fetch_rows(
    conn: sqlite3.Connection,
    active_terms: list[str],
    row_limit: int = 200,
) -> list[sqlite3.Row]:
    """Build and execute a LIKE-based SQL query for the given terms.

    Each term generates an OR predicate across title, content, and file_path.
    All term values flow through bind parameters.
    """
    where_parts: list[str] = []
    args: list[Any] = []
    for term in active_terms:
        like_term = f"%{term.lower()}%"
        where_parts.append("(lower(title) LIKE ? OR lower(content) LIKE ? OR lower(file_path) LIKE ?)")
        args.extend([like_term, like_term, like_term])
    where_clause = f"WHERE {' OR '.join(where_parts)}" if where_parts else ""
    sql = f"SELECT * FROM session_documents {where_clause} ORDER BY created_at_epoch DESC LIMIT ?"
    args.append(max(1, int(row_limit)))
    return conn.execute(sql, args).fetchall()


def _rank_rows(
    candidate_rows: list[sqlite3.Row],
    active_terms: list[str],
    *,
    skip_cwd_title: bool = False,
) -> list[tuple[int, sqlite3.Row]]:
    """Score each candidate row and return those with a positive score.

    Scoring factors (additive):
    - Base weight from SOURCE_WEIGHT by source type.
    - Per-term hit bonus: max(4, len(term) squared).
    - Path-only content penalty: -180.
    - Noise penalty from ``_search_noise_penalty``.
    """
    ranked: list[tuple[int, sqlite3.Row]] = []
    cwd_str = str(Path.cwd().resolve())
    for row in candidate_rows:
        if skip_cwd_title and row["title"] == cwd_str:
            continue
        if _is_current_repo_meta_result(row["title"], row["content"], row["file_path"]):
            continue
        haystack = f"{row['title']}\n{row['content']}\n{row['file_path']}".lower()
        score = SOURCE_WEIGHT.get(str(row["source_type"]), 1)
        for term in active_terms:
            if term.lower() in haystack:
                score += max(4, len(term) * len(term))
        if _looks_like_path_only_content(row["title"], row["content"]):
            score -= 180
        score -= _search_noise_penalty(row["title"], row["content"], row["file_path"])
        if score > 0:
            ranked.append((score, row))
    return ranked


def _search_rows(query: str, limit: int = 10, literal: bool = False) -> list[dict[str, Any]]:
    """Execute a ranked search against the local session index.

    Search pipeline:
    1. Sync the index (honours ``SYNC_MIN_INTERVAL_SEC`` throttle).
    2. Attempt native backend if configured.
    3. Build LIKE-based SQL from ``build_query_terms`` output.
    4. Score and rank candidates; apply noise penalties.
    5. Fall back to anchor-term re-query when literal mode yields no results.

    Returns at most *limit* result dicts with keys:
    ``source_type``, ``session_id``, ``title``, ``file_path``,
    ``created_at``, ``created_at_epoch``, ``snippet``.
    """
    max_results = max(1, min(limit, 100))
    db_path = ensure_session_db()
    sync_session_index()

    with _open_db(db_path) as conn:
        terms = [query.strip()] if literal else build_query_terms(query)
        literal_fallback = False

        native_rows = _native_search_rows(query, limit=max_results)
        if native_rows:
            return _enrich_native_rows(native_rows, conn, terms, max_results)

        rows = _fetch_rows(conn, terms)
        if literal and not rows:
            expanded = build_query_terms(query)
            if expanded and expanded != terms:
                terms = expanded
                literal_fallback = True
                rows = _fetch_rows(conn, terms, row_limit=1000)

        ranked = _rank_rows(rows, terms, skip_cwd_title=literal_fallback)

        if literal and not ranked and rows:
            rows = _fetch_rows(conn, terms, row_limit=1000)
            ranked = _rank_rows(rows, terms, skip_cwd_title=literal_fallback)

        # Anchor-term fallback: find the 2 most-frequent terms and retry.
        if literal_fallback and not ranked and rows:
            term_freq: list[tuple[int, str]] = []
            for term in terms:
                term_lower = term.lower()
                freq = sum(
                    1 for row in rows if term_lower in f"{row['title']}\n{row['content']}\n{row['file_path']}".lower()
                )
                if freq > 0:
                    term_freq.append((freq, term))
            term_freq.sort(key=lambda item: (item[0], -len(item[1])))
            anchor_terms = [term for _, term in term_freq[:2]]
            if anchor_terms and anchor_terms != terms:
                terms = anchor_terms
                rows = _fetch_rows(conn, terms, row_limit=1000)
                ranked = _rank_rows(rows, terms, skip_cwd_title=literal_fallback)

        ranked.sort(key=lambda item: (item[0], item[1]["created_at_epoch"]), reverse=True)

        return [
            {
                "source_type": row["source_type"],
                "session_id": row["session_id"],
                "title": row["title"],
                "file_path": row["file_path"],
                "created_at": row["created_at"],
                "created_at_epoch": row["created_at_epoch"],
                "snippet": _build_snippet(row["content"], terms),
            }
            for _, row in ranked[:max_results]
        ]


# Public API


def format_search_results(
    query: str,
    *,
    search_type: str = "all",  # noqa: ARG001  (reserved for future filtering)
    limit: int = 10,
    literal: bool = False,
) -> str:
    """Format session search results as a human-readable multi-line string.

    Args:
        query:       The search query.
        search_type: Reserved; currently unused.
        limit:       Maximum number of results to return (1-100).
        literal:     When ``True``, treat *query* as a literal string before
                     falling back to term expansion.

    Returns a plain-text block suitable for display in a terminal or chat UI.
    """
    results = _search_rows(query, limit=limit, literal=literal)
    if not results:
        return "No matches found in local session index."

    lines = [f"Found {len(results)} sessions (local index):"]
    for idx, row in enumerate(results, 1):
        lines.append(f"[{idx}] {row['created_at'][:10]} | {row['session_id']} | {row['source_type']}")
        lines.append(f"    {row['title']}")
        lines.append(f"    File: {row['file_path']}")
        lines.append(f"    > {_compact_snippet(row['snippet'])}")
    return "\n".join(lines)


def health_payload() -> dict[str, Any]:
    """Return a health-check dict for the session index subsystem.

    Triggers a sync and queries the database for aggregate statistics.
    """
    sync_info = sync_session_index()
    db_path = ensure_session_db()
    with _open_db(db_path) as conn:
        total = conn.execute(_SQL_COUNT_DOCS).fetchone()[0]
        latest = conn.execute(_SQL_MAX_EPOCH).fetchone()[0]
    return {
        "session_index_db_exists": db_path.exists(),
        "session_index_db": str(db_path),
        "total_sessions": int(total or 0),
        "latest_epoch": int(latest or 0),
        "sync": sync_info,
    }
