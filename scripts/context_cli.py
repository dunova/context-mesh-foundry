#!/usr/bin/env python3
"""Lightweight MCP-free context CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


HOME = Path.home()
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
RECALL_CANDIDATES = [
    SKILL_DIR.parent / "recall" / "scripts" / "recall.py",
    HOME / ".agents" / "skills" / "recall" / "scripts" / "recall.py",
    HOME / ".codex" / "skills" / "recall" / "scripts" / "recall.py",
    HOME / ".claude" / "skills" / "recall" / "scripts" / "recall.py",
    HOME / "skills-repo" / "recall" / "scripts" / "recall.py",
]
LOCAL_STORAGE_ROOT = Path(
    os.environ.get(
        "UNIFIED_CONTEXT_STORAGE_ROOT",
        os.environ.get("OPENVIKING_STORAGE_ROOT", str(HOME / ".unified_context_data")),
    )
)
LOCAL_SHARED_ROOT = LOCAL_STORAGE_ROOT / "resources" / "shared"
LOCAL_CONVERSATIONS_ROOT = LOCAL_SHARED_ROOT / "conversations"
LOCAL_SCAN_MAX_FILES = max(50, int(os.environ.get("CONTEXT_CLI_LOCAL_SCAN_MAX_FILES", "300")))
LOCAL_SCAN_READ_BYTES = max(4096, int(os.environ.get("CONTEXT_CLI_LOCAL_SCAN_READ_BYTES", "120000")))
RECALL_TIMEOUT_SEC = max(3, int(os.environ.get("CONTEXT_CLI_RECALL_TIMEOUT_SEC", "20")))
ENABLE_OPENVIKING_HTTP = str(os.environ.get("CONTEXT_CLI_ENABLE_OPENVIKING_HTTP", "0")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
OPENVIKING_URL = os.environ.get("OPENVIKING_URL", "http://127.0.0.1:8090/api/v1")


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


def _resolve_recall_script() -> Path | None:
    for candidate in RECALL_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _run_recall(
    *,
    query: str | None = None,
    search_type: str = "all",
    limit: int = 10,
    literal: bool = False,
    health: bool = False,
) -> tuple[int, str, str]:
    recall_script = _resolve_recall_script()
    if not recall_script:
        return 1, "", "recall.py not found"

    cmd = [sys.executable, str(recall_script)]
    if health:
        cmd.append("--health")
    else:
        cmd.extend(
            [
                query or "",
                "--backend",
                "hybrid",
                "--type",
                search_type,
                "--limit",
                str(limit),
            ]
        )
        if literal:
            cmd.append("--no-regex")

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=RECALL_TIMEOUT_SEC,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _parse_health_payload(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return {}


def _iter_local_shared_files() -> list[Path]:
    if not LOCAL_SHARED_ROOT.is_dir():
        return []
    files: list[Path] = []
    for path in LOCAL_SHARED_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in {".md", ".txt", ".json", ".jsonl", ".log"}:
            continue
        files.append(path)
    files.sort(key=_safe_mtime, reverse=True)
    return files[:LOCAL_SCAN_MAX_FILES]


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _local_memory_matches(query: str, limit: int = 3) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []

    ql = q.lower()
    matches: list[dict] = []
    for path in _iter_local_shared_files():
        rel = path.relative_to(LOCAL_SHARED_ROOT).as_posix()
        matched_in = None
        snippet = ""
        if ql in rel.lower():
            matched_in = "path"
            snippet = rel
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:LOCAL_SCAN_READ_BYTES]
            except Exception:
                continue
            idx = text.lower().find(ql)
            if idx >= 0:
                matched_in = "content"
                start = max(0, idx - 120)
                end = min(len(text), idx + len(q) + 120)
                snippet = _compact_text(text[start:end])

        if matched_in:
            matches.append(
                {
                    "uri_hint": f"local://{rel}",
                    "file_path": str(path),
                    "matched_in": matched_in,
                    "mtime": datetime.fromtimestamp(_safe_mtime(path)).isoformat(),
                    "snippet": snippet,
                }
            )
        if len(matches) >= limit:
            break
    return matches


def _save_local_memory(title: str, content: str, tags: list[str]) -> str:
    title = (title or "").strip()
    content = (content or "").strip()
    if not title:
        return "Failed to save memory: title cannot be empty."
    if not content:
        return "Failed to save memory: content cannot be empty."

    LOCAL_CONVERSATIONS_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(LOCAL_CONVERSATIONS_ROOT, 0o700)
    except OSError:
        pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = re.sub(r"[^a-zA-Z0-9._-]+", "_", title.lower()).strip("._-") or "memory"
    filename = filename[:120]
    path = LOCAL_CONVERSATIONS_ROOT / f"{timestamp}_{filename}.md"
    body = f"# {title}\n\nTags: {', '.join(tags)}\nDate: {datetime.now().isoformat()}\n\n{content}\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)

    if ENABLE_OPENVIKING_HTTP:
        try:
            import urllib.request

            payload = json.dumps(
                {
                    "path": str(path),
                    "target": "viking://resources/shared/conversations",
                    "reason": "save_conversation",
                    "instruction": f"Index global conversation memory: {title}",
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{OPENVIKING_URL}/resources",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
            return f"Saved locally and indexed remotely: {path}"
        except Exception as exc:
            return f"Saved locally: {path} (remote indexing skipped: {exc})"

    return f"Saved locally: {path}"


def _source_freshness() -> dict:
    antigravity_candidates = sorted(
        (HOME / ".gemini" / "antigravity" / "brain").glob("*/walkthrough.md"),
        key=_safe_mtime,
        reverse=True,
    )
    sources = {
        "codex_history": HOME / ".codex" / "history.jsonl",
        "claude_history": HOME / ".claude" / "history.jsonl",
        "opencode_history": HOME / ".local" / "state" / "opencode" / "prompt-history.jsonl",
        "shell_zsh": HOME / ".zsh_history",
        "antigravity_latest": antigravity_candidates[0] if antigravity_candidates else None,
    }
    payload: dict[str, dict] = {}
    for name, path in sources.items():
        if not path:
            payload[name] = {"exists": False}
            continue
        p = Path(path)
        payload[name] = {
            "exists": p.exists(),
            "path": str(p),
            "mtime": datetime.fromtimestamp(_safe_mtime(p)).isoformat() if p.exists() else None,
        }
    return payload


def _openviking_process_count() -> int:
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "openviking_mcp.py"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return 0
    return len([line for line in (proc.stdout or "").splitlines() if line.strip()])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI-first unified context entrypoint.")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Search session/history context")
    search.add_argument("query", help="Query text")
    search.add_argument("--type", default="all", choices=["all", "event", "session", "turn", "content"])
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--literal", action="store_true")

    semantic = sub.add_parser("semantic", help="Search local memories, then fallback to history content")
    semantic.add_argument("query", help="Query text")
    semantic.add_argument("--limit", type=int, default=5)

    save = sub.add_parser("save", help="Save key conclusion to local memory")
    save.add_argument("--title", required=True)
    save.add_argument("--content", required=True)
    save.add_argument("--tags", default="")

    sub.add_parser("health", help="Check context system health")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "search":
        rc, out, err = _run_recall(
            query=args.query,
            search_type=args.type,
            limit=args.limit,
            literal=bool(args.literal),
        )
        text = out.strip() or err.strip()
        print(text)
        return 0 if rc == 0 else 1

    if args.command == "semantic":
        matches = _local_memory_matches(args.query, limit=args.limit)
        if matches:
            print("--- LOCAL MEMORY MATCHES ---")
            for item in matches:
                print(json.dumps(item, ensure_ascii=False, indent=2))
            return 0
        rc, out, err = _run_recall(
            query=args.query,
            search_type="content",
            limit=min(args.limit, 10),
            literal=True,
        )
        text = out.strip() or err.strip()
        if text:
            print("--- HISTORY CONTENT FALLBACK ---")
            print(text)
        return 0 if rc == 0 else 1

    if args.command == "save":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        message = _save_local_memory(args.title, args.content, tags)
        print(message)
        return 0 if not message.startswith("Failed to save memory:") else 1

    if args.command == "health":
        rc, out, err = _run_recall(health=True)
        recall_payload = _parse_health_payload(out or err)
        payload = {
            "checked_at": datetime.now().isoformat(),
            "recall_lite": {
                "ok": bool(recall_payload.get("recall_db_exists")),
                "sessions": recall_payload.get("total_sessions"),
                "messages": recall_payload.get("total_messages"),
                "indexed_this_run": recall_payload.get("indexed_this_run"),
                "db": recall_payload.get("recall_db"),
            },
            "source_freshness": _source_freshness(),
            "local_memory_root": {
                "exists": LOCAL_SHARED_ROOT.exists(),
                "path": str(LOCAL_SHARED_ROOT),
            },
            "openviking_policy": {
                "enabled": ENABLE_OPENVIKING_HTTP,
                "mode": "optional-http" if ENABLE_OPENVIKING_HTTP else "disabled-by-policy",
                "legacy_mcp_processes": _openviking_process_count(),
            },
            "all_ok": rc == 0 and bool(recall_payload.get("recall_db_exists")),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["all_ok"] else 1

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
