#!/usr/bin/env python3
"""Lightweight MCP-free context CLI."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from datetime import datetime
from pathlib import Path

from context_config import env_bool, env_int, env_str, storage_root
import context_core
from memory_index import export_observations_payload, import_observations_payload
import session_index


HOME = Path.home()
LOCAL_STORAGE_ROOT = Path(
    storage_root()
)
LOCAL_SHARED_ROOT = LOCAL_STORAGE_ROOT / "resources" / "shared"
LOCAL_CONVERSATIONS_ROOT = LOCAL_SHARED_ROOT / "conversations"
LOCAL_SCAN_MAX_FILES = env_int("CONTEXT_CLI_LOCAL_SCAN_MAX_FILES", "CONTEXT_MESH_LOCAL_SCAN_MAX_FILES", default=300, minimum=50)
LOCAL_SCAN_READ_BYTES = env_int("CONTEXT_CLI_LOCAL_SCAN_READ_BYTES", "CONTEXT_MESH_LOCAL_SCAN_READ_BYTES", default=120000, minimum=4096)
ENABLE_OPENVIKING_HTTP = env_bool("CONTEXT_MESH_ENABLE_REMOTE_MEMORY_HTTP", "CONTEXT_CLI_ENABLE_OPENVIKING_HTTP", default=False)
OPENVIKING_URL = env_str("CONTEXT_MESH_REMOTE_URL", "OPENVIKING_URL", default="http://127.0.0.1:8090/api/v1")


def _safe_mtime(path: Path) -> float:
    return context_core.safe_mtime(path)


def _iter_local_shared_files() -> list[Path]:
    return context_core.iter_shared_files(LOCAL_SHARED_ROOT, LOCAL_SCAN_MAX_FILES)


def _compact_text(text: str) -> str:
    return context_core.compact_text(text)


def _local_memory_matches(query: str, limit: int = 3) -> list[dict]:
    return context_core.local_memory_matches(
        query,
        shared_root=LOCAL_SHARED_ROOT,
        limit=limit,
        max_files=LOCAL_SCAN_MAX_FILES,
        read_bytes=LOCAL_SCAN_READ_BYTES,
        uri_prefix="local://",
    )


def _save_local_memory(title: str, content: str, tags: list[str]) -> str:
    try:
        path = context_core.write_memory_markdown(
            title,
            content,
            tags,
            conversations_root=LOCAL_CONVERSATIONS_ROOT,
        )
    except ValueError as exc:
        return f"Failed to save memory: {exc}."

    if ENABLE_OPENVIKING_HTTP:
        try:
            import urllib.request

            payload = json.dumps(
                {
                    "path": str(path),
                    "target": "viking://resources/shared/conversations",
                    "reason": "save_conversation",
                    "instruction": f"Index global conversation memory: {(title or '').strip()}",
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


def _load_memory_viewer():
    return importlib.import_module("context_server")


def _load_context_maintenance():
    return importlib.import_module("context_maintenance")


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


def _legacy_bridge_process_count() -> int:
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

    export = sub.add_parser("export", help="Export indexed observations to JSON")
    export.add_argument("query", nargs="?", default="", help="Search query, empty for all")
    export.add_argument("output", help="Output JSON path")
    export.add_argument("--limit", type=int, default=5000)
    export.add_argument("--source-type", default="all", choices=["all", "history", "conversation"])

    import_cmd = sub.add_parser("import", help="Import observations from JSON")
    import_cmd.add_argument("input", help="Input JSON path")
    import_cmd.add_argument("--no-sync", action="store_true")

    serve = sub.add_parser("serve", help="Start local memory viewer")
    serve.add_argument("--host", default=env_str("CONTEXT_VIEWER_HOST", "CONTEXT_MESH_VIEWER_HOST", default="127.0.0.1"))
    serve.add_argument("--port", type=int, default=env_int("CONTEXT_VIEWER_PORT", "CONTEXT_MESH_VIEWER_PORT", default=37677, minimum=1))
    serve.add_argument("--token", default=env_str("CONTEXT_VIEWER_TOKEN", "CONTEXT_MESH_VIEWER_TOKEN", default=""))

    maintain = sub.add_parser("maintain", aliases=["onecontext-maintain"], help="Run local maintenance workflow")
    maintain.add_argument("--db", default="~/.aline/db/aline.db")
    maintain.add_argument("--codex-root", default="~/.codex/sessions")
    maintain.add_argument("--claude-root", default="~/.claude/projects")
    maintain.add_argument("--include-subagents", action="store_true")
    maintain.add_argument("--repair-queue", action="store_true")
    maintain.add_argument("--enqueue-missing", action="store_true")
    maintain.add_argument("--max-enqueue", type=int, default=2000)
    maintain.add_argument("--stale-minutes", type=int, default=15)
    maintain.add_argument("--dry-run", action="store_true")

    sub.add_parser("health", help="Check context system health")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "search":
        text = session_index.format_search_results(
            args.query,
            search_type=args.type,
            limit=args.limit,
            literal=bool(args.literal),
        )
        print(text)
        return 0 if not text.startswith("No matches found") else 1

    if args.command == "semantic":
        matches = _local_memory_matches(args.query, limit=args.limit)
        if matches:
            print("--- LOCAL MEMORY MATCHES ---")
            for item in matches:
                print(json.dumps(item, ensure_ascii=False, indent=2))
            return 0
        text = session_index.format_search_results(
            args.query,
            search_type="content",
            limit=min(args.limit, 10),
            literal=True,
        )
        if text:
            print("--- HISTORY CONTENT FALLBACK ---")
            print(text)
        return 0 if not text.startswith("No matches found") else 1

    if args.command == "save":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        message = _save_local_memory(args.title, args.content, tags)
        print(message)
        return 0 if not message.startswith("Failed to save memory:") else 1

    if args.command == "export":
        payload = export_observations_payload(
            args.query,
            limit=args.limit,
            source_type=args.source_type,
        )
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"exported observations={payload['total_observations']} -> {output_path}")
        return 0

    if args.command == "import":
        input_path = Path(args.input).expanduser()
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        result = import_observations_payload(payload, sync_from_storage=not args.no_sync)
        print(
            f"import done inserted={result['inserted']} skipped={result['skipped']} db={result['db_path']}"
        )
        return 0

    if args.command == "serve":
        memory_viewer = _load_memory_viewer()
        os.environ["CONTEXT_VIEWER_HOST"] = str(args.host)
        os.environ["CONTEXT_VIEWER_PORT"] = str(args.port)
        if args.token:
            os.environ["CONTEXT_VIEWER_TOKEN"] = str(args.token)
        memory_viewer.HOST = os.environ["CONTEXT_VIEWER_HOST"]
        memory_viewer.PORT = int(os.environ["CONTEXT_VIEWER_PORT"])
        memory_viewer.VIEWER_TOKEN = os.environ.get("CONTEXT_VIEWER_TOKEN", "").strip()
        memory_viewer.main()
        return 0

    if args.command in {"maintain", "onecontext-maintain"}:
        context_maintenance = _load_context_maintenance()
        forwarded = [
            "--db",
            args.db,
            "--codex-root",
            args.codex_root,
            "--claude-root",
            args.claude_root,
            "--max-enqueue",
            str(args.max_enqueue),
            "--stale-minutes",
            str(args.stale_minutes),
        ]
        if args.include_subagents:
            forwarded.append("--include-subagents")
        if args.repair_queue:
            forwarded.append("--repair-queue")
        if args.enqueue_missing:
            forwarded.append("--enqueue-missing")
        if args.dry_run:
            forwarded.append("--dry-run")
        return context_maintenance.main(forwarded)

    if args.command == "health":
        recall_payload = session_index.health_payload()
        payload = {
            "checked_at": datetime.now().isoformat(),
            "session_search_lite": {
                "ok": bool(recall_payload.get("session_index_db_exists")),
                "sessions": recall_payload.get("total_sessions"),
                "indexed_this_run": recall_payload.get("sync"),
                "db": recall_payload.get("session_index_db"),
            },
            "source_freshness": _source_freshness(),
            "local_memory_root": {
                "exists": LOCAL_SHARED_ROOT.exists(),
                "path": str(LOCAL_SHARED_ROOT),
            },
            "remote_sync_policy": {
                "enabled": ENABLE_OPENVIKING_HTTP,
                "mode": "optional-http" if ENABLE_OPENVIKING_HTTP else "disabled-by-policy",
                "legacy_bridge_processes": _legacy_bridge_process_count(),
            },
            "all_ok": bool(recall_payload.get("session_index_db_exists")),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["all_ok"] else 1

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
