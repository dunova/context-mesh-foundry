#!/usr/bin/env python3
"""Unit tests for context_smoke module."""

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

import context_smoke  # noqa: E402


class TestRunCmd(unittest.TestCase):
    def test_env_overlay_is_applied(self) -> None:
        """run_cmd merges *env* on top of the process environment."""
        with mock.patch("context_smoke.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout=b"", stderr=b"", returncode=0)
            context_smoke.run_cmd(["echo"], env={"MY_VAR": "hello"})
            call_kwargs = mock_run.call_args.kwargs
            self.assertIn("env", call_kwargs)
            self.assertEqual(call_kwargs["env"]["MY_VAR"], "hello")

    def test_no_env_passes_none(self) -> None:
        """run_cmd without env does not set an env kwarg."""
        with mock.patch("context_smoke.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout=b"", stderr=b"", returncode=0)
            context_smoke.run_cmd(["echo"])
            call_kwargs = mock_run.call_args.kwargs
            self.assertIsNone(call_kwargs.get("env"))


class TestFreePort(unittest.TestCase):
    def test_returns_integer_port(self) -> None:
        port = context_smoke._free_port()
        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)
        self.assertLessEqual(port, 65535)


class TestAvailableNativeBackends(unittest.TestCase):
    def test_reads_health_payload(self) -> None:
        payload = {
            "native_backends": {
                "available_backends": ["rust", "go"],
            }
        }
        with mock.patch.object(
            context_smoke,
            "run_cmd",
            return_value=(0, json.dumps(payload), ""),
        ):
            backends = context_smoke._available_native_backends(Path("/tmp/context_cli.py"))
        self.assertEqual(backends, ["rust", "go"])

    def test_returns_empty_on_health_failure(self) -> None:
        with mock.patch.object(context_smoke, "run_cmd", return_value=(1, "", "error")):
            backends = context_smoke._available_native_backends(Path("/tmp/context_cli.py"))
        self.assertEqual(backends, [])

    def test_returns_empty_on_invalid_json(self) -> None:
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "not-json", "")):
            backends = context_smoke._available_native_backends(Path("/tmp/context_cli.py"))
        self.assertEqual(backends, [])

    def test_filters_unknown_backends(self) -> None:
        payload = {"native_backends": {"available_backends": ["rust", "wasm", "go"]}}
        with mock.patch.object(
            context_smoke, "run_cmd", return_value=(0, json.dumps(payload), "")
        ):
            backends = context_smoke._available_native_backends(Path("/tmp/context_cli.py"))
        self.assertEqual(set(backends), {"rust", "go"})

    def test_env_forwarded_to_run_cmd(self) -> None:
        received: list[dict | None] = []

        def fake_run_cmd(
            args: list[str], timeout: int = 60, env: dict | None = None
        ) -> tuple[int, str, str]:
            received.append(env)
            return (1, "", "")

        context_smoke.run_cmd = fake_run_cmd  # type: ignore[assignment]
        try:
            context_smoke._available_native_backends(
                Path("/tmp/context_cli.py"), env={"FOO": "bar"}
            )
        finally:
            # restore from module
            import importlib
            import context_smoke as _cs  # noqa: F401
            importlib.reload(context_smoke)

        self.assertEqual(received[0], {"FOO": "bar"})


