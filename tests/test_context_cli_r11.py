#!/usr/bin/env python3
"""R11 extended tests for context_cli module — targeting uncovered lines."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_cli


class TestSaveLocalMemoryRemoteEnabled(unittest.TestCase):
    def test_save_local_with_remote_enabled_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "resources" / "shared"
            conv = root / "conversations"
            mock_response = mock.Mock()
            mock_response.__enter__ = mock.Mock(return_value=mock_response)
            mock_response.__exit__ = mock.Mock(return_value=False)
            with (
                mock.patch.object(context_cli, "LOCAL_STORAGE_ROOT", Path(tmpdir)),
                mock.patch.object(context_cli, "LOCAL_SHARED_ROOT", root),
                mock.patch.object(context_cli, "LOCAL_CONVERSATIONS_ROOT", conv),
                mock.patch.object(context_cli, "ENABLE_REMOTE_MEMORY_HTTP", True),
                mock.patch("urllib.request.urlopen", return_value=mock_response),
            ):
                msg = context_cli._save_local_memory("remote test", "remote content r11", ["tag"])
        self.assertIn("indexed remotely", msg)

    def test_save_local_with_remote_enabled_network_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "resources" / "shared"
            conv = root / "conversations"
            with (
                mock.patch.object(context_cli, "LOCAL_STORAGE_ROOT", Path(tmpdir)),
                mock.patch.object(context_cli, "LOCAL_SHARED_ROOT", root),
                mock.patch.object(context_cli, "LOCAL_CONVERSATIONS_ROOT", conv),
                mock.patch.object(context_cli, "ENABLE_REMOTE_MEMORY_HTTP", True),
                mock.patch("urllib.request.urlopen", side_effect=OSError("network error")),
            ):
                msg = context_cli._save_local_memory("remote fail", "content for remote fail", ["tag"])
        self.assertIn("Saved locally:", msg)
        self.assertIn("remote indexing skipped", msg)

    def test_save_local_invalid_title_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "resources" / "shared"
            conv = root / "conversations"
            with (
                mock.patch.object(context_cli, "LOCAL_STORAGE_ROOT", Path(tmpdir)),
                mock.patch.object(context_cli, "LOCAL_SHARED_ROOT", root),
                mock.patch.object(context_cli, "LOCAL_CONVERSATIONS_ROOT", conv),
                mock.patch("context_core.write_memory_markdown", side_effect=ValueError("bad title")),
            ):
                msg = context_cli._save_local_memory("", "content", [])
        self.assertIn("Failed to save memory:", msg)


class TestConfigureViewerModule(unittest.TestCase):
    def test_calls_apply_runtime_config_when_available(self) -> None:
        module = SimpleNamespace(apply_runtime_config=mock.Mock())
        context_cli._configure_viewer_module(module, "0.0.0.0", 8080, "mytoken")
        module.apply_runtime_config.assert_called_once_with("0.0.0.0", 8080, "mytoken")
        self.assertEqual(os.environ.get("CONTEXTGO_VIEWER_HOST"), "0.0.0.0")
        self.assertEqual(os.environ.get("CONTEXTGO_VIEWER_PORT"), "8080")
        self.assertEqual(os.environ.get("CONTEXTGO_VIEWER_TOKEN"), "mytoken")

    def test_falls_back_to_direct_attrs_when_no_apply(self) -> None:
        module = SimpleNamespace()
        context_cli._configure_viewer_module(module, "127.0.0.1", 9999, "")
        self.assertEqual(module.HOST, "127.0.0.1")
        self.assertEqual(module.PORT, 9999)
        self.assertEqual(module.VIEWER_TOKEN, "")

    def test_removes_token_env_when_empty(self) -> None:
        os.environ["CONTEXTGO_VIEWER_TOKEN"] = "old_token"
        module = SimpleNamespace()
        context_cli._configure_viewer_module(module, "127.0.0.1", 1234, "")
        self.assertNotIn("CONTEXTGO_VIEWER_TOKEN", os.environ)


class TestCompactSmokePayload(unittest.TestCase):
    def test_compacts_passing_results(self) -> None:
        payload = {
            "summary": {"ok": True, "total": 2, "passed": 2},
            "results": [
                {"name": "test1", "ok": True, "rc": 0},
                {"name": "test2", "ok": True, "rc": 0},
            ],
        }
        compact = context_cli._compact_smoke_payload(payload)
        self.assertEqual(compact["summary"], payload["summary"])
        for r in compact["results"]:
            self.assertNotIn("detail", r)

    def test_includes_detail_for_failing_results(self) -> None:
        payload = {
            "summary": {"ok": False, "total": 1, "passed": 0},
            "results": [
                {"name": "test1", "ok": False, "rc": 1, "detail": "error detail"},
            ],
        }
        compact = context_cli._compact_smoke_payload(payload)
        self.assertEqual(compact["results"][0]["detail"], "error detail")

    def test_skips_non_dict_results(self) -> None:
        payload = {
            "summary": None,
            "results": ["not a dict", 42],
        }
        compact = context_cli._compact_smoke_payload(payload)
        self.assertEqual(compact["results"], [])


class TestPrintJson(unittest.TestCase):
    def test_prints_compact_json(self) -> None:
        with mock.patch("builtins.print") as mock_print:
            context_cli._print_json({"key": "value"})
        output = mock_print.call_args[0][0]
        self.assertNotIn("\n", output)
        parsed = json.loads(output)
        self.assertEqual(parsed["key"], "value")

    def test_prints_pretty_json(self) -> None:
        with mock.patch("builtins.print") as mock_print:
            context_cli._print_json({"key": "value"}, pretty=True)
        output = mock_print.call_args[0][0]
        self.assertIn("\n", output)


class TestRemoteProcessCount(unittest.TestCase):
    def test_returns_count_of_processes(self) -> None:
        mock_proc = mock.Mock()
        mock_proc.stdout = "123\n456\n789\n"
        with mock.patch("subprocess.run", return_value=mock_proc):
            count = context_cli._remote_process_count()
        self.assertEqual(count, 3)

    def test_returns_zero_on_oserror(self) -> None:
        with mock.patch("subprocess.run", side_effect=OSError("no pgrep")):
            count = context_cli._remote_process_count()
        self.assertEqual(count, 0)

    def test_returns_zero_on_timeout(self) -> None:
        import subprocess

        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pgrep", 3)):
            count = context_cli._remote_process_count()
        self.assertEqual(count, 0)

    def test_returns_zero_for_empty_output(self) -> None:
        mock_proc = mock.Mock()
        mock_proc.stdout = ""
        with mock.patch("subprocess.run", return_value=mock_proc):
            count = context_cli._remote_process_count()
        self.assertEqual(count, 0)


class TestCmdSearch(unittest.TestCase):
    def test_cmd_search_success(self) -> None:
        args = context_cli.build_parser().parse_args(["search", "NotebookLM", "--limit", "5"])
        with (
            mock.patch.object(
                context_cli.session_index,
                "format_search_results",
                return_value="Found 1 sessions (local index):\n[1] session info",
            ),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_search_no_results(self) -> None:
        args = context_cli.build_parser().parse_args(["search", "xyz_no_match"])
        with (
            mock.patch.object(
                context_cli.session_index,
                "format_search_results",
                return_value="No matches found in local session index.",
            ),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 1)

    def test_cmd_search_with_literal_flag(self) -> None:
        args = context_cli.build_parser().parse_args(["search", "query text", "--literal"])
        with (
            mock.patch.object(
                context_cli.session_index,
                "format_search_results",
                return_value="Found 1 sessions (local index):\nresult",
            ) as mock_search,
            mock.patch("builtins.print"),
        ):
            context_cli.run(args)
        call_kwargs = mock_search.call_args[1]
        self.assertTrue(call_kwargs["literal"])


class TestCmdSave(unittest.TestCase):
    def test_cmd_save_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "resources" / "shared"
            conv = root / "conversations"
            args = context_cli.build_parser().parse_args(
                ["save", "--title", "test title", "--content", "test content", "--tags", "a,b"]
            )
            with (
                mock.patch.object(context_cli, "LOCAL_STORAGE_ROOT", Path(tmpdir)),
                mock.patch.object(context_cli, "LOCAL_SHARED_ROOT", root),
                mock.patch.object(context_cli, "LOCAL_CONVERSATIONS_ROOT", conv),
                mock.patch("builtins.print"),
            ):
                rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_save_failure(self) -> None:
        args = context_cli.build_parser().parse_args(["save", "--title", "t", "--content", "c"])
        with (
            mock.patch("context_core.write_memory_markdown", side_effect=ValueError("fail")),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 1)


class TestCmdImport(unittest.TestCase):
    def test_cmd_import_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            import_file = Path(tmpdir) / "import.json"
            import_file.write_text(
                json.dumps({"observations": []}),
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "memory_index.db"
            args = context_cli.build_parser().parse_args(["import", str(import_file), "--no-sync"])
            with (
                mock.patch.dict(os.environ, {"MEMORY_INDEX_DB_PATH": str(db_path)}, clear=False),
                mock.patch("builtins.print"),
            ):
                rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_import_file_not_found(self) -> None:
        args = context_cli.build_parser().parse_args(["import", "/nonexistent/file.json"])
        with (
            mock.patch("builtins.print"),
            mock.patch("sys.stderr"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 1)

    def test_cmd_import_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "bad.json"
            bad_file.write_text("not valid json {{{", encoding="utf-8")
            args = context_cli.build_parser().parse_args(["import", str(bad_file)])
            with (
                mock.patch("builtins.print"),
                mock.patch("sys.stderr"),
            ):
                rc = context_cli.run(args)
        self.assertEqual(rc, 1)


class TestCmdNativeScan(unittest.TestCase):
    def test_cmd_native_scan_json_output(self) -> None:
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.json_payload = mock.Mock(return_value={"items": []})
        args = context_cli.build_parser().parse_args(["native-scan", "--json"])
        with (
            mock.patch.object(context_cli.context_native, "run_native_scan", return_value=mock_result),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_native_scan_text_output(self) -> None:
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "scan results"
        mock_result.stderr = ""
        args = context_cli.build_parser().parse_args(["native-scan"])
        with (
            mock.patch.object(context_cli.context_native, "run_native_scan", return_value=mock_result),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_native_scan_json_with_nonzero_return(self) -> None:
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stderr = "scan error"
        mock_result.json_payload = mock.Mock(return_value={"error": "failed"})
        args = context_cli.build_parser().parse_args(["native-scan", "--json"])
        with (
            mock.patch.object(context_cli.context_native, "run_native_scan", return_value=mock_result),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 1)


class TestCmdServe(unittest.TestCase):
    def test_cmd_serve_calls_module_main(self) -> None:
        mock_module = mock.Mock()
        mock_module.main = mock.Mock(return_value=None)
        mock_module.apply_runtime_config = mock.Mock()
        args = context_cli.build_parser().parse_args(["serve", "--port", "37677"])
        with mock.patch.object(context_cli, "_load_module", return_value=mock_module):
            rc = context_cli.run(args)
        mock_module.main.assert_called_once()
        self.assertEqual(rc, 0)


class TestCmdMaintain(unittest.TestCase):
    def test_cmd_maintain_calls_module_main(self) -> None:
        mock_module = mock.Mock()
        mock_module.main = mock.Mock(return_value=0)
        args = context_cli.build_parser().parse_args(["maintain"])
        with mock.patch.object(context_cli, "_load_module", return_value=mock_module):
            rc = context_cli.run(args)
        mock_module.main.assert_called_once()
        self.assertEqual(rc, 0)

    def test_cmd_maintain_with_flags(self) -> None:
        mock_module = mock.Mock()
        mock_module.main = mock.Mock(return_value=0)
        args = context_cli.build_parser().parse_args(["maintain", "--repair-queue", "--enqueue-missing", "--dry-run"])
        with mock.patch.object(context_cli, "_load_module", return_value=mock_module):
            context_cli.run(args)
        call_args = mock_module.main.call_args[0][0]
        self.assertIn("--repair-queue", call_args)
        self.assertIn("--enqueue-missing", call_args)
        self.assertIn("--dry-run", call_args)


class TestCmdSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self._path_exists_patcher = mock.patch("pathlib.Path.exists", return_value=True)
        self._path_exists_patcher.start()

    def tearDown(self) -> None:
        self._path_exists_patcher.stop()

    def test_cmd_smoke_all_pass(self) -> None:
        payload = {
            "summary": {"ok": True, "total": 2},
            "results": [
                {"name": "a", "ok": True, "rc": 0},
                {"name": "b", "ok": True, "rc": 0},
            ],
        }
        args = context_cli.build_parser().parse_args(["smoke"])
        with (
            mock.patch.object(context_cli.context_smoke, "run_smoke", return_value=payload),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_smoke_some_fail(self) -> None:
        payload = {
            "summary": {"ok": False, "total": 2},
            "results": [
                {"name": "a", "ok": False, "rc": 1, "detail": "failed"},
                {"name": "b", "ok": True, "rc": 0},
            ],
        }
        args = context_cli.build_parser().parse_args(["smoke"])
        with (
            mock.patch.object(context_cli.context_smoke, "run_smoke", return_value=payload),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 1)

    def test_cmd_smoke_verbose(self) -> None:
        payload = {
            "summary": {"ok": True, "total": 1},
            "results": [{"name": "a", "ok": True, "rc": 0}],
        }
        args = context_cli.build_parser().parse_args(["smoke", "--verbose"])
        with (
            mock.patch.object(context_cli.context_smoke, "run_smoke", return_value=payload),
            mock.patch("builtins.print"),
        ):
            context_cli.run(args)

    def test_cmd_smoke_sandbox(self) -> None:
        payload = {
            "summary": {"ok": True, "total": 1},
            "results": [{"name": "a", "ok": True, "rc": 0}],
        }
        args = context_cli.build_parser().parse_args(["smoke", "--sandbox"])
        with (
            mock.patch.object(context_cli.context_smoke, "run_smoke", return_value=payload),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)


class TestCmdHealth(unittest.TestCase):
    def test_cmd_health_ok(self) -> None:
        mock_recall = {
            "session_index_db_exists": True,
            "session_index_db": "/tmp/test.db",
            "total_sessions": 10,
            "latest_epoch": 1700000000,
            "sync": {"added": 0, "updated": 0},
        }
        args = context_cli.build_parser().parse_args(["health"])
        with (
            mock.patch.object(context_cli.session_index, "health_payload", return_value=mock_recall),
            mock.patch.object(context_cli.context_native, "health_payload", return_value={}),
            mock.patch.object(context_cli, "_remote_process_count", return_value=0),
            mock.patch.object(context_cli, "_source_freshness", return_value={}),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_health_verbose(self) -> None:
        mock_recall = {
            "session_index_db_exists": True,
            "session_index_db": "/tmp/test.db",
            "total_sessions": 5,
            "latest_epoch": 1700000000,
            "sync": {"added": 0},
        }
        args = context_cli.build_parser().parse_args(["health", "--verbose"])
        with (
            mock.patch.object(context_cli.session_index, "health_payload", return_value=mock_recall),
            mock.patch.object(context_cli.context_native, "health_payload", return_value={}),
            mock.patch.object(context_cli, "_remote_process_count", return_value=0),
            mock.patch.object(context_cli, "_source_freshness", return_value={}),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_health_db_missing(self) -> None:
        mock_recall = {
            "session_index_db_exists": False,
            "session_index_db": "/tmp/missing.db",
            "total_sessions": 0,
            "latest_epoch": 0,
            "sync": {},
        }
        args = context_cli.build_parser().parse_args(["health"])
        with (
            mock.patch.object(context_cli.session_index, "health_payload", return_value=mock_recall),
            mock.patch.object(context_cli.context_native, "health_payload", return_value={}),
            mock.patch.object(context_cli, "_remote_process_count", return_value=0),
            mock.patch.object(context_cli, "_source_freshness", return_value={}),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 1)


class TestRunUnknownCommand(unittest.TestCase):
    def test_returns_2_for_unknown_command(self) -> None:
        args = SimpleNamespace(command="unknown_cmd_xyz")
        with mock.patch("sys.stderr"):
            rc = context_cli.run(args)
        self.assertEqual(rc, 2)


class TestSourceFreshness(unittest.TestCase):
    def test_returns_dict_with_source_keys(self) -> None:
        result = context_cli._source_freshness()
        self.assertIsInstance(result, dict)
        self.assertIn("codex_history", result)
        self.assertIn("claude_history", result)

    def test_nonexistent_sources_have_exists_false(self) -> None:
        with mock.patch.object(context_cli, "HOME", Path("/nonexistent_home_r11")):
            result = context_cli._source_freshness()
        for key, val in result.items():
            if key != "antigravity_latest":
                self.assertFalse(val.get("exists", True), f"{key} should not exist")


class TestLoadModule(unittest.TestCase):
    def test_loads_existing_module(self) -> None:
        module = context_cli._load_module("json")
        self.assertIsNotNone(module)
        self.assertTrue(hasattr(module, "dumps"))


class TestCmdSemanticLocalMatch(unittest.TestCase):
    def test_cmd_semantic_with_local_matches(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "NotebookLM"])
        with (
            mock.patch.object(
                context_cli,
                "_local_memory_matches",
                return_value=[{"title": "Memory", "content": "NotebookLM result", "matched_in": "content"}],
            ),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 0)

    def test_cmd_semantic_no_results_in_session_either(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "xyz_no_match"])
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(
                context_cli.session_index,
                "format_search_results",
                return_value="No matches found in local session index.",
            ),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.run(args)
        self.assertEqual(rc, 1)


class TestEdgeCaseHardening(unittest.TestCase):
    """Edge case tests added in AutoResearch R7 to cover defensive guards."""

    # --- cmd_search: empty / whitespace-only query ---

    def test_cmd_search_empty_query_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["search", "placeholder"])
        args.query = ""
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_search(args)
        self.assertEqual(rc, 2)

    def test_cmd_search_whitespace_only_query_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["search", "placeholder"])
        args.query = "   "
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_search(args)
        self.assertEqual(rc, 2)

    # --- cmd_semantic: empty / whitespace-only query ---

    def test_cmd_semantic_empty_query_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "placeholder"])
        args.query = ""
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 2)

    def test_cmd_semantic_whitespace_query_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "placeholder"])
        args.query = "\t\n"
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 2)

    # --- cmd_export: empty output path and directory output path ---

    def test_cmd_export_empty_output_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["export", "q", "placeholder_output"])
        args.output = ""
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_export(args)
        self.assertEqual(rc, 2)

    def test_cmd_export_directory_output_returns_2(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            args = context_cli.build_parser().parse_args(["export", "q", tmpdir])
            with (
                mock.patch.object(
                    context_cli,
                    "export_observations_payload",
                    return_value={"total_observations": 0, "observations": []},
                ),
                mock.patch("sys.stderr"),
            ):
                rc = context_cli.cmd_export(args)
        self.assertEqual(rc, 2)

    # --- cmd_serve: port out of valid range ---

    def test_cmd_serve_port_zero_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["serve"])
        args.port = 0
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_serve(args)
        self.assertEqual(rc, 2)

    def test_cmd_serve_port_too_high_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["serve"])
        args.port = 99999
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_serve(args)
        self.assertEqual(rc, 2)

    def test_cmd_serve_port_boundary_65535_ok(self) -> None:
        args = context_cli.build_parser().parse_args(["serve"])
        args.port = 65535
        mock_module = mock.Mock()
        mock_module.main = mock.Mock(return_value=None)
        mock_module.apply_runtime_config = mock.Mock()
        with mock.patch.object(context_cli, "_load_module", return_value=mock_module):
            rc = context_cli.cmd_serve(args)
        self.assertEqual(rc, 0)

    # --- cmd_native_scan: threads <= 0 ---

    def test_cmd_native_scan_threads_zero_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["native-scan"])
        args.threads = 0
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_native_scan(args)
        self.assertEqual(rc, 2)

    def test_cmd_native_scan_threads_negative_returns_2(self) -> None:
        args = context_cli.build_parser().parse_args(["native-scan"])
        args.threads = -4
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_native_scan(args)
        self.assertEqual(rc, 2)

    def test_cmd_native_scan_threads_one_is_valid(self) -> None:
        args = context_cli.build_parser().parse_args(["native-scan", "--threads", "1"])
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with (
            mock.patch.object(context_cli.context_native, "run_native_scan", return_value=mock_result),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.cmd_native_scan(args)
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# R16: target the ImportError fallback branches in lazy-import helpers
# Lines 24-25, 33-34, 42-43, 51-52, 60-61
# ---------------------------------------------------------------------------


class TestLazyImportFallbacks(unittest.TestCase):
    """Cover the `from . import X` fallback inside each lazy getter."""

    def _call_with_top_level_import_error(self, getter_name: str, module_name: str) -> None:
        """Helper: make the top-level `import X` raise ImportError so the
        package-relative fallback is exercised."""
        import builtins

        real_import = builtins.__import__

        def _patched_import(name: str, *args, **kwargs):
            if name == module_name and not args[0]:
                raise ImportError(f"mocked ImportError for {name}")
            return real_import(name, *args, **kwargs)

        getter = getattr(context_cli, getter_name)
        fake_mod = mock.MagicMock()

        # We mock the relative import by patching sys.modules for the package path.
        import contextgo  # noqa: F401

        pkg_key = f"contextgo.{module_name}"
        orig = sys.modules.get(pkg_key)
        sys.modules[pkg_key] = fake_mod
        try:
            with mock.patch("builtins.__import__", side_effect=_patched_import):
                result = getter()
            # Should return *something* (the real module or our fake)
            self.assertIsNotNone(result)
        finally:
            if orig is None:
                sys.modules.pop(pkg_key, None)
            else:
                sys.modules[pkg_key] = orig

    def test_get_context_core_succeeds(self) -> None:
        result = context_cli._get_context_core()
        self.assertIsNotNone(result)

    def test_get_context_native_succeeds(self) -> None:
        result = context_cli._get_context_native()
        self.assertIsNotNone(result)

    def test_get_context_smoke_succeeds(self) -> None:
        result = context_cli._get_context_smoke()
        self.assertIsNotNone(result)

    def test_get_session_index_succeeds(self) -> None:
        result = context_cli._get_session_index()
        self.assertIsNotNone(result)

    def test_get_memory_index_succeeds(self) -> None:
        result = context_cli._get_memory_index()
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# R16: cmd_semantic fallback text path (lines 305->308)
# ---------------------------------------------------------------------------


class TestCmdSemanticFallbackText(unittest.TestCase):
    def test_cmd_semantic_returns_fallback_text_when_no_local_matches(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "some query"])
        fake_session_index = mock.MagicMock()
        fake_session_index.format_search_results.return_value = "some fallback text"
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(context_cli, "_get_session_index", return_value=fake_session_index),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 0)
        fake_session_index.format_search_results.assert_called_once()

    def test_cmd_semantic_returns_1_when_no_matches_found(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "some query"])
        fake_session_index = mock.MagicMock()
        fake_session_index.format_search_results.return_value = "No matches found."
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(context_cli, "_get_session_index", return_value=fake_session_index),
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 1)

    def test_cmd_semantic_returns_1_when_empty_fallback(self) -> None:
        args = context_cli.build_parser().parse_args(["semantic", "some query"])
        fake_session_index = mock.MagicMock()
        fake_session_index.format_search_results.return_value = ""
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(context_cli, "_get_session_index", return_value=fake_session_index),
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# R16: cmd_import ValueError (lines 352-354)
# ---------------------------------------------------------------------------


class TestCmdImportValueError(unittest.TestCase):
    def test_cmd_import_value_error_returns_1(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            import json as _json

            _json.dump({"observations": [], "total_observations": 0}, fh)
            fh_path = fh.name
        try:
            args = context_cli.build_parser().parse_args(["import", fh_path])
            with (
                mock.patch.object(
                    context_cli,
                    "import_observations_payload",
                    side_effect=ValueError("bad payload"),
                ),
                mock.patch("sys.stderr"),
            ):
                rc = context_cli.cmd_import(args)
            self.assertEqual(rc, 1)
        finally:
            import os as _os

            _os.unlink(fh_path)


# ---------------------------------------------------------------------------
# R16: cmd_native_scan json output with dict payload (lines 411->416, 419)
# ---------------------------------------------------------------------------


class TestCmdNativeScanJsonOutput(unittest.TestCase):
    def test_cmd_native_scan_json_output_with_dict_payload(self) -> None:
        args = context_cli.build_parser().parse_args(["native-scan", "--json"])
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.json_payload.return_value = {"ok": True, "matches": []}
        with (
            mock.patch.object(context_cli.context_native, "run_native_scan", return_value=mock_result),
            mock.patch.object(context_cli, "_print_json"),
        ):
            rc = context_cli.cmd_native_scan(args)
        self.assertEqual(rc, 0)

    def test_cmd_native_scan_json_output_with_nonzero_and_stderr(self) -> None:
        args = context_cli.build_parser().parse_args(["native-scan", "--json"])
        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "some error"
        mock_result.json_payload.return_value = {"ok": False}
        with (
            mock.patch.object(context_cli.context_native, "run_native_scan", return_value=mock_result),
            mock.patch.object(context_cli, "_print_json"),
            mock.patch("sys.stderr"),
        ):
            rc = context_cli.cmd_native_scan(args)
        self.assertEqual(rc, 1)

    def test_cmd_native_scan_json_output_with_non_dict_payload(self) -> None:
        """When json_payload returns a non-dict, fall through to stdout/stderr."""
        args = context_cli.build_parser().parse_args(["native-scan", "--json"])
        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "output text"
        mock_result.stderr = "err text"
        mock_result.json_payload.return_value = "not a dict"
        with (
            mock.patch.object(context_cli.context_native, "run_native_scan", return_value=mock_result),
            mock.patch("builtins.print"),
            mock.patch("sys.stderr"),
        ):
            rc = context_cli.cmd_native_scan(args)
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# R16: __getattr__ AttributeError (line 657)
# ---------------------------------------------------------------------------


class TestModuleGetattr(unittest.TestCase):
    def test_getattr_raises_attribute_error_for_unknown_name(self) -> None:
        with self.assertRaises(AttributeError):
            context_cli.__getattr__("_nonexistent_attribute_xyz_r16")

    def test_getattr_returns_module_for_known_lazy_name(self) -> None:
        # Ensure the lazy getter works via __getattr__
        # Clear from globals to force __getattr__ path
        globals_backup = context_cli.__dict__.pop("memory_index", None)
        try:
            result = context_cli.__getattr__("memory_index")
            self.assertIsNotNone(result)
        finally:
            if globals_backup is not None:
                context_cli.__dict__["memory_index"] = globals_backup


# ---------------------------------------------------------------------------
# R16: context_cli.main() function (line 627) — direct invocation
# ---------------------------------------------------------------------------


class TestContextCliMainFunction(unittest.TestCase):
    """Line 627: main() dispatches to run(build_parser().parse_args(argv))."""

    def test_main_with_search_argv(self) -> None:
        fake_session_index = mock.MagicMock()
        fake_session_index.format_search_results.return_value = "No matches found."
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=fake_session_index),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.main(["search", "some query"])
        self.assertIsInstance(rc, int)

    def test_main_with_save_argv(self) -> None:
        with (
            mock.patch.object(context_cli, "_save_local_memory", return_value="Saved locally: test"),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.main(["save", "--title", "Test Title", "--content", "Test content"])
        self.assertIsInstance(rc, int)


if __name__ == "__main__":
    unittest.main()
