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
        try:
            port = context_smoke._free_port()
        except PermissionError:
            self.skipTest("loopback socket bind is not permitted in this environment")
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
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, json.dumps(payload), "")):
            backends = context_smoke._available_native_backends(Path("/tmp/context_cli.py"))
        self.assertEqual(set(backends), {"rust", "go"})

    def test_env_forwarded_to_run_cmd(self) -> None:
        received: list[dict | None] = []

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            received.append(env)
            return (1, "", "")

        context_smoke.run_cmd = fake_run_cmd  # type: ignore[assignment]
        try:
            context_smoke._available_native_backends(Path("/tmp/context_cli.py"), env={"FOO": "bar"})
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

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            calls.append(args)
            if "health" in args:
                payload = {"native_backends": {"available_backends": ["rust", "go"]}}
                return 0, json.dumps(payload), ""
            query = args[marker_index]
            payload = {
                "matches": [
                    {
                        "session_id": "native-fixture-session",
                        "snippet": (f"最终交付：ContextGO native smoke marker {query} 已验证。"),
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

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
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
                        "snippet": (f"最终交付：ContextGO native smoke marker {query} 已验证。"),
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
        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
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
            codex_root, claude_root = context_smoke._write_native_fixture(Path(tmpdir), "marker-123")
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

    def test_runs_when_script_exists(self) -> None:
        """test_healthcheck runs the bash command when the script file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "context_healthcheck.sh"
            script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
            with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "ok\n", "")) as mock_run:
                result = context_smoke.test_healthcheck(script)
        self.assertTrue(result["ok"])
        self.assertEqual(result["rc"], 0)
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "bash")

    def test_runs_and_reports_failure_when_script_fails(self) -> None:
        """test_healthcheck reports failure when the script exits non-zero."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "context_healthcheck.sh"
            script.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            with mock.patch.object(context_smoke, "run_cmd", return_value=(1, "", "error output")):
                result = context_smoke.test_healthcheck(script)
        self.assertFalse(result["ok"])
        self.assertEqual(result["rc"], 1)
        self.assertEqual(result["detail"]["stderr"], "error output")


class TestTestHealth(unittest.TestCase):
    def test_returns_ok_when_all_ok(self) -> None:
        """test_health returns ok=True when health payload has all_ok=True."""
        payload = {"all_ok": True, "checks": {}}
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, json.dumps(payload), "")):
            result = context_smoke.test_health(Path("/tmp/context_cli.py"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "health")
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["detail"]["all_ok"], True)

    def test_returns_fail_when_all_ok_false(self) -> None:
        """test_health returns ok=False when all_ok is missing or False."""
        payload = {"all_ok": False}
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, json.dumps(payload), "")):
            result = context_smoke.test_health(Path("/tmp/context_cli.py"))
        self.assertFalse(result["ok"])

    def test_handles_json_decode_error(self) -> None:
        """test_health handles non-JSON output gracefully."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "not-json", "")):
            result = context_smoke.test_health(Path("/tmp/context_cli.py"))
        self.assertFalse(result["ok"])
        self.assertIn("error", result["detail"])
        self.assertIn("raw", result["detail"])

    def test_uses_stderr_when_stdout_empty(self) -> None:
        """test_health falls back to stderr when stdout is empty."""
        payload = {"all_ok": True}
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "", json.dumps(payload))):
            result = context_smoke.test_health(Path("/tmp/context_cli.py"))
        self.assertTrue(result["ok"])

    def test_env_forwarded(self) -> None:
        """test_health forwards env to run_cmd."""
        payload = {"all_ok": True}
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, json.dumps(payload), "")) as mock_run:
            context_smoke.test_health(Path("/tmp/context_cli.py"), env={"X": "y"})
        self.assertEqual(mock_run.call_args.kwargs.get("env"), {"X": "y"})


class TestTestQualityGate(unittest.TestCase):
    def test_returns_ok_on_zero_exit(self) -> None:
        """test_quality_gate returns ok=True when rc==0."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "All checks passed", "")):
            result = context_smoke.test_quality_gate(Path("/tmp/e2e_quality_gate.py"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["name"], "quality_gate")

    def test_returns_fail_on_nonzero_exit(self) -> None:
        """test_quality_gate returns ok=False when rc!=0."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(1, "", "Error occurred")):
            result = context_smoke.test_quality_gate(Path("/tmp/e2e_quality_gate.py"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["rc"], 1)

    def test_env_forwarded(self) -> None:
        """test_quality_gate forwards env to run_cmd."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "ok", "")) as mock_run:
            context_smoke.test_quality_gate(Path("/tmp/e2e_quality_gate.py"), env={"KEY": "val"})
        self.assertEqual(mock_run.call_args.kwargs.get("env"), {"KEY": "val"})


class TestTestMaintain(unittest.TestCase):
    def test_returns_ok_when_snapshot_in_output(self) -> None:
        """test_maintain returns ok=True when rc==0 and 'Snapshot' in output."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "Snapshot created ok", "")):
            result = context_smoke.test_maintain(Path("/tmp/context_cli.py"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "maintain")
        self.assertEqual(result["rc"], 0)

    def test_returns_fail_when_snapshot_missing(self) -> None:
        """test_maintain returns ok=False when 'Snapshot' is not in output."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "done", "")):
            result = context_smoke.test_maintain(Path("/tmp/context_cli.py"))
        self.assertFalse(result["ok"])

    def test_returns_fail_on_nonzero_exit(self) -> None:
        """test_maintain returns ok=False on non-zero rc."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(1, "Snapshot error", "")):
            result = context_smoke.test_maintain(Path("/tmp/context_cli.py"))
        self.assertFalse(result["ok"])

    def test_uses_stderr_when_stdout_empty(self) -> None:
        """test_maintain uses stderr when stdout is empty."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "", "Snapshot from stderr")):
            result = context_smoke.test_maintain(Path("/tmp/context_cli.py"))
        self.assertTrue(result["ok"])

    def test_env_forwarded(self) -> None:
        """test_maintain forwards env to run_cmd."""
        with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "Snapshot", "")) as mock_run:
            context_smoke.test_maintain(Path("/tmp/context_cli.py"), env={"ENV": "val"})
        self.assertEqual(mock_run.call_args.kwargs.get("env"), {"ENV": "val"})


