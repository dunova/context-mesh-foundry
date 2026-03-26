#!/usr/bin/env python3
"""Shared runtime helpers for ContextGO."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


TEXT_FILE_SUFFIXES = frozenset({".md", ".txt", ".json", ".jsonl", ".log"})

_WHITESPACE_RE = re.compile(r"\s+")


def safe_mtime(path: Path | str) -> float:
    """Return file mtime as float, or 0.0 on any error."""
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def compact_text(text: str) -> str:
    """Collapse all whitespace runs to a single space and strip edges."""
    return _WHITESPACE_RE.sub(" ", text or "").strip()


def iter_shared_files(shared_root: Path | str, max_files: int) -> list[Path]:
    """Return up to max_files text files under shared_root, newest first."""
    root = Path(shared_root)
    if not root.is_dir():
        return []
    files: list[Path] = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in TEXT_FILE_SUFFIXES
    ]
    files.sort(key=safe_mtime, reverse=True)
    return files[: max(1, int(max_files))]


def _build_uri_hint(rel_path: str, uri_prefix: str) -> str:
    prefix = (uri_prefix or "").strip()
    if not prefix:
        return rel_path
    return f"{prefix}{rel_path}"


def local_memory_matches(
    query: str,
    *,
    shared_root: Path | str,
    limit: int = 3,
    max_files: int = 300,
    read_bytes: int = 120000,
    uri_prefix: str = "local://",
    files: Iterable[Path | str] | None = None,
) -> list[dict[str, Any]]:
    """Search shared_root for files matching query; return up to limit results."""
    q = (query or "").strip()
    if not q:
        return []

    root = Path(shared_root)
    search_files: list[Path] = (
        [Path(p) for p in files] if files is not None else iter_shared_files(root, max_files=max_files)
    )
    ql = q.lower()
    cap = max(1, int(limit))
    read_cap = max(4096, int(read_bytes))
    matches: list[dict[str, Any]] = []

    for path in search_files:
        matched_in: str | None = None
        snippet = ""
        try:
            rel_path = path.relative_to(root).as_posix()
        except ValueError:
            rel_path = path.name
        if ql in rel_path.lower():
            matched_in = "path"
            snippet = rel_path
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:read_cap]
            except OSError:
                continue
            idx = text.lower().find(ql)
            if idx >= 0:
                matched_in = "content"
                start = max(0, idx - 120)
                end = min(len(text), idx + len(q) + 120)
                snippet = compact_text(text[start:end])

        if matched_in:
            matches.append(
                {
                    "uri_hint": _build_uri_hint(rel_path, uri_prefix),
                    "file_path": str(path),
                    "matched_in": matched_in,
                    "mtime": datetime.fromtimestamp(safe_mtime(path)).isoformat(),
                    "snippet": snippet,
                }
            )
        if len(matches) >= cap:
            break
    return matches


def normalize_tags(tags: list[str] | str | None) -> list[str]:
    """Normalize tags from list, comma-separated string, or JSON array string."""
    if tags is None:
        return []
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    if isinstance(tags, str):
        raw = tags.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except (json.JSONDecodeError, ValueError):
            pass
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(tags).strip()]


_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")

MEMORY_FILENAME_MAX_CHARS = 120
MEMORY_CONTENT_SNIPPET_RADIUS = 120


def safe_filename(value: str) -> str:
    """Return a filesystem-safe lowercase filename slug (max 120 chars)."""
    s = _SAFE_FILENAME_RE.sub("_", (value or "").strip().lower())
    s = s.strip("._-")
    return (s or "memory")[:MEMORY_FILENAME_MAX_CHARS]


def write_memory_markdown(
    title: str,
    content: str,
    tags: list[str] | str | None,
    *,
    conversations_root: Path | str,
    timestamp: str | None = None,
) -> Path:
    """Write a memory as a Markdown file and return its path.

    Raises ValueError if title or content is empty.
    File is created with mode 0o600; parent directory is chmod 0o700.
    """
    clean_title = (title or "").strip()
    clean_content = (content or "").strip()
    if not clean_title:
        raise ValueError("title cannot be empty")
    if not clean_content:
        raise ValueError("content cannot be empty")

    normalized_tags = normalize_tags(tags)
    root = Path(conversations_root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass

    safe_timestamp = (timestamp or "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / f"{safe_timestamp}_{safe_filename(clean_title)}.md"
    body = (
        f"# {clean_title}\n\n"
        f"Tags: {', '.join(normalized_tags)}\n"
        f"Date: {datetime.now().isoformat()}\n\n"
        f"{clean_content}\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path
