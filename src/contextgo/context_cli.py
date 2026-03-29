#!/usr/bin/env python3
"""ContextGO unified CLI — search, viewer, native scan, smoke, and maintenance."""

from __future__ import annotations

import atexit
import os
import sys
import threading
from pathlib import Path
from types import ModuleType

__all__ = [
    "build_parser",
    "cmd_export",
    "cmd_health",
    "cmd_import",
    "cmd_maintain",
    "cmd_native_scan",
    "cmd_q",
    "cmd_save",
    "cmd_search",
    "cmd_semantic",
    "cmd_serve",
    "cmd_shell_init",
    "cmd_smoke",
    "cmd_vector_status",
    "cmd_vector_sync",
    "export_observations_payload",
    "import_observations_payload",
    "main",
    "run",
]

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
# Lazy thread pool — initialized once, reused across calls
# ───────────────────────────────────────────────

_THREAD_POOL_LOCK = threading.Lock()
_THREAD_POOL: object | None = None  # type: concurrent.futures.ThreadPoolExecutor | None


def _get_thread_pool() -> object:
    """Return the module-level lazy ThreadPoolExecutor (max_workers=3).

    The pool is created on first call and reused for subsequent calls, avoiding
    repeated thread-creation overhead across parallel search and health checks.
    Uses a lock to prevent race conditions during initialization.
    """
    global _THREAD_POOL  # noqa: PLW0603
    if _THREAD_POOL is not None:
        return _THREAD_POOL
    with _THREAD_POOL_LOCK:
        if _THREAD_POOL is None:
            from concurrent.futures import ThreadPoolExecutor  # deferred: only needed for parallel ops

            _THREAD_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="contextgo")
            atexit.register(_THREAD_POOL.shutdown, wait=False)
    return _THREAD_POOL


# ───────────────────────────────────────────────
# Cached argument parser — built once, reused on every main() call
# ───────────────────────────────────────────────

_PARSER: object | None = None  # type: argparse.ArgumentParser | None


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
        return f"Failed to save memory: {exc}. Ensure --title and --content are non-empty. / 内存保存失败：{exc}。请确保 --title 和 --content 非空。"

    if not ENABLE_REMOTE_MEMORY_HTTP:
        return f"Saved locally: {path}"

    # Security: non-localhost remote URLs must use HTTPS.
    from urllib.parse import urlparse as _urlparse

    _parsed_remote = _urlparse(REMOTE_MEMORY_URL)
    _remote_host = _parsed_remote.hostname or ""
    if _remote_host not in ("127.0.0.1", "localhost", "::1") and _parsed_remote.scheme != "https":
        return f"Saved locally: {path} (remote indexing skipped: HTTPS required for non-localhost URL)"

    import json  # deferred: only needed when remote HTTP sync is active
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

    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        # When installed as a package (e.g. via pipx), modules live under the
        # ``scripts`` package.  Fall back to a package-relative import so that
        # ``contextgo serve`` and ``contextgo maintain`` work in both dev
        # (direct script execution) and installed-package modes.
        return importlib.import_module(f".{name}", package=__package__)


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
    import json  # deferred: only needed when a command emits JSON output

    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _source_freshness() -> dict[str, dict[str, object]]:
    """Return a mapping of known history source names to their path and mtime."""
    try:
        from source_adapters import source_freshness_snapshot  # noqa: PLC0415
    except ImportError:
        from .source_adapters import source_freshness_snapshot  # type: ignore[import-not-found]  # noqa: PLC0415, I001

    return source_freshness_snapshot(HOME)


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


def cmd_search(args: object) -> int:
    """Search session/history context and print results."""

    if not args.query or not args.query.strip():
        print("Error: search query must not be empty. / 错误：搜索查询不能为空。", file=sys.stderr)
        return 2
    text = _get_session_index().format_search_results(
        args.query,
        search_type=args.type,
        limit=args.limit,
        literal=args.literal,
    )
    print(text)
    return 0 if text and not text.startswith("No matches found") else 1


