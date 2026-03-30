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

__all__ = [
    "SessionDocument",
    "build_query_terms",
    "ensure_session_db",
    "format_search_results",
    "get_session_db_path",
    "health_payload",
    "lookup_session_by_id",
    "sync_session_index",
]

import contextlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from context_config import env_int, storage_root
    from source_adapters import adapter_dirty_epoch, discover_index_sources, sync_all_adapters
    from sqlite_retry import retry_commit as _rc
    from sqlite_retry import retry_sqlite as _rs
    from sqlite_retry import retry_sqlite_many as _rsm
except ImportError:  # pragma: no cover
    from .context_config import env_int, storage_root  # type: ignore[import-not-found]
    from .source_adapters import (  # type: ignore[import-not-found]
        adapter_dirty_epoch,
        discover_index_sources,
        sync_all_adapters,
    )
    from .sqlite_retry import retry_commit as _rc  # type: ignore[import-not-found]
    from .sqlite_retry import retry_sqlite as _rs
    from .sqlite_retry import retry_sqlite_many as _rsm


def _get_context_native() -> Any:
    """Lazily import and return the context_native module."""
    try:
        import context_native as _cn  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        from . import context_native as _cn  # type: ignore[import-not-found]
    return _cn


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

#: Set to True once FTS5 availability has been confirmed in the current process.
#: None = not yet checked; True = available; False = unavailable.
_FTS5_AVAILABLE: bool | None = None

#: Number of upsert rows per SQLite transaction batch during sync.
_BATCH_COMMIT_SIZE: int = env_int("CONTEXTGO_INDEX_BATCH_SIZE", default=100, minimum=10)

_logger = logging.getLogger(__name__)


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
    "CREATE INDEX IF NOT EXISTS idx_session_created    ON session_documents(created_at_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_session_source     ON session_documents(source_type, created_at_epoch DESC)",
    # Accelerates session_id look-ups in ranking and enrichment paths.
    "CREATE INDEX IF NOT EXISTS idx_session_session_id ON session_documents(session_id)",
    # Accelerates updated_at_epoch sorts used in health/stats queries.
    "CREATE INDEX IF NOT EXISTS idx_session_updated    ON session_documents(updated_at_epoch DESC)",
]

_DDL_SESSION_DOCUMENTS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS session_documents_fts
USING fts5(title, content, file_path, content=session_documents, content_rowid=rowid, tokenize='unicode61 remove_diacritics 1')
"""

# Triggers to keep the FTS5 shadow tables in sync with the main table.
_DDL_FTS_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS session_documents_fts_ai
    AFTER INSERT ON session_documents BEGIN
        INSERT INTO session_documents_fts(rowid, title, content, file_path)
        VALUES (new.rowid, new.title, new.content, new.file_path);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS session_documents_fts_ad
    AFTER DELETE ON session_documents BEGIN
        INSERT INTO session_documents_fts(session_documents_fts, rowid, title, content, file_path)
        VALUES ('delete', old.rowid, old.title, old.content, old.file_path);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS session_documents_fts_au
    AFTER UPDATE ON session_documents BEGIN
        INSERT INTO session_documents_fts(session_documents_fts, rowid, title, content, file_path)
        VALUES ('delete', old.rowid, old.title, old.content, old.file_path);
        INSERT INTO session_documents_fts(rowid, title, content, file_path)
        VALUES (new.rowid, new.title, new.content, new.file_path);
    END
    """,
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
    _keys = (
        "search_noise_markers",
        "native_noise_markers",
        "text_noise_markers",
        "text_noise_lower_markers",
        "noise_prefixes",
    )
    # Search multiple candidate locations for the config file:
    # 1. Package data: src/contextgo/data/ (works after pip-install)
    # 2. Repository root: config/ (works during development)
    _here = Path(__file__).resolve().parent
    candidates = [
        _here / "data" / "noise_markers.json",                  # pip-installed (package data)
        _here.parent.parent / "config" / "noise_markers.json",  # in-repo: config/ at project root
    ]
    for config_path in candidates:
        if config_path.exists():
            with open(config_path) as fh:
                data = json.load(fh)
            return {k: list(data.get(k, [])) for k in _keys}
    return {k: [] for k in _keys}


# Lazily loaded on first use; None means not yet loaded.
_NOISE_CONFIG: dict[str, list[str]] | None = None


def _get_noise_config() -> dict[str, list[str]]:
    """Return the noise config dict, loading it on first call (lazy initializer)."""
    global _NOISE_CONFIG
    if _NOISE_CONFIG is None:
        _NOISE_CONFIG = _load_noise_config()
    return _NOISE_CONFIG


# Module-level sentinel tuples; populated lazily via _ensure_noise_markers().
SEARCH_NOISE_MARKERS: tuple[str, ...] = ()
NATIVE_NOISE_MARKERS: tuple[str, ...] = ()
_NOISE_TEXT_MARKERS: tuple[str, ...] = ()
_NOISE_TEXT_LOWER_MARKERS: tuple[str, ...] = ()
_noise_markers_initialized: bool = False


def _ensure_noise_markers() -> None:
    """Populate noise-marker module globals on first call (idempotent)."""
    global SEARCH_NOISE_MARKERS, NATIVE_NOISE_MARKERS
    global _NOISE_TEXT_MARKERS, _NOISE_TEXT_LOWER_MARKERS, _noise_markers_initialized
    if _noise_markers_initialized:
        return
    cfg = _get_noise_config()
    SEARCH_NOISE_MARKERS = tuple(cfg["search_noise_markers"])
    NATIVE_NOISE_MARKERS = tuple(cfg["native_noise_markers"])
    _NOISE_TEXT_MARKERS = tuple(cfg["text_noise_markers"])
    _NOISE_TEXT_LOWER_MARKERS = tuple(cfg["text_noise_lower_markers"])
    _noise_markers_initialized = True

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
    }
)

