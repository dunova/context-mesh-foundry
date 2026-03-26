#!/usr/bin/env python3
"""ContextGO unified CLI — search, viewer, native scan, smoke, and maintenance."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from types import ModuleType

try:
    import context_core
    import context_native
    import context_smoke
    import session_index
    from context_config import env_bool, env_int, env_str, storage_root
    from memory_index import export_observations_payload, import_observations_payload
except ImportError:  # pragma: no cover
    from . import context_core, context_native, context_smoke, session_index  # type: ignore[import-not-found]
    from .context_config import env_bool, env_int, env_str, storage_root  # type: ignore[import-not-found]
    from .memory_index import export_observations_payload, import_observations_payload  # type: ignore[import-not-found]


HOME = Path.home()
LOCAL_STORAGE_ROOT = storage_root()
LOCAL_SHARED_ROOT = LOCAL_STORAGE_ROOT / "resources" / "shared"
LOCAL_CONVERSATIONS_ROOT = LOCAL_SHARED_ROOT / "conversations"
LOCAL_SCAN_MAX_FILES = env_int(
    "CONTEXT_CLI_LOCAL_SCAN_MAX_FILES",
    "CONTEXTGO_LOCAL_SCAN_MAX_FILES",
    default=300,
    minimum=50,
)
LOCAL_SCAN_READ_BYTES = env_int(
    "CONTEXT_CLI_LOCAL_SCAN_READ_BYTES",
    "CONTEXTGO_LOCAL_SCAN_READ_BYTES",
    default=120000,
    minimum=4096,
)
ENABLE_REMOTE_MEMORY_HTTP = env_bool(
    "CONTEXTGO_ENABLE_REMOTE_MEMORY_HTTP",
    "CONTEXT_CLI_ENABLE_REMOTE_MEMORY_HTTP",
    default=False,
)
REMOTE_MEMORY_URL = env_str("CONTEXTGO_REMOTE_URL", default="http://127.0.0.1:8090/api/v1")


# ═══════════════════════════════════════════════════════════════
# Section: Shared Helpers
# ═══════════════════════════════════════════════════════════════


def _local_memory_matches(query: str, limit: int = 3) -> list[dict]:
    """Return local memory items matching query, up to limit results."""
    return context_core.local_memory_matches(
        query,
        shared_root=LOCAL_SHARED_ROOT,
        limit=limit,
        max_files=LOCAL_SCAN_MAX_FILES,
        read_bytes=LOCAL_SCAN_READ_BYTES,
        uri_prefix="local://",
    )


def _save_local_memory(title: str, content: str, tags: list[str]) -> str:
    """Write a memory markdown file and optionally index it remotely.

    Returns a human-readable status message.
    """
    try:
        path = context_core.write_memory_markdown(
            title,
            content,
            tags,
            conversations_root=LOCAL_CONVERSATIONS_ROOT,
        )
    except ValueError as exc:
        return f"Failed to save memory: {exc}."

    if not ENABLE_REMOTE_MEMORY_HTTP:
        return f"Saved locally: {path}"

    payload = json.dumps(
        {
            "path": str(path),
            "target": "contextgo://resources/shared/conversations",
            "reason": "save_conversation",
            "instruction": f"Index global conversation memory: {(title or '').strip()}",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{REMOTE_MEMORY_URL}/resources",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
        return f"Saved locally and indexed remotely: {path}"
    except OSError as exc:
        return f"Saved locally: {path} (remote indexing skipped: {exc})"


def _load_module(name: str) -> ModuleType:
    """Dynamically import and return a module by name."""
    return importlib.import_module(name)


def _configure_viewer_module(module: ModuleType, host: str, port: int, token: str) -> None:
    """Push viewer runtime config into environment variables and the module itself.

    Prefers module.apply_runtime_config() when available; falls back to direct
    attribute assignment so that both old and new viewer implementations are
    supported without an adapter layer.
    """
    token_value = (token or "").strip()
    os.environ["CONTEXTGO_VIEWER_HOST"] = host
    os.environ["CONTEXTGO_VIEWER_PORT"] = str(port)
    if token_value:
        os.environ["CONTEXTGO_VIEWER_TOKEN"] = token_value
    else:
        os.environ.pop("CONTEXTGO_VIEWER_TOKEN", None)

    if hasattr(module, "apply_runtime_config"):
        module.apply_runtime_config(host, port, token_value)
    else:
        module.HOST = host
        module.PORT = port
        module.VIEWER_TOKEN = token_value


def _compact_smoke_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return a condensed smoke payload with only name/ok/rc (plus detail on failure)."""
    results = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        row: dict[str, object] = {
            "name": item.get("name"),
            "ok": item.get("ok"),
            "rc": item.get("rc"),
        }
        if not item.get("ok"):
            row["detail"] = item.get("detail")
        results.append(row)
    return {
        "summary": payload.get("summary"),
        "results": results,
    }


