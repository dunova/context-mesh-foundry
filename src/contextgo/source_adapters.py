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
import logging
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

try:
    from context_config import storage_root
except ImportError:  # pragma: no cover
    from .context_config import storage_root

__all__ = [
    "discover_index_sources",
    "source_freshness_snapshot",
    "source_inventory",
    "sync_all_adapters",
    "adapter_dirty_epoch",
]

ADAPTER_SCHEMA_VERSION = "2026-03-31-adapter-v2"


def _home() -> Path:
    return Path.home()


def _adapter_root(home: Path | None = None) -> Path:
    current_home = home or _home()
    digest = hashlib.sha256(str(current_home).encode("utf-8")).hexdigest()[:12]
    root = Path(storage_root()) / "raw" / "adapters" / digest
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_text(rendered, encoding="utf-8")
            with contextlib.suppress(OSError):
                tmp_path.chmod(0o600)
            os.replace(str(tmp_path), str(path))
        except OSError:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise
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
    marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


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


# ---------------------------------------------------------------------------
# Path candidate helpers
# ---------------------------------------------------------------------------


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


def _cline_family_task_roots(home: Path, extension_id: str) -> list[Path]:
    """Return existing task root directories for a Cline-family VS Code extension."""
    candidates = [
        home / "Library" / "Application Support" / "Code" / "User" / "globalStorage" / extension_id / "tasks",
        home / ".config" / "Code" / "User" / "globalStorage" / extension_id / "tasks",
        home / ".vscode-server" / "data" / "User" / "globalStorage" / extension_id / "tasks",
    ]
    return [p for p in candidates if p.is_dir()]


def _continue_session_roots(home: Path) -> list[Path]:
    """Return existing Continue.dev session directories."""
    candidates = [
        home / ".continue" / "sessions",
        home / "Library" / "Application Support" / "Continue" / "sessions",
    ]
    return [p for p in candidates if p.is_dir()]


def _zed_conversation_roots(home: Path) -> list[Path]:
    """Return existing Zed conversation directories."""
    candidates = [
        home / "Library" / "Application Support" / "Zed" / "conversations",
        home / ".config" / "zed" / "conversations",
    ]
    return [p for p in candidates if p.is_dir()]


def _aider_history_candidates(home: Path) -> list[Path]:
    """Find .aider.chat.history.md files in common project directories."""
    matches: list[Path] = []
    scan_roots = [home]
    for name in ("Projects", "projects", "code", "Code", "dev", "src", "repos", "work"):
        candidate = home / name
        if candidate.is_dir():
            scan_roots.append(candidate)
    for root in scan_roots:
        try:
            if root == home:
                for child in root.iterdir():
                    if child.is_dir() and not child.name.startswith("."):
                        hist = child / ".aider.chat.history.md"
                        if hist.is_file():
                            matches.append(hist)
            else:
                matches.extend(root.rglob(".aider.chat.history.md"))
        except OSError:
            continue
    return sorted(matches, key=lambda p: _safe_mtime(p), reverse=True)[:50]


def _vscdb_workspace_roots(home: Path, app_name: str) -> list[Path]:
    """Return existing workspaceStorage directories for a VS Code fork (Cursor, Windsurf)."""
    candidates = [
        home / "Library" / "Application Support" / app_name / "User" / "workspaceStorage",
        home / ".config" / app_name / "User" / "workspaceStorage",
    ]
    return [p for p in candidates if p.is_dir()]


def _safe_mtime(path: Path) -> float:
    """Return mtime or 0.0 on error."""
    with contextlib.suppress(OSError):
        return path.stat().st_mtime
    return 0.0


# ---------------------------------------------------------------------------
# Existing adapters (OpenCode, Kilo, OpenClaw)
# ---------------------------------------------------------------------------