def cmd_semantic(args: object) -> int:
    """Search local memories and session index in parallel, merging by relevance.

    Both search paths run concurrently via ThreadPoolExecutor with a 5-second
    timeout each. Memory matches are preferred; if the memory path returns
    enough results the session path result is discarded.
    """
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    if not args.query or not args.query.strip():
        print("Error: search query must not be empty. / 错误：搜索查询不能为空。", file=sys.stderr)
        return 2

    query: str = args.query
    limit: int = args.limit
    _SEARCH_TIMEOUT = 5.0  # seconds per search path

    pool = _get_thread_pool()
    if not hasattr(pool, "submit"):
        raise RuntimeError("Thread pool is not properly initialized")

    # Submit both search paths in parallel.
    future_memory = pool.submit(_local_memory_matches, query, limit)
    future_session = pool.submit(
        _get_session_index().format_search_results,
        query,
        "content",
        min(limit, 10),
        True,  # literal=True
    )

    matches: list[dict] = []
    session_text: str = ""

    # Collect memory result first (preferred path).
    try:
        matches = future_memory.result(timeout=_SEARCH_TIMEOUT)
    except FuturesTimeoutError:
        matches = []
    except Exception:  # noqa: BLE001
        matches = []

    # If memory returned enough results, cancel the session future (best-effort).
    if matches:
        future_session.cancel()
        import json  # deferred: only needed when memory matches are found

        print("--- LOCAL MEMORY MATCHES ---")
        for item in matches:
            print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0

    # Memory came back empty — collect the session result.
    try:
        session_text = future_session.result(timeout=_SEARCH_TIMEOUT)
    except FuturesTimeoutError:
        session_text = ""
    except Exception:  # noqa: BLE001
        session_text = ""

    if session_text:
        print("--- HISTORY CONTENT FALLBACK ---")
        print(session_text)
        return 0 if not session_text.startswith("No matches found") else 1
    return 1  # Both memory and session came back empty


def cmd_save(args: object) -> int:
    """Save a key conclusion to local memory storage."""

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    message = _save_local_memory(args.title, args.content, tags)
    print(message)
    return 0 if not message.startswith("Failed to save memory:") else 1


def cmd_export(args: object) -> int:
    """Export indexed observations to a JSON file."""

    if not args.output or not args.output.strip():
        print("Error: export output path must not be empty. / 错误：导出输出路径不能为空。", file=sys.stderr)
        return 2
    payload = export_observations_payload(
        args.query,
        limit=args.limit,
        source_type=args.source_type,
    )
    output_path = Path(args.output).expanduser()
    if output_path.is_dir():
        print(
            f"Error: output path '{output_path}' is a directory, not a file. / 错误：输出路径 '{output_path}' 是目录而非文件。",
            file=sys.stderr,
        )
        return 2
    import json  # deferred: only needed for export serialisation

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported observations={payload.get('total_observations', 0)} -> {output_path}")
    return 0


def cmd_import(args: object) -> int:
    """Import observations from a previously exported JSON file."""
    import json  # deferred: only needed for import deserialisation

    input_path = Path(args.input).expanduser()
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(
            f"Error reading import file '{input_path}': {exc}. Check that the file exists and is readable. / 错误：读取导入文件 '{input_path}' 失败：{exc}。请检查文件是否存在且可读。",
            file=sys.stderr,
        )
        return 1
    except json.JSONDecodeError as exc:
        print(
            f"Error parsing JSON from '{input_path}': {exc}. Ensure the file is a valid JSON export produced by 'contextgo export'. / 错误：解析 '{input_path}' 中的 JSON 失败：{exc}。请确认文件是由 'contextgo export' 生成的有效 JSON 导出。",
            file=sys.stderr,
        )
        return 1
    try:
        result = import_observations_payload(payload, sync_from_storage=not args.no_sync)
    except ValueError as exc:
        print(
            f"Invalid import payload from '{input_path}': {exc}. Ensure the file was created by 'contextgo export'. / 无效的导入数据来自 '{input_path}'：{exc}。请确认文件由 'contextgo export' 创建。",
            file=sys.stderr,
        )
        return 1
    print(
        f"import done inserted={result.get('inserted', 0)} skipped={result.get('skipped', 0)} db={result.get('db_path', '')}"
    )
    return 0


