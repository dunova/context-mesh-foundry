#!/usr/bin/env python3
"""Regression suite for standalone hit-first retrieval via unified CLI."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
import os


CLI_PATH = Path(__file__).resolve().parent / "context_cli.py"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    elapsed_sec: float


def run_cmd(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_cli(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    return run_cmd([sys.executable, str(CLI_PATH), *args], timeout=timeout)


def check_cli_fixed_cases() -> list[Check]:
    cases: list[Check] = []
    fixed_inputs = [
        ("cli-health", ["health"], '"all_ok": true'),
        ("cli-keyword", ["search", "NotebookLM", "--limit", "5", "--literal"], "notebooklm"),
        ("cli-long-query", ["search", "继续搜索 GitHub 和 X 研究 notebookLM 的终端调用方案", "--limit", "5"], "notebooklm"),
        ("cli-date", ["search", "2026-03-06", "--limit", "5"], "2026-03-06"),
    ]
    for name, args, marker in fixed_inputs:
        t0 = time.time()
        rc, out, err = run_cli(*args, timeout=60)
        text = (out + "\n" + err).lower()
        passed = rc == 0 and marker.lower() in text
        cases.append(Check(name, passed, f"rc={rc}, marker={marker}, tail={(out or err)[-220:]}", time.time() - t0))
    return cases


def main() -> int:
    checks: list[Check] = []
    checks.extend(check_cli_fixed_cases())

    passed = sum(1 for item in checks if item.passed)
    failed = len(checks) - passed

    print(json.dumps(
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
    ))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