def _sync_opencode_sessions(home: Path) -> dict[str, object]:
    db_path = _resolve_existing(_opencode_db_candidates(home))
    adapter_dir = _adapter_root(home) / "opencode_session"
    keep: set[Path] = set()
    if db_path is None:
        removed = _prune_stale(adapter_dir, keep)
        return {"detected": False, "sessions": 0, "removed": removed, "path": None}

    sessions_written = 0
    changed = False
    with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
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
        updated_sec = max(1, int(str(meta.get("updated") or 1)))
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
        try:
            mtime = max(1, int(source_file.stat().st_mtime))
        except OSError:
            # File was deleted between read_text and stat (TOCTOU); skip it.
            continue
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


# ---------------------------------------------------------------------------
# New adapters: Cline / Roo Code (VS Code extensions, shared format)
# ---------------------------------------------------------------------------


def _sync_cline_family_sessions(home: Path, extension_id: str, source_type: str) -> dict[str, object]:
    """Sync tasks from a Cline-family VS Code extension (Cline, Roo Code, etc.)."""
    task_roots = _cline_family_task_roots(home, extension_id)
    adapter_dir = _adapter_root(home) / source_type
    keep: set[Path] = set()
    sessions_written = 0
    changed = False
    detected = False

    for tasks_dir in task_roots:
        detected = True
        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            history_file = task_dir / "api_conversation_history.json"
            if not history_file.is_file():
                continue
            sid = task_dir.name
            texts: list[str] = []
            title = sid
            try:
                data = json.loads(history_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, list):
                continue
            for msg in data:
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    texts.append(content.strip())
                elif isinstance(content, list):
                    texts.extend(_extract_text_fragments(content))
            # Try to get title from metadata
            meta_file = task_dir / "task_metadata.json"
            with contextlib.suppress(OSError, json.JSONDecodeError):
                meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                if isinstance(meta_data, dict):
                    raw_title = meta_data.get("task") or meta_data.get("title") or ""
                    if isinstance(raw_title, str) and raw_title.strip():
                        title = raw_title.strip()[:200]
            if title.strip():
                texts.insert(0, f"[title] {title.strip()}")
            mtime = max(1, int(_safe_mtime(history_file)))
            out_path = adapter_dir / f"{_safe_name(sid)}__{_safe_name(title, 'task')}.jsonl"
            out_changed = _write_adapter_file(
                out_path, texts, mtime,
                meta={"session_id": sid, "title": title, "source_type": source_type},
            )
            if out_path.exists():
                keep.add(out_path)
                sessions_written += 1
            if out_changed:
                changed = True

    removed = _prune_stale(adapter_dir, keep)
    if changed or removed:
        _mark_dirty(home)
    return {"detected": detected, "sessions": sessions_written, "removed": removed, "path": str(task_roots[0]) if task_roots else None}


def _sync_cline_sessions(home: Path) -> dict[str, object]:
    return _sync_cline_family_sessions(home, "saoudrizwan.claude-dev", "cline_session")


def _sync_roo_sessions(home: Path) -> dict[str, object]:
    return _sync_cline_family_sessions(home, "rooveterinaryinc.roo-cline", "roo_session")


# ---------------------------------------------------------------------------
# New adapter: Continue.dev
# ---------------------------------------------------------------------------


def _sync_continue_sessions(home: Path) -> dict[str, object]:
    """Sync Continue.dev session JSON files."""
    session_roots = _continue_session_roots(home)
    adapter_dir = _adapter_root(home) / "continue_session"
    keep: set[Path] = set()
    sessions_written = 0
    changed = False
    detected = False

    for sessions_dir in session_roots:
        detected = True
        for session_file in sorted(sessions_dir.glob("*.json")):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            sid = str(data.get("sessionId") or data.get("id") or session_file.stem).strip()
            title = str(data.get("title") or sid)
            directory = str(data.get("workspaceDirectory") or "")
            texts: list[str] = []
            if title.strip():
                texts.append(f"[title] {title.strip()}")
            if directory.strip():
                texts.append(f"[directory] {directory.strip()}")
            history = data.get("history") or data.get("messages") or []
            if isinstance(history, list):
                for msg in history:
                    if isinstance(msg, dict):
                        texts.extend(_extract_text_fragments(msg))
            mtime = max(1, int(_safe_mtime(session_file)))
            out_path = adapter_dir / f"{_safe_name(sid)}__{_safe_name(title, 'session')}.jsonl"
            out_changed = _write_adapter_file(
                out_path, texts, mtime,
                meta={"session_id": sid, "title": title, "directory": directory, "source_type": "continue_session"},
            )
            if out_path.exists():
                keep.add(out_path)
                sessions_written += 1
            if out_changed:
                changed = True

    removed = _prune_stale(adapter_dir, keep)
    if changed or removed:
        _mark_dirty(home)
    return {"detected": detected, "sessions": sessions_written, "removed": removed, "path": str(session_roots[0]) if session_roots else None}


