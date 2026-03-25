#!/usr/bin/env python3
"""Benchmark harness for the Context Mesh Foundry context chain."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
import statistics
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")


def _prepare_fake_home(home: Path, query: str) -> None:
    codex_session = home / ".codex" / "sessions" / "2026" / "03" / "bench.jsonl"
    _write_jsonl(
        codex_session,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "bench-codex",
                    "cwd": "/tmp/context-bench",
                    "timestamp": "2026-03-25T00:00:00Z",
                },
            },
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": f"{query} health check"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "context_cli benchmark assistant"},
                    ],
                },
            },
        ],
    )

    claude_session = home / ".claude" / "projects" / "bench" / "session.jsonl"
    _write_jsonl(
        claude_session,
        [
            {
                "type": "session_meta",
                "sessionId": "bench-claude",
                "cwd": "/tmp/context-bench",
                "timestamp": "2026-03-25T01:00:00Z",
            },
            {"type": "user", "message": {"content": f"{query} claude input"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "output_text", "text": "claude benchmark response"},
                    ]
                },
            },
        ],
    )

    history_dir = home / ".codex"
    _write_jsonl(
        history_dir / "history.jsonl",
        [
            {"message": f"{query} direct history"},
            {"display": "extra entry"},
        ],
    )
    _write_jsonl(
        home / ".claude" / "history.jsonl",
        [{"text": f"{query} claude history"}],
    )
    _write_jsonl(
        home / ".local" / "state" / "opencode" / "prompt-history.jsonl",
        [{"prompt": f"{query} OpenCode"}],
    )

    (home / ".zsh_history").write_text(f"ls\n{query} zsh\n", encoding="utf-8")
    (home / ".bash_history").write_text(f"pwd\n{query} bash\n", encoding="utf-8")


def _format_ms(label: str, durations: list[float]) -> str:
    mean = statistics.mean(durations)
    minimum = min(durations)
    maximum = max(durations)
    stdev = statistics.stdev(durations) if len(durations) > 1 else 0.0
    return (
        f"{label.ljust(32)} mean={mean * 1000:.1f}ms min={minimum * 1000:.1f}ms"
        f" max={maximum * 1000:.1f}ms stdev={stdev * 1000:.1f}ms"
    )


def _run_context_cli(context_cli, parser, argv: list[str]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = context_cli.run(parser.parse_args(argv))
    if rc != 0:
        raise RuntimeError(f"context_cli {argv} -> exit {rc}")
    return buffer.getvalue().strip()


def _benchmark(action: Callable[[], object], warmup: int, iterations: int) -> list[float]:
    if iterations <= 0:
        raise ValueError("iterations must be >= 1")
    durations: list[float] = []
    for idx in range(warmup + iterations):
        start = time.perf_counter()
        action()
        durations.append(time.perf_counter() - start)
    return durations[warmup:]


def _print_sample(label: str, output: str | None) -> None:
    print(f"\nSample output ({label}):")
    if not output:
        print("  <no output captured>")
        return
    print(textwrap.indent(output.strip(), "  "))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Context Mesh Foundry benchmarks.")
    parser.add_argument("--iterations", type=int, default=3, help="Measured iterations per case")
    parser.add_argument("--warmup", type=int, default=1, help="Warm-up runs to skip")
    parser.add_argument("--query", default="benchmark", help="Search query to drive context_cli search")
    args = parser.parse_args()

    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if args.warmup < 0:
        parser.error("--warmup cannot be negative")

    with tempfile.TemporaryDirectory(prefix="cmf-bench-") as tmpdir:
        fake_home = Path(tmpdir)
        storage_root = fake_home / ".unified_context_data"
        env_vars = {
            "HOME": str(fake_home),
            "UNIFIED_CONTEXT_STORAGE_ROOT": str(storage_root),
            "CONTEXT_MESH_STORAGE_ROOT": str(storage_root),
            "OPENVIKING_STORAGE_ROOT": str(storage_root),
            "CMF_SESSION_SYNC_MIN_INTERVAL_SEC": "0",
        }
        env_vars["CONTEXT_MESH_SOURCE_CACHE_TTL_SEC"] = os.environ.get("CMF_SOURCE_CACHE_TTL_SEC", "60")
        os.environ.update(env_vars)
        _prepare_fake_home(fake_home, args.query)

        # Modules must be imported from scripts/ after HOME/UNIFIED_CONTEXT_STORAGE_ROOT are pinned.
        scripts_path = str(SCRIPTS_DIR)
        if scripts_path not in sys.path:
            sys.path.insert(0, scripts_path)
        import scripts.session_index as session_index  # noqa: E402
        import scripts.context_cli as context_cli_module  # noqa: E402

        importlib.reload(session_index)
        importlib.reload(context_cli_module)

        cli_parser = context_cli_module.build_parser()

        print("Benchmark environment:")
        print(f"  fake HOME: {fake_home}")
        print(f"  storage root: {storage_root}")
        print("  iterations:", args.iterations, "warmup:", args.warmup)

        # Ensure the session index exists before measuring.
        session_index.sync_session_index(force=True)

        results: list[tuple[str, list[float]]] = []
        samples: dict[str, str] = {}

        print("\nRunning context_cli health benchmark...")
        def health_action() -> None:  # type: ignore[no-untyped-def]
            _run_context_cli(context_cli_module, cli_parser, ["health"])

        health_durations = _benchmark(health_action, args.warmup, args.iterations)
        samples["health"] = _run_context_cli(context_cli_module, cli_parser, ["health"])
        results.append(("context_cli health", health_durations))

        print("Running context_cli search benchmark...")
        def search_action() -> None:  # type: ignore[no-untyped-def]
            _run_context_cli(
                context_cli_module,
                cli_parser,
                ["search", args.query, "--limit", "5", "--literal"],
            )

        search_durations = _benchmark(search_action, args.warmup, args.iterations)
        samples["search"] = _run_context_cli(
            context_cli_module,
            cli_parser,
            ["search", args.query, "--limit", "5", "--literal"],
        )
        results.append((f"context_cli search ({args.query})", search_durations))

        print("Running session_index.sync_session_index benchmark...")
        def sync_action() -> None:  # type: ignore[no-untyped-def]
            session_index.sync_session_index(force=True)

        sync_durations = _benchmark(sync_action, args.warmup, args.iterations)
        stats = session_index.sync_session_index(force=True)
        samples["sync"] = json.dumps(stats, ensure_ascii=False, indent=2)
        results.append(("session_index.sync_session_index", sync_durations))

        print("\nBenchmark Summary")
        for label, durations in results:
            print(" ", _format_ms(label, durations))

        _print_sample("context_cli health", samples.get("health"))
        _print_sample("context_cli search", samples.get("search"))
        _print_sample("session_index.sync_session_index stats", samples.get("sync"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
