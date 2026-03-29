#!/usr/bin/env python3
"""Regression suite for standalone hit-first retrieval via unified CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CLI_PATH = Path(__file__).resolve().parent / "context_cli.py"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    elapsed_sec: float


def run_cmd(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_cli(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Invoke context_cli.py with the given args and return (returncode, stdout, stderr)."""
    return run_cmd([sys.executable, str(CLI_PATH), *args], timeout=timeout)


def check_cli_fixed_cases() -> list[Check]:
    """Run a set of fixed regression queries against the CLI and return results."""
    cases: list[Check] = []
    fixed_inputs = [
        ("cli-health", ["health"], '"all_ok"'),
        ("cli-keyword", ["search", "NotebookLM", "--limit", "5", "--literal"], "notebooklm"),
        (
            "cli-long-query",
            ["search", "继续搜索 GitHub 和 X 研究 notebookLM 的终端调用方案", "--limit", "5"],
            "notebooklm",
        ),
        ("cli-date", ["search", "2026-03-06", "--limit", "5"], "2026-03-06"),
    ]
    for name, args, marker in fixed_inputs:
        t0 = time.time()
        rc, out, err = run_cli(*args, timeout=60)
        text = (out + "\n" + err).lower()
        if name == "cli-health":
            # Parse health output as JSON to avoid compact-vs-pretty formatting mismatches.
            try:
                health = json.loads(out)
                passed = rc == 0 and health.get("all_ok") is True
            except (json.JSONDecodeError, TypeError):
                passed = False
        else:
            passed = rc == 0 and marker.lower() in text
        cases.append(Check(name, passed, f"rc={rc}, marker={marker}, tail={(out or err)[-220:]}", time.time() - t0))
    return cases


def main() -> int:
    """Run all regression checks and print a JSON summary."""
    checks: list[Check] = []
    checks.extend(check_cli_fixed_cases())

    passed = sum(1 for item in checks if item.passed)
    failed = len(checks) - passed

    print(
        json.dumps(
            {
                "passed": passed,
                "failed": failed,
                "checks": [
                    {
                        "name": item.name,
                        "passed": item.passed,
                        "detail": item.detail,
                        "elapsed_sec": round(item.elapsed_sec, 3),
                    }
                    for item in checks
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
