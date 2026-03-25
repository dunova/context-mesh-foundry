#!/usr/bin/env python3
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

import context_native


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
            with mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", cache_path):
                with mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30):
                    context_native._store_health_cache(payload)
                    cached = context_native._load_health_cache()
            self.assertEqual(cached, payload)

    def test_health_payload_uses_cache(self) -> None:
        cached = {"available_backends": ["go"], "go": {"ok": True}}
        with mock.patch.object(context_native, "_load_health_cache", return_value=cached):
            with mock.patch.object(context_native, "run_native_scan") as mock_run:
                payload = context_native.health_payload(probe=True)
        self.assertEqual(payload, cached)
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
