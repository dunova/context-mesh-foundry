#!/usr/bin/env python3
"""Shared runtime helpers for Context Mesh Foundry."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


TEXT_FILE_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".log"}


def safe_mtime(path: Path | str) -> float:
    try:
        return Path(path).stat().st_mtime
    except Exception:
        return 0.0


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def iter_shared_files(shared_root: Path | str, max_files: int) -> list[Path]:
    root = Path(shared_root)
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
            continue
        files.append(path)
    files.sort(key=safe_mtime, reverse=True)
    return files[: max(1, int(max_files))]


def _build_uri_hint(rel_path: str, uri_prefix: str) -> str:
    prefix = (uri_prefix or "").strip()
    if not prefix:
        return rel_path
    if prefix.endswith("/"):
        return f"{prefix}{rel_path}"
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
    q = (query or "").strip()
    if not q:
        return []

    root = Path(shared_root)
    search_files = [Path(p) for p in files] if files is not None else iter_shared_files(root, max_files=max_files)
    ql = q.lower()
    matches: list[dict[str, Any]] = []

    for path in search_files:
        matched_in = None
        snippet = ""
        try:
            rel_path = path.relative_to(root).as_posix()
        except Exception:
            rel_path = path.name
        if ql in rel_path.lower():
            matched_in = "path"
            snippet = rel_path
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[: max(4096, int(read_bytes))]
            except Exception:
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
        if len(matches) >= max(1, int(limit)):
            break
    return matches


def normalize_tags(tags: list[str] | str | None) -> list[str]:
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
        except Exception:
            pass
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(tags).strip()]


def safe_filename(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip().lower())
    s = s.strip("._-")
    return (s or "memory")[:120]


def write_memory_markdown(
    title: str,
    content: str,
    tags: list[str] | str | None,
    *,
    conversations_root: Path | str,
    timestamp: str | None = None,
) -> Path:
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