class TestNativeScanContract(unittest.TestCase):
    def _make_run_cmd(self, marker_index: int = 10) -> tuple[list, object]:
        """Return (calls_list, fake_run_cmd) for native scan tests."""
        calls: list[list[str]] = []

        def fake_run_cmd(
            args: list[str], timeout: int = 60, env: dict | None = None
        ) -> tuple[int, str, str]:
            calls.append(args)
            if "health" in args:
                payload = {"native_backends": {"available_backends": ["rust", "go"]}}
                return 0, json.dumps(payload), ""
            query = args[marker_index]
            payload = {
                "matches": [
                    {
                        "session_id": "native-fixture-session",
                        "snippet": (
                            f"最终交付：ContextGO native smoke marker {query} 已验证。"
                        ),
                    }
                ]
            }
            return 0, json.dumps(payload), ""

        return calls, fake_run_cmd

    def test_uses_fixture_and_filters_noise(self) -> None:
        calls, fake_run_cmd = self._make_run_cmd()
        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "native_scan")
        self.assertEqual(len(result["detail"]["backends"]), 2)
        native_calls = [call for call in calls if "native-scan" in call]
        self.assertEqual(len(native_calls), 2)
        for call in native_calls:
            self.assertIn("--codex-root", call)
            self.assertIn("--claude-root", call)
            self.assertIn("--json", call)

    def test_retries_transient_backend_lock(self) -> None:
        calls: list[list[str]] = []
        first_go = {"value": True}

        def fake_run_cmd(
            args: list[str], timeout: int = 60, env: dict | None = None
        ) -> tuple[int, str, str]:
            calls.append(args)
            if "health" in args:
                payload = {"native_backends": {"available_backends": ["go"]}}
                return 0, json.dumps(payload), ""
            if args[4] == "go" and first_go["value"]:
                first_go["value"] = False
                return 1, "", "native/session_scan_go/go.mod: resource temporarily unavailable"
            query = args[10]
            payload = {
                "matches": [
                    {
                        "session_id": "native-fixture-session",
                        "snippet": (
                            f"最终交付：ContextGO native smoke marker {query} 已验证。"
                        ),
                    }
                ]
            }
            return 0, json.dumps(payload), ""

        with (
            mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd),
            mock.patch.object(context_smoke.time, "sleep") as mock_sleep,
        ):
            result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))

        self.assertTrue(result["ok"])
        self.assertEqual(mock_sleep.call_count, 1)
        native_calls = [call for call in calls if "native-scan" in call]
        self.assertEqual(len(native_calls), 2)

    def test_skipped_when_no_backends(self) -> None:
        with mock.patch.object(context_smoke, "run_cmd", return_value=(1, "", "")):
            result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["detail"].get("skipped"))

    def test_handles_invalid_json_response(self) -> None:
        def fake_run_cmd(
            args: list[str], timeout: int = 60, env: dict | None = None
        ) -> tuple[int, str, str]:
            if "health" in args:
                payload = {"native_backends": {"available_backends": ["rust"]}}
                return 0, json.dumps(payload), ""
            return 0, "not-valid-json", ""

        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))

        self.assertFalse(result["ok"])
        backend = result["detail"]["backends"][0]
        self.assertIn("error", backend)


class TestWriteNativeFixture(unittest.TestCase):
    def test_creates_expected_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_root, claude_root = context_smoke._write_native_fixture(
                Path(tmpdir), "marker-123"
            )
            self.assertTrue(codex_root.exists())
            self.assertTrue(claude_root.exists())
            files = list(codex_root.rglob("*.jsonl"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("native-fixture-session", text)
            self.assertIn("marker-123", text)

    def test_fixture_excludes_agents_md_in_snippet_line(self) -> None:
        """The AGENTS.md line must not appear in the message snippet."""
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_root, _ = context_smoke._write_native_fixture(Path(tmpdir), "m")
            files = list(codex_root.rglob("*.jsonl"))
            lines = files[0].read_text(encoding="utf-8").splitlines()
            # The message line (last) should NOT contain "# AGENTS.md instructions"
            last_line = json.loads(lines[-1])
            content = last_line["payload"]["content"][0]["text"]
            self.assertNotIn("# AGENTS.md instructions", content)


class TestSummarizeResults(unittest.TestCase):
    def test_all_pass(self) -> None:
        results = [
            {"name": "a", "ok": True},
            {"name": "b", "ok": True},
        ]
        summary = context_smoke.summarize_results(results)
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["failed_names"], [])

    def test_one_failure(self) -> None:
        results = [
            {"name": "a", "ok": True},
            {"name": "b", "ok": False},
        ]
        summary = context_smoke.summarize_results(results)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failed_names"], ["b"])

    def test_empty(self) -> None:
        summary = context_smoke.summarize_results([])
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(summary["total"], 0)


class TestHealthcheckSkipsWhenMissing(unittest.TestCase):
    def test_skipped_gracefully(self) -> None:
        result = context_smoke.test_healthcheck(Path("/nonexistent/context_healthcheck.sh"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["detail"].get("skipped"))


if __name__ == "__main__":
    unittest.main()