def _print_json(payload: object, *, pretty: bool = False) -> None:
    """Serialize payload to stdout as compact or pretty-printed JSON."""
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _source_freshness() -> dict[str, dict[str, object]]:
    """Return a mapping of known history source names to their path and mtime."""
    antigravity_candidates = sorted(
        (HOME / ".gemini" / "antigravity" / "brain").glob("*/walkthrough.md"),
        key=context_core.safe_mtime,
        reverse=True,
    )
    sources: dict[str, Path | None] = {
        "codex_history": HOME / ".codex" / "history.jsonl",
        "claude_history": HOME / ".claude" / "history.jsonl",
        "opencode_history": HOME / ".local" / "state" / "opencode" / "prompt-history.jsonl",
        "shell_zsh": HOME / ".zsh_history",
        "antigravity_latest": antigravity_candidates[0] if antigravity_candidates else None,
    }
    payload: dict[str, dict[str, object]] = {}
    for name, path in sources.items():
        if path is None:
            payload[name] = {"exists": False}
            continue
        p = Path(path)
        payload[name] = {
            "exists": p.exists(),
            "path": str(p),
            "mtime": datetime.fromtimestamp(context_core.safe_mtime(p)).isoformat() if p.exists() else None,
        }
    return payload


def _remote_process_count() -> int:
    """Return the number of running contextgo-remote processes, or 0 on error."""
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "contextgo-remote"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return sum(1 for line in (proc.stdout or "").splitlines() if line.strip())


# ═══════════════════════════════════════════════════════════════
# Section: Command Handlers
# ═══════════════════════════════════════════════════════════════


def cmd_search(args: argparse.Namespace) -> int:
    """Search session/history context and print results."""
    text = session_index.format_search_results(
        args.query,
        search_type=args.type,
        limit=args.limit,
        literal=args.literal,
    )
    print(text)
    return 0 if not text.startswith("No matches found") else 1


def cmd_semantic(args: argparse.Namespace) -> int:
    """Search local memories first, then fall back to history content search."""
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


def cmd_save(args: argparse.Namespace) -> int:
    """Save a key conclusion to local memory storage."""
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    message = _save_local_memory(args.title, args.content, tags)
    print(message)
    return 0 if not message.startswith("Failed to save memory:") else 1


def cmd_export(args: argparse.Namespace) -> int:
    """Export indexed observations to a JSON file."""
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


def cmd_import(args: argparse.Namespace) -> int:
    """Import observations from a previously exported JSON file."""
    input_path = Path(args.input).expanduser()
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Error reading import file {input_path}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Error parsing JSON from {input_path}: {exc}", file=sys.stderr)
        return 1
    result = import_observations_payload(payload, sync_from_storage=not args.no_sync)
    print(f"import done inserted={result['inserted']} skipped={result['skipped']} db={result['db_path']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the local memory viewer server (blocks until interrupted)."""
    viewer_module = _load_module("context_server")
    _configure_viewer_module(viewer_module, args.host, args.port, args.token)
    viewer_module.main()
    return 0


def cmd_maintain(args: argparse.Namespace) -> int:
    """Run local index maintenance (repair queue, enqueue missing sessions)."""
    maintenance_module = _load_module("context_maintenance")
    # Forward flags to context_maintenance.main() as an argv list.
    # context_maintenance.parse_args() owns its own argument definitions;
    # we re-serialise only the values that were actually parsed here so
    # the downstream parser remains the single source of truth for defaults.
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
    return maintenance_module.main(forwarded)


def cmd_native_scan(args: argparse.Namespace) -> int:
    """Run the native Rust/Go scan backend and print results."""
    result = context_native.run_native_scan(
        backend=args.backend,
        codex_root=args.codex_root,
        claude_root=args.claude_root,
        threads=args.threads,
        release=not args.debug_build,
        query=args.query,
        json_output=args.json,
        limit=args.limit,
    )
    if args.json:
        json_payload = result.json_payload()
        if isinstance(json_payload, dict):
            _print_json(json_payload)
            if result.returncode != 0 and result.stderr:
                print(result.stderr.rstrip(), file=sys.stderr)
            return result.returncode
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def cmd_smoke(args: argparse.Namespace) -> int:
    """Run the end-to-end smoke gate and print a JSON result summary."""
    scripts_dir = Path(__file__).resolve().parent
    smoke_args = (
        scripts_dir / "context_cli.py",
        scripts_dir / "e2e_quality_gate.py",
    )

    if args.sandbox:
        with tempfile.TemporaryDirectory(prefix="contextgo-sandbox-") as sandbox_dir:
            os.environ["CONTEXTGO_STORAGE_ROOT"] = sandbox_dir
            try:
                payload = context_smoke.run_smoke(*smoke_args)
            finally:
                os.environ.pop("CONTEXTGO_STORAGE_ROOT", None)
    else:
        payload = context_smoke.run_smoke(*smoke_args)

    output = payload if args.verbose else _compact_smoke_payload(payload)
    _print_json(output, pretty=args.verbose)
    failed = [item for item in payload["results"] if not item["ok"]]
    return 1 if failed else 0


def cmd_health(args: argparse.Namespace) -> int:
    """Check context system health and print a JSON status payload."""
    recall_payload = session_index.health_payload()
    payload: dict[str, object] = {
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
            "enabled": ENABLE_REMOTE_MEMORY_HTTP,
            "mode": "optional-http" if ENABLE_REMOTE_MEMORY_HTTP else "disabled-by-policy",
            "remote_processes": _remote_process_count(),
        },
        "native_backends": context_native.health_payload(),
        "all_ok": bool(recall_payload.get("session_index_db_exists")),
    }

    if args.verbose:
        output: object = payload
    else:
        session_lite = payload["session_search_lite"]  # type: ignore[index]
        output = {
            "checked_at": payload["checked_at"],
            "all_ok": payload["all_ok"],
            "session_search_lite": {
                "ok": session_lite["ok"],
                "sessions": session_lite["sessions"],
                "db": session_lite["db"],
            },
            "remote_sync_policy": payload["remote_sync_policy"],
            "native_backends": payload["native_backends"],
        }

    _print_json(output, pretty=args.verbose)
    return 0 if payload["all_ok"] else 1


