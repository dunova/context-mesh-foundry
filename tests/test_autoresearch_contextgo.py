#!/usr/bin/env python3
"""Unit tests for autoresearch_contextgo module."""

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


class AutoResearchTests(unittest.TestCase):
    def test_append_log_replaces_same_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            payload = {
                "timestamp": "2026-03-26T10:00:00",
                "dimensions": {"stability": 100, "recall": 100, "token_efficiency": 90},
                "total_score": 98.0,
            }
            payload2 = {
                "timestamp": "2026-03-26T10:05:00",
                "dimensions": {"stability": 100, "recall": 100, "token_efficiency": 95},
                "total_score": 99.0,
            }
            with mock.patch.object(ar, "LOG_PATH", log_path), mock.patch.object(ar, "STATE_PATH", state_path):
                ar.append_log(8, payload, "KEEP", "first")
                ar.append_log(8, payload2, "KEEP", "second")
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("second", lines[1])
            latest = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(latest["total_score"], 99.0)

    def test_append_log_updates_metrics_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            metrics_path = Path(tmpdir) / "metrics.json"
            best_path = Path(tmpdir) / "best.json"
            payload = {
                "round": 12,
                "timestamp": "2026-03-26T10:17:49",
                "git_commit": "abc1234",
                "note": "metrics",
                "dimensions": {"stability": 100, "recall": 100, "token_efficiency": 95},
                "total_score": 99.0,
                "signals": {
                    "health_bytes": 386,
                    "search_bytes": 1417,
                    "smoke_bytes": 346,
                    "native_total_bytes": 4382,
                    "native_text_bytes": 579,
                },
            }
            with (
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
            ):
                ar.append_log(12, payload, "KEEP", "metrics")
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertEqual(metrics[0]["round"], 12)
            self.assertEqual(metrics[0]["health_bytes"], 386)
            self.assertEqual(metrics[0]["git_commit"], "abc1234")
            best = json.loads(best_path.read_text(encoding="utf-8"))
            self.assertEqual(best["round"], 12)
            self.assertEqual(best["note"], "metrics")
            self.assertEqual(best["target_score"], None)
            self.assertIn("generated_at", best)

    def test_append_log_keeps_recent_metric_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            metrics_path = Path(tmpdir) / "metrics.json"
            best_path = Path(tmpdir) / "best.json"
            with (
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
                mock.patch.object(ar, "MAX_METRIC_HISTORY", 3),
            ):
                for round_no in range(1, 6):
                    payload = {
                        "round": round_no,
                        "timestamp": f"2026-03-26T10:0{round_no}:00",
                        "git_commit": f"c{round_no}",
                        "note": f"n{round_no}",
                        "dimensions": {"stability": 100, "recall": 100, "token_efficiency": 95},
                        "total_score": 99.0,
                        "signals": {
                            "health_bytes": 386,
                            "search_bytes": 1417,
                            "smoke_bytes": 346,
                            "native_total_bytes": 4382,
                            "native_text_bytes": 579,
                        },
                    }
                    ar.append_log(round_no, payload, "KEEP", f"n{round_no}")
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertEqual([item["round"] for item in metrics], [3, 4, 5])


class RunCmdTests(unittest.TestCase):
    """Tests for run_cmd (lines 24-34)."""

    def test_run_cmd_success(self) -> None:
        """run_cmd returns (0, stdout, '') for a simple echo command."""
        rc, out, err = ar.run_cmd(["python3", "-c", "print('hello')"])
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)

    def test_run_cmd_failure(self) -> None:
        """run_cmd returns non-zero rc for a failing command."""
        rc, out, err = ar.run_cmd(["python3", "-c", "import sys; sys.exit(1)"])
        self.assertNotEqual(rc, 0)

    def test_run_cmd_stderr(self) -> None:
        """run_cmd captures stderr output."""
        rc, out, err = ar.run_cmd(["python3", "-c", "import sys; sys.stderr.write('err\n')"])
        self.assertIn("err", err)