class TestTestRwCycle(unittest.TestCase):
    def _make_rw_run_cmd(
        self,
        save_rc: int = 0,
        semantic_rc: int = 0,
        export_rc: int = 0,
        import_rc: int = 0,
        semantic_found: bool = True,
        export_count: int = 1,
        write_export: bool = True,
    ):
        """Return a fake run_cmd for test_rw_cycle that simulates the subprocess calls."""

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            cmd = args[2] if len(args) > 2 else ""
            if cmd == "save":
                return save_rc, "saved", ""
            if cmd == "semantic":
                query = args[3] if len(args) > 3 else ""
                out = query if semantic_found else "nothing"
                return semantic_rc, out, ""
            if cmd == "export":
                # write the export file if requested
                if write_export and export_rc == 0:
                    export_file = Path(args[4])
                    payload = {"total_observations": export_count}
                    export_file.write_text(json.dumps(payload), encoding="utf-8")
                return export_rc, "exported", ""
            if cmd == "import":
                return import_rc, "imported", ""
            return 0, "", ""

        return fake_run_cmd

    def test_returns_ok_on_full_success(self) -> None:
        """test_rw_cycle returns ok=True when all sub-commands succeed."""
        fake = self._make_rw_run_cmd()
        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake):
            result = context_smoke.test_rw_cycle(Path("/tmp/context_cli.py"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "rw_cycle")
        self.assertEqual(result["rc"], 0)
        self.assertTrue(result["detail"]["semantic_found"])
        self.assertGreaterEqual(result["detail"]["export_count"], 1)

    def test_returns_fail_when_save_fails(self) -> None:
        """test_rw_cycle returns ok=False when save sub-command fails."""
        fake = self._make_rw_run_cmd(save_rc=1)
        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake):
            result = context_smoke.test_rw_cycle(Path("/tmp/context_cli.py"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["rc"], 1)

    def test_returns_fail_when_semantic_not_found(self) -> None:
        """test_rw_cycle returns ok=False when marker not in semantic results."""
        fake = self._make_rw_run_cmd(semantic_found=False)
        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake):
            result = context_smoke.test_rw_cycle(Path("/tmp/context_cli.py"))
        self.assertFalse(result["ok"])
        self.assertFalse(result["detail"]["semantic_found"])

    def test_returns_fail_when_export_not_produced(self) -> None:
        """test_rw_cycle returns ok=False when export file is not produced."""
        fake = self._make_rw_run_cmd(write_export=False, export_rc=1)
        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake):
            result = context_smoke.test_rw_cycle(Path("/tmp/context_cli.py"))
        self.assertFalse(result["ok"])

    def test_no_export_file_sets_import_failure(self) -> None:
        """test_rw_cycle uses placeholder import failure when export not produced."""

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            cmd = args[2] if len(args) > 2 else ""
            if cmd == "save":
                return 0, "saved", ""
            if cmd == "semantic":
                query = args[3] if len(args) > 3 else ""
                return 0, query, ""
            if cmd == "export":
                return 1, "", "export failed"
            return 0, "", ""

        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            result = context_smoke.test_rw_cycle(Path("/tmp/context_cli.py"))
        self.assertEqual(result["detail"]["import_rc"], 1)

    def test_env_forwarded(self) -> None:
        """test_rw_cycle forwards env to run_cmd."""
        received_envs: list[dict | None] = []

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            received_envs.append(env)
            cmd = args[2] if len(args) > 2 else ""
            if cmd == "export":
                export_file = Path(args[4])
                export_file.write_text(json.dumps({"total_observations": 1}), encoding="utf-8")
                return 0, "", ""
            if cmd == "semantic":
                query = args[3] if len(args) > 3 else ""
                return 0, query, ""
            return 0, "", ""

        env = {"MY_ENV": "value"}
        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            context_smoke.test_rw_cycle(Path("/tmp/context_cli.py"), env=env)
        self.assertTrue(all(e == env for e in received_envs))