# Chinese stopwords are only applied when the query contains enough
# non-stop CJK tokens (see ``build_query_terms``).  This avoids
# over-filtering short Chinese queries like "搜索方案" where every
# token would otherwise be discarded.
CJK_STOPWORDS: frozenset[str] = frozenset(
    {
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
    "opencode_session": 36,
    "kilo_session": 36,
    "openclaw_session": 36,
    "codex_history": 8,
    "claude_history": 8,
    "opencode_history": 6,
    "kilo_history": 6,
    "shell_zsh": 2,
    "shell_bash": 2,
}

# In-process cache for source-file discovery results.
_SOURCE_CACHE: dict[str, Any] = {"expires_at": 0.0, "items": [], "home": None}


def _cache_put_results(cache_key: str, results: list[dict[str, Any]]) -> None:
    """Insert *results* into the search result cache, evicting stale/excess entries."""
    if _SEARCH_RESULT_CACHE_TTL <= 0:
        return
    if len(_SEARCH_RESULT_CACHE) >= _SEARCH_CACHE_MAX_ENTRIES:
        _now = time.monotonic()
        expired = [k for k, (exp, _) in _SEARCH_RESULT_CACHE.items() if exp <= _now]
        for k in expired:
            del _SEARCH_RESULT_CACHE[k]
        while len(_SEARCH_RESULT_CACHE) >= _SEARCH_CACHE_MAX_ENTRIES:
            _SEARCH_RESULT_CACHE.pop(next(iter(_SEARCH_RESULT_CACHE)))
    _SEARCH_RESULT_CACHE[cache_key] = (time.monotonic() + _SEARCH_RESULT_CACHE_TTL, results)

# ---------------------------------------------------------------------------
# In-process search result cache (TTL-based)
# ---------------------------------------------------------------------------
# Cache TTL in seconds.  Set CONTEXTGO_SESSION_SEARCH_CACHE_TTL=0 to disable.
try:
    _SEARCH_RESULT_CACHE_TTL: int = int(os.environ.get("CONTEXTGO_SESSION_SEARCH_CACHE_TTL", "5") or "5")
except (ValueError, TypeError):
    _SEARCH_RESULT_CACHE_TTL: int = 5
# Mapping of cache_key -> (expiry_monotonic_float, results_list)
_SEARCH_RESULT_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_SEARCH_CACHE_MAX_ENTRIES: int = 64

# Pre-compiled whitespace normalizer used throughout this module.
_WHITESPACE_RE = re.compile(r"\s+")

# Pre-compiled CJK character matcher used in snippet and scoring helpers.
_CJK_CHAR_RE: re.Pattern[str] = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

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


def _highlight_query(text: str, query: str) -> str:
    """Highlight *query* terms in *text* using ANSI bold (case-insensitive).

    Only applies when stdout is a TTY.  Falls back to plain text otherwise.
    """
    if not sys.stdout.isatty() or not query.strip():
        return text
    _BOLD = "\033[1m"
    _RESET = "\033[0m"
    for term in query.split():
        if len(term) < 2:
            continue
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        text = pattern.sub(lambda m: f"{_BOLD}{m.group()}{_RESET}", text)
    return text


# Noise Filtering


def _is_noise_text(text: str) -> bool:
    """Return ``True`` if *text* should be excluded from the session index."""
    _ensure_noise_markers()
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
    _ensure_noise_markers()
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
    file_size: int | None = None,
) -> SessionDocument:
    """Build a SessionDocument from already-parsed fields.

    *file_size* may be supplied from a pre-fetched ``os.stat`` result to avoid
    an extra filesystem call; it is resolved via ``path.stat()`` when omitted.
    """
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
        file_size=file_size if file_size is not None else path.stat().st_size,
    )


def _iter_jsonl_objects(path: Path) -> Generator[dict[str, Any], None, None]:
    """Yield parsed JSON objects from a JSONL file, skipping blank/invalid lines."""
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


def _parse_codex_session(path: Path, file_stat: os.stat_result | None = None) -> SessionDocument | None:
    """Parse a Codex JSONL session file into a ``SessionDocument``.

    *file_stat* may be a pre-fetched ``os.stat_result`` to avoid a redundant
    filesystem call; the file is re-stat'd when omitted.
    """
    session_id = path.stem
    title = ""
    created_at = ""
    pieces: list[str] = []
    st = file_stat if file_stat is not None else path.stat()
    mtime = int(st.st_mtime)
    file_size = st.st_size
    try:
        for obj in _iter_jsonl_objects(path):
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
    return _finish_session_doc(path, "codex_session", session_id, title, created_at, pieces, mtime, file_size)


def _parse_claude_session(path: Path, file_stat: os.stat_result | None = None) -> SessionDocument | None:
    """Parse a Claude JSONL session file into a ``SessionDocument``.

    *file_stat* may be a pre-fetched ``os.stat_result`` to avoid a redundant
    filesystem call; the file is re-stat'd when omitted.
    """
    session_id = path.stem
    title = ""
    created_at = ""
    pieces: list[str] = []
    st = file_stat if file_stat is not None else path.stat()
    mtime = int(st.st_mtime)
    file_size = st.st_size
    try:
        for obj in _iter_jsonl_objects(path):
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
    return _finish_session_doc(path, "claude_session", session_id, title, created_at, pieces, mtime, file_size)


def _make_flat_doc(
    path: Path,
    source_type: str,
    texts: list[str],
    mtime: int,
    file_size: int | None = None,
) -> SessionDocument | None:
    """Build a flat ``SessionDocument`` from extracted text lines, or return ``None``.

    *file_size* may be supplied from a pre-fetched ``os.stat`` result to avoid
    an extra filesystem call; it is resolved via ``path.stat()`` when omitted.
    """
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
        file_size=file_size if file_size is not None else path.stat().st_size,
    )


def _parse_history_jsonl(
    path: Path, source_type: str, file_stat: os.stat_result | None = None
) -> SessionDocument | None:
    """Parse a flat JSONL history file into a ``SessionDocument``.

    *file_stat* may be a pre-fetched ``os.stat_result`` to avoid a redundant
    filesystem call; the file is re-stat'd when omitted.
    """
    st = file_stat if file_stat is not None else path.stat()
    mtime = int(st.st_mtime)
    file_size = st.st_size
    texts: list[str] = []
    try:
        for obj in _iter_jsonl_objects(path):
            if not isinstance(obj, dict):
                continue
            for key in ("display", "text", "input", "prompt", "message"):
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    texts.append(value)
                    break
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return _make_flat_doc(path, source_type, texts, mtime, file_size)


