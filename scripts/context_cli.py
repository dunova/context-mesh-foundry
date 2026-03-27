#!/usr/bin/env python3
"""ContextGO unified CLI — search, viewer, native scan, smoke, and maintenance."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType

try:
    from context_config import env_bool, env_int, env_str, storage_root
except ImportError:  # pragma: no cover
    from .context_config import env_bool, env_int, env_str, storage_root  # type: ignore[import-not-found]


def _get_context_core() -> ModuleType:
    """Lazy import of context_core — deferred until first use."""
    try:
        import context_core as _m  # type: ignore[import-not-found]
    except ImportError:
        from . import context_core as _m  # type: ignore[import-not-found]
    return _m


def _get_context_native() -> ModuleType:
    """Lazy import of context_native — deferred until first use."""
    try:
        import context_native as _m  # type: ignore[import-not-found]
    except ImportError:
        from . import context_native as _m  # type: ignore[import-not-found]
    return _m


def _get_context_smoke() -> ModuleType:
    """Lazy import of context_smoke — deferred until first use."""
    try:
        import context_smoke as _m  # type: ignore[import-not-found]
    except ImportError:
        from . import context_smoke as _m  # type: ignore[import-not-found]
    return _m


def _get_session_index() -> ModuleType:
    """Lazy import of session_index — deferred until first use."""
    try:
        import session_index as _m  # type: ignore[import-not-found]
    except ImportError:
        from . import session_index as _m  # type: ignore[import-not-found]
    return _m


def _get_memory_index() -> ModuleType:
    """Lazy import of memory_index — deferred until first use."""
    try:
        import memory_index as _m  # type: ignore[import-not-found]
    except ImportError:
        from . import memory_index as _m  # type: ignore[import-not-found]
    return _m


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


# ───────────────────────────────────────────────
# Thin wrappers for deferred memory_index callables
# (kept as module-level names so tests can mock them directly)
# ───────────────────────────────────────────────


def export_observations_payload(query: str = "", *, limit: int = 5000, source_type: str = "all") -> dict:
    """Delegate to memory_index.export_observations_payload (deferred import)."""
    return _get_memory_index().export_observations_payload(query, limit=limit, source_type=source_type)


def import_observations_payload(payload: dict, *, sync_from_storage: bool = True) -> dict:
    """Delegate to memory_index.import_observations_payload (deferred import)."""
    return _get_memory_index().import_observations_payload(payload, sync_from_storage=sync_from_storage)


# ───────────────────────────────────────────────
# Shared helpers
# ───────────────────────────────────────────────


def _local_memory_matches(query: str, limit: int = 3) -> list[dict]:
    """Return local memory items matching *query*, up to *limit* results."""
    return _get_context_core().local_memory_matches(
        query,
        shared_root=LOCAL_SHARED_ROOT,
        limit=limit,
        max_files=LOCAL_SCAN_MAX_FILES,
        read_bytes=LOCAL_SCAN_READ_BYTES,
        uri_prefix="local://",
    )


def _save_local_memory(title: str, content: str, tags: list[str]) -> str:
    """Write a memory markdown file and optionally notify the remote index.

    Returns a human-readable status message.
    """
    try:
        path = _get_context_core().write_memory_markdown(
            title,
            content,
            tags,
            conversations_root=LOCAL_CONVERSATIONS_ROOT,
        )
    except ValueError as exc:
        return f"Failed to save memory: {exc}. Ensure --title and --content are non-empty."

    if not ENABLE_REMOTE_MEMORY_HTTP:
        return f"Saved locally: {path}"

    import urllib.request  # deferred: only needed for remote HTTP sync

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
    """Import and return a module by name.

    Exists as a named function so tests can mock late imports (serve, maintain)
    without patching importlib globally.
    """
    import importlib  # deferred: only needed for serve/maintain commands

    return importlib.import_module(name)


def _configure_viewer_module(module: ModuleType, host: str, port: int, token: str) -> None:
    """Push viewer runtime config into environment variables and the module.

    Calls ``module.apply_runtime_config()`` when available; falls back to
    direct attribute assignment for older viewer implementations.
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
    """Return a condensed smoke payload with name/ok/rc fields only (plus detail on failure)."""
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
    return {"summary": payload.get("summary"), "results": results}


