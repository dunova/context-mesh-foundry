#!/usr/bin/env python3
"""Unit tests for context_native module."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_native  # noqa: E402


class ContextNativeTests(unittest.TestCase):
    def test_json_payload_falls_back_to_embedded_json_object(self) -> None:
        result = context_native.NativeRunResult(
            backend="go",
            returncode=0,
            stdout='warning: noisy prefix\n{"matches": [], "query": "x"}\ntrailing note',
            stderr="",
            command=["go", "run"],
        )
        payload = result.json_payload()
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["query"], "x")

    def test_json_payload_records_parse_error_when_invalid(self) -> None:
        result = context_native.NativeRunResult(
            backend="go",
            returncode=0,
            stdout="not json at all",
            stderr="",
            command=["go", "run"],
        )
        payload = result.json_payload()
        self.assertIsNone(payload)
        self.assertTrue(result.error_details())

    def test_health_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "native_health_cache.json"
            payload = {"available_backends": ["go"], "go": {"ok": True}}
            with (
                mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", cache_path),
                mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            ):
                context_native._store_health_cache(payload)
                cached = context_native._load_health_cache()
            self.assertEqual(cached, payload)

    def test_health_payload_uses_cache(self) -> None:
        cached = {"available_backends": ["go"], "go": {"ok": True}}
        with (
            mock.patch.object(context_native, "_load_health_cache", return_value=cached),
            mock.patch.object(context_native, "run_native_scan") as mock_run,
        ):
            payload = context_native.health_payload(probe=True)
        self.assertEqual(payload, cached)
        mock_run.assert_not_called()

    def test_build_commands_export_active_workdir(self) -> None:
        with mock.patch("pathlib.Path.cwd", return_value=Path("/tmp/contextgo-active")):
            rust_cmd, rust_cwd, rust_env = context_native._build_rust_cmd(
                codex_root=None,
                claude_root=None,
                threads=2,
                release=False,
                query="NotebookLM",
                json_output=True,
                limit=3,
            )
            go_cmd, go_cwd, go_env = context_native._build_go_cmd(
                codex_root=None,
                claude_root=None,
                threads=2,
                query="NotebookLM",
                json_output=True,
                limit=3,
            )
        self.assertIn("CONTEXTGO_ACTIVE_WORKDIR", rust_env)
        self.assertEqual(rust_env["CONTEXTGO_ACTIVE_WORKDIR"], "/tmp/contextgo-active")
        self.assertIn("CONTEXTGO_ACTIVE_WORKDIR", go_env)
        self.assertEqual(go_env["CONTEXTGO_ACTIVE_WORKDIR"], "/tmp/contextgo-active")
        self.assertIn("--query", rust_cmd)
        self.assertIn("--query", go_cmd)


if __name__ == "__main__":
    unittest.main()
