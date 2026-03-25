#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys
import json
import os

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_cli


class ContextCliTests(unittest.TestCase):
    def test_parse_health_payload_skips_prefix(self) -> None:
        raw = 'Indexed 3 sessions in 0.5s\n{"recall_db_exists": true, "total_sessions": 1}'
        payload = context_cli._parse_health_payload(raw)
        self.assertTrue(payload["recall_db_exists"])
        self.assertEqual(payload["total_sessions"], 1)

    def test_save_then_local_match_immediate_readback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "resources" / "shared"
            conv = root / "conversations"
            with mock.patch.object(context_cli, "LOCAL_STORAGE_ROOT", Path(tmpdir)):
                with mock.patch.object(context_cli, "LOCAL_SHARED_ROOT", root):
                    with mock.patch.object(context_cli, "LOCAL_CONVERSATIONS_ROOT", conv):
                        msg = context_cli._save_local_memory(
                            "unit-test-memory",
                            "unique_token_context_cli_unit",
                            ["unit", "memory"],
                        )
                        self.assertIn("Saved locally:", msg)
                        matches = context_cli._local_memory_matches("unique_token_context_cli_unit", limit=3)
                        self.assertEqual(len(matches), 1)
                        self.assertEqual(matches[0]["matched_in"], "content")

    def test_semantic_falls_back_to_recall_content(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "foo", "--limit", "2"])
        with mock.patch.object(context_cli, "_local_memory_matches", return_value=[]):
            with mock.patch.object(
                context_cli,
                "_run_recall",
                return_value=(0, "Found 1 sessions\nSession: abc", ""),
            ):
                with mock.patch("builtins.print") as mock_print:
                    rc = context_cli.run(args)
        self.assertEqual(rc, 0)
        printed = "\n".join(" ".join(str(x) for x in call.args) for call in mock_print.call_args_list)
        self.assertIn("HISTORY CONTENT FALLBACK", printed)
        self.assertIn("Session: abc", printed)

    def test_export_then_import_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            output_path = tmp_root / "export.json"
            env_key = "UNIFIED_CONTEXT_STORAGE_ROOT"
            old_env = os.environ.get(env_key)
            os.environ[env_key] = str(tmp_root)
            try:
                with mock.patch.object(context_cli, "LOCAL_STORAGE_ROOT", tmp_root):
                    with mock.patch.object(context_cli, "LOCAL_SHARED_ROOT", tmp_root / "resources" / "shared"):
                        with mock.patch.object(
                            context_cli,
                            "LOCAL_CONVERSATIONS_ROOT",
                            tmp_root / "resources" / "shared" / "conversations",
                        ):
                            msg = context_cli._save_local_memory("roundtrip", "roundtrip_token_cli", ["rt"])
                            self.assertIn("Saved locally:", msg)
                            export_args = context_cli.build_parser().parse_args(
                                ["export", "roundtrip_token_cli", str(output_path)]
                            )
                            self.assertEqual(context_cli.run(export_args), 0)
                            payload = json.loads(output_path.read_text(encoding="utf-8"))
                            self.assertEqual(payload["total_observations"], 1)
                            import_args = context_cli.build_parser().parse_args(
                                ["import", str(output_path)]
                            )
                            with mock.patch("builtins.print") as mock_print:
                                self.assertEqual(context_cli.run(import_args), 0)
                            printed = "\n".join(
                                " ".join(str(x) for x in call.args) for call in mock_print.call_args_list
                            )
                            self.assertIn("inserted=0", printed)
            finally:
                if old_env is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = old_env


if __name__ == "__main__":
    unittest.main()