class CurrentGitCommitTests(unittest.TestCase):
    """Tests for current_git_commit (lines 37-40)."""

    def test_returns_string_on_success(self) -> None:
        """current_git_commit returns a non-empty string when git succeeds."""
        with mock.patch.object(ar, "run_cmd", return_value=(0, "abc1234\n", "")):
            result = ar.current_git_commit()
        self.assertEqual(result, "abc1234")

    def test_returns_empty_on_failure(self) -> None:
        """current_git_commit returns '' when git fails."""
        with mock.patch.object(ar, "run_cmd", return_value=(1, "", "not a git repo")):
            result = ar.current_git_commit()
        self.assertEqual(result, "")

    def test_returns_empty_when_output_blank(self) -> None:
        """current_git_commit returns '' when git returns empty output."""
        with mock.patch.object(ar, "run_cmd", return_value=(0, "   \n", "")):
            result = ar.current_git_commit()
        self.assertEqual(result, "")


class JsonFromTextTests(unittest.TestCase):
    """Tests for _json_from_text (line 44)."""

    def test_parses_valid_json(self) -> None:
        result = ar._json_from_text('{"key": "value"}')
        self.assertEqual(result, {"key": "value"})

    def test_empty_string_returns_empty_dict(self) -> None:
        result = ar._json_from_text("")
        self.assertEqual(result, {})

    def test_whitespace_returns_empty_dict(self) -> None:
        result = ar._json_from_text("   \n  ")
        self.assertEqual(result, {})

    def test_none_like_empty_returns_empty_dict(self) -> None:
        result = ar._json_from_text(None)  # type: ignore[arg-type]
        self.assertEqual(result, {})


class NativeEvalTests(unittest.TestCase):
    """Tests for _native_eval (lines 47-73)."""

    def test_native_eval_success_with_matches(self) -> None:
        """_native_eval returns ok=True when rc=0 and matches present."""
        payload = json.dumps({"matches": [{"file": "a.py"}]})
        with mock.patch.object(ar, "run_cmd", return_value=(0, payload, "")):
            result = ar._native_eval("rust", "query")
        self.assertTrue(result["ok"])
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["backend"], "rust")

    def test_native_eval_failure_no_matches(self) -> None:
        """_native_eval returns ok=False when rc!=0 or no matches."""
        with mock.patch.object(ar, "run_cmd", return_value=(1, '{"matches": []}', "")):
            result = ar._native_eval("go", "query")
        self.assertFalse(result["ok"])
        self.assertEqual(result["match_count"], 0)

    def test_native_eval_uses_stderr_when_stdout_empty(self) -> None:
        """_native_eval falls back to stderr for byte count."""
        err_payload = json.dumps({"matches": []})
        with mock.patch.object(ar, "run_cmd", return_value=(0, "", err_payload)):
            result = ar._native_eval("rust", "query")
        self.assertGreater(result["bytes"], 0)


class NativeTextEvalTests(unittest.TestCase):
    """Tests for _native_text_eval (lines 76-98)."""

    def test_native_text_eval_ok(self) -> None:
        """_native_text_eval returns ok=True when rc=0."""
        with mock.patch.object(ar, "run_cmd", return_value=(0, "some output", "")):
            result = ar._native_text_eval("rust", "query")
        self.assertTrue(result["ok"])
        self.assertEqual(result["backend"], "rust")
        self.assertGreater(result["bytes"], 0)

    def test_native_text_eval_failure(self) -> None:
        """_native_text_eval returns ok=False when rc!=0."""
        with mock.patch.object(ar, "run_cmd", return_value=(1, "", "error text")):
            result = ar._native_text_eval("go", "query")
        self.assertFalse(result["ok"])
        self.assertGreater(result["bytes"], 0)