# ---------------------------------------------------------------------------
# New adapter: Zed
# ---------------------------------------------------------------------------


def _sync_zed_sessions(home: Path) -> dict[str, object]:
    """Sync Zed editor conversation JSON files."""
    conv_roots = _zed_conversation_roots(home)
    adapter_dir = _adapter_root(home) / "zed_session"
    keep: set[Path] = set()
    sessions_written = 0
    changed = False
    detected = False

    for conv_dir in conv_roots:
        detected = True
        for conv_file in sorted(conv_dir.glob("*.json")):
            try:
                data = json.loads(conv_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            sid = str(data.get("id") or conv_file.stem).strip()
            title = str(data.get("title") or data.get("summary") or sid)
            texts: list[str] = []
            if title.strip():
                texts.append(f"[title] {title.strip()}")
            messages = data.get("messages") or data.get("message_metadata") or []
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict):
                        texts.extend(_extract_text_fragments(msg))
            elif isinstance(messages, dict):
                texts.extend(_extract_text_fragments(messages))
            mtime = max(1, int(_safe_mtime(conv_file)))
            out_path = adapter_dir / f"{_safe_name(sid)}__{_safe_name(title, 'conversation')}.jsonl"
            out_changed = _write_adapter_file(
                out_path, texts, mtime,
                meta={"session_id": sid, "title": title, "source_type": "zed_session"},
            )
            if out_path.exists():
                keep.add(out_path)
                sessions_written += 1
            if out_changed:
                changed = True

    removed = _prune_stale(adapter_dir, keep)
    if changed or removed:
        _mark_dirty(home)
    return {"detected": detected, "sessions": sessions_written, "removed": removed, "path": str(conv_roots[0]) if conv_roots else None}


# ---------------------------------------------------------------------------
# New adapter: Aider
# ---------------------------------------------------------------------------