def cmd_serve(args: object) -> int:
    """Start the local memory viewer server (blocks until interrupted)."""

    if not (1 <= args.port <= 65535):
        print(
            f"Error: port {args.port} is out of valid range 1-65535. / 错误：端口 {args.port} 超出有效范围 1-65535。",
            file=sys.stderr,
        )
        return 2
    viewer_module = _load_module("context_server")
    _configure_viewer_module(viewer_module, args.host, args.port, args.token)
    viewer_module.main()
    return 0


def cmd_maintain(args: object) -> int:
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


def cmd_native_scan(args: object) -> int:
    """Run the native Rust/Go scan backend and print results."""

    if args.threads < 1:
        print(
            f"Error: --threads must be at least 1, got {args.threads}. / 错误：--threads 至少为 1，实际值为 {args.threads}。",
            file=sys.stderr,
        )
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
        if isinstance(json_payload, (dict, list)):
            _print_json(json_payload)
            if result.returncode != 0 and result.stderr:
                print(result.stderr.rstrip(), file=sys.stderr)
            return result.returncode
        # json_payload is neither dict nor list — fall through to text output
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def cmd_smoke(args: object) -> int:
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
    results = payload.get("results", [])
    return 1 if any(not item.get("ok", False) for item in results if isinstance(item, dict)) else 0


def cmd_health(args: object) -> int:
    """Check context system health and print a JSON status payload.

    Runs session_index health, memory_index stats, and native backend checks in
    parallel via ThreadPoolExecutor to reduce wall-clock time.
    """
    from concurrent.futures import TimeoutError as FuturesTimeoutError
    from datetime import datetime  # deferred: only needed for health timestamp

    _HEALTH_TIMEOUT = 10.0  # seconds per health sub-check

    pool = _get_thread_pool()
    if not hasattr(pool, "submit"):
        raise RuntimeError("Thread pool is not properly initialized")

    # Submit all three independent health checks in parallel.
    future_session = pool.submit(_get_session_index().health_payload)
    future_memory_root = pool.submit(lambda: LOCAL_SHARED_ROOT.exists())
    future_native = pool.submit(_get_context_native().health_payload)

    # Collect session index result.
    try:
        recall: dict = future_session.result(timeout=_HEALTH_TIMEOUT)
    except FuturesTimeoutError:
        recall = {}
    except Exception:  # noqa: BLE001
        recall = {}

    db_ok = bool(recall.get("session_index_db_exists"))

    # Collect memory root existence check.
    try:
        memory_root_exists: bool = future_memory_root.result(timeout=_HEALTH_TIMEOUT)
    except (FuturesTimeoutError, Exception):  # noqa: BLE001
        memory_root_exists = LOCAL_SHARED_ROOT.exists()

    # Collect native backends health.
    try:
        native_health: object = future_native.result(timeout=_HEALTH_TIMEOUT)
    except FuturesTimeoutError:
        native_health = {}
    except Exception:  # noqa: BLE001
        native_health = {}

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
            "exists": memory_root_exists,
            "path": str(LOCAL_SHARED_ROOT),
        },
        "remote_sync_policy": {
            "enabled": ENABLE_REMOTE_MEMORY_HTTP,
            "mode": "optional-http" if ENABLE_REMOTE_MEMORY_HTTP else "disabled-by-policy",
            "remote_processes": _remote_process_count(),
        },
        "native_backends": native_health,
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
# Vector index commands
# ───────────────────────────────────────────────


def cmd_vector_sync(args: object) -> int:
    """Embed pending session documents into the vector index."""
    import time as _time  # noqa: PLC0415

    si = _get_session_index()
    db_path = si.ensure_session_db() if hasattr(si, "ensure_session_db") else si.get_session_db_path()

    try:
        from vector_index import embed_pending_session_docs, get_vector_db_path, vector_available  # noqa: PLC0415
    except ImportError:
        try:
            from .vector_index import embed_pending_session_docs, get_vector_db_path, vector_available  # type: ignore[import-not-found]  # noqa: PLC0415, I001
        except ImportError:
            print(
                'Error: vector dependencies not installed. Run: pipx install "contextgo[vector]" / 错误：向量依赖未安装，请运行：pipx install "contextgo[vector]"',
                file=sys.stderr,
            )
            return 1

    if not vector_available():
        print(
            'Error: model2vec or numpy not available. Run: pipx install "contextgo[vector]" / 错误：model2vec 或 numpy 不可用，请运行：pipx install "contextgo[vector]"',
            file=sys.stderr,
        )
        return 1

    force = getattr(args, "force", False)
    vdb = get_vector_db_path(db_path)

    t0 = _time.monotonic()
    result = embed_pending_session_docs(db_path, vdb, force=force)
    elapsed = _time.monotonic() - t0

    _print_json(
        {
            "embedded": result.get("embedded", 0),
            "skipped": result.get("skipped", 0),
            "deleted": result.get("deleted", 0),
            "elapsed_sec": round(elapsed, 3),
            "vector_db": str(vdb),
        }
    )
    return 0


