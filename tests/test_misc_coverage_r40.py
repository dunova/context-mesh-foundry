#!/usr/bin/env python3
"""Coverage gap tests for AutoResearch R40.

Targets:
  - autoresearch_contextgo.py  line 310  (__main__ block)
  - autoresearch_contextgo.py  branch 210->217  (log file exists but is empty)
  - autoresearch_contextgo.py  branch 258->exit (existing_metrics list is empty)
  - context_smoke.py           line 482  (__main__ block)
  - e2e_quality_gate.py        line 341  (__main__ block)
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import autoresearch_contextgo as ar  # noqa: E402
import context_smoke  # noqa: E402
import e2e_quality_gate as qg  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: exec only the ``if __name__ == "__main__":`` block of a module,
# padding the code with blank lines so that coverage attributes the executed
# lines to their correct line numbers in the real source file.
# The stub for *stub_name* is placed in the namespace BEFORE exec so it is
# available when the ``if __name__ == "__main__":`` guard fires.  Because we
# only compile the trailing block (not the whole file), no ``def`` statement
# overwrites the stub.
# ---------------------------------------------------------------------------
def _exec_main_block(module_obj, stub_name: str, stub_fn):
    """Exec the ``__main__`` block of *module_obj* with *stub_name* replaced.

    Returns whatever SystemExit is raised (re-raises other exceptions).
    """
    source_path = Path(module_obj.__file__)  # type: ignore[arg-type]
    source = source_path.read_text(encoding="utf-8")
    lines = source.splitlines()

    # Locate ``if __name__ == "__main__":``
    block_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith('if __name__ == "__main__":'):
            block_start = i
            break
    if block_start is None:
        raise RuntimeError(f"No __main__ block found in {source_path}")

    # Pad with blank lines so compile() sees the correct line numbers
    snippet = "\n" * block_start + "\n".join(lines[block_start:])

    ns: dict = {}
    ns.update(module_obj.__dict__)
    ns["__name__"] = "__main__"
    ns[stub_name] = stub_fn

    exec(compile(snippet, str(source_path.resolve()), "exec"), ns)  # noqa: S102


# ---------------------------------------------------------------------------
# autoresearch_contextgo.py — branch 210->217
# The branch is at line 210: ``if existing:``
# It is False when LOG_PATH exists but contains only blank / whitespace lines.
# ---------------------------------------------------------------------------
class TestAppendLogEmptyLogFile(unittest.TestCase):
    """append_log with a log file that exists but is all whitespace."""

    def _make_payload(self) -> dict:
        return {
            "round": 1,
            "timestamp": "2026-03-27T00:00:00",
            "dimensions": {"stability": 100, "recall": 100, "token_efficiency": 90},
            "total_score": 95.0,
            "signals": {
                "health_bytes": 300,
                "search_bytes": 500,
                "smoke_bytes": 200,
                "native_total_bytes": 1000,
                "native_text_bytes": 400,
            },
        }

    def test_log_exists_but_is_empty_string(self) -> None:
        """When the log file exists but contains only an empty string, existing
        is [] so the 210->217 branch (``if existing:``) evaluates to False and
        we fall through to the default header-only ``lines`` list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            metrics_path = Path(tmpdir) / "metrics.json"
            best_path = Path(tmpdir) / "best.json"
            # Create an empty file — existing will be []
            log_path.write_text("", encoding="utf-8")
            payload = self._make_payload()
            with (
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
            ):
                ar.append_log(1, payload, "KEEP", "empty-log")
            content = log_path.read_text(encoding="utf-8")
            lines = [ln for ln in content.splitlines() if ln.strip()]
            # header line + the new data row
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].startswith("round\t"))
            self.assertIn("R001", lines[1])

    def test_log_exists_with_only_whitespace_lines(self) -> None:
        """When the log file exists but all lines are blank/whitespace, existing
        is [] (filtered by ``if line.strip()``), so ``if existing:`` is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            metrics_path = Path(tmpdir) / "metrics.json"
            best_path = Path(tmpdir) / "best.json"
            # File exists but contains only whitespace
            log_path.write_text("   \n\n   \n", encoding="utf-8")
            payload = self._make_payload()
            with (
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
            ):
                ar.append_log(1, payload, "KEEP", "whitespace-log")
            content = log_path.read_text(encoding="utf-8")
            lines = [ln for ln in content.splitlines() if ln.strip()]
            self.assertEqual(len(lines), 2)
            self.assertIn("R001", lines[1])


# ---------------------------------------------------------------------------
# autoresearch_contextgo.py — branch 258->exit
# ``if existing_metrics:`` is False when the metrics list ends up empty after
# truncation.  Using MAX_METRIC_HISTORY = -100 makes the slice
# ``existing_metrics[-(-100):]`` = ``existing_metrics[100:]`` = [] for a list
# shorter than 100 items, exercising the False (skip) branch.
# ---------------------------------------------------------------------------
class TestAppendLogEmptyMetrics(unittest.TestCase):
    """append_log when existing_metrics ends up empty after truncation."""

    def test_no_best_file_written_when_metrics_truncated_to_empty(self) -> None:
        """When MAX_METRIC_HISTORY is a large negative number the slice
        ``existing_metrics[-MAX_METRIC_HISTORY:]`` evaluates to ``[]`` because
        ``-(-100)`` = 100 and ``list[100:]`` is empty for short lists.
        This makes the ``if existing_metrics:`` guard at line 258 False so
        BEST_PATH is never written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            metrics_path = Path(tmpdir) / "metrics.json"
            best_path = Path(tmpdir) / "best.json"
            payload = {
                "round": 2,
                "timestamp": "2026-03-27T01:00:00",
                "dimensions": {"stability": 100, "recall": 100, "token_efficiency": 90},
                "total_score": 95.0,
                # No "signals" key — health_bytes will be None in the metrics row
            }
            with (
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
                # -100 → slice becomes [100:] which is [] for a 1-item list
                mock.patch.object(ar, "MAX_METRIC_HISTORY", -100),
            ):
                ar.append_log(2, payload, "KEEP", "truncated")
            # BEST_PATH should not exist because existing_metrics was truncated to []
            self.assertFalse(best_path.exists(), "BEST_PATH should not be written when metrics is empty")
            # METRICS_PATH should contain an empty list
            saved = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(saved, [])


