#!/usr/bin/env python3
"""Simple benchmark harness for the standalone session index path."""

from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
CONTEXT_CLI = SCRIPTS_DIR / "context_cli.py"
QUERY = os.environ.get("CMF_BENCH_QUERY", "NotebookLM")
ITERATIONS = max(1, int(os.environ.get("CMF_BENCH_ITERATIONS", "5")))
SEARCH_LIMIT = max(1, int(os.environ.get("CMF_BENCH_SEARCH_LIMIT", "5")))


def run_cmd(args: list[str]) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    return proc.returncode, proc.stdout or "", proc.stderr or "", elapsed


def summarize(name: str, samples: list[float]) -> dict[str, float]:
    return {
        "name": name,
        "iterations": len(samples),
        "min_ms": round(min(samples) * 1000, 2),
        "median_ms": round(statistics.median(samples) * 1000, 2),
        "mean_ms": round(statistics.mean(samples) * 1000, 2),
        "max_ms": round(max(samples) * 1000, 2),
    }


def bench_health() -> dict[str, float]:
    samples: list[float] = []
    for _ in range(ITERATIONS):
        rc, out, err, elapsed = run_cmd([sys.executable, str(CONTEXT_CLI), "health"])
        text = out or err
        if rc != 0 or '"all_ok": true' not in text:
            raise SystemExit(f"health benchmark failed rc={rc}: {text[:300]}")
        samples.append(elapsed)
    return summarize("health", samples)


def bench_search() -> dict[str, float]:
    samples: list[float] = []
    for _ in range(ITERATIONS):
        rc, out, err, elapsed = run_cmd(
            [sys.executable, str(CONTEXT_CLI), "search", QUERY, "--limit", str(SEARCH_LIMIT), "--literal"]
        )
        text = out or err
        if rc != 0 or "Found" not in text:
            raise SystemExit(f"search benchmark failed rc={rc}: {text[:300]}")
        samples.append(elapsed)
    return summarize("search", samples)


def bench_sync() -> dict[str, float]:
    samples: list[float] = []
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(SCRIPTS_DIR)!r}); "
        "import session_index; "
        "import json; "
        "result=session_index.sync_session_index(force=False); "
        "print(json.dumps(result))"
    )
    for _ in range(ITERATIONS):
        rc, out, err, elapsed = run_cmd([sys.executable, "-c", code])
        text = out or err
        if rc != 0:
            raise SystemExit(f"sync benchmark failed rc={rc}: {text[:300]}")
        json.loads(text)
        samples.append(elapsed)
    return summarize("sync_session_index", samples)


def main() -> int:
    results = {
        "query": QUERY,
        "iterations": ITERATIONS,
        "benchmarks": [
            bench_health(),
            bench_search(),
            bench_sync(),
        ],
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