def cmd_vector_status(args: object) -> int:
    """Show vector index statistics."""
    si = _get_session_index()
    db_path = si.get_session_db_path()

    try:
        from vector_index import get_vector_db_path, vector_status  # noqa: PLC0415
    except ImportError:
        try:
            from .vector_index import get_vector_db_path, vector_status  # type: ignore[import-not-found]  # noqa: PLC0415, I001
        except ImportError:
            print(
                'Error: vector dependencies not installed. Run: pipx install "contextgo[vector]" / 错误：向量依赖未安装，请运行：pipx install "contextgo[vector]"',
                file=sys.stderr,
            )
            return 1

    vdb = get_vector_db_path(db_path)
    status = vector_status(db_path, vdb)
    _print_json(status)
    return 0


def cmd_sources(args: object) -> int:
    """Print detected source platforms and adapter status."""
    try:
        from source_adapters import source_inventory  # noqa: PLC0415
    except ImportError:
        from .source_adapters import source_inventory  # type: ignore[import-not-found]  # noqa: PLC0415, I001

    _print_json(source_inventory(HOME), pretty=True)
    return 0


# ───────────────────────────────────────────────
# Quick recall: contextgo q
# ───────────────────────────────────────────────

_UUID_PREFIX_RE: object | None = None


def _uuid_prefix_pattern() -> object:
    """Compile and cache the UUID-prefix regex on first use."""
    global _UUID_PREFIX_RE  # noqa: PLW0603
    if _UUID_PREFIX_RE is None:
        import re as _re  # noqa: PLC0415

        _UUID_PREFIX_RE = _re.compile(r"^[0-9a-f]{8}[-0-9a-f]*$", _re.IGNORECASE)
    return _UUID_PREFIX_RE


def _print_q_results(results: list[dict], *, as_json: bool = False) -> None:
    """Print quick-recall results in compact format."""
    if as_json:
        import json  # noqa: PLC0415

        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for idx, row in enumerate(results, 1):
        sid = row.get("session_id", "")[:8]
        created = (row.get("created_at") or "")[:10]
        src = row.get("source_type", "")
        title = row.get("title", "")
        snippet = row.get("snippet", "")[:200].replace("\n", " ").strip()
        print(f"[{idx}] {created} | {sid} | {src} | {title}")
        if snippet:
            print(f"    > {snippet}")


def _q_session_lookup(session_id: str, limit: int, as_json: bool) -> int:
    """Look up sessions by ID prefix."""
    si = _get_session_index()
    rows = si.lookup_session_by_id(session_id, limit=limit)
    if not rows:
        print(f"No session found matching: {session_id} / 未找到匹配的会话：{session_id}", file=sys.stderr)
        return 1
    _print_q_results(rows, as_json=as_json)
    return 0


