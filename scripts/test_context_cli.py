#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys
import json
import os
import importlib

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_cli


class ContextCliTests(unittest.TestCase):
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

    def test_semantic_falls_back_to_session_index(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "foo", "--limit", "2"])
        with mock.patch.object(context_cli, "_local_memory_matches", return_value=[]):
            with mock.patch.object(
                context_cli.session_index,
                "format_search_results",
                return_value="Found 1 sessions\nSession: abc",
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

    def test_serve_subcommand_delegates_to_viewer(self) -> None:
        args = context_cli.build_parser().parse_args(["serve", "--host", "127.0.0.1", "--port", "40001"])
        viewer = mock.Mock()
        viewer.main.return_value = None
        viewer.apply_runtime_config = mock.Mock()
        with mock.patch.object(context_cli, "_load_memory_viewer", return_value=viewer):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)
        viewer.apply_runtime_config.assert_called_once()
        viewer.main.assert_called_once()

    def test_maintain_subcommand_delegates(self) -> None:
        args = context_cli.build_parser().parse_args(
            ["maintain", "--repair-queue", "--enqueue-missing", "--dry-run"]
        )
        maintenance = mock.Mock()
        maintenance.main.return_value = 0
        with mock.patch.object(context_cli, "_load_context_maintenance", return_value=maintenance):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)
        forwarded = maintenance.main.call_args.args[0]
        self.assertIn("--repair-queue", forwarded)
        self.assertIn("--enqueue-missing", forwarded)
        self.assertIn("--dry-run", forwarded)

    def test_native_scan_subcommand_delegates(self) -> None:
        args = context_cli.build_parser().parse_args(
            ["native-scan", "--backend", "go", "--threads", "2", "--debug-build"]
        )
        result = mock.Mock()
        result.returncode = 0
        result.stdout = "native ok\n"
        result.stderr = ""
        with mock.patch.object(context_cli.context_native, "run_native_scan", return_value=result) as mock_run:
            with mock.patch("builtins.print") as mock_print:
                rc = context_cli.run(args)
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with(
            backend="go",
            codex_root=None,
            claude_root=None,
            threads=2,
            release=False,
            query=None,
            json_output=False,
            limit=None,
        )
        printed = "\n".join(" ".join(str(x) for x in call.args) for call in mock_print.call_args_list)
        self.assertIn("native ok", printed)

    def test_smoke_subcommand_delegates(self) -> None:
        args = context_cli.build_parser().parse_args(["smoke"])
        payload = {"results": [{"name": "health", "ok": True}]}
        with mock.patch.object(context_cli.context_smoke, "run_smoke", return_value=payload) as mock_run:
            with mock.patch("builtins.print") as mock_print:
                rc = context_cli.run(args)
        self.assertEqual(rc, 0)
        mock_run.assert_called_once()
        printed = "\n".join(" ".join(str(x) for x in call.args) for call in mock_print.call_args_list)
        self.assertIn('"health"', printed)

    def test_package_import_context_cli(self) -> None:
        sys.path.insert(0, str(SCRIPT_DIR.parent))
        try:
            mod = importlib.import_module("scripts.context_cli")
            self.assertTrue(callable(mod.main))
        finally:
            if str(SCRIPT_DIR.parent) in sys.path:
                sys.path.remove(str(SCRIPT_DIR.parent))

    def test_configure_viewer_module_prefers_apply_runtime_config(self) -> None:
        class Viewer:
            def __init__(self) -> None:
                self.calls: list[tuple[str, int, str]] = []

            def apply_runtime_config(self, host: str, port: int, token: str) -> None:
                self.calls.append((host, port, token))

        viewer = Viewer()
        saved = {
            key: os.environ.get(key)
            for key in ("CONTEXT_VIEWER_HOST", "CONTEXT_VIEWER_PORT", "CONTEXT_VIEWER_TOKEN")
        }
        try:
            context_cli._configure_viewer_module(viewer, "0.0.0.0", 1234, " secret ")
            self.assertEqual(viewer.calls, [("0.0.0.0", 1234, "secret")])
            self.assertEqual(os.environ["CONTEXT_VIEWER_HOST"], "0.0.0.0")
            self.assertEqual(os.environ["CONTEXT_VIEWER_PORT"], "1234")
            self.assertEqual(os.environ["CONTEXT_VIEWER_TOKEN"], "secret")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_configure_viewer_module_sets_attrs_without_runtime_config(self) -> None:
        class Viewer:
            pass

        viewer = Viewer()
        saved = {
            key: os.environ.get(key)
            for key in ("CONTEXT_VIEWER_HOST", "CONTEXT_VIEWER_PORT", "CONTEXT_VIEWER_TOKEN")
        }
        try:
            context_cli._configure_viewer_module(viewer, "1.2.3.4", 5678, "")
            self.assertEqual(viewer.HOST, "1.2.3.4")
            self.assertEqual(viewer.PORT, 5678)
            self.assertEqual(viewer.VIEWER_TOKEN, "")
            self.assertEqual(os.environ["CONTEXT_VIEWER_HOST"], "1.2.3.4")
            self.assertEqual(os.environ["CONTEXT_VIEWER_PORT"], "5678")
            self.assertFalse(os.environ.get("CONTEXT_VIEWER_TOKEN"))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