def _sync_aider_sessions(home: Path) -> dict[str, object]:
    """Sync Aider chat history Markdown files from project directories."""
    history_files = _aider_history_candidates(home)
    adapter_dir = _adapter_root(home) / "aider_session"
    keep: set[Path] = set()
    sessions_written = 0
    changed = False

    for hist_file in history_files:
        try:
            raw = hist_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Split by markdown headers (#### or ---) to get conversation turns
        chunks: list[str] = []
        current: list[str] = []
        for line in raw.splitlines():
            if line.startswith("####") or (line.strip() == "---" and current):
                if current:
                    chunks.append("\n".join(current).strip())
                    current = []
            else:
                current.append(line)
        if current:
            chunks.append("\n".join(current).strip())
        texts = [c for c in chunks if c and len(c) > 10]
        if not texts:
            continue
        project_dir = hist_file.parent
        sid = hashlib.sha256(str(hist_file).encode()).hexdigest()[:16]
        title = project_dir.name
        texts.insert(0, f"[title] aider: {title}")
        texts.insert(1, f"[directory] {project_dir}")
        mtime = max(1, int(_safe_mtime(hist_file)))
        out_path = adapter_dir / f"{_safe_name(sid)}__{_safe_name(title, 'aider')}.jsonl"
        out_changed = _write_adapter_file(
            out_path, texts, mtime,
            meta={"session_id": sid, "title": f"aider: {title}", "directory": str(project_dir), "source_type": "aider_session"},
        )
        if out_path.exists():
            keep.add(out_path)
            sessions_written += 1
        if out_changed:
            changed = True

    removed = _prune_stale(adapter_dir, keep)
    if changed or removed:
        _mark_dirty(home)
    return {"detected": bool(history_files), "sessions": sessions_written, "removed": removed, "path": str(history_files[0].parent) if history_files else None}


# ---------------------------------------------------------------------------
# New adapters: Cursor / Windsurf (VS Code forks with .vscdb)
# ---------------------------------------------------------------------------


def _sync_vscdb_sessions(home: Path, app_name: str, source_type: str) -> dict[str, object]:
    """Sync chat history from a VS Code fork's .vscdb workspaceStorage files."""
    ws_roots = _vscdb_workspace_roots(home, app_name)
    adapter_dir = _adapter_root(home) / source_type
    keep: set[Path] = set()
    sessions_written = 0
    changed = False
    detected = False

    _CHAT_KEY_PATTERNS = ("%chat%", "%conversation%", "%Cascade%", "%aiChat%")

    for ws_root in ws_roots:
        detected = True
        for ws_dir in sorted(ws_root.iterdir()):
            vscdb = ws_dir / "state.vscdb"
            if not vscdb.is_file():
                continue
            texts: list[str] = []
            workspace_name = ws_dir.name
            try:
                with contextlib.closing(sqlite3.connect(f"file:{vscdb}?mode=ro", uri=True, timeout=5)) as conn:
                    # Check if ItemTable exists
                    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                    if "ItemTable" not in tables:
                        continue
                    for pattern in _CHAT_KEY_PATTERNS:
                        rows = conn.execute("SELECT key, value FROM ItemTable WHERE key LIKE ?", (pattern,)).fetchall()
                        for _key, value in rows:
                            if not isinstance(value, str) or len(value) < 10:
                                continue
                            with contextlib.suppress(json.JSONDecodeError, TypeError):
                                texts.extend(_extract_text_fragments(json.loads(value)))
            except (sqlite3.Error, OSError) as exc:
                _logger.debug("_sync_vscdb_sessions: %s/%s: %s", app_name, workspace_name, exc)
                continue
            if not texts:
                continue
            sid = workspace_name
            title = f"{app_name}: {workspace_name}"
            texts.insert(0, f"[title] {title}")
            mtime = max(1, int(_safe_mtime(vscdb)))
            out_path = adapter_dir / f"{_safe_name(sid)}__{_safe_name(app_name, 'workspace')}.jsonl"
            out_changed = _write_adapter_file(
                out_path, texts, mtime,
                meta={"session_id": sid, "title": title, "source_type": source_type},
            )
            if out_path.exists():
                keep.add(out_path)
                sessions_written += 1
            if out_changed:
                changed = True

    removed = _prune_stale(adapter_dir, keep)
    if changed or removed:
        _mark_dirty(home)
    return {"detected": detected, "sessions": sessions_written, "removed": removed, "path": str(ws_roots[0]) if ws_roots else None}


def _sync_cursor_sessions(home: Path) -> dict[str, object]:
    return _sync_vscdb_sessions(home, "Cursor", "cursor_session")


def _sync_windsurf_sessions(home: Path) -> dict[str, object]:
    return _sync_vscdb_sessions(home, "Windsurf", "windsurf_session")


# ---------------------------------------------------------------------------
# Adapter orchestration
# ---------------------------------------------------------------------------


def sync_all_adapters(home: Path | None = None) -> dict[str, dict[str, object]]:
    current_home = home or _home()
    _adapters: dict[str, Any] = {
        "opencode_session": _sync_opencode_sessions,
        "kilo_session": _sync_kilo_sessions,
        "openclaw_session": _sync_openclaw_sessions,
        "cline_session": _sync_cline_sessions,
        "roo_session": _sync_roo_sessions,
        "continue_session": _sync_continue_sessions,
        "zed_session": _sync_zed_sessions,
        "aider_session": _sync_aider_sessions,
        "cursor_session": _sync_cursor_sessions,
        "windsurf_session": _sync_windsurf_sessions,
    }
    result: dict[str, dict[str, object]] = {}
    for name, fn in _adapters.items():
        try:
            result[name] = fn(current_home)
        except Exception as exc:
            _logger.warning("sync_all_adapters: adapter %r failed: %s", name, exc)
            result[name] = {"sessions": 0, "error": str(exc)}
    return result


_ALL_ADAPTER_TYPES = (
    "opencode_session",
    "kilo_session",
    "openclaw_session",
    "cline_session",
    "roo_session",
    "continue_session",
    "zed_session",
    "aider_session",
    "cursor_session",
    "windsurf_session",
)


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

    adapter_root = _adapter_root(current_home)
    for source_type in _ALL_ADAPTER_TYPES:
        root = adapter_root / source_type
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
        try:
            if path is None:
                result[name] = {"exists": False}
                continue
            p = Path(path)
            result[name] = {
                "exists": p.exists(),
                "path": str(p),
                "mtime": _iso_or_none(p.stat().st_mtime) if p.exists() else None,
            }
        except OSError as exc:
            _logger.warning("source_freshness_snapshot: source %r failed: %s", name, exc)
            result[name] = {"exists": False, "error": str(exc)}
    try:
        adapter_summary: dict[str, object] = {
            "exists": any(int(str(adapter_stats[name]["sessions"])) > 0 for name in _ALL_ADAPTER_TYPES),
            "path": str(_adapter_root(current_home)),
        }
        for name in _ALL_ADAPTER_TYPES:
            adapter_summary[f"{name}_count"] = adapter_stats[name]["sessions"]
        result["adapter_sessions"] = adapter_summary
    except (KeyError, TypeError, ValueError) as exc:
        _logger.warning("source_freshness_snapshot: adapter_sessions summary failed: %s", exc)
        result["adapter_sessions"] = {"exists": False, "error": str(exc)}
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
            "adapter": adapter_stats.get("opencode_session", {}),
        },
        {
            "platform": "kilo",
            "detected": bool(by_type.get("kilo_session") or by_type.get("kilo_history")),
            "session_files": len(by_type.get("kilo_session", [])),
            "history_files": len(by_type.get("kilo_history", [])),
            "adapter": adapter_stats.get("kilo_session", {}),
        },
        {
            "platform": "openclaw",
            "detected": bool(by_type.get("openclaw_session")),
            "session_files": len(by_type.get("openclaw_session", [])),
            "history_files": 0,
            "adapter": adapter_stats.get("openclaw_session", {}),
        },
        {
            "platform": "cline",
            "detected": bool(by_type.get("cline_session")),
            "session_files": len(by_type.get("cline_session", [])),
            "history_files": 0,
            "adapter": adapter_stats.get("cline_session", {}),
        },
        {
            "platform": "roo_code",
            "detected": bool(by_type.get("roo_session")),
            "session_files": len(by_type.get("roo_session", [])),
            "history_files": 0,
            "adapter": adapter_stats.get("roo_session", {}),
        },
        {
            "platform": "continue",
            "detected": bool(by_type.get("continue_session")),
            "session_files": len(by_type.get("continue_session", [])),
            "history_files": 0,
            "adapter": adapter_stats.get("continue_session", {}),
        },
        {
            "platform": "zed",
            "detected": bool(by_type.get("zed_session")),
            "session_files": len(by_type.get("zed_session", [])),
            "history_files": 0,
            "adapter": adapter_stats.get("zed_session", {}),
        },
        {
            "platform": "aider",
            "detected": bool(by_type.get("aider_session")),
            "session_files": len(by_type.get("aider_session", [])),
            "history_files": 0,
            "adapter": adapter_stats.get("aider_session", {}),
        },
        {
            "platform": "cursor",
            "detected": bool(by_type.get("cursor_session")),
            "session_files": len(by_type.get("cursor_session", [])),
            "history_files": 0,
            "adapter": adapter_stats.get("cursor_session", {}),
        },
        {
            "platform": "windsurf",
            "detected": bool(by_type.get("windsurf_session")),
            "session_files": len(by_type.get("windsurf_session", [])),
            "history_files": 0,
            "adapter": adapter_stats.get("windsurf_session", {}),
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