def _parse_generic_session_jsonl(
    path: Path, source_type: str, file_stat: os.stat_result | None = None
) -> SessionDocument | None:
    """Parse a generic JSONL session transcript into a ``SessionDocument``.

    This is intentionally permissive so newly-supported tools can be indexed
    from normalized adapter output or native JSONL session files without
    needing a bespoke parser for every vendor-specific event envelope.
    """

    def _extract_texts(node: Any) -> list[str]:
        texts: list[str] = []
        seen: set[str] = set()

        def add(value: Any) -> None:
            if not isinstance(value, str):
                return
            text = value.strip()
            if not text or text in seen:
                return
            seen.add(text)
            texts.append(text)

        def walk(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, str):
                add(value)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return
            node_type = str(value.get("type") or "").strip().lower()
            if node_type in {"text", "input_text", "output_text", "reasoning"}:
                add(value.get("text"))
            for key in ("text", "input", "prompt", "display", "message", "body", "summary", "title"):
                add(value.get(key))
            for key in ("content", "parts", "messages", "items", "payload", "data", "state", "response"):
                if key in value:
                    walk(value[key])

        walk(node)
        return texts

    st = file_stat if file_stat is not None else path.stat()
    mtime = int(st.st_mtime)
    file_size = st.st_size
    texts: list[str] = []
    session_id = path.stem
    title = path.name
    try:
        for obj in _iter_jsonl_objects(path):
            if not isinstance(obj, dict):
                continue
            raw_session_id = obj.get("session_id") or obj.get("sessionId") or obj.get("id")
            if isinstance(raw_session_id, str) and raw_session_id.strip():
                session_id = raw_session_id.strip()
            raw_title = obj.get("title")
            if isinstance(raw_title, str) and raw_title.strip():
                title = raw_title.strip()
            texts.extend(_extract_texts(obj))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    content = _truncate(texts)
    if not content:
        return None
    return SessionDocument(
        file_path=str(path),
        source_type=source_type,
        session_id=session_id,
        title=title,
        content=content,
        created_at=datetime.fromtimestamp(mtime).isoformat(),
        created_at_epoch=mtime,
        file_mtime=mtime,
        file_size=file_size,
    )


def _parse_shell_history(
    path: Path, source_type: str, file_stat: os.stat_result | None = None
) -> SessionDocument | None:
    """Parse a shell history file (zsh or bash) into a ``SessionDocument``.

    *file_stat* may be a pre-fetched ``os.stat_result`` to avoid a redundant
    filesystem call; the file is re-stat'd when omitted.
    """
    st = file_stat if file_stat is not None else path.stat()
    mtime = int(st.st_mtime)
    file_size = st.st_size
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
    return _make_flat_doc(path, source_type, texts, mtime, file_size)


def _parse_source(source_type: str, path: Path, file_stat: os.stat_result | None = None) -> SessionDocument | None:
    """Dispatch a source file to the appropriate parser.

    *file_stat* is forwarded to the individual parsers so they can reuse an
    already-fetched ``os.stat_result`` rather than re-stat'ing the file.
    """
    if source_type == "codex_session":
        return _parse_codex_session(path, file_stat)
    if source_type == "claude_session":
        return _parse_claude_session(path, file_stat)
    if source_type in {"opencode_session", "kilo_session", "openclaw_session"} and path.suffix == ".jsonl":
        return _parse_generic_session_jsonl(path, source_type, file_stat)
    if source_type.endswith("_history") and path.suffix == ".jsonl":
        return _parse_history_jsonl(path, source_type, file_stat)
    if source_type.startswith("shell_"):
        return _parse_shell_history(path, source_type, file_stat)
    return None


# Source Discovery


