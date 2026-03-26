#!/usr/bin/env python3
"""Unit tests for context_smoke module."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_smoke


class ContextSmokeTests(unittest.TestCase):
    def test_available_native_backends_reads_health_payload(self) -> None:
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

    def test_native_scan_contract_uses_fixture_and_filters_noise(self) -> None:
        calls: list[list[str]] = []

        def fake_run_cmd(args: list[str], timeout: int = 60):
            calls.append(args)
            if args[1:] == ["/tmp/context_cli.py", "health"]:
                payload = {"native_backends": {"available_backends": ["rust", "go"]}}
                return 0, json.dumps(payload), ""
            backend = args[4]
            query = args[10]
            payload = {
                "matches": [
                    {
                        "session_id": "native-fixture-session",
                        "snippet": f"最终交付：ContextGO native smoke marker {query} 已验证。",
                    }
                ]
            }
            return 0, json.dumps(payload), ""

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

    def test_native_scan_contract_retries_transient_backend_lock(self) -> None:
        calls: list[list[str]] = []
        first_go = {"value": True}

        def fake_run_cmd(args: list[str], timeout: int = 60):
            calls.append(args)
            if args[1:] == ["/tmp/context_cli.py", "health"]:
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
                        "snippet": f"最终交付：ContextGO native smoke marker {query} 已验证。",
                    }
                ]
            }
            return 0, json.dumps(payload), ""

        with mock.patch.object(context_smoke, "run_cmd", side_effect=fake_run_cmd):
            with mock.patch.object(context_smoke.time, "sleep") as mock_sleep:
                result = context_smoke.test_native_scan_contract(Path("/tmp/context_cli.py"))

        self.assertTrue(result["ok"])
        self.assertEqual(mock_sleep.call_count, 1)
        native_calls = [call for call in calls if "native-scan" in call]
        self.assertEqual(len(native_calls), 2)

    def test_write_native_fixture_creates_expected_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_root, claude_root = context_smoke._write_native_fixture(Path(tmpdir), "marker-123")
            self.assertTrue(codex_root.exists())
            self.assertTrue(claude_root.exists())
            files = list(codex_root.rglob("*.jsonl"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("native-fixture-session", text)
            self.assertIn("marker-123", text)


if __name__ == "__main__":
    unittest.main()