def _q_search(query: str, limit: int, as_json: bool) -> int:
    """Fast hybrid search — tries vector first, then FTS5/LIKE fallback."""
    si = _get_session_index()
    db_path = si.get_session_db_path()

    # Try vector search (always, regardless of EXPERIMENTAL_SEARCH_BACKEND)
    try:
        try:
            from vector_index import (  # noqa: PLC0415
                fetch_enriched_results,
                get_vector_db_path,
                hybrid_search_session,
                vector_available,
            )
        except ImportError:
            from .vector_index import (  # type: ignore[import-not-found]  # noqa: PLC0415, I001
                fetch_enriched_results,
                get_vector_db_path,
                hybrid_search_session,
                vector_available,
            )
        if vector_available():
            vdb = get_vector_db_path(db_path)
            ranked = hybrid_search_session(query, db_path, vdb, limit=limit)
            if ranked:
                results = fetch_enriched_results(ranked, db_path, query)
                if results:
                    _print_q_results(results, as_json=as_json)
                    return 0
    except Exception:  # noqa: BLE001
        pass

    # Fallback to FTS5/LIKE
    text = si.format_search_results(query, limit=limit)
    if as_json:
        import json  # noqa: PLC0415

        rows = si._search_rows(query, limit=limit) if hasattr(si, "_search_rows") else []
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0 if text and not text.startswith("No matches found") else 1


def cmd_q(args: object) -> int:
    """Quick recall — auto-routes to session ID lookup or hybrid search.

    Accepts natural language queries or session ID prefixes.
    """
    query = " ".join(args.query).strip()  # type: ignore[union-attr]
    if not query:
        print("Usage: contextgo q <query or session-id> / 用法：contextgo q <查询或会话 ID>", file=sys.stderr)
        return 2

    as_json = getattr(args, "json", False)
    limit = getattr(args, "limit", 5)

    # Session ID detection: 8+ hex chars, optional dashes
    if _uuid_prefix_pattern().match(query):
        return _q_session_lookup(query, limit, as_json)

    return _q_search(query, limit, as_json)


# ───────────────────────────────────────────────
# Shell integration: contextgo shell-init
# ───────────────────────────────────────────────

_SHELL_INTEGRATION = """\
# ContextGO shell integration
# Add to ~/.bashrc or ~/.zshrc, or run: eval "$(contextgo shell-init)"

# Quick recall — search or session ID lookup
cg() { contextgo q "$@"; }

# Shorthand aliases
alias cgs='contextgo search'
alias cgse='contextgo semantic'
alias cgvs='contextgo vector-sync'
"""


def cmd_shell_init(args: object) -> int:
    """Print shell integration script to stdout."""
    print(_SHELL_INTEGRATION)
    return 0


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
    "vector-sync": cmd_vector_sync,
    "vector-status": cmd_vector_status,
    "sources": cmd_sources,
    "q": cmd_q,
    "shell-init": cmd_shell_init,
}


# ───────────────────────────────────────────────
# Argument parser
# ───────────────────────────────────────────────


def build_parser() -> object:
    """Build and return the top-level argument parser for the ContextGO CLI.

    The parser is cached at module level after the first call so that repeated
    invocations (e.g. in tests) pay the argparse construction cost only once.
    """
    global _PARSER  # noqa: PLW0603
    if _PARSER is not None:
        return _PARSER

    import argparse  # deferred: only needed when the parser is first built

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

    # vector-sync
    p = sub.add_parser("vector-sync", help="Embed pending session documents into vector index")
    p.add_argument("--force", action="store_true", help="Re-embed all documents")

    # vector-status
    sub.add_parser("vector-status", help="Show vector index statistics")

    # sources
    sub.add_parser("sources", help="Show detected source platforms and adapter status")

    # q (quick recall)
    p = sub.add_parser("q", help="Quick recall — search or session ID lookup")
    p.add_argument("query", nargs="+", help="Query text or session ID prefix")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--json", action="store_true", help="Output as JSON")

    # shell-init
    sub.add_parser("shell-init", help="Print shell integration script (source or eval)")

    _PARSER = parser
    return _PARSER


# ───────────────────────────────────────────────
# Entry points
# ───────────────────────────────────────────────


def run(args: object) -> int:
    """Dispatch parsed arguments to the appropriate command handler."""
    handler = COMMANDS.get(args.command)  # type: ignore[union-attr]
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2
    return handler(args)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and run the selected command. Returns an exit code."""
    parser = build_parser()
    # build_parser() returns argparse.ArgumentParser; the type annotation uses
    # ``object`` to avoid a top-level ``import argparse`` on every startup.
    args = parser.parse_args(argv)  # type: ignore[union-attr]
    return run(args)


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
        # Cache it as a real attribute so subsequent accesses skip __getattr__
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    raise SystemExit(main())
