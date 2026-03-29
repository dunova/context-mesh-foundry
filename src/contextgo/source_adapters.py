#!/usr/bin/env python3
"""Extensible source discovery and adapter sync for ContextGO.

This module normalizes external tool storage layouts into adapter-owned JSONL
session mirrors under ``$CONTEXTGO_STORAGE_ROOT/raw/adapters`` so the rest of
ContextGO can index them uniformly.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from context_config import storage_root
except ImportError:  # pragma: no cover
    from .context_config import storage_root  # type: ignore[import-not-found]

__all__ = [
    "discover_index_sources",
    "source_freshness_snapshot",
    "source_inventory",
    "sync_all_adapters",
    "adapter_dirty_epoch",
]

ADAPTER_SCHEMA_VERSION = "2026-03-29-adapter-v1"


def _home() -> Path:
    return Path.home()


def _adapter_root(home: Path | None = None) -> Path:
    current_home = home or _home()
    digest = hashlib.sha256(str(current_home).encode("utf-8")).hexdigest()[:12]
    root = storage_root() / "raw" / "adapters" / digest
    root.mkdir(parents=True, exist_ok=True)
    _ensure_adapter_schema(root)
    return root


def _ensure_adapter_schema(root: Path) -> None:
    version_file = root / ".schema_version"
    current = ""
    with contextlib.suppress(OSError):
        current = version_file.read_text(encoding="utf-8").strip()
    if current == ADAPTER_SCHEMA_VERSION:
        return
    for child in root.iterdir():
        if child == version_file:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                child.unlink()
    version_file.write_text(ADAPTER_SCHEMA_VERSION, encoding="utf-8")


def _safe_name(raw: str, default: str = "session") -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip()).strip("._-")
    return clean[:80] if clean else default


def _write_adapter_file(path: Path, texts: list[str], mtime_epoch: int, meta: dict[str, object] | None = None) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: list[dict[str, object]] = []
    if meta:
        payload.append({k: v for k, v in meta.items() if v not in (None, "")})
    payload.extend({"text": text} for text in texts if isinstance(text, str) and text.strip())
    if not payload:
        return False
    rendered = "\n".join(json.dumps(item, ensure_ascii=False) for item in payload)
    changed = True
    with contextlib.suppress(OSError):
        if path.read_text(encoding="utf-8") == rendered:
            changed = False
    if changed:
        path.write_text(rendered, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
        os.utime(path, (mtime_epoch, mtime_epoch))
    return changed


def _prune_stale(adapter_dir: Path, keep: set[Path]) -> int:
    removed = 0
    if not adapter_dir.is_dir():
        return removed
    for path in adapter_dir.glob("*.jsonl"):
        if path not in keep:
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
    return removed


def _dirty_marker(home: Path | None = None) -> Path:
    return _adapter_root(home) / ".last_change"


def _mark_dirty(home: Path | None = None) -> None:
    marker = _dirty_marker(home)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now().isoformat(), encoding="utf-8")


def adapter_dirty_epoch(home: Path | None = None) -> int:
    marker = _dirty_marker(home)
    with contextlib.suppress(OSError):
        return int(marker.stat().st_mtime)
    return 0


def _resolve_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _iso_or_none(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch).isoformat()


def _normalize_text_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(text)
            if isinstance(decoded, str) and decoded.strip():
                text = decoded.strip()
    return text


def _extract_text_fragments(value: Any) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()

    def add(text: str | None) -> None:
        if not text:
            return
        if text in seen:
            return
        seen.add(text)
        texts.append(text)

    def walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, str):
            add(_normalize_text_value(node))
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        node_type = str(node.get("type") or "").strip().lower()
        if node_type in {"text", "input_text", "output_text", "reasoning"}:
            add(_normalize_text_value(node.get("text")))
        for key in ("text", "input", "prompt", "display", "message", "body", "summary", "title"):
            add(_normalize_text_value(node.get(key)))
        for key in ("content", "parts", "messages", "items", "payload", "data", "state", "response"):
            if key in node:
                walk(node[key])

    walk(value)
    return texts


def _opencode_db_candidates(home: Path) -> list[Path]:
    return [
        home / ".local" / "share" / "opencode" / "opencode.db",
        home / "Library" / "Application Support" / "ai.opencode.desktop" / "opencode.db",
    ]


def _kilo_storage_candidates(home: Path) -> list[Path]:
    return [
        home / ".local" / "share" / "kilo" / "storage",
        home / "Library" / "Application Support" / "ai.kilo.desktop" / "storage",
    ]


def _openclaw_session_candidates(home: Path) -> list[Path]:
    matches: list[Path] = []
    patterns = [
        home / ".openclaw" / "agents",
        home / ".local" / "share" / "openclaw" / "agents",
        home / "Library" / "Application Support" / "openclaw" / "agents",
    ]
    for root in patterns:
        if not root.is_dir():
            continue
        matches.extend(root.glob("*/sessions/*.jsonl"))
    return sorted(matches)


def _sync_opencode_sessions(home: Path) -> dict[str, object]:
    db_path = _resolve_existing(_opencode_db_candidates(home))
    adapter_dir = _adapter_root(home) / "opencode_session"
    keep: set[Path] = set()
    if db_path is None:
        removed = _prune_stale(adapter_dir, keep)
        return {"detected": False, "sessions": 0, "removed": removed, "path": None}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sessions_written = 0
    changed = False
    try:
        sessions = conn.execute(
            "SELECT id, title, directory, time_created, time_updated FROM session ORDER BY time_updated DESC"
        ).fetchall()
        for row in sessions:
            sid = str(row["id"])
            title = str(row["title"] or sid)
            directory = str(row["directory"] or "")
            updated_sec = max(1, int((row["time_updated"] or row["time_created"] or 0) / 1000))
            texts: list[str] = []
            if title.strip():
                texts.append(f"[title] {title.strip()}")
            if directory.strip():
                texts.append(f"[directory] {directory.strip()}")

            parts = conn.execute(
                "SELECT data FROM part WHERE session_id = ? ORDER BY time_created ASC, id ASC",
                (sid,),
            ).fetchall()
            for part in parts:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    texts.extend(_extract_text_fragments(json.loads(part["data"])))

            if len(texts) <= 2:
                messages = conn.execute(
                    "SELECT data FROM message WHERE session_id = ? ORDER BY time_created ASC, id ASC",
                    (sid,),
                ).fetchall()
                for msg in messages:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        texts.extend(_extract_text_fragments(json.loads(msg["data"])))

            out_path = adapter_dir / f"{_safe_name(sid)}__{_safe_name(title, 'session')}.jsonl"
            out_changed = _write_adapter_file(
                out_path,
                texts,
                updated_sec,
                meta={"session_id": sid, "title": title, "directory": directory, "source_type": "opencode_session"},
            )
            if out_path.exists():
                keep.add(out_path)
                sessions_written += 1
            if out_changed:
                changed = True
        removed = _prune_stale(adapter_dir, keep)
        if changed or removed:
            _mark_dirty(home)
        return {"detected": True, "sessions": sessions_written, "removed": removed, "path": str(db_path)}
    finally:
        conn.close()


def _sync_kilo_sessions(home: Path) -> dict[str, object]:
    storage_root_path = _resolve_existing(_kilo_storage_candidates(home))
    adapter_dir = _adapter_root(home) / "kilo_session"
    keep: set[Path] = set()
    if storage_root_path is None:
        removed = _prune_stale(adapter_dir, keep)
        return {"detected": False, "sessions": 0, "removed": removed, "path": None}

    session_meta: dict[str, dict[str, object]] = {}
    for session_file in sorted((storage_root_path / "session").rglob("*.json")):
        with contextlib.suppress(OSError, json.JSONDecodeError):
            data = json.loads(session_file.read_text(encoding="utf-8"))
            sid = str(data.get("id") or "").strip()
            if sid:
                session_meta[sid] = {
                    "title": str(data.get("title") or sid),
                    "directory": str(data.get("directory") or ""),
                    "updated": int(((data.get("time") or {}).get("updated") or 0) / 1000),
                }

    parts_by_session: dict[str, list[str]] = defaultdict(list)
    for part_file in sorted((storage_root_path / "part").rglob("*.json")):
        with contextlib.suppress(OSError, json.JSONDecodeError):
            data = json.loads(part_file.read_text(encoding="utf-8"))
            sid = str(data.get("sessionID") or data.get("sessionId") or "").strip()
            text = _normalize_text_value(data.get("text"))
            if sid and text:
                parts_by_session[sid].append(text)

    all_session_ids = sorted(set(session_meta) | set(parts_by_session))
    sessions_written = 0
    changed = False
    for sid in all_session_ids:
        meta = session_meta.get(sid, {})
        title = str(meta.get("title") or sid)
        directory = str(meta.get("directory") or "")
        updated_sec = max(1, int(meta.get("updated") or 1))
        texts: list[str] = []
        if title.strip():
            texts.append(f"[title] {title.strip()}")
        if directory.strip():
            texts.append(f"[directory] {directory.strip()}")
        texts.extend(parts_by_session.get(sid, []))
        out_path = adapter_dir / f"{_safe_name(sid)}__{_safe_name(title, 'session')}.jsonl"
        out_changed = _write_adapter_file(
            out_path,
            texts,
            updated_sec,
            meta={"session_id": sid, "title": title, "directory": directory, "source_type": "kilo_session"},
        )
        if out_path.exists():
            keep.add(out_path)
            sessions_written += 1
        if out_changed:
            changed = True

    removed = _prune_stale(adapter_dir, keep)
    if changed or removed:
        _mark_dirty(home)
    return {"detected": True, "sessions": sessions_written, "removed": removed, "path": str(storage_root_path)}


def _sync_openclaw_sessions(home: Path) -> dict[str, object]:
    session_files = _openclaw_session_candidates(home)
    adapter_dir = _adapter_root(home) / "openclaw_session"
    keep: set[Path] = set()
    sessions_written = 0
    changed = False
    for source_file in session_files:
        texts: list[str] = []
        with contextlib.suppress(OSError, UnicodeDecodeError):
            for line in source_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    texts.extend(_extract_text_fragments(json.loads(raw)))
        out_path = adapter_dir / f"{_safe_name(source_file.stem)}__{_safe_name(source_file.stem, 'session')}.jsonl"
        mtime = max(1, int(source_file.stat().st_mtime))
        out_changed = _write_adapter_file(
            out_path,
            texts,
            mtime,
            meta={"session_id": source_file.stem, "title": source_file.stem, "source_type": "openclaw_session"},
        )
        if out_path.exists():
            keep.add(out_path)
            sessions_written += 1
        if out_changed:
            changed = True
    removed = _prune_stale(adapter_dir, keep)
    if changed or removed:
        _mark_dirty(home)
    return {
        "detected": bool(session_files),
        "sessions": sessions_written,
        "removed": removed,
        "path": str(session_files[0].parent) if session_files else None,
    }


def sync_all_adapters(home: Path | None = None) -> dict[str, dict[str, object]]:
    current_home = home or _home()
    return {
        "opencode_session": _sync_opencode_sessions(current_home),
        "kilo_session": _sync_kilo_sessions(current_home),
        "openclaw_session": _sync_openclaw_sessions(current_home),
    }


def discover_index_sources(home: Path | None = None) -> list[tuple[str, Path]]:
    current_home = home or _home()
    sync_all_adapters(current_home)
    discovered: list[tuple[str, Path]] = []

    for source_type, root in [
        ("codex_session", current_home / ".codex" / "sessions"),
        ("codex_session", current_home / ".codex" / "archived_sessions"),
        ("claude_session", current_home / ".claude" / "projects"),
        ("claude_session", current_home / ".claude" / "transcripts"),
    ]:
        if root.is_dir():
            for path in root.rglob("*.jsonl"):
                discovered.append((source_type, path))

    for source_type, path in [
        ("codex_history", current_home / ".codex" / "history.jsonl"),
        ("claude_history", current_home / ".claude" / "history.jsonl"),
        ("opencode_history", current_home / ".local" / "state" / "opencode" / "prompt-history.jsonl"),
        ("opencode_history", current_home / ".config" / "opencode" / "prompt-history.jsonl"),
        ("opencode_history", current_home / ".opencode" / "prompt-history.jsonl"),
        ("kilo_history", current_home / ".local" / "state" / "kilo" / "prompt-history.jsonl"),
        ("kilo_history", current_home / ".config" / "kilo" / "prompt-history.jsonl"),
        ("shell_zsh", current_home / ".zsh_history"),
        ("shell_bash", current_home / ".bash_history"),
    ]:
        if path.is_file():
            discovered.append((source_type, path))

    adapter_roots = [
        ("opencode_session", _adapter_root(current_home) / "opencode_session"),
        ("kilo_session", _adapter_root(current_home) / "kilo_session"),
        ("openclaw_session", _adapter_root(current_home) / "openclaw_session"),
    ]
    for source_type, root in adapter_roots:
        if root.is_dir():
            for path in root.glob("*.jsonl"):
                discovered.append((source_type, path))

    deduped: list[tuple[str, Path]] = []
    seen: set[tuple[str, str]] = set()
    for source_type, path in discovered:
        key = (source_type, str(path))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((source_type, path))
    return deduped


def source_freshness_snapshot(home: Path | None = None) -> dict[str, dict[str, object]]:
    current_home = home or _home()
    adapter_stats = sync_all_adapters(current_home)
    openclaw_sessions = _openclaw_session_candidates(current_home)
    antigravity_candidates = sorted(
        (current_home / ".gemini" / "antigravity" / "brain").glob("*/walkthrough.md"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )

    sources: dict[str, Path | None] = {
        "codex_history": _resolve_existing([current_home / ".codex" / "history.jsonl"]),
        "claude_history": _resolve_existing([current_home / ".claude" / "history.jsonl"]),
        "opencode_history": _resolve_existing(
            [
                current_home / ".local" / "state" / "opencode" / "prompt-history.jsonl",
                current_home / ".config" / "opencode" / "prompt-history.jsonl",
                current_home / ".opencode" / "prompt-history.jsonl",
            ]
        ),
        "kilo_history": _resolve_existing(
            [
                current_home / ".local" / "state" / "kilo" / "prompt-history.jsonl",
                current_home / ".config" / "kilo" / "prompt-history.jsonl",
            ]
        ),
        "opencode_db": _resolve_existing(_opencode_db_candidates(current_home)),
        "kilo_storage": _resolve_existing(_kilo_storage_candidates(current_home)),
        "openclaw_sessions_root": openclaw_sessions[0].parent if openclaw_sessions else None,
        "shell_zsh": _resolve_existing([current_home / ".zsh_history"]),
        "shell_bash": _resolve_existing([current_home / ".bash_history"]),
        "antigravity_latest": antigravity_candidates[0] if antigravity_candidates else None,
    }

    result: dict[str, dict[str, object]] = {}
    for name, path in sources.items():
        if path is None:
            result[name] = {"exists": False}
            continue
        p = Path(path)
        result[name] = {
            "exists": p.exists(),
            "path": str(p),
            "mtime": _iso_or_none(p.stat().st_mtime) if p.exists() else None,
        }
    result["adapter_sessions"] = {
        "exists": any(
            int(adapter_stats[name]["sessions"]) > 0
            for name in ("opencode_session", "kilo_session", "openclaw_session")
        ),
        "path": str(_adapter_root(current_home)),
        "opencode_session_count": adapter_stats["opencode_session"]["sessions"],
        "kilo_session_count": adapter_stats["kilo_session"]["sessions"],
        "openclaw_session_count": adapter_stats["openclaw_session"]["sessions"],
    }
    return result


def source_inventory(home: Path | None = None) -> dict[str, object]:
    current_home = home or _home()
    adapter_stats = sync_all_adapters(current_home)
    discovered = discover_index_sources(current_home)
    by_type: dict[str, list[str]] = defaultdict(list)
    for source_type, path in discovered:
        by_type[source_type].append(str(path))

    platforms = [
        {
            "platform": "codex",
            "detected": bool(by_type.get("codex_session") or by_type.get("codex_history")),
            "session_files": len(by_type.get("codex_session", [])),
            "history_files": len(by_type.get("codex_history", [])),
        },
        {
            "platform": "claude_code",
            "detected": bool(by_type.get("claude_session") or by_type.get("claude_history")),
            "session_files": len(by_type.get("claude_session", [])),
            "history_files": len(by_type.get("claude_history", [])),
        },
        {
            "platform": "opencode",
            "detected": bool(by_type.get("opencode_session") or by_type.get("opencode_history")),
            "session_files": len(by_type.get("opencode_session", [])),
            "history_files": len(by_type.get("opencode_history", [])),
            "adapter": adapter_stats["opencode_session"],
        },
        {
            "platform": "kilo",
            "detected": bool(by_type.get("kilo_session") or by_type.get("kilo_history")),
            "session_files": len(by_type.get("kilo_session", [])),
            "history_files": len(by_type.get("kilo_history", [])),
            "adapter": adapter_stats["kilo_session"],
        },
        {
            "platform": "openclaw",
            "detected": bool(by_type.get("openclaw_session")),
            "session_files": len(by_type.get("openclaw_session", [])),
            "history_files": 0,
            "adapter": adapter_stats["openclaw_session"],
        },
        {
            "platform": "shell",
            "detected": bool(by_type.get("shell_zsh") or by_type.get("shell_bash")),
            "session_files": 0,
            "history_files": len(by_type.get("shell_zsh", [])) + len(by_type.get("shell_bash", [])),
        },
    ]
    return {
        "adapter_root": str(_adapter_root(current_home)),
        "platforms": platforms,
        "discovered_sources": {key: len(value) for key, value in sorted(by_type.items())},
    }
