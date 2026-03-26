#!/usr/bin/env python3
"""Shared runtime helpers for ContextGO.

Public surface
--------------
safe_mtime              -- mtime as float, 0.0 on error
iter_shared_files       -- newest-first list of text files under a root
local_memory_matches    -- keyword search across shared memory files
normalize_tags          -- normalise tags from list / CSV / JSON string
safe_filename           -- filesystem-safe slug
write_memory_markdown   -- write a memory entry as a Markdown file
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: File suffixes treated as readable text during scans.
TEXT_FILE_SUFFIXES: frozenset[str] = frozenset({".md", ".txt", ".json", ".jsonl", ".log"})

#: Maximum length for slugified filenames.
_FILENAME_MAX_CHARS: int = 120

#: Context radius (characters) shown either side of a search match.
_SNIPPET_RADIUS: int = 120

# ---------------------------------------------------------------------------
# Internal compiled patterns
# ---------------------------------------------------------------------------

_WHITESPACE_RE: re.Pattern[str] = re.compile(r"\s+")
_SAFE_FILENAME_RE: re.Pattern[str] = re.compile(r"[^a-zA-Z0-9._-]+")

# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def safe_mtime(path: Path | str) -> float:
    """Return the mtime of *path* as a float, or ``0.0`` on any OS error."""
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def iter_shared_files(shared_root: Path | str, max_files: int) -> list[Path]:
    """Return up to *max_files* text files beneath *shared_root*, newest first.

    Hidden files (names starting with ``.``) are excluded.  Returns an empty
    list when *shared_root* does not exist or is not a directory.
    """
    root = Path(shared_root)
    if not root.is_dir():
        return []

    files: list[Path] = [
        p
        for p in root.rglob("*")
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in TEXT_FILE_SUFFIXES
    ]
    files.sort(key=safe_mtime, reverse=True)
    return files[: max(1, int(max_files))]


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------


def compact_text(text: str) -> str:
    """Collapse all whitespace runs to a single space and strip leading/trailing whitespace."""
    return _WHITESPACE_RE.sub(" ", text or "").strip()


# ---------------------------------------------------------------------------
# Memory search
# ---------------------------------------------------------------------------


def local_memory_matches(
    query: str,
    *,
    shared_root: Path | str,
    limit: int = 3,
    max_files: int = 300,
    read_bytes: int = 120_000,
    uri_prefix: str = "local://",
    files: Iterable[Path | str] | None = None,
) -> list[dict[str, Any]]:
    """Search *shared_root* for files whose path or content contains *query*.

    Parameters
    ----------
    query:
        Case-insensitive search term.
    shared_root:
        Directory to scan (ignored when *files* is provided).
    limit:
        Maximum number of results to return.
    max_files:
        Maximum number of files to consider during a scan.
    read_bytes:
        Maximum bytes read per file for content matching.
    uri_prefix:
        Prefix prepended to relative paths in the ``uri_hint`` field.
    files:
        Optional explicit file list; skips the directory scan when supplied.

    Returns
    -------
    list[dict[str, Any]]
        Each entry contains ``uri_hint``, ``file_path``, ``matched_in``,
        ``mtime`` (ISO-8601), and ``snippet``.
    """
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
    prefix = (uri_prefix or "").strip()
    matches: list[dict[str, Any]] = []

    for path in search_files:
        matched_in: str | None = None
        snippet: str = ""

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
                start = max(0, idx - _SNIPPET_RADIUS)
                end = min(len(text), idx + len(q) + _SNIPPET_RADIUS)
                snippet = compact_text(text[start:end])

        if matched_in:
            matches.append(
                {
                    "uri_hint": f"{prefix}{rel_path}" if prefix else rel_path,
                    "file_path": str(path),
                    "matched_in": matched_in,
                    "mtime": datetime.fromtimestamp(safe_mtime(path)).isoformat(),
                    "snippet": snippet,
                }
            )
        if len(matches) >= cap:
            break

    return matches


# ---------------------------------------------------------------------------
# Tag normalisation
# ---------------------------------------------------------------------------


def normalize_tags(tags: list[str] | str | None) -> list[str]:
    """Normalise *tags* from a list, a comma-separated string, or a JSON array string.

    Returns a deduplicated list of stripped, non-empty strings preserving
    original order.  ``None`` and empty inputs return an empty list.
    """
    if tags is None:
        return []
    raw_items: list[str]
    if isinstance(tags, list):
        raw_items = [str(t).strip() for t in tags]
    elif isinstance(tags, str):
        s = tags.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            raw_items = [str(t).strip() for t in parsed] if isinstance(parsed, list) else [s]
        except (json.JSONDecodeError, ValueError):
            raw_items = [part.strip() for part in s.split(",")]
    else:
        raw_items = [str(tags).strip()]

    seen: set[str] = set()
    result: list[str] = []
    for item in raw_items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Memory file writing
# ---------------------------------------------------------------------------


def safe_filename(value: str) -> str:
    """Return a filesystem-safe lowercase slug of *value* (max ``_FILENAME_MAX_CHARS`` chars).

    Characters outside ``[a-zA-Z0-9._-]`` are replaced with underscores.
    Leading/trailing punctuation is stripped.  Falls back to ``"memory"``
    when the result would otherwise be empty.
    """
    s = _SAFE_FILENAME_RE.sub("_", (value or "").strip().lower()).strip("._-")
    return (s or "memory")[:_FILENAME_MAX_CHARS]


def write_memory_markdown(
    title: str,
    content: str,
    tags: list[str] | str | None,
    *,
    conversations_root: Path | str,
    timestamp: str | None = None,
) -> Path:
    """Write a memory entry as a Markdown file and return its path.

    The parent directory is created with mode ``0o700`` if it does not exist.
    The file itself is written with mode ``0o600``.

    Parameters
    ----------
    title:
        Human-readable title; must be non-empty.
    content:
        Body text; must be non-empty.
    tags:
        Optional tags accepted as a list, CSV string, or JSON array string.
    conversations_root:
        Directory in which the file is written.
    timestamp:
        Optional ``YYYYMMDD_HHMMSS`` prefix for the filename.  Defaults to
        the current local time when omitted.

    Returns
    -------
    Path
        Absolute path to the written file.

    Raises
    ------
    ValueError
        If *title* or *content* is empty after stripping whitespace.
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
    with contextlib.suppress(OSError):
        os.chmod(root, 0o700)

    safe_ts = (timestamp or "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / f"{safe_ts}_{safe_filename(clean_title)}.md"

    body = (
        f"# {clean_title}\n\n"
        f"Tags: {', '.join(normalized_tags)}\n"
        f"Date: {datetime.now().isoformat()}\n\n"
        f"{clean_content}\n"
    )

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)

    return path