def _iter_sources() -> list[tuple[str, Path]]:
    """Return cached ``(source_type, path)`` pairs for all discoverable sources."""
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
            _cn = _get_context_native()
            result = _cn.run_native_scan(
                backend=native_backend,
                threads=4,
                json_output=True,
                release=(native_backend == "rust"),
                timeout=180,
            )
            if result.returncode == 0:
                items: list[tuple[str, Path]] = _cn.inventory_items(result)
                if items:
                    _update_source_cache(items, now, current_home)
                    return items
        except (OSError, RuntimeError):
            pass

    home = Path(current_home)
    discovered = discover_index_sources(home)

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
    """Return the session index DB path (env override or storage root)."""
    override = os.environ.get(SESSION_DB_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return storage_root() / "index" / "session_index.db"


def ensure_session_db() -> Path:
    """Create the session index database and schema if absent; return the path."""
    db_path = get_session_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _db_is_new = not db_path.exists()
    with _open_db(db_path) as conn:
        if _db_is_new:
            # Restrict the newly created SQLite file to owner-only access so
            # that session data (which may contain code and commands) is not
            # world-readable on shared machines.
            with contextlib.suppress(OSError):
                os.chmod(db_path, 0o600)
        _retry_sqlite(conn, _DDL_SESSION_DOCUMENTS)
        for ddl in _DDL_INDEXES:
            _retry_sqlite(conn, ddl)
        _retry_sqlite(conn, _DDL_SESSION_META)
        # Attempt to create the FTS5 virtual table and sync triggers.
        # If the SQLite build does not support FTS5 this is silently skipped.
        if _check_fts5_available(conn):
            try:
                _retry_sqlite(conn, _DDL_SESSION_DOCUMENTS_FTS)
                for trigger_ddl in _DDL_FTS_TRIGGERS:
                    _retry_sqlite(conn, trigger_ddl)
                # Populate the FTS index for any rows that already exist
                # (e.g. after a schema migration where the table was recreated).
                _retry_sqlite(conn, "INSERT INTO session_documents_fts(session_documents_fts) VALUES ('rebuild')")
            except sqlite3.OperationalError as exc:
                _logger.debug("FTS5 setup skipped: %s", exc)
        _retry_commit(conn)
    return db_path


@contextmanager
def _open_db(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection with WAL mode and ensure it is closed on exit.

    The connection is always closed in the ``finally`` block, even when a
    PRAGMA statement raises before ``yield`` is reached.
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-32000")
        conn.execute("PRAGMA mmap_size=536870912")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA page_size=4096")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        yield conn
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# SQLite retry helpers (delegated to shared sqlite_retry module)
# ---------------------------------------------------------------------------

# Thin wrappers that forward the module-level logger so callers see named
# warnings from this module rather than from sqlite_retry itself.


def _retry_sqlite(
    conn: sqlite3.Connection,
    sql: str,
    params: Any = None,
    max_retries: int = 3,
) -> sqlite3.Cursor:
    """Execute *sql* on *conn* with retry-on-busy logic."""
    return _rs(conn, sql, params, max_retries, _logger=_logger)


def _retry_sqlite_many(
    conn: sqlite3.Connection,
    sql: str,
    params_seq: Any,
    max_retries: int = 3,
) -> sqlite3.Cursor:
    """Like :func:`_retry_sqlite` but calls ``executemany`` instead of ``execute``."""
    return _rsm(conn, sql, params_seq, max_retries, _logger=_logger)


def _retry_commit(conn: sqlite3.Connection, max_retries: int = 3) -> None:
    """Commit *conn* with retry-on-busy logic."""
    _rc(conn, max_retries, _logger=_logger)


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    """Retrieve a value from the ``session_index_meta`` table, or ``None``."""
    row = _retry_sqlite(conn, _SQL_META_GET, (key,)).fetchone()
    return str(row[0]) if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a key/value pair into the ``session_index_meta`` table."""
    _retry_sqlite(conn, _SQL_META_SET, (key, value))


# Index Synchronisation


def _try_sync(force: bool = False) -> dict[str, int]:
    """Best-effort sync that degrades gracefully in read-only environments.

    Checks whether the database file (or its parent directory, when the file
    does not yet exist) is writable before attempting a sync.  If the
    filesystem is read-only the sync is skipped and a warning is logged so
    that callers (search, health) continue with whatever index already exists.

    Returns the sync result dict, or an empty dict when the sync was skipped.
    """
    db_path = get_session_db_path()
    # Determine the path to check: existing file beats parent directory.
    check_path = db_path if db_path.exists() else db_path.parent
    if not os.access(check_path, os.W_OK):
        _logger.warning(
            "_try_sync: database path %s is not writable — skipping sync "
            "(read-only environment); search/health will use existing index",
            check_path,
        )
        return {}
    try:
        return sync_session_index(force=force)
    except Exception as exc:
        _logger.warning(
            "_try_sync: sync failed (%s) — continuing with existing index",
            exc,
        )
        return {}


def sync_session_index(force: bool = False) -> dict[str, int]:
    """Scan source files and upsert changed documents (mtime+size based).

    Forces full re-index when the schema version changes or *force* is True.

    Batch behaviour is controlled by ``_BATCH_COMMIT_SIZE`` (configurable via
    the ``CONTEXTGO_INDEX_BATCH_SIZE`` env var): upsert rows are flushed to
    SQLite in chunks of that size rather than accumulated indefinitely, which
    bounds memory usage when indexing large collections.
    """
    _t_start = time.monotonic()
    db_path = ensure_session_db()
    added = updated = removed = scanned = 0
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    seen_paths: set[str] = set()

    with _open_db(db_path) as conn:
        current_version = _meta_get(conn, "schema_version")
        if current_version != SESSION_INDEX_SCHEMA_VERSION:
            _retry_sqlite(conn, "DELETE FROM session_documents")
            _meta_set(conn, "schema_version", SESSION_INDEX_SCHEMA_VERSION)
            _retry_commit(conn)
            force = True

        last_sync_raw = _meta_get(conn, "last_sync_epoch")
        try:
            last_sync_epoch = int(last_sync_raw or "0")
        except (ValueError, TypeError):
            last_sync_epoch = 0
        # Fast-path throttle check: skip the expensive adapter refresh and full
        # re-scan when the index was synced recently and no adapter has been dirtied.
        # We intentionally read the *cached* adapter_dirty epoch here (without
        # calling sync_all_adapters first) so that frequent search calls do not
        # trigger a full filesystem scan on every invocation.
        adapter_dirty = adapter_dirty_epoch(_home())
        if (
            not force
            and last_sync_epoch
            and (now_epoch - last_sync_epoch) < SYNC_MIN_INTERVAL_SEC
            and adapter_dirty < last_sync_epoch
        ):
            total = _retry_sqlite(conn, _SQL_COUNT_DOCS).fetchone()[0]
            _logger.debug(
                "sync_session_index skipped (last_sync %ds ago, threshold %ds)",
                now_epoch - last_sync_epoch,
                SYNC_MIN_INTERVAL_SEC,
            )
            return {
                "scanned": 0,
                "added": 0,
                "updated": 0,
                "removed": 0,
                "skipped_recent": 1,
                "last_sync_epoch": last_sync_epoch,
                "total_sessions": int(total or 0),
            }

        # Throttle did not fire — we are about to do a full re-scan.  Only now
        # refresh external adapters so newly installed platforms become searchable
        # without waiting for the next TTL cycle.
        sync_all_adapters(_home())

        _t_scan_start = time.monotonic()
        upsert_batch: list[tuple[Any, ...]] = []
        queued_paths: set[str] = set()

        # --- P0 Fix 1: bulk-load all existing mtime/size into memory to avoid N+1 SELECTs ---
        existing_meta: dict[str, tuple[int, int]] = {
            row[0]: (int(row[1]), int(row[2]))
            for row in _retry_sqlite(
                conn, "SELECT file_path, file_mtime, file_size FROM session_documents"
            ).fetchall()
        }

        def _flush_upsert_batch() -> None:
            """Flush the current upsert batch to the database and commit."""
            if upsert_batch:
                _retry_sqlite_many(conn, _SQL_UPSERT_DOC, upsert_batch)
                _retry_commit(conn)
                _logger.debug("sync_session_index: flushed %d upsert rows", len(upsert_batch))
                upsert_batch.clear()

        for source_type, path in _iter_sources():
            scanned += 1
            canonical_path = _normalize_file_path(path)
            seen_paths.add(canonical_path)

            # Skip duplicates that resolve to the same canonical path.
            if canonical_path in queued_paths:
                continue

            try:
                stat = path.stat()
            except FileNotFoundError:
                continue

            # O(1) in-memory lookup instead of per-file SELECT query.
            cached = existing_meta.get(canonical_path)
            row = cached  # truthy when the record already exists
            if cached and cached[0] == int(stat.st_mtime) and cached[1] == int(stat.st_size):
                continue

            # Pass the already-fetched stat result to avoid redundant syscalls
            # inside the parser (each parser would otherwise re-stat the file).
            doc = _parse_source(source_type, path, file_stat=stat)
            if not doc:
                continue

            upsert_batch.append(
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
            queued_paths.add(canonical_path)
            updated += 1 if row else 0
            added += 0 if row else 1

            # Flush to DB when the batch reaches the configured threshold.
            if len(upsert_batch) >= _BATCH_COMMIT_SIZE:
                _flush_upsert_batch()

        # Flush any remaining upsert rows.
        _flush_upsert_batch()

        _t_scan_elapsed = time.monotonic() - _t_scan_start
        _logger.debug(
            "sync_session_index: scanned %d sources in %.3fs (added=%d updated=%d)",
            scanned,
            _t_scan_elapsed,
            added,
            updated,
        )

        # Remove index entries whose source files no longer exist.
        # --- P0 Fix 2: use a temporary table + single DELETE to avoid full-scan + Python set-diff ---
        _t_remove_start = time.monotonic()
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _temp_seen_paths (path TEXT PRIMARY KEY)"
        )
        conn.execute("DELETE FROM _temp_seen_paths")
        # Insert seen paths in batches to avoid SQLite variable limit.
        seen_list = list(seen_paths)
        for i in range(0, len(seen_list), _BATCH_COMMIT_SIZE):
            chunk = seen_list[i : i + _BATCH_COMMIT_SIZE]
            conn.executemany(
                "INSERT OR IGNORE INTO _temp_seen_paths(path) VALUES (?)",
                ((p,) for p in chunk),
            )
        # Count stale rows before deletion for the return value.
        stale_count_row = conn.execute(
            "SELECT COUNT(*) FROM session_documents"
            " WHERE file_path NOT IN (SELECT path FROM _temp_seen_paths)"
        ).fetchone()
        removed = int(stale_count_row[0]) if stale_count_row else 0
        if removed:
            conn.execute(
                "DELETE FROM session_documents"
                " WHERE file_path NOT IN (SELECT path FROM _temp_seen_paths)"
            )
            _logger.debug("sync_session_index: deleted %d stale rows via temp table", removed)
        conn.execute("DROP TABLE IF EXISTS _temp_seen_paths")
        _retry_commit(conn)

        _meta_set(conn, "last_sync_epoch", str(now_epoch))

        # Rebuild the FTS5 index after bulk inserts/updates/deletes so that BM25
        # scores remain accurate.  This is a no-op when FTS5 is unavailable.
        if (added or updated or removed or force) and _check_fts5_available(conn):
            try:
                _retry_sqlite(conn, "INSERT INTO session_documents_fts(session_documents_fts) VALUES ('rebuild')")
                _logger.debug("sync_session_index: FTS5 index rebuilt")
            except sqlite3.OperationalError as exc:
                _logger.debug("FTS5 rebuild skipped: %s", exc)

        _retry_commit(conn)

        # --- Vector embedding: embed new/updated session documents ---
        if EXPERIMENTAL_SEARCH_BACKEND == "vector":
            try:
                try:
                    from vector_index import embed_pending_session_docs, get_vector_db_path, vector_available  # noqa: PLC0415, I001
                except ImportError:
                    from .vector_index import embed_pending_session_docs, get_vector_db_path, vector_available  # type: ignore[import-not-found]  # noqa: PLC0415, I001

                if vector_available():
                    _vdb = get_vector_db_path(db_path)
                    _vresult = embed_pending_session_docs(db_path, _vdb, force=force)
                    _logger.debug(
                        "sync_session_index: vector embed result: embedded=%d skipped=%d deleted=%d",
                        _vresult.get("embedded", 0),
                        _vresult.get("skipped", 0),
                        _vresult.get("deleted", 0),
                    )
            except Exception as exc:
                _logger.debug("sync_session_index: vector embedding skipped: %s", exc)

        total = _retry_sqlite(conn, _SQL_COUNT_DOCS).fetchone()[0]

        _t_remove_elapsed = time.monotonic() - _t_remove_start
        _logger.debug(
            "sync_session_index: removed %d stale entries in %.3fs",
            removed,
            _t_remove_elapsed,
        )

    _t_total = time.monotonic() - _t_start
    _logger.debug(
        "sync_session_index complete in %.3fs: total=%d scanned=%d added=%d updated=%d removed=%d",
        _t_total,
        int(total or 0),
        scanned,
        added,
        updated,
        removed,
    )
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
    """Decompose a query into at most 8 deduplicated, stopword-filtered search terms.

    CJK stopwords are only applied when the query yields at least one
    non-CJK-stop term, preventing short Chinese queries (e.g. "搜索方案")
    from being entirely discarded.
    """
    raw = (query or "").strip()
    if not raw:
        return []

    terms: list[str] = []
    seen: set[str] = set()
    # Collect CJK tokens that matched a CJK stopword so we can add them
    # back if the final term list would otherwise be empty.
    cjk_stopped: list[str] = []

    def _add(term: str) -> None:
        clean = term.strip().strip("\"'")
        if not clean or len(clean) < 2:
            return
        lower = clean.lower()
        if lower in seen or lower in STOPWORDS:
            return
        if lower in CJK_STOPWORDS:
            if lower not in seen:
                cjk_stopped.append(clean)
                seen.add(lower)
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
        if token.lower() not in STOPWORDS and token.lower() not in CJK_STOPWORDS:
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
        # Re-add CJK-stopped tokens when no other terms survived filtering;
        # fall back to the raw query as a last resort.
        if cjk_stopped:
            terms.extend(cjk_stopped)
        else:
            # Bypass stopword checks — the raw query itself is the only signal.
            clean = raw.strip().strip("\"'")
            if clean and len(clean) >= 2 and clean.lower() not in seen:
                seen.add(clean.lower())
                terms.append(clean)
    return terms[:8]


def _cjk_safe_boundary(text: str, pos: int, direction: int) -> int:
    """Adjust *pos* so it does not split the middle of a CJK character run.

    *direction* should be -1 (move left) for a start boundary or +1 (move
    right) for an end boundary.  The function walks at most 4 characters in
    the given direction until it finds a non-CJK character or a whitespace
    boundary.
    """
    length = len(text)
    adjusted = max(0, min(pos, length))
    for _ in range(4):
        check = adjusted + (direction if direction == 1 else 0) - (1 if direction == -1 else 0)
        if check < 0 or check >= length:
            break
        if _CJK_CHAR_RE.match(text[check]):
            adjusted += direction
            adjusted = max(0, min(adjusted, length))
        else:
            break
    return adjusted


def _build_snippet(text: str, terms: list[str], radius: int = 80) -> str:
    """Extract a context window around the best term match in *text*.

    Snippet selection strategy (in priority order):
      1. Find all candidate windows (one per term occurrence) and pick the
         window that covers the *most distinct query terms* (coverage scoring).
      2. Among windows with equal coverage, prefer those containing a
         conclusion marker (最终, 结论, …) and those closer to the start.
      3. If no term matches, fall back to known summary headings or the
         first ``2*radius`` characters.

    CJK boundary handling: start/end positions are nudged outward to avoid
    splitting a contiguous run of CJK characters.
    """
    compact = _WHITESPACE_RE.sub(" ", text or "").strip()
    if not compact:
        return ""
    lower = compact.lower()
    conclusion_markers = ("最终", "结论", "交付", "已完成", "核心")
    lower_terms = [t.lower() for t in terms if t]

    # Detect CJK query so we apply boundary adjustment selectively.
    is_cjk_query = any(_CJK_CHAR_RE.search(t) for t in lower_terms)

    # --- Phase 1: collect candidate windows ---
    # Each candidate is (start, end, matched_term_lower).
    candidates: list[tuple[int, int, str]] = []
    for term_lower in lower_terms:
        start = 0
        while True:
            pos = lower.find(term_lower, start)
            if pos < 0:
                break
            w_start = max(0, pos - radius)
            w_end = min(len(compact), pos + len(term_lower) + radius)
            if is_cjk_query:
                w_start = _cjk_safe_boundary(compact, w_start, -1)
                w_end = _cjk_safe_boundary(compact, w_end, 1)
            candidates.append((w_start, w_end, term_lower))
            start = pos + max(1, len(term_lower))

    if not candidates:
        # Fallback: summary headings.
        for marker in ("最终交付", "变更概览", "核心变化", "改动文件", "建议验证", "结论", "Summary"):
            pos = compact.find(marker)
            if pos >= 0:
                fb_start = max(0, pos - radius // 2)
                fb_end = min(len(compact), pos + len(marker) + radius + radius // 2)
                return compact[fb_start:fb_end]
        return compact[: radius * 2]

    # --- Phase 2: score each candidate window by coverage ---
    best_window: tuple[int, int] = candidates[0][:2]
    best_coverage = -1
    best_has_conclusion = False
    best_pos = len(compact)

    for w_start, w_end, _ in candidates:
        window_lower = lower[w_start:w_end]
        coverage = sum(1 for t in lower_terms if t in window_lower)
        has_conclusion = any(m in window_lower for m in conclusion_markers)
        # Higher coverage wins; tie-break: conclusion markers > earlier position.
        if (
            coverage > best_coverage
            or (coverage == best_coverage and has_conclusion and not best_has_conclusion)
            or (coverage == best_coverage and has_conclusion == best_has_conclusion and w_start < best_pos)
        ):
            best_coverage = coverage
            best_has_conclusion = has_conclusion
            best_pos = w_start
            best_window = (w_start, w_end)

    return compact[best_window[0] : best_window[1]]


def _native_search_rows(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Run a query against the native (Rust/Go) backend.

    Returns an empty list when the backend is not configured or fails.
    """
    _ensure_noise_markers()
    if not query.strip():
        return []
    backend = EXPERIMENTAL_SEARCH_BACKEND
    if backend not in {"rust", "go"}:
        return []

    try:
        _cn = _get_context_native()
        result = _cn.run_native_scan(
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

    for item in _cn.extract_matches(result):
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
    return {str(row["file_path"]): row for row in _retry_sqlite(conn, sql, tuple(unique_paths))}


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


def _check_fts5_available(conn: sqlite3.Connection) -> bool:
    """Return True if the current SQLite build supports FTS5.

    The result is cached in the module-level ``_FTS5_AVAILABLE`` flag so that
    subsequent calls within the same process skip the probe query entirely.
    """
    global _FTS5_AVAILABLE  # noqa: PLW0603
    if _FTS5_AVAILABLE is not None:
        return _FTS5_AVAILABLE
    try:
        conn.execute("SELECT fts5(?)", ("test",))
        _FTS5_AVAILABLE = True
    except sqlite3.OperationalError:
        # fts5() scalar is not available — try creating a temp table instead.
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._fts5_probe USING fts5(x)")
            conn.execute("DROP TABLE IF EXISTS temp._fts5_probe")
            _FTS5_AVAILABLE = True
        except sqlite3.OperationalError:
            _FTS5_AVAILABLE = False
    return bool(_FTS5_AVAILABLE)


def _fts5_search_rows(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
) -> list[sqlite3.Row]:
    """Search the FTS5 virtual table using BM25 ranking.

    Builds a simple FTS5 MATCH expression from *query*.  Each whitespace-
    separated token is treated as a prefix match (``token*``) which works
    well for both ASCII and CJK text (CJK characters have no word boundaries
    so individual character n-grams match naturally).

    Returns an empty list when the FTS5 table does not exist, when *query* is
    empty, or when the query fails for any reason.
    """
    if not query.strip():
        return []

    def _build_fts_query(raw: str) -> str:
        """Convert *raw* to a safe FTS5 MATCH expression.

        Special FTS5 characters are escaped and each non-empty token is
        wrapped in double-quotes so that punctuation inside tokens is treated
        literally.  A trailing ``*`` enables prefix matching.
        """
        # FTS5 special characters that must be escaped inside quoted phrases
        # are limited to '"' itself (double-quote closes a phrase).
        tokens = raw.split()
        parts: list[str] = []
        for tok in tokens:
            if not tok:
                continue
            # Escape embedded double-quotes
            safe = tok.replace('"', '""')
            parts.append(f'"{safe}"*')
        return " ".join(parts) if parts else '""'

    fts_query = _build_fts_query(query)
    row_limit = max(1, min(limit * 10, 500))

    try:
        # bm25 weights: title(10x) > content(5x) > file_path(1x) for better relevance
        sql = (
            "SELECT sd.* FROM session_documents sd "
            "JOIN session_documents_fts fts ON sd.rowid = fts.rowid "
            "WHERE session_documents_fts MATCH ? "
            "ORDER BY bm25(session_documents_fts, 10.0, 5.0, 1.0) "
            "LIMIT ?"
        )
        return _retry_sqlite(conn, sql, (fts_query, row_limit)).fetchall()
    except sqlite3.OperationalError as exc:
        _logger.debug("FTS5 search failed, falling back to LIKE: %s", exc)
        return []


def _fetch_rows(
    conn: sqlite3.Connection,
    active_terms: list[str],
    row_limit: int = 200,
) -> list[sqlite3.Row]:
    """Build and execute a LIKE-based SQL query for the given terms.

    Each term generates an OR predicate across title, content, and file_path.
    All term values flow through bind parameters.  ``COLLATE NOCASE`` is used
    instead of wrapping columns in ``lower()`` so that SQLite avoids a
    per-row function call and can potentially leverage an index with a
    matching collation.
    """
    where_parts: list[str] = []
    args: list[Any] = []
    for term in active_terms:
        like_term = f"%{term.lower()}%"
        where_parts.append(
            "(title LIKE ? COLLATE NOCASE OR content LIKE ? COLLATE NOCASE OR file_path LIKE ? COLLATE NOCASE)"
        )
        args.extend([like_term, like_term, like_term])
    where_clause = f"WHERE {' OR '.join(where_parts)}" if where_parts else ""
    sql = f"SELECT * FROM session_documents {where_clause} ORDER BY created_at_epoch DESC, file_path DESC LIMIT ?"
    args.append(max(1, int(row_limit)))
    return _retry_sqlite(conn, sql, args).fetchall()


def _score_term_frequency(text: str, terms: list[str]) -> float:
    """Compute a TF-IDF-inspired term-frequency score for *text* against *terms*.

    Each term's contribution is ``count * sqrt(len(term))`` so longer terms
    get a mild length bonus without overwhelming the score.  The final value
    is capped at 100 to keep it bounded relative to SOURCE_WEIGHT values.
    """
    if not text or not terms:
        return 0.0
    lower = text.lower()
    total = 0.0
    for term in terms:
        term_lower = term.lower()
        if not term_lower:
            continue
        count = lower.count(term_lower)
        if count:
            total += count * (len(term_lower) ** 0.5)
    return min(total, 100.0)


# Approximate current Unix epoch for recency scoring; recomputed each call to
# _rank_rows so it stays accurate across long-running daemon processes without
# being re-imported.
def _recency_bonus(created_at_epoch: int) -> float:
    """Return a mild logarithmic recency bonus in the range [0, 20].

    Documents created within the last day score close to 20; documents older
    than ~100 days score near 0.  The log base is chosen so the decay is
    gradual enough not to bury high-quality older results.
    """
    age_secs = max(0, int(time.time()) - int(created_at_epoch))
    age_days = age_secs / 86400.0
    # log2(1) = 0, log2(2) ≈ 1, log2(128) = 7 → scores 20, ~17, ~0
    return max(0.0, 20.0 - math.log2(1.0 + age_days) * 3.0)


def _rank_rows(
    candidate_rows: list[sqlite3.Row],
    active_terms: list[str],
    *,
    skip_cwd_title: bool = False,
) -> list[tuple[int, sqlite3.Row]]:
    """Score and filter candidate rows, returning those with positive scores.

    Scoring components (in priority order):
      1. SOURCE_WEIGHT  — primary discriminator, unchanged from baseline.
      2. Term-length-squared match bonus — baseline term bonus.
      3. TF score       — term frequency reward (capped at 100).
      4. Title bonus    — 3× weight for terms found in the title field.
      5. Exact-phrase bonus — +50 when the joined query phrase hits verbatim.
      6. CJK bigram bonus — extra credit for CJK bigram overlaps.
      7. Recency bonus  — logarithmic [0, 20] boost for newer documents.
      8. Noise penalties — unchanged from baseline.
    """
    ranked: list[tuple[int, sqlite3.Row]] = []
    cwd_str = str(Path.cwd().resolve())

    # Precompute lowercased terms once.
    lower_terms = [t.lower() for t in active_terms if t]

    # Build the exact phrase to check for the phrase bonus.
    # We join the original query terms with a space for a "natural" phrase.
    exact_phrase = " ".join(lower_terms)

    # Detect CJK queries: at least one term contains a CJK character.
    has_cjk = any(_CJK_CHAR_RE.search(t) for t in lower_terms)

    # Build CJK bigrams from all terms for bigram matching.
    cjk_bigrams: list[str] = []
    if has_cjk:
        for term in lower_terms:
            cjk_chars = _CJK_CHAR_RE.findall(term)
            for i in range(len(cjk_chars) - 1):
                bg = cjk_chars[i] + cjk_chars[i + 1]
                if bg not in cjk_bigrams:
                    cjk_bigrams.append(bg)

    for row in candidate_rows:
        if skip_cwd_title and row["title"] == cwd_str:
            continue
        if _is_current_repo_meta_result(row["title"], row["content"], row["file_path"]):
            continue

        title_lower = str(row["title"] or "").lower()
        content_lower = str(row["content"] or "").lower()
        fp_lower = str(row["file_path"] or "").lower()
        haystack = f"{title_lower}\n{content_lower}\n{fp_lower}"

        # 1. SOURCE_WEIGHT — primary factor.
        score: float = SOURCE_WEIGHT.get(str(row["source_type"]), 1)

        # 2. Baseline term-length-squared bonus + 3. TF + 4. Title bonus.
        for term_lower in lower_terms:
            if term_lower in haystack:
                # Baseline squared-length bonus.
                score += max(4, len(term_lower) * len(term_lower))

                # TF score from full content.
                score += _score_term_frequency(content_lower, [term_lower])

                # Title position bonus: 3× extra for title matches.
                if term_lower in title_lower:
                    score += max(4, len(term_lower) * len(term_lower)) * 2  # 3× total

        # 5. Exact-phrase bonus.
        if exact_phrase and len(exact_phrase) >= 2 and exact_phrase in haystack:
            score += 50

        # 6. CJK bigram bonus.
        if cjk_bigrams:
            bigram_hits = sum(1 for bg in cjk_bigrams if bg in haystack)
            score += bigram_hits * 8

        # 7. Recency bonus.
        score += _recency_bonus(int(row["created_at_epoch"] or 0))

        # 8. Noise penalties (unchanged).
        if _looks_like_path_only_content(row["title"], row["content"]):
            score -= 180
        score -= _search_noise_penalty(row["title"], row["content"], row["file_path"])

        if score > 0:
            ranked.append((int(score), row))
    return ranked


def _search_rows(query: str, limit: int = 10, literal: bool = False) -> list[dict[str, Any]]:
    """Execute a ranked search against the local session index.

    Attempts FTS5 full-text search first for dramatically faster lookups when
    the ``session_documents_fts`` virtual table is available.  Falls back to
    ``LIKE``-based scanning (``COLLATE NOCASE``) when FTS5 is unavailable or
    the query fails.

    Results are cached in-process for ``_SEARCH_RESULT_CACHE_TTL`` seconds
    so that repeated identical queries within a single CLI invocation avoid
    redundant I/O and ranking work.
    """
    max_results = max(1, min(limit, 100))

    db_path = ensure_session_db()

    # Build a stable cache key that includes the DB path so results from
    # different databases (e.g. per-test temp DBs) never cross-contaminate.
    cache_key = json.dumps([str(db_path), query, max_results, literal], ensure_ascii=False)

    if _SEARCH_RESULT_CACHE_TTL > 0:
        now_mono = time.monotonic()
        cached = _SEARCH_RESULT_CACHE.get(cache_key)
        if cached is not None and cached[0] > now_mono:
            return list(cached[1])

    _try_sync()

    with _open_db(db_path) as conn:
        terms = [query.strip()] if literal else build_query_terms(query)
        literal_fallback = False

        # --- Vector hybrid search backend ---
        if EXPERIMENTAL_SEARCH_BACKEND == "vector":
            try:
                try:
                    from vector_index import (  # noqa: PLC0415
                        fetch_enriched_results,
                        get_vector_db_path,
                        hybrid_search_session,
                        vector_available,
                    )
                except ImportError:
                    from .vector_index import (  # type: ignore[import-not-found]  # noqa: PLC0415
                        fetch_enriched_results,
                        get_vector_db_path,
                        hybrid_search_session,
                        vector_available,
                    )

                if vector_available():
                    _vdb = get_vector_db_path(db_path)
                    ranked = hybrid_search_session(query, db_path, _vdb, limit=max_results)
                    if ranked:
                        results = fetch_enriched_results(ranked, db_path, query)
                        if results:
                            _cache_put_results(cache_key, results)
                            return results
            except Exception as exc:
                _logger.debug("_search_rows: vector search fallback: %s", exc)

        native_rows = _native_search_rows(query, limit=max_results)
        if native_rows:
            results = _enrich_native_rows(native_rows, conn, terms, max_results)
            _cache_put_results(cache_key, results)
            return results

        # Try FTS5 first; fall back to LIKE-based scan when unavailable.
        fts5_rows = _fts5_search_rows(conn, query, limit=max_results) if _check_fts5_available(conn) else []
        rows: list[sqlite3.Row] = fts5_rows if fts5_rows else _fetch_rows(conn, terms)
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

        results = [
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

    _cache_put_results(cache_key, results)
    return results


# Public API


_VALID_SEARCH_TYPES = frozenset({"all", "codex", "claude", "shell", "event", "session", "turn", "content"})


def lookup_session_by_id(
    session_id_prefix: str,
    *,
    limit: int = 10,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Look up session documents by session_id prefix.

    Returns a list of dicts matching the ``_search_rows`` output contract.
    """
    db = Path(db_path) if db_path else ensure_session_db()
    with _open_db(db) as conn:
        rows = conn.execute(
            "SELECT source_type, session_id, title, file_path, "
            "created_at, created_at_epoch, content "
            "FROM session_documents WHERE LOWER(session_id) LIKE ? "
            "ORDER BY created_at_epoch DESC LIMIT ?",
            (session_id_prefix.lower() + "%", limit),
        ).fetchall()
    return [
        {
            "source_type": r["source_type"],
            "session_id": r["session_id"],
            "title": r["title"],
            "file_path": r["file_path"],
            "created_at": r["created_at"],
            "created_at_epoch": r["created_at_epoch"],
            "snippet": (r["content"] or "")[:240].strip(),
        }
        for r in rows
    ]


def format_search_results(
    query: str,
    *,
    search_type: str = "all",
    limit: int = 10,
    literal: bool = False,
) -> str:
    """Format session search results as a human-readable multi-line string.

    *search_type* filters results by source type (e.g. ``"codex"``, ``"claude"``).
    ``"all"`` returns every source type.
    """
    effective_limit = limit * 5 if (search_type != "all" and search_type in _VALID_SEARCH_TYPES) else limit
    results = _search_rows(query, limit=effective_limit, literal=literal)
    if search_type != "all" and search_type in _VALID_SEARCH_TYPES:
        results = [r for r in results if r.get("source_type", "").startswith(search_type)]
    results = results[:limit]
    if not results:
        return "No matches found in local session index."

    lines = [f"Found {len(results)} sessions (local index):"]
    for idx, row in enumerate(results, 1):
        title = _highlight_query(row["title"], query)
        snippet = _highlight_query(_compact_snippet(row["snippet"]), query)
        lines.append(f"[{idx}] {row['created_at'][:10]} | {row['session_id']} | {row['source_type']}")
        lines.append(f"    {title}")
        lines.append(f"    File: {row['file_path']}")
        lines.append(f"    > {snippet}")
    return "\n".join(lines)


def health_payload() -> dict[str, Any]:
    """Return a health-check dict for the session index subsystem.

    Attempts a best-effort sync before querying statistics.  In read-only
    environments the sync is skipped gracefully and health data is still
    returned from the existing index.
    """
    sync_info = _try_sync()
    db_path = ensure_session_db()
    with _open_db(db_path) as conn:
        total = _retry_sqlite(conn, _SQL_COUNT_DOCS).fetchone()[0]
        latest = _retry_sqlite(conn, _SQL_MAX_EPOCH).fetchone()[0]
    return {
        "session_index_db_exists": db_path.exists(),
        "session_index_db": str(db_path),
        "total_sessions": int(total or 0),
        "latest_epoch": int(latest or 0),
        "sync": sync_info,
    }