class SmokeEvalTests(unittest.TestCase):
    """Tests for _smoke_eval (lines 101-108)."""

    def test_smoke_eval_pass_on_first_try(self) -> None:
        """_smoke_eval returns rc=0 and payload when smoke passes first time."""
        payload = json.dumps({"summary": {"status": "pass"}})
        with mock.patch.object(ar, "run_cmd", return_value=(0, payload, "")):
            rc, result_payload, byte_count = ar._smoke_eval()
        self.assertEqual(rc, 0)
        self.assertEqual((result_payload.get("summary") or {}).get("status"), "pass")
        self.assertGreater(byte_count, 0)

    def test_smoke_eval_retries_on_fail(self) -> None:
        """_smoke_eval retries once on failure then returns last result."""
        fail_payload = json.dumps({"summary": {"status": "fail"}})
        call_count = {"n": 0}

        def side_effect(*_args, **_kwargs):
            call_count["n"] += 1
            return (1, fail_payload, "")

        with mock.patch.object(ar, "run_cmd", side_effect=side_effect):
            rc, result_payload, _ = ar._smoke_eval()
        self.assertEqual(call_count["n"], 2)
        self.assertEqual(rc, 1)

    def test_smoke_eval_passes_on_second_try(self) -> None:
        """_smoke_eval returns early success on second attempt."""
        fail_payload = json.dumps({"summary": {"status": "fail"}})
        pass_payload = json.dumps({"summary": {"status": "pass"}})
        responses = [(1, fail_payload, ""), (0, pass_payload, "")]

        with mock.patch.object(ar, "run_cmd", side_effect=responses):
            rc, result_payload, _ = ar._smoke_eval()
        self.assertEqual(rc, 0)
        self.assertEqual((result_payload.get("summary") or {}).get("status"), "pass")


class EvaluateTests(unittest.TestCase):
    """Tests for evaluate() (lines 111-188)."""

    def _make_run_cmd(self, scenarios: dict) -> object:
        """Helper that returns a mock run_cmd based on called args."""

        def side_effect(args, timeout=180):
            # Identify by the command verb
            if "health" in args:
                return scenarios.get("health", (0, '{"all_ok": true}', ""))
            if "smoke" in args:
                return scenarios.get("smoke", (0, '{"summary": {"status": "pass"}}', ""))
            if "search" in args:
                return scenarios.get("search", (0, "results", ""))
            if "native-scan" in args:
                return scenarios.get("native", (0, '{"matches": [{"f": "a"}]}', ""))
            return (0, "{}", "")

        return side_effect

    def test_evaluate_full_score(self) -> None:
        """evaluate() returns structured dict with all required keys."""
        scenarios = {
            "health": (0, '{"all_ok": true}', ""),
            "smoke": (0, '{"summary": {"status": "pass"}}', ""),
            "search": (0, "results", ""),
            "native": (0, '{"matches": [{"file": "x.py"}]}', ""),
        }
        with mock.patch.object(ar, "run_cmd", side_effect=self._make_run_cmd(scenarios)):
            result = ar.evaluate("TestQuery")
        self.assertIn("total_score", result)
        self.assertIn("dimensions", result)
        self.assertIn("signals", result)
        self.assertEqual(result["query"], "TestQuery")
        dims = result["dimensions"]
        self.assertIn("stability", dims)
        self.assertIn("recall", dims)
        self.assertIn("token_efficiency", dims)

    def test_evaluate_partial_score_health_fails(self) -> None:
        """evaluate() scores lower when health check fails."""
        scenarios = {
            "health": (1, '{"all_ok": false}', ""),
            "smoke": (0, '{"summary": {"status": "pass"}}', ""),
            "search": (0, '{"results": []}', ""),
            "native": (0, '{"matches": []}', ""),
        }
        with mock.patch.object(ar, "run_cmd", side_effect=self._make_run_cmd(scenarios)):
            result = ar.evaluate("query")
        self.assertLessEqual(result["dimensions"]["stability"], 50)

    def test_evaluate_token_efficiency_penalized_for_large_bytes(self) -> None:
        """evaluate() reduces token_efficiency when byte sizes exceed thresholds."""
        # health_bytes > 600, smoke_bytes > 2000, native_bytes > 3500
        # Use valid JSON padded via a long string field
        large_health = json.dumps({"all_ok": True, "padding": "x" * 700})
        large_smoke = json.dumps({"summary": {"status": "pass"}, "padding": "x" * 3000})
        large_native = json.dumps({"matches": [{"f": "a"}, {"f": "b"}], "padding": "x" * 2000})

        def side_effect(args, timeout=180):
            if "health" in args:
                return (0, large_health, "")
            if "smoke" in args:
                return (0, large_smoke, "")
            if "search" in args:
                return (0, '{"results": []}', "")
            if "native-scan" in args:
                return (0, large_native, "")
            return (0, "{}", "")

        with mock.patch.object(ar, "run_cmd", side_effect=side_effect):
            result = ar.evaluate("query")
        self.assertLess(result["dimensions"]["token_efficiency"], 100)

    def test_evaluate_token_efficiency_never_negative(self) -> None:
        """evaluate() token_efficiency is clamped to >= 0."""
        # Extremely large valid JSON to trigger maximum penalties
        huge_health = json.dumps({"all_ok": True, "data": "x" * 100_000})
        huge_smoke = json.dumps({"summary": {"status": "pass"}, "data": "x" * 100_000})
        huge_native = json.dumps({"matches": [{"f": "a"}], "data": "x" * 100_000})

        def side_effect(args, timeout=180):
            if "health" in args:
                return (0, huge_health, "")
            if "smoke" in args:
                return (0, huge_smoke, "")
            if "search" in args:
                return (0, '{"results": []}', "")
            if "native-scan" in args:
                return (0, huge_native, "")
            return (0, "{}", "")

        with mock.patch.object(ar, "run_cmd", side_effect=side_effect):
            result = ar.evaluate("query")
        self.assertGreaterEqual(result["dimensions"]["token_efficiency"], 0)