class TestTestViewer(unittest.TestCase):
    def test_returns_skipped_when_loopback_bind_not_permitted(self) -> None:
        """test_viewer skips gracefully when the environment forbids loopback sockets."""
        with mock.patch.object(context_smoke, "_free_port", side_effect=PermissionError(1, "Operation not permitted")):
            result = context_smoke.test_viewer(Path("/tmp/context_cli.py"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["rc"], 0)
        self.assertTrue(result["detail"]["skipped"])

    def test_returns_ok_when_server_responds(self) -> None:
        """test_viewer returns ok=True when health endpoint responds with 200."""
        mock_proc = mock.MagicMock()
        mock_proc.terminate = mock.MagicMock()
        mock_proc.wait = mock.MagicMock()

        mock_resp = mock.MagicMock()
        mock_resp.__enter__ = mock.Mock(return_value=mock_resp)
        mock_resp.__exit__ = mock.Mock(return_value=False)
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.status = 200

        with (
            mock.patch("context_smoke.subprocess.Popen", return_value=mock_proc),
            mock.patch("context_smoke.urllib.request.urlopen", return_value=mock_resp),
            mock.patch.object(context_smoke, "_free_port", return_value=19999),
        ):
            result = context_smoke.test_viewer(Path("/tmp/context_cli.py"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "viewer")
        self.assertEqual(result["rc"], 0)
        self.assertIn("port", result["detail"])

    def test_returns_fail_when_server_never_responds(self) -> None:
        """test_viewer returns ok=False when server never responds within deadline."""
        import urllib.error

        mock_proc = mock.MagicMock()
        mock_proc.terminate = mock.MagicMock()
        mock_proc.wait = mock.MagicMock()

        with (
            mock.patch("context_smoke.subprocess.Popen", return_value=mock_proc),
            mock.patch(
                "context_smoke.urllib.request.urlopen",
                side_effect=urllib.error.URLError("connection refused"),
            ),
            mock.patch.object(context_smoke, "_free_port", return_value=19998),
            mock.patch("context_smoke.time.time", side_effect=[100.0, 100.1, 116.0]),
            mock.patch("context_smoke.time.sleep"),
        ):
            result = context_smoke.test_viewer(Path("/tmp/context_cli.py"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["rc"], 1)

    def test_kills_process_on_timeout_expiry(self) -> None:
        """test_viewer kills the process when wait() times out."""
        mock_proc = mock.MagicMock()
        mock_proc.terminate = mock.MagicMock()
        mock_proc.wait = mock.MagicMock(side_effect=context_smoke.subprocess.TimeoutExpired("cmd", 3))
        mock_proc.kill = mock.MagicMock()

        mock_resp = mock.MagicMock()
        mock_resp.__enter__ = mock.Mock(return_value=mock_resp)
        mock_resp.__exit__ = mock.Mock(return_value=False)
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.status = 200

        with (
            mock.patch("context_smoke.subprocess.Popen", return_value=mock_proc),
            mock.patch("context_smoke.urllib.request.urlopen", return_value=mock_resp),
            mock.patch.object(context_smoke, "_free_port", return_value=19997),
        ):
            context_smoke.test_viewer(Path("/tmp/context_cli.py"))

        mock_proc.kill.assert_called_once()


class TestRunSmoke(unittest.TestCase):
    def _stub_result(self, name: str, ok: bool = True) -> dict:
        return {"name": name, "rc": 0 if ok else 1, "ok": ok, "detail": {}}

    def test_orchestrates_all_tests(self) -> None:
        """run_smoke calls all seven test functions and returns aggregated report."""
        stub = self._stub_result
        with (
            mock.patch.object(context_smoke, "test_health", return_value=stub("health")) as m_health,
            mock.patch.object(
                context_smoke,
                "test_native_scan_contract",
                return_value=stub("native_scan"),
            ) as m_native,
            mock.patch.object(
                context_smoke,
                "test_healthcheck",
                return_value=stub("healthcheck"),
            ) as m_hc,
            mock.patch.object(
                context_smoke,
                "test_quality_gate",
                return_value=stub("quality_gate"),
            ) as m_qg,
            mock.patch.object(
                context_smoke,
                "test_rw_cycle",
                return_value=stub("rw_cycle"),
            ) as m_rw,
            mock.patch.object(
                context_smoke,
                "test_maintain",
                return_value=stub("maintain"),
            ) as m_maintain,
            mock.patch.object(
                context_smoke,
                "test_viewer",
                return_value=stub("viewer"),
            ) as m_viewer,
        ):
            report = context_smoke.run_smoke(
                Path("/tmp/context_cli.py"),
                Path("/tmp/e2e_quality_gate.py"),
            )

        for m in (m_health, m_native, m_hc, m_qg, m_rw, m_maintain, m_viewer):
            m.assert_called_once()

        self.assertIn("summary", report)
        self.assertIn("results", report)
        self.assertEqual(len(report["results"]), 7)
        self.assertEqual(report["summary"]["status"], "pass")

    def test_healthcheck_path_derived_from_cli_path(self) -> None:
        """run_smoke derives healthcheck path as sibling of cli_path."""
        stub = self._stub_result
        captured: list[Path] = []

        def fake_healthcheck(p: Path) -> dict:
            captured.append(p)
            return stub("healthcheck")

        with (
            mock.patch.object(context_smoke, "test_health", return_value=stub("health")),
            mock.patch.object(context_smoke, "test_native_scan_contract", return_value=stub("native_scan")),
            mock.patch.object(context_smoke, "test_healthcheck", side_effect=fake_healthcheck),
            mock.patch.object(context_smoke, "test_quality_gate", return_value=stub("quality_gate")),
            mock.patch.object(context_smoke, "test_rw_cycle", return_value=stub("rw_cycle")),
            mock.patch.object(context_smoke, "test_maintain", return_value=stub("maintain")),
            mock.patch.object(context_smoke, "test_viewer", return_value=stub("viewer")),
        ):
            context_smoke.run_smoke(
                Path("/some/dir/context_cli.py"),
                Path("/some/dir/e2e_quality_gate.py"),
            )

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].name, "context_healthcheck.sh")
        self.assertEqual(captured[0].parent, Path("/some/dir"))

    def test_env_forwarded_to_all_tests(self) -> None:
        """run_smoke forwards env kwarg to all test functions that accept it."""
        stub = self._stub_result
        received_envs: list[dict | None] = []

        def capture_env(*args, env: dict | None = None, **kwargs) -> dict:
            received_envs.append(env)
            return stub("x")

        with (
            mock.patch.object(context_smoke, "test_health", side_effect=capture_env),
            mock.patch.object(context_smoke, "test_native_scan_contract", side_effect=capture_env),
            mock.patch.object(context_smoke, "test_healthcheck", return_value=stub("healthcheck")),
            mock.patch.object(context_smoke, "test_quality_gate", side_effect=capture_env),
            mock.patch.object(context_smoke, "test_rw_cycle", side_effect=capture_env),
            mock.patch.object(context_smoke, "test_maintain", side_effect=capture_env),
            mock.patch.object(context_smoke, "test_viewer", side_effect=capture_env),
        ):
            context_smoke.run_smoke(
                Path("/tmp/context_cli.py"),
                Path("/tmp/e2e_quality_gate.py"),
                env={"CONTEXTGO_STORAGE_ROOT": "/tmp/test"},
            )

        for env in received_envs:
            self.assertEqual(env, {"CONTEXTGO_STORAGE_ROOT": "/tmp/test"})


class TestMain(unittest.TestCase):
    def _make_passing_report(self) -> dict:
        return {
            "summary": {"status": "pass", "total": 7, "failed": 0, "failed_names": []},
            "results": [],
        }

    def _make_failing_report(self) -> dict:
        return {
            "summary": {"status": "fail", "total": 7, "failed": 1, "failed_names": ["health"]},
            "results": [],
        }

    def test_exits_zero_on_all_pass(self) -> None:
        """main() returns 0 when all smoke tests pass."""
        with (
            mock.patch.object(context_smoke, "run_smoke", return_value=self._make_passing_report()),
            mock.patch("builtins.print"),
        ):
            rc = context_smoke.main()
        self.assertEqual(rc, 0)

    def test_exits_one_on_failure(self) -> None:
        """main() returns 1 when any smoke test fails."""
        with (
            mock.patch.object(context_smoke, "run_smoke", return_value=self._make_failing_report()),
            mock.patch("builtins.print"),
        ):
            rc = context_smoke.main()
        self.assertEqual(rc, 1)

    def test_prints_valid_json_to_stdout(self) -> None:
        """main() prints a valid JSON document to stdout."""
        printed: list[str] = []
        with (
            mock.patch.object(context_smoke, "run_smoke", return_value=self._make_passing_report()),
            mock.patch("builtins.print", side_effect=lambda s: printed.append(s)),
        ):
            context_smoke.main()

        self.assertEqual(len(printed), 1)
        parsed = json.loads(printed[0])
        self.assertIn("scope", parsed)
        self.assertIn("workspace_root", parsed)
        self.assertIn("cli_path", parsed)
        self.assertIn("quality_gate_path", parsed)
        self.assertIn("summary", parsed)

    def test_passes_correct_paths_to_run_smoke(self) -> None:
        """main() derives cli_path and quality_gate_path relative to context_smoke module."""
        captured: list[tuple] = []

        def fake_run_smoke(cli_path, quality_gate_path, env=None) -> dict:
            captured.append((cli_path, quality_gate_path))
            return self._make_passing_report()

        with (
            mock.patch.object(context_smoke, "run_smoke", side_effect=fake_run_smoke),
            mock.patch("builtins.print"),
        ):
            context_smoke.main()

        self.assertEqual(len(captured), 1)
        cli_path, qg_path = captured[0]
        self.assertEqual(cli_path.name, "context_cli.py")
        self.assertEqual(qg_path.name, "e2e_quality_gate.py")
        self.assertEqual(cli_path.parent, qg_path.parent)


# ---------------------------------------------------------------------------
# Edge-case Tests: R6 hardening
# ---------------------------------------------------------------------------


class TestWriteNativeFixtureMissingDir(unittest.TestCase):
    """Edge cases for _write_native_fixture with missing / unusual directories."""

    def test_creates_directory_structure_when_missing(self) -> None:
        """_write_native_fixture creates nested codex date dirs from scratch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "deep" / "nested" / "root"
            # root does NOT exist yet — _write_native_fixture must create it
            codex_root, claude_root = context_smoke._write_native_fixture(root, "edge-marker")
            self.assertTrue(codex_root.exists())
            self.assertTrue(claude_root.exists())
            jsonl_files = list(codex_root.rglob("*.jsonl"))
            self.assertEqual(len(jsonl_files), 1)

    def test_fixture_content_valid_jsonl_lines(self) -> None:
        """Every line written by _write_native_fixture is valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_root, _ = context_smoke._write_native_fixture(Path(tmpdir), "marker-xyz")
            files = list(codex_root.rglob("*.jsonl"))
            lines = files[0].read_text(encoding="utf-8").splitlines()
            self.assertGreater(len(lines), 0)
            for line in lines:
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    self.fail(f"Invalid JSON line in fixture: {exc!r}: {line!r}")

    def test_fixture_marker_present_in_file(self) -> None:
        """The marker string appears in the written fixture file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = "unique-edge-case-marker-99"
            codex_root, _ = context_smoke._write_native_fixture(Path(tmpdir), marker)
            files = list(codex_root.rglob("*.jsonl"))
            content = files[0].read_text(encoding="utf-8")
            self.assertIn(marker, content)

    def test_fixture_unicode_marker(self) -> None:
        """Unicode markers (CJK) are written correctly to the fixture file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = "测试标记-unicode-边缘案例"
            codex_root, _ = context_smoke._write_native_fixture(Path(tmpdir), marker)
            files = list(codex_root.rglob("*.jsonl"))
            content = files[0].read_text(encoding="utf-8")
            self.assertIn(marker, content)


class TestRunCmdEdgeCases(unittest.TestCase):
    """Edge cases for run_cmd: timeout, encoding errors, empty output."""

    def test_empty_stdout_and_stderr_returns_empty_strings(self) -> None:
        """run_cmd returns empty strings when both stdout and stderr are empty."""
        with mock.patch("context_smoke.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout=b"", stderr=b"", returncode=0)
            rc, out, err = context_smoke.run_cmd(["echo"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_non_utf8_bytes_decoded_with_replacement(self) -> None:
        """run_cmd decodes non-UTF8 bytes using errors='replace' (no exception)."""
        bad_bytes = b"hello \xff\xfe world"
        with mock.patch("context_smoke.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout=bad_bytes, stderr=b"", returncode=0)
            rc, out, err = context_smoke.run_cmd(["cmd"])
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)
        # Replacement character should appear (or the bytes silently replaced)
        self.assertIsInstance(out, str)

    def test_env_merges_with_existing_os_environ(self) -> None:
        """run_cmd merges custom env on top of os.environ, not replacing it entirely."""
        import os

        with mock.patch("context_smoke.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout=b"", stderr=b"", returncode=0)
            context_smoke.run_cmd(["cmd"], env={"CUSTOM_KEY": "custom_val"})
            call_env = mock_run.call_args.kwargs["env"]
        # os.environ keys should still be present
        for key in list(os.environ.keys())[:3]:
            self.assertIn(key, call_env)
        self.assertEqual(call_env["CUSTOM_KEY"], "custom_val")

    def test_none_stdout_handled_gracefully(self) -> None:
        """run_cmd handles None stdout/stderr without raising AttributeError."""
        with mock.patch("context_smoke.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout=None, stderr=None, returncode=1)
            rc, out, err = context_smoke.run_cmd(["cmd"])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "")


class TestHealthcheckPermissionDenied(unittest.TestCase):
    """Edge cases: permission denied when running healthcheck script."""

    def test_healthcheck_returns_failure_on_permission_denied(self) -> None:
        """test_healthcheck records failure when script exits with permission-denied rc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "context_healthcheck.sh"
            script.write_text("#!/usr/bin/env bash\nexit 126\n", encoding="utf-8")
            with mock.patch.object(context_smoke, "run_cmd", return_value=(126, "", "Permission denied")):
                result = context_smoke.test_healthcheck(script)
        self.assertFalse(result["ok"])
        self.assertEqual(result["rc"], 126)

    def test_healthcheck_script_with_empty_output(self) -> None:
        """test_healthcheck with rc=0 but empty output is still reported as ok."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "context_healthcheck.sh"
            script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            with mock.patch.object(context_smoke, "run_cmd", return_value=(0, "", "")):
                result = context_smoke.test_healthcheck(script)
        self.assertTrue(result["ok"])
        self.assertEqual(result["rc"], 0)

    def test_healthcheck_truncates_long_stdout(self) -> None:
        """test_healthcheck truncates stdout/stderr detail to 400 chars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "context_healthcheck.sh"
            script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            long_output = "x" * 1000
            with mock.patch.object(context_smoke, "run_cmd", return_value=(0, long_output, "")):
                result = context_smoke.test_healthcheck(script)
        self.assertTrue(result["ok"])
        self.assertLessEqual(len(result["detail"]["stdout"]), 400)


class TestNativeScanEdgeCases(unittest.TestCase):
    """Edge cases for native scan: AGENTS.md filter, missing matches."""

    def test_snippet_with_agents_md_instruction_marked_as_fail(self) -> None:
        """A snippet containing '# AGENTS.md instructions' causes ok=False."""

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            if "health" in args:
                payload = {"native_backends": {"available_backends": ["rust"]}}
                return 0, json.dumps(payload), ""
            query = args[10] if len(args) > 10 else "marker"
            payload = {
                "matches": [
                    {
                        "session_id": "native-fixture-session",
                        # Contains the forbidden prefix — should cause ok=False
                        "snippet": f"# AGENTS.md instructions for root {query}",
                    }
                ]
            }
            return 0, json.dumps(payload), ""

        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))

        self.assertFalse(result["ok"])

    def test_empty_matches_list_marks_backend_as_fail(self) -> None:
        """An empty matches list in backend response marks that backend as failed."""

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            if "health" in args:
                payload = {"native_backends": {"available_backends": ["go"]}}
                return 0, json.dumps(payload), ""
            return 0, json.dumps({"matches": []}), ""

        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))

        self.assertFalse(result["ok"])
        backend = result["detail"]["backends"][0]
        self.assertEqual(backend["match_count"], 0)
        self.assertFalse(backend["ok"])

    def test_wrong_session_id_marks_backend_as_fail(self) -> None:
        """A match with wrong session_id causes backend ok=False."""

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            if "health" in args:
                payload = {"native_backends": {"available_backends": ["rust"]}}
                return 0, json.dumps(payload), ""
            query = args[10] if len(args) > 10 else "marker"
            payload = {
                "matches": [
                    {
                        "session_id": "wrong-session-id",
                        "snippet": f"ContextGO native smoke marker {query} verified",
                    }
                ]
            }
            return 0, json.dumps(payload), ""

        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))

        self.assertFalse(result["ok"])

    def test_nonzero_rc_without_transient_error_marks_fail(self) -> None:
        """Non-zero rc without 'resource temporarily unavailable' marks backend as failed."""

        def fake_run_cmd(args: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str, str]:
            if "health" in args:
                payload = {"native_backends": {"available_backends": ["rust"]}}
                return 0, json.dumps(payload), ""
            return 1, "", "fatal: unexpected error"

        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))

        self.assertFalse(result["ok"])


class TestSummarizeResultsEdgeCases(unittest.TestCase):
    """Edge cases for summarize_results."""

    def test_all_failed(self) -> None:
        """summarize_results reports all tests failed."""
        results = [
            {"name": "a", "ok": False},
            {"name": "b", "ok": False},
            {"name": "c", "ok": False},
        ]
        summary = context_smoke.summarize_results(results)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["failed"], 3)
        self.assertEqual(sorted(summary["failed_names"]), ["a", "b", "c"])

    def test_missing_ok_key_treated_as_fail(self) -> None:
        """Items without an 'ok' key are treated as failed (falsy)."""
        results = [{"name": "x"}]  # no 'ok' key
        summary = context_smoke.summarize_results(results)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["failed"], 1)

    def test_result_with_ok_none_treated_as_fail(self) -> None:
        """ok=None is falsy, so treated as failure."""
        results = [{"name": "x", "ok": None}]
        summary = context_smoke.summarize_results(results)
        self.assertEqual(summary["status"], "fail")


if __name__ == "__main__":
    unittest.main()