def _print_json(payload: object, *, pretty: bool = False) -> None:
    """Serialize *payload* to stdout as compact or pretty-printed JSON."""
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _source_freshness() -> dict[str, dict[str, object]]:
    """Return a mapping of known history source names to their path and mtime."""
    _core = _get_context_core()
    antigravity_candidates = sorted(
        (HOME / ".gemini" / "antigravity" / "brain").glob("*/walkthrough.md"),
        key=_core.safe_mtime,
        reverse=True,
    )
    sources: dict[str, Path | None] = {
        "codex_history": HOME / ".codex" / "history.jsonl",
        "claude_history": HOME / ".claude" / "history.jsonl",
        "opencode_history": HOME / ".local" / "state" / "opencode" / "prompt-history.jsonl",
        "shell_zsh": HOME / ".zsh_history",
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
            "mtime": datetime.fromtimestamp(_core.safe_mtime(p)).isoformat() if p.exists() else None,
        }
    return result


def _remote_process_count() -> int:
    """Return the number of running contextgo-remote processes, or 0 on error."""
    import subprocess  # deferred: only needed for health command

    try:
        proc = subprocess.run(
            ["pgrep", "-f", "contextgo-remote"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return sum(1 for line in (proc.stdout or "").splitlines() if line.strip())


# ───────────────────────────────────────────────
# Command handlers
# ───────────────────────────────────────────────


def cmd_search(args: argparse.Namespace) -> int:
    """Search session/history context and print results."""
    if not args.query or not args.query.strip():
        print("Error: search query must not be empty.", file=sys.stderr)
        return 2
    text = _get_session_index().format_search_results(
        args.query,
        search_type=args.type,
        limit=args.limit,
        literal=args.literal,
    )
    print(text)
    return 0 if not text.startswith("No matches found") else 1


def cmd_semantic(args: argparse.Namespace) -> int:
    """Search local memories first, then fall back to history content search."""
    if not args.query or not args.query.strip():
        print("Error: search query must not be empty.", file=sys.stderr)
        return 2
    matches = _local_memory_matches(args.query, limit=args.limit)
    if matches:
        print("--- LOCAL MEMORY MATCHES ---")
        for item in matches:
            print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0
    text = _get_session_index().format_search_results(
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
    if not args.output or not args.output.strip():
        print("Error: export output path must not be empty.", file=sys.stderr)
        return 2
    payload = export_observations_payload(
        args.query,
        limit=args.limit,
        source_type=args.source_type,
    )
    output_path = Path(args.output).expanduser()
    if output_path.is_dir():
        print(f"Error: output path '{output_path}' is a directory, not a file.", file=sys.stderr)
        return 2
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
        print(
            f"Error reading import file '{input_path}': {exc}. Check that the file exists and is readable.",
            file=sys.stderr,
        )
        return 1
    except json.JSONDecodeError as exc:
        print(
            f"Error parsing JSON from '{input_path}': {exc}. Ensure the file is a valid JSON export produced by 'contextgo export'.",
            file=sys.stderr,
        )
        return 1
    try:
        result = import_observations_payload(payload, sync_from_storage=not args.no_sync)
    except ValueError as exc:
        print(
            f"Invalid import payload from '{input_path}': {exc}. Ensure the file was created by 'contextgo export'.",
            file=sys.stderr,
        )
        return 1
    print(f"import done inserted={result['inserted']} skipped={result['skipped']} db={result['db_path']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the local memory viewer server (blocks until interrupted)."""
    if not (1 <= args.port <= 65535):
        print(f"Error: port {args.port} is out of valid range 1-65535.", file=sys.stderr)
        return 2
    viewer_module = _load_module("context_server")
    _configure_viewer_module(viewer_module, args.host, args.port, args.token)
    viewer_module.main()
    return 0


def cmd_maintain(args: argparse.Namespace) -> int:
    """Run local index maintenance (repair queue, enqueue missing sessions)."""
    maintenance_module = _load_module("context_maintenance")
    # Re-serialize only the flags parsed here so context_maintenance.parse_args()
    # remains the single source of truth for its own defaults.
    forwarded: list[str] = [
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
    for flag in ("--include-subagents", "--repair-queue", "--enqueue-missing", "--dry-run"):
        attr = flag.lstrip("-").replace("-", "_")
        if getattr(args, attr, False):
            forwarded.append(flag)
    return maintenance_module.main(forwarded)


def cmd_native_scan(args: argparse.Namespace) -> int:
    """Run the native Rust/Go scan backend and print results."""
    if args.threads < 1:
        print(f"Error: --threads must be at least 1, got {args.threads}.", file=sys.stderr)
        return 2
    result = _get_context_native().run_native_scan(
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
        import tempfile  # deferred: only needed for sandbox smoke runs

        with tempfile.TemporaryDirectory(prefix="contextgo-sandbox-") as sandbox_dir:
            os.environ["CONTEXTGO_STORAGE_ROOT"] = sandbox_dir
            try:
                payload = _get_context_smoke().run_smoke(*smoke_args)
            finally:
                os.environ.pop("CONTEXTGO_STORAGE_ROOT", None)
    else:
        payload = _get_context_smoke().run_smoke(*smoke_args)

    output = payload if args.verbose else _compact_smoke_payload(payload)
    _print_json(output, pretty=args.verbose)
    return 1 if any(not item["ok"] for item in payload["results"]) else 0


def cmd_health(args: argparse.Namespace) -> int:
    """Check context system health and print a JSON status payload."""
    recall = _get_session_index().health_payload()
    db_ok = bool(recall.get("session_index_db_exists"))
    payload: dict[str, object] = {
        "checked_at": datetime.now().isoformat(),
        "session_search_lite": {
            "ok": db_ok,
            "sessions": recall.get("total_sessions"),
            "indexed_this_run": recall.get("sync"),
            "db": recall.get("session_index_db"),
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
        "native_backends": _get_context_native().health_payload(),
        "all_ok": db_ok,
    }

    if args.verbose:
        _print_json(payload, pretty=True)
    else:
        session_lite = payload["session_search_lite"]  # type: ignore[index]
        _print_json(
            {
                "checked_at": payload["checked_at"],
                "all_ok": payload["all_ok"],
                "session_search_lite": {
                    "ok": session_lite["ok"],  # type: ignore[index]
                    "sessions": session_lite["sessions"],  # type: ignore[index]
                    "db": session_lite["db"],  # type: ignore[index]
                },
                "remote_sync_policy": payload["remote_sync_policy"],
                "native_backends": payload["native_backends"],
            }
        )
    return 0 if payload["all_ok"] else 1


# ───────────────────────────────────────────────
# Command dispatch table
# ───────────────────────────────────────────────

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


# ───────────────────────────────────────────────
# Argument parser
# ───────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser for the ContextGO CLI."""
    parser = argparse.ArgumentParser(
        description="ContextGO unified CLI (search, viewer, native scan, smoke, and maintenance)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p = sub.add_parser("search", help="Search session/history context")
    p.add_argument("query", help="Query text")
    p.add_argument("--type", default="all", choices=["all", "event", "session", "turn", "content"])
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--literal", action="store_true")

    # semantic
    p = sub.add_parser("semantic", help="Search local memories, then fallback to history content")
    p.add_argument("query", help="Query text")
    p.add_argument("--limit", type=int, default=5)

    # save
    p = sub.add_parser("save", help="Save key conclusion to local memory")
    p.add_argument("--title", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--tags", default="")

    # export
    p = sub.add_parser("export", help="Export indexed observations to JSON")
    p.add_argument("query", nargs="?", default="", help="Search query, empty for all")
    p.add_argument("output", help="Output JSON path")
    p.add_argument("--limit", type=int, default=5000)
    p.add_argument("--source-type", default="all", choices=["all", "history", "conversation"])

    # import
    p = sub.add_parser("import", help="Import observations from JSON")
    p.add_argument("input", help="Input JSON path")
    p.add_argument("--no-sync", action="store_true")

    # serve
    p = sub.add_parser("serve", help="Start local memory viewer")
    p.add_argument("--host", default=env_str("CONTEXTGO_VIEWER_HOST", default="127.0.0.1"))
    p.add_argument("--port", type=int, default=env_int("CONTEXTGO_VIEWER_PORT", default=37677, minimum=1))
    p.add_argument("--token", default=env_str("CONTEXTGO_VIEWER_TOKEN", default=""))

    # maintain
    p = sub.add_parser("maintain", help="Run local maintenance workflow")
    p.add_argument("--db", default="~/.contextgo/db/contextgo.db")
    p.add_argument("--codex-root", default="~/.codex/sessions")
    p.add_argument("--claude-root", default="~/.claude/projects")
    p.add_argument("--include-subagents", action="store_true")
    p.add_argument("--repair-queue", action="store_true")
    p.add_argument("--enqueue-missing", action="store_true")
    p.add_argument("--max-enqueue", type=int, default=2000)
    p.add_argument("--stale-minutes", type=int, default=15)
    p.add_argument("--dry-run", action="store_true")

    # native-scan
    p = sub.add_parser(
        "native-scan",
        help="Run the ContextGO native scan workflow",
        description="Run the native scan workflow that exercises the Rust/Go backends without extra wrappers.",
    )
    p.add_argument("--backend", choices=["auto", "rust", "go"], default="auto")
    p.add_argument("--codex-root")
    p.add_argument("--claude-root")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--query")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--debug-build", action="store_true")

    # smoke
    p = sub.add_parser(
        "smoke",
        help="Run the ContextGO smoke gate",
        description="Run the smoke gate that checks CLI, viewer, and memory flows end to end.",
    )
    p.add_argument("--verbose", action="store_true", help="Print full smoke payload")
    p.add_argument(
        "--sandbox",
        action="store_true",
        help=(
            "Run smoke in an isolated temporary directory. "
            "Sets CONTEXTGO_STORAGE_ROOT to a fresh tempfile.TemporaryDirectory() "
            "and cleans up on exit so the developer's ~/.contextgo is never touched."
        ),
    )

    # health
    p = sub.add_parser("health", help="Check context system health")
    p.add_argument("--verbose", action="store_true", help="Print full health payload")

    return parser


# ───────────────────────────────────────────────
# Entry points
# ───────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    """Dispatch parsed arguments to the appropriate command handler."""
    handler = COMMANDS.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2
    return handler(args)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and run the selected command. Returns an exit code."""
    return run(build_parser().parse_args(argv))


_LAZY_MODULE_MAP: dict[str, object] = {}
_LAZY_MODULE_GETTERS: dict[str, object] = {
    "context_core": _get_context_core,
    "context_native": _get_context_native,
    "context_smoke": _get_context_smoke,
    "session_index": _get_session_index,
    "memory_index": _get_memory_index,
}


def __getattr__(name: str) -> object:
    """Support lazy access to deferred modules as attributes of this module.

    This allows ``context_cli.context_native`` etc. to work for test mocking
    while still deferring the actual import until first use.
    """
    getter = _LAZY_MODULE_GETTERS.get(name)
    if getter is not None:
        module = getter()  # type: ignore[call-arg]
        _LAZY_MODULE_MAP[name] = module
        # Cache it as a real attribute so subsequent accesses skip __getattr__
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    raise SystemExit(main())