class AppendLogCorruptMetricsTests(unittest.TestCase):
    """Test append_log with a corrupted metrics file (lines 246-247)."""

    def test_append_log_handles_corrupt_metrics_json(self) -> None:
        """append_log silently resets to [] when metrics file is corrupted JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            metrics_path = Path(tmpdir) / "metrics.json"
            best_path = Path(tmpdir) / "best.json"
            # Write deliberately broken JSON
            metrics_path.write_text("NOT_VALID_JSON", encoding="utf-8")
            payload = {
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
            with (
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
            ):
                # Should not raise despite corrupt file
                ar.append_log(1, payload, "KEEP", "corrupt-test")
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertIsInstance(metrics, list)


class BuildParserTests(unittest.TestCase):
    """Tests for build_parser (lines 283-290)."""

    def test_build_parser_defaults(self) -> None:
        """build_parser returns parser with correct defaults."""
        parser = ar.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.round, 1)
        self.assertEqual(args.max_rounds, ar.DEFAULT_MAX_ROUNDS)
        self.assertEqual(args.query, ar.DEFAULT_QUERY)
        self.assertEqual(args.note, "baseline")

    def test_build_parser_custom_values(self) -> None:
        """build_parser accepts custom --round, --max-rounds, --query, --note."""
        parser = ar.build_parser()
        args = parser.parse_args(["--round", "5", "--max-rounds", "50", "--query", "test", "--note", "custom"])
        self.assertEqual(args.round, 5)
        self.assertEqual(args.max_rounds, 50)
        self.assertEqual(args.query, "test")
        self.assertEqual(args.note, "custom")


class MainTests(unittest.TestCase):
    """Tests for main() and __main__ block (lines 293-310)."""

    def _fake_evaluate(self, query: str) -> dict:
        return {
            "timestamp": "2026-03-27T00:00:00",
            "query": query,
            "dimensions": {"stability": 100, "recall": 100, "token_efficiency": 95},
            "total_score": 98.5,
            "signals": {
                "health_ok": True,
                "health_bytes": 300,
                "smoke_ok": True,
                "search_ok": True,
                "search_bytes": 500,
                "smoke_bytes": 200,
                "native_total_bytes": 1000,
                "native_text_bytes": 400,
                "rust": {"ok": True, "rc": 0, "match_count": 1, "bytes": 500, "backend": "rust"},
                "go": {"ok": True, "rc": 0, "match_count": 1, "bytes": 500, "backend": "go"},
                "rust_text": {"ok": True, "rc": 0, "bytes": 200, "backend": "rust"},
                "go_text": {"ok": True, "rc": 0, "bytes": 200, "backend": "go"},
            },
        }

    def test_main_returns_zero(self) -> None:
        """main() returns 0 on success."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            metrics_path = Path(tmpdir) / "metrics.json"
            best_path = Path(tmpdir) / "best.json"
            with (
                mock.patch.object(ar, "evaluate", side_effect=self._fake_evaluate),
                mock.patch.object(ar, "current_git_commit", return_value="deadbeef"),
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
                mock.patch.object(ar, "ARTIFACT_ROOT", Path(tmpdir)),
            ):
                rc = ar.main(["--round", "1", "--note", "test"])
        self.assertEqual(rc, 0)

    def test_main_decision_iterate_when_low_score(self) -> None:
        """main() writes ITERATE decision when score < 80."""

        def low_score_evaluate(query: str) -> dict:
            return {
                "timestamp": "2026-03-27T00:00:00",
                "query": query,
                "dimensions": {"stability": 0, "recall": 0, "token_efficiency": 0},
                "total_score": 0.0,
                "signals": {
                    "health_ok": False,
                    "health_bytes": 100,
                    "smoke_ok": False,
                    "search_ok": False,
                    "search_bytes": 100,
                    "smoke_bytes": 100,
                    "native_total_bytes": 100,
                    "native_text_bytes": 100,
                    "rust": {"ok": False, "rc": 1, "match_count": 0, "bytes": 50, "backend": "rust"},
                    "go": {"ok": False, "rc": 1, "match_count": 0, "bytes": 50, "backend": "go"},
                    "rust_text": {"ok": False, "rc": 1, "bytes": 50, "backend": "rust"},
                    "go_text": {"ok": False, "rc": 1, "bytes": 50, "backend": "go"},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            art_root = Path(tmpdir) / "artifacts"
            art_root.mkdir()
            log_path = art_root / "log.tsv"
            state_path = art_root / "latest.json"
            metrics_path = art_root / "metrics.json"
            best_path = art_root / "best.json"
            with (
                mock.patch.object(ar, "evaluate", side_effect=low_score_evaluate),
                mock.patch.object(ar, "current_git_commit", return_value=""),
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
                mock.patch.object(ar, "ARTIFACT_ROOT", art_root),
            ):
                rc = ar.main(["--round", "2", "--note", "low"])
            self.assertEqual(rc, 0)
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertTrue(any("ITERATE" in line for line in lines))

    def test_main_prints_json_to_stdout(self) -> None:
        """main() prints valid JSON to stdout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.tsv"
            state_path = Path(tmpdir) / "latest.json"
            metrics_path = Path(tmpdir) / "metrics.json"
            best_path = Path(tmpdir) / "best.json"
            with (
                mock.patch.object(ar, "evaluate", side_effect=self._fake_evaluate),
                mock.patch.object(ar, "current_git_commit", return_value="abc"),
                mock.patch.object(ar, "LOG_PATH", log_path),
                mock.patch.object(ar, "STATE_PATH", state_path),
                mock.patch.object(ar, "METRICS_PATH", metrics_path),
                mock.patch.object(ar, "BEST_PATH", best_path),
                mock.patch.object(ar, "ARTIFACT_ROOT", Path(tmpdir)),
                mock.patch("builtins.print") as mock_print,
            ):
                ar.main([])
        # Verify print was called with JSON-parseable content
        call_args = mock_print.call_args[0][0]
        parsed = json.loads(call_args)
        self.assertIn("total_score", parsed)
        self.assertIn("task_name", parsed)
        self.assertEqual(parsed["task_name"], "ContextGO AutoResearch")


if __name__ == "__main__":
    unittest.main()
