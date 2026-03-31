#!/usr/bin/env python3
"""Unified benchmark harness for the ContextGO context chain."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "contextgo"
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEFAULT_QUERY = os.environ.get("CONTEXTGO_BENCH_QUERY", "benchmark")
DEFAULT_ITERATIONS = max(1, int(os.environ.get("CONTEXTGO_BENCH_ITERATIONS", "3")))
DEFAULT_SEARCH_LIMIT = max(1, int(os.environ.get("CONTEXTGO_BENCH_SEARCH_LIMIT", "5")))
DEFAULT_WARMUP = 1
DEFAULT_FORMAT = "text"
DEFAULT_SOURCE_CACHE_TTL = os.environ.get("CONTEXTGO_SOURCE_CACHE_TTL_SEC", "60")

_SYNC_PREAMBLE = f"import sys;sys.path.insert(0, {str(SRC_DIR)!r});import session_index;"
SYNC_ACTION_CODE = _SYNC_PREAMBLE + "session_index.sync_session_index(force=True)"
SYNC_JSON_CODE = (
    _SYNC_PREAMBLE + "import json;print(json.dumps(session_index.sync_session_index(force=True), ensure_ascii=False))"
)

MAX_SAMPLE_LINES = 5


@dataclass
class BenchmarkCase:
    name: str
    action: Callable[[], None]
    sample: Callable[[], str | None]


@dataclass
class BenchmarkStats:
    name: str
    iterations: int
    mean_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float
    sample: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "iterations": self.iterations,
            "mean_ms": round(self.mean_ms, 2),
            "min_ms": round(self.min_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "stdev_ms": round(self.stdev_ms, 2),
            "sample": self.sample,
        }


def _prepare_fake_home(home: Path, query: str) -> None:
    _write_jsonl(
        home / ".codex" / "sessions" / "2026" / "03" / "bench.jsonl",
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
                "payload": {
                    "type": "user_message",
                    "message": f"{query} health check",
                },
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

    _write_jsonl(
        home / ".claude" / "projects" / "bench" / "session.jsonl",
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
    history_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        history_dir / "history.jsonl",
        [{"message": f"{query} direct history"}, {"display": "extra entry"}],
    )

    claude_history = home / ".claude" / "history.jsonl"
    claude_history.parent.mkdir(parents=True, exist_ok=True)
    claude_history.write_text(json.dumps({"text": f"{query} claude history"}, ensure_ascii=False), encoding="utf-8")

    (home / ".local" / "state" / "opencode").mkdir(parents=True, exist_ok=True)
    (home / ".local" / "state" / "opencode" / "prompt-history.jsonl").write_text(
        json.dumps([{"prompt": f"{query} OpenCode"}], ensure_ascii=False), encoding="utf-8"
    )

    (home / ".zsh_history").write_text(f"ls\n{query} zsh\n", encoding="utf-8")
    (home / ".bash_history").write_text(f"pwd\n{query} bash\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[object]) -> None:
    """Write a list of records as newline-delimited JSON to *path*, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ContextGO benchmarks.")
    parser.add_argument(
        "--mode",
        choices=("python", "native", "both"),
        default="python",
        help="Execution path (python/native) or run both for side-by-side comparison.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default=DEFAULT_FORMAT,
        help="Output format for the benchmark summary.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help="Measured iterations per case.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help="Warm-up runs that are not measured.",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Search query used by context_cli search.")
    parser.add_argument(
        "--search-limit",
        type=int,
        default=DEFAULT_SEARCH_LIMIT,
        help="Limit passed to the context_cli search command.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if args.warmup < 0:
        parser.error("--warmup cannot be negative")
    if args.search_limit < 1:
        parser.error("--search-limit must be at least 1")
    return args


def _run_context_cli(context_cli, parser, argv: list[str]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = context_cli.run(parser.parse_args(argv))
    if rc != 0:
        raise RuntimeError(f"context_cli {argv} -> exit {rc}")
    return buffer.getvalue().strip()


def _benchmark(action: Callable[[], object], warmup: int, iterations: int) -> list[float]:
    durations: list[float] = []
    for _ in range(warmup + iterations):
        start = time.perf_counter()
        action()
        durations.append(time.perf_counter() - start)
    return durations[warmup:]


def _summarize_stats(name: str, durations: list[float], sample: str | None) -> BenchmarkStats:
    mean = statistics.mean(durations)
    minimum = min(durations)
    maximum = max(durations)
    stdev = statistics.stdev(durations) if len(durations) > 1 else 0.0
    return BenchmarkStats(
        name=name,
        iterations=len(durations),
        mean_ms=mean * 1000,
        min_ms=minimum * 1000,
        max_ms=maximum * 1000,
        stdev_ms=stdev * 1000,
        sample=sample,
    )


def _reset_fake_home(fake_home: Path) -> None:
    fake_home.mkdir(parents=True, exist_ok=True)
    for child in fake_home.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _build_mode_sequence(mode: str) -> list[str]:
    if mode == "both":
        return ["python", "native"]
    return [mode]


def _display_mode_label(mode: str) -> str:
    if mode == "native":
        return "native-wrapper"
    return mode


def _execute_mode(mode: str, args: argparse.Namespace) -> list[BenchmarkStats]:
    if mode == "python":
        src_path = str(SRC_DIR)
        if src_path not in sys.path:
            sys.path.insert(0, src_path)
        import context_cli as context_cli_module  # noqa: E402
        import session_index as session_index_module  # noqa: E402

        importlib.reload(context_cli_module)
        importlib.reload(session_index_module)

        cli_parser = context_cli_module.build_parser()
        session_index_module.sync_session_index(force=True)
        cases = _build_python_cases(
            context_cli_module,
            cli_parser,
            session_index_module,
            args.query,
            args.search_limit,
        )
    else:
        subprocess_env = os.environ.copy()
        pythonpath = subprocess_env.get("PYTHONPATH", "")
        subprocess_env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{pythonpath}" if pythonpath else str(SRC_DIR)
        _run_native_command([sys.executable, "-c", SYNC_ACTION_CODE], subprocess_env)
        cases = _build_native_cases(subprocess_env, args.query, args.search_limit)

    results: list[BenchmarkStats] = []
    for case in cases:
        try:
            durations = _benchmark(case.action, args.warmup, args.iterations)
        except Exception as exc:
            raise RuntimeError(f"Benchmark case '{case.name}' failed during timing: {exc}") from exc
        try:
            sample = case.sample()
        except Exception as exc:
            # Sample is best-effort; don't abort the whole run.
            print(f"  [warning] sample() for '{case.name}' raised: {exc}", file=sys.stderr)
            sample = None
        results.append(_summarize_stats(case.name, durations, sample))
    return results


def _format_stats_line(stats: BenchmarkStats) -> str:
    return (
        f"{stats.name.ljust(32)} mean={stats.mean_ms:.1f}ms min={stats.min_ms:.1f}ms"
        f" max={stats.max_ms:.1f}ms stdev={stats.stdev_ms:.1f}ms"
    )


def _print_summary_text(stats_list: list[BenchmarkStats], header: str = "Benchmark Summary") -> None:
    print(header)
    for stats in stats_list:
        print("  ", _format_stats_line(stats))


def _print_sample(label: str, output: str | None) -> None:
    print(f"\nSample output ({label}):")
    if not output:
        print("  <no output captured>")
        return
    lines = output.strip().splitlines()
    display = lines[:MAX_SAMPLE_LINES]
    for line in display:
        print("  " + line)
    if len(lines) > MAX_SAMPLE_LINES:
        print(f"  ... ({len(lines) - MAX_SAMPLE_LINES} more lines truncated)")


def _build_comparison_summary(
    python_stats: list[BenchmarkStats], native_stats: list[BenchmarkStats]
) -> list[dict[str, float | None]]:
    native_map = {stats.name: stats for stats in native_stats}
    comparisons: list[dict[str, float | None]] = []
    for stats in python_stats:
        native = native_map.get(stats.name)
        if not native:
            continue
        python_mean = stats.mean_ms
        native_mean = native.mean_ms
        diff = native_mean - python_mean
        ratio = native_mean / python_mean if python_mean else None
        comparisons.append(
            {
                "name": stats.name,
                "python_mean_ms": round(python_mean, 2),
                "native_mean_ms": round(native_mean, 2),
                "mean_diff_ms": round(diff, 2),
                "mean_ratio": round(ratio, 2) if ratio is not None else None,
            }
        )
    return comparisons


def _print_comparison_text(comparisons: list[dict[str, float | None]]) -> None:
    if not comparisons:
        return
    print("\nBenchmark Comparison (python vs native-wrapper)")
    header = "  " + "name".ljust(32) + "python".rjust(10) + "native".rjust(10) + "diff".rjust(10) + "ratio".rjust(10)
    print(header)
    for entry in comparisons:
        ratio = entry["mean_ratio"]
        ratio_str = f"{ratio:.2f}x" if ratio is not None else "n/a"
        print(
            "  "
            + entry["name"].ljust(32)
            + f"{entry['python_mean_ms']:10.1f}"
            + f"{entry['native_mean_ms']:10.1f}"
            + f"{entry['mean_diff_ms']:10.1f}"
            + f"{ratio_str:>10}"
        )


def _run_native_command(cmd: list[str], env: dict[str, str]) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    output = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd!r} -> exit {proc.returncode}: {output}")
    return output


def _make_case_from_callable(name: str, fn: Callable[[], str | None]) -> BenchmarkCase:
    """Build a BenchmarkCase where the action discards the return value of *fn*."""
    return BenchmarkCase(
        name=name,
        action=lambda: fn(),
        sample=fn,
    )


def _build_python_cases(
    context_cli_module, cli_parser, session_index_module, query: str, search_limit: int
) -> list[BenchmarkCase]:
    def health() -> str:
        return _run_context_cli(context_cli_module, cli_parser, ["health"])

    def search() -> str:
        return _run_context_cli(
            context_cli_module,
            cli_parser,
            ["search", query, "--limit", str(search_limit), "--literal"],
        )

    def sync() -> str:
        stats = session_index_module.sync_session_index(force=True)
        return json.dumps(stats, ensure_ascii=False, indent=2)

    return [
        _make_case_from_callable("context_cli health", health),
        _make_case_from_callable("context_cli search", search),
        _make_case_from_callable("session_index.sync_session_index", sync),
    ]


def _build_native_cases(env: dict[str, str], query: str, search_limit: int) -> list[BenchmarkCase]:
    context_cli_path = str(SRC_DIR / "context_cli.py")
    search_args = ["search", query, "--limit", str(search_limit), "--literal"]

    def health() -> str:
        return _run_native_command([sys.executable, context_cli_path, "health"], env)

    def search() -> str:
        return _run_native_command([sys.executable, context_cli_path, *search_args], env)

    def sync() -> str:
        return _run_native_command([sys.executable, "-c", SYNC_JSON_CODE], env)

    return [
        _make_case_from_callable("context_cli health", health),
        _make_case_from_callable("context_cli search", search),
        _make_case_from_callable("session_index.sync_session_index", sync),
    ]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="contextgo-bench-") as tmpdir:
        fake_home = Path(tmpdir)
        storage_root = fake_home / ".contextgo"
        env_vars = {
            "HOME": str(fake_home),
            "CONTEXTGO_STORAGE_ROOT": str(storage_root),
            "CONTEXTGO_SESSION_SYNC_MIN_INTERVAL_SEC": "0",
            "CONTEXTGO_SOURCE_CACHE_TTL_SEC": DEFAULT_SOURCE_CACHE_TTL,
        }
        # Preserve original env values so they can be restored after the benchmark.
        _original_env: dict[str, str | None] = {k: os.environ.get(k) for k in env_vars}
        os.environ.update(env_vars)

        print("Benchmark environment:")
        print(f"  fake HOME: {fake_home}")
        print(f"  storage root: {storage_root}")
        print(f"  mode: {args.mode}  format: {args.format}")
        print(f"  iterations: {args.iterations} warmup: {args.warmup}")
        print(f"  search limit: {args.search_limit}")
        print("  source cache TTL:", env_vars["CONTEXTGO_SOURCE_CACHE_TTL_SEC"], "sec")

        mode_sequence = _build_mode_sequence(args.mode)
        results_by_mode: list[tuple[str, list[BenchmarkStats]]] = []
        for index, mode in enumerate(mode_sequence):
            _reset_fake_home(fake_home)
            storage_root.mkdir(parents=True, exist_ok=True)
            _prepare_fake_home(fake_home, args.query)
            stats_list = _execute_mode(mode, args)
            results_by_mode.append((mode, stats_list))
            if args.mode == "both" and index < len(mode_sequence) - 1:
                shutil.rmtree(storage_root, ignore_errors=True)

        # Restore original environment variables to avoid polluting the caller's process.
        for key, original_value in _original_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value

        if args.format == "json":
            base_payload: dict[str, object] = {
                "mode": args.mode,
                "query": args.query,
                "search_limit": args.search_limit,
                "iterations": args.iterations,
                "warmup": args.warmup,
                "source_cache_ttl_sec": int(env_vars["CONTEXTGO_SOURCE_CACHE_TTL_SEC"]),
            }
            if args.mode == "both" and len(results_by_mode) == 2:
                base_payload["benchmarks"] = {
                    mode: [stats.to_dict() for stats in stats_list] for mode, stats_list in results_by_mode
                }
                base_payload["comparison"] = _build_comparison_summary(results_by_mode[0][1], results_by_mode[1][1])
            else:
                base_payload["benchmarks"] = [stats.to_dict() for stats in results_by_mode[0][1]]
            print(json.dumps(base_payload, ensure_ascii=False, indent=2))
        else:
            for index, (mode, stats_list) in enumerate(results_by_mode):
                if index:
                    print()
                display_mode = _display_mode_label(mode)
                _print_summary_text(stats_list, header=f"Benchmark Summary ({display_mode})")
                for stats in stats_list:
                    _print_sample(f"{display_mode} · {stats.name}", stats.sample)
            if args.mode == "both" and len(results_by_mode) == 2:
                print(
                    "\nNote: `native-wrapper` measures subprocess CLI/native-wrapper overhead, not pure Go/Rust core execution."
                )
                _print_comparison_text(_build_comparison_summary(results_by_mode[0][1], results_by_mode[1][1]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