# ---------------------------------------------------------------------------
# autoresearch_contextgo.py — line 310  (__main__ block)
# ---------------------------------------------------------------------------
class TestAutoresearchMainBlock(unittest.TestCase):
    """Exercise line 310: ``raise SystemExit(main())`` under __name__ == '__main__'."""

    def test_main_block_calls_main_and_raises_system_exit_zero(self) -> None:
        """When main() returns 0, the __main__ block raises SystemExit(0)."""
        called: list[int] = []

        def _stub_main(argv=None) -> int:
            called.append(1)
            return 0

        with self.assertRaises(SystemExit) as ctx:
            _exec_main_block(ar, "main", _stub_main)
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(len(called), 1)

    def test_main_block_propagates_nonzero_exit(self) -> None:
        """When main() returns non-zero, SystemExit carries that code."""

        def _stub_main(argv=None) -> int:
            return 2

        with self.assertRaises(SystemExit) as ctx:
            _exec_main_block(ar, "main", _stub_main)
        self.assertEqual(ctx.exception.code, 2)


# ---------------------------------------------------------------------------
# context_smoke.py — line 482  (__main__ block)
# ---------------------------------------------------------------------------
class TestContextSmokeMainBlock(unittest.TestCase):
    """Exercise line 482: ``raise SystemExit(main())`` under __name__ == '__main__'."""

    def test_main_block_exit_zero_on_all_pass(self) -> None:
        """main() returns 0 (no failures) → __main__ raises SystemExit(0)."""
        called: list[int] = []

        def _stub_main() -> int:
            called.append(1)
            return 0

        with self.assertRaises(SystemExit) as ctx:
            _exec_main_block(context_smoke, "main", _stub_main)
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(len(called), 1)

    def test_main_block_exit_one_on_failure(self) -> None:
        """main() returns 1 (some failures) → __main__ raises SystemExit(1)."""

        def _stub_main() -> int:
            return 1

        with self.assertRaises(SystemExit) as ctx:
            _exec_main_block(context_smoke, "main", _stub_main)
        self.assertEqual(ctx.exception.code, 1)


# ---------------------------------------------------------------------------
# e2e_quality_gate.py — line 341  (__main__ block)
# ---------------------------------------------------------------------------
class TestE2EQualityGateMainBlock(unittest.TestCase):
    """Exercise line 341: ``raise SystemExit(main())`` under __name__ == '__main__'."""

    def test_main_block_exit_zero_when_all_cases_pass(self) -> None:
        """main() returns 0 → __main__ raises SystemExit(0)."""
        called: list[int] = []

        def _stub_main() -> int:
            called.append(1)
            return 0

        with self.assertRaises(SystemExit) as ctx:
            _exec_main_block(qg, "main", _stub_main)
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(len(called), 1)

    def test_main_block_exit_one_when_cases_fail(self) -> None:
        """main() returns 1 → __main__ raises SystemExit(1)."""

        def _stub_main() -> int:
            return 1

        with self.assertRaises(SystemExit) as ctx:
            _exec_main_block(qg, "main", _stub_main)
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