# ═══════════════════════════════════════════════════════════════
# Section: Command Dispatch Table
# ═══════════════════════════════════════════════════════════════

COMMANDS: dict[str, object] = {
    "search": cmd_search,
    "semantic": cmd_semantic,
    "save": cmd_save,
    "export": cmd_export,
    "import": cmd_import,
    "serve": cmd_serve,
    "maintain": cmd_maintain,
    "native-scan": cmd_native_scan,
    "smoke": cmd_smoke,
    "health": cmd_health,
}


# ═══════════════════════════════════════════════════════════════
# Section: Argument Parser
# ═══════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser for the ContextGO CLI."""
    parser = argparse.ArgumentParser(
        description="ContextGO unified CLI (search, viewer, native scan, smoke, and maintenance)."
    )
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
    serve.add_argument("--host", default=env_str("CONTEXTGO_VIEWER_HOST", default="127.0.0.1"))
    serve.add_argument("--port", type=int, default=env_int("CONTEXTGO_VIEWER_PORT", default=37677, minimum=1))
    serve.add_argument("--token", default=env_str("CONTEXTGO_VIEWER_TOKEN", default=""))

    maintain = sub.add_parser("maintain", help="Run local maintenance workflow")
    maintain.add_argument("--db", default="~/.aline/db/aline.db")
    maintain.add_argument("--codex-root", default="~/.codex/sessions")
    maintain.add_argument("--claude-root", default="~/.claude/projects")
    maintain.add_argument("--include-subagents", action="store_true")
    maintain.add_argument("--repair-queue", action="store_true")
    maintain.add_argument("--enqueue-missing", action="store_true")
    maintain.add_argument("--max-enqueue", type=int, default=2000)
    maintain.add_argument("--stale-minutes", type=int, default=15)
    maintain.add_argument("--dry-run", action="store_true")

    native_scan = sub.add_parser(
        "native-scan",
        help="Run the ContextGO native scan workflow",
        description="Run the native scan workflow that exercises the Rust/Go backends without extra wrappers.",
    )
    native_scan.add_argument("--backend", choices=["auto", "rust", "go"], default="auto")
    native_scan.add_argument("--codex-root")
    native_scan.add_argument("--claude-root")
    native_scan.add_argument("--threads", type=int, default=4)
    native_scan.add_argument("--query")
    native_scan.add_argument("--limit", type=int)
    native_scan.add_argument("--json", action="store_true")
    native_scan.add_argument("--debug-build", action="store_true")

    smoke = sub.add_parser(
        "smoke",
        help="Run the ContextGO smoke gate",
        description="Run the smoke gate that checks CLI, viewer, and memory flows end to end.",
    )
    smoke.add_argument("--verbose", action="store_true", help="Print full smoke payload")
    smoke.add_argument(
        "--sandbox",
        action="store_true",
        help=(
            "Run smoke in an isolated temporary directory. "
            "Sets CONTEXTGO_STORAGE_ROOT to a fresh tempfile.TemporaryDirectory() "
            "and cleans up on exit so the developer's ~/.contextgo is never touched."
        ),
    )

    health = sub.add_parser("health", help="Check context system health")
    health.add_argument("--verbose", action="store_true", help="Print full health payload")
    return parser


# ═══════════════════════════════════════════════════════════════
# Section: Entry Points
# ═══════════════════════════════════════════════════════════════


def run(args: argparse.Namespace) -> int:
    """Dispatch parsed arguments to the appropriate command handler."""
    handler = COMMANDS.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2
    return handler(args)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    """Parse argv and run the selected command. Returns an exit code."""
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
