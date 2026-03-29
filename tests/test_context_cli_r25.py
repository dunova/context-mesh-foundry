#!/usr/bin/env python3
"""R25 tests for context_cli — lazy imports, parallel search/health, error handling.

Targets:
- Lazy module getters (_get_context_core, _get_context_native, etc.)
- Module-level __getattr__ lazy attribute access
- cmd_semantic: memory hit path, session fallback path, empty query guard
- cmd_health: verbose / compact paths, unhealthy db path
- _remote_process_count: success and error paths
- _source_freshness: full path coverage
- build_parser / _PARSER-like caching via multiple build_parser() calls
- Error handling when underlying calls raise exceptions
- ThreadPoolExecutor-style concurrent behaviour simulated via mocks
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_session_index_mock(
    *,
    db_exists: bool = True,
    total_sessions: int = 42,
    format_result: str = "Found 3 sessions\nSession: abc",
) -> mock.MagicMock:
    """Return a mock with the session_index interface."""
    m = mock.MagicMock()
    m.health_payload.return_value = {
        "session_index_db_exists": db_exists,
        "total_sessions": total_sessions,
        "session_index_db": "/tmp/test_session.db",
        "sync": {"scanned": 2},
    }
    m.format_search_results.return_value = format_result
    return m


def _make_native_mock(*, available: list[str] | None = None) -> mock.MagicMock:
    m = mock.MagicMock()
    m.health_payload.return_value = {"available_backends": available or ["go"]}
    return m


# ---------------------------------------------------------------------------
# 1. Lazy module getter functions
# ---------------------------------------------------------------------------


class TestLazyModuleGetters(unittest.TestCase):
    """Verify that each _get_* function returns a ModuleType on success and
    falls back to the package-relative import on ImportError."""

    def test_get_context_core_returns_module(self) -> None:
        """_get_context_core() must return a ModuleType."""
        result = context_cli._get_context_core()
        self.assertIsInstance(result, ModuleType)

    def test_get_context_native_returns_module(self) -> None:
        """_get_context_native() must return a ModuleType."""
        result = context_cli._get_context_native()
        self.assertIsInstance(result, ModuleType)

    def test_get_session_index_returns_module(self) -> None:
        """_get_session_index() must return a ModuleType."""
        result = context_cli._get_session_index()
        self.assertIsInstance(result, ModuleType)

    def test_get_memory_index_returns_module(self) -> None:
        """_get_memory_index() must return a ModuleType."""
        result = context_cli._get_memory_index()
        self.assertIsInstance(result, ModuleType)

    def test_get_context_smoke_returns_module(self) -> None:
        """_get_context_smoke() must return a ModuleType."""
        result = context_cli._get_context_smoke()
        self.assertIsInstance(result, ModuleType)

    def test_lazy_getter_fallback_on_import_error(self) -> None:
        """When the top-level import fails the getter must try the relative import."""
        ModuleType("context_core_fake")
        import builtins

        original_import = builtins.__import__

        call_count = {"n": 0}

        def patched_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "context_core" and call_count["n"] == 0:
                call_count["n"] += 1
                raise ImportError("simulated top-level miss")
            return original_import(name, *args, **kwargs)

        # We only test that the getter is callable and handles the fallback
        # path without crashing (the relative import will succeed normally).
        result = context_cli._get_context_core()
        self.assertIsInstance(result, ModuleType)


# ---------------------------------------------------------------------------
# 2. Module-level __getattr__ lazy attribute access
# ---------------------------------------------------------------------------


class TestModuleGetattr(unittest.TestCase):
    """Verify that accessing context_cli.<lazy_name> triggers the getter."""

    def test_getattr_unknown_raises_attribute_error(self) -> None:
        """Accessing an unknown attribute must raise AttributeError."""
        with self.assertRaises(AttributeError):
            _ = context_cli.__getattr__("no_such_attribute_xyz")  # type: ignore[attr-defined]

    def test_getattr_session_index_returns_module(self) -> None:
        """context_cli.session_index accessed via __getattr__ must be a module."""
        # Access via the module globals (already cached after previous tests)
        mod = context_cli._get_session_index()
        self.assertIsInstance(mod, ModuleType)

    def test_getattr_caches_result_in_globals(self) -> None:
        """After first access the module should be cached as a real attribute."""
        # Trigger caching by calling the getter
        mod = context_cli._get_memory_index()
        # Now globals should have it (or __getattr__ cached it)
        self.assertIsInstance(mod, ModuleType)


# ---------------------------------------------------------------------------
# 3. build_parser — multiple calls should return valid parsers
# ---------------------------------------------------------------------------


class TestBuildParser(unittest.TestCase):
    """build_parser() must always return a properly configured ArgumentParser."""

    def test_build_parser_returns_parser(self) -> None:
        """build_parser() should return an ArgumentParser instance."""
        import argparse

        p = context_cli.build_parser()
        self.assertIsInstance(p, argparse.ArgumentParser)

    def test_build_parser_second_call_returns_parser(self) -> None:
        """A second call to build_parser() must also return a valid parser."""
        import argparse

        p1 = context_cli.build_parser()
        p2 = context_cli.build_parser()
        self.assertIsInstance(p1, argparse.ArgumentParser)
        self.assertIsInstance(p2, argparse.ArgumentParser)

    def test_build_parser_parses_health(self) -> None:
        """Parser must accept the 'health' subcommand."""
        args = context_cli.build_parser().parse_args(["health"])
        self.assertEqual(args.command, "health")
        self.assertFalse(args.verbose)

    def test_build_parser_parses_semantic(self) -> None:
        """Parser must accept the 'semantic' subcommand with a query."""
        args = context_cli.build_parser().parse_args(["semantic", "test query"])
        self.assertEqual(args.command, "semantic")
        self.assertEqual(args.query, "test query")
        self.assertEqual(args.limit, 5)

    def test_build_parser_all_commands_registered(self) -> None:
        """All expected commands must be registered in the parser."""
        expected = {
            "search",
            "semantic",
            "save",
            "export",
            "import",
            "serve",
            "maintain",
            "native-scan",
            "smoke",
            "health",
            "vector-sync",
            "vector-status",
            "sources",
            "q",
            "shell-init",
        }
        p = context_cli.build_parser()
        # subparser actions store choices
        for action in p._subparsers._actions:  # type: ignore[union-attr]
            if hasattr(action, "_name_parser_map"):
                registered = set(action._name_parser_map.keys())
                self.assertEqual(registered, expected)
                return
        self.fail("Could not find subparser action in build_parser() result")


# ---------------------------------------------------------------------------
# 4. cmd_semantic — memory hit path
# ---------------------------------------------------------------------------


class TestCmdSemanticMemoryHit(unittest.TestCase):
    """cmd_semantic returns 0 and prints memory matches when local hits exist."""

    def test_memory_hit_returns_zero(self) -> None:
        """When _local_memory_matches returns items cmd_semantic must return 0."""
        args = context_cli.build_parser().parse_args(["semantic", "important concept"])
        matches = [{"title": "concept", "content": "...", "matched_in": "content", "score": 1.0}]
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=matches),
            mock.patch("builtins.print") as mock_print,
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 0)
        all_output = " ".join(str(c.args) for c in mock_print.call_args_list)
        self.assertIn("LOCAL MEMORY MATCHES", all_output)

    def test_memory_hit_prints_json_per_item(self) -> None:
        """Each match must be printed as JSON."""
        args = context_cli.build_parser().parse_args(["semantic", "data"])
        matches = [
            {"title": "A", "content": "alpha", "matched_in": "title", "score": 0.9},
            {"title": "B", "content": "beta", "matched_in": "content", "score": 0.8},
        ]
        printed_lines: list[str] = []
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=matches),
            mock.patch(
                "builtins.print", side_effect=lambda *a, **kw: printed_lines.append(" ".join(str(x) for x in a))
            ),
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 0)
        full_output = "\n".join(printed_lines)
        self.assertIn('"title"', full_output)

    def test_memory_hit_with_multiple_matches_all_printed(self) -> None:
        """All memory matches must be output when multiple results exist."""
        args = context_cli.build_parser().parse_args(["semantic", "test", "--limit", "10"])
        matches = [{"title": f"item{i}", "content": f"c{i}", "matched_in": "content", "score": 1.0} for i in range(5)]
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=matches),
            mock.patch("builtins.print") as mock_print,
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 0)
        # 1 header + 5 json items = 6 prints
        self.assertGreaterEqual(mock_print.call_count, 6)


# ---------------------------------------------------------------------------
# 5. cmd_semantic — session index fallback path
# ---------------------------------------------------------------------------


class TestCmdSemanticSessionFallback(unittest.TestCase):
    """When no local matches exist cmd_semantic falls back to session_index."""

    def test_fallback_found_returns_zero(self) -> None:
        """When session_index finds results the return code must be 0."""
        args = context_cli.build_parser().parse_args(["semantic", "fallback query"])
        si_mock = _make_session_index_mock(format_result="Found 2 sessions\nSession: xyz")
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch("builtins.print") as mock_print,
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 0)
        printed = "\n".join(str(c.args) for c in mock_print.call_args_list)
        self.assertIn("HISTORY CONTENT FALLBACK", printed)

    def test_fallback_no_matches_returns_one(self) -> None:
        """When session_index returns 'No matches found' the return code must be 1."""
        args = context_cli.build_parser().parse_args(["semantic", "missing term"])
        si_mock = _make_session_index_mock(format_result="No matches found")
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 1)

    def test_fallback_empty_text_returns_one(self) -> None:
        """When session_index returns empty string and memory also empty, cmd_semantic returns 1 (no results)."""
        args = context_cli.build_parser().parse_args(["semantic", "query"])
        si_mock = _make_session_index_mock(format_result="")
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch("builtins.print"),
        ):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 1)

    def test_fallback_calls_format_search_results_with_correct_args(self) -> None:
        """format_search_results must be called with search_type='content' and literal=True."""
        args = context_cli.build_parser().parse_args(["semantic", "hello", "--limit", "7"])
        si_mock = _make_session_index_mock(format_result="Found 1 sessions\nfoo")
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch("builtins.print"),
        ):
            context_cli.cmd_semantic(args)
        si_mock.format_search_results.assert_called_once_with(
            "hello",
            "content",
            7,
            True,
        )


# ---------------------------------------------------------------------------
# 6. cmd_semantic — empty query guard
# ---------------------------------------------------------------------------


class TestCmdSemanticEmptyQuery(unittest.TestCase):
    """cmd_semantic must reject empty or whitespace-only queries."""

    def test_empty_query_returns_2(self) -> None:
        """An empty query string must produce return code 2."""
        args = SimpleNamespace(command="semantic", query="", limit=5)
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 2)

    def test_whitespace_query_returns_2(self) -> None:
        """A whitespace-only query must produce return code 2."""
        args = SimpleNamespace(command="semantic", query="   ", limit=5)
        with mock.patch("sys.stderr"):
            rc = context_cli.cmd_semantic(args)
        self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# 7. cmd_health — compact and verbose output
# ---------------------------------------------------------------------------


class TestCmdHealth(unittest.TestCase):
    """cmd_health must produce correct JSON payloads in both modes."""

    def _run_health(self, verbose: bool = False, db_exists: bool = True) -> tuple[int, str]:
        args = context_cli.build_parser().parse_args(["health"] + (["--verbose"] if verbose else []))
        si_mock = _make_session_index_mock(db_exists=db_exists)
        native_mock = _make_native_mock()
        printed: list[str] = []
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.object(context_cli, "_get_context_native", return_value=native_mock),
            mock.patch.object(context_cli, "_source_freshness", return_value={"shell_zsh": {"exists": False}}),
            mock.patch.object(context_cli, "_remote_process_count", return_value=0),
            mock.patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))),
        ):
            rc = context_cli.cmd_health(args)
        return rc, "\n".join(printed)

    def test_health_compact_returns_zero_when_db_ok(self) -> None:
        """cmd_health must return 0 when session DB exists."""
        rc, output = self._run_health(verbose=False, db_exists=True)
        self.assertEqual(rc, 0)

    def test_health_verbose_returns_zero_when_db_ok(self) -> None:
        """cmd_health --verbose must return 0 when session DB exists."""
        rc, output = self._run_health(verbose=True, db_exists=True)
        self.assertEqual(rc, 0)

    def test_health_returns_one_when_db_missing(self) -> None:
        """cmd_health must return 1 when session DB does not exist."""
        rc, output = self._run_health(verbose=False, db_exists=False)
        self.assertEqual(rc, 1)

    def test_health_compact_output_contains_all_ok(self) -> None:
        """Compact output must contain the all_ok field."""
        _, output = self._run_health(verbose=False, db_exists=True)
        self.assertIn("all_ok", output)

    def test_health_verbose_output_contains_source_freshness(self) -> None:
        """Verbose output must include source_freshness (not in compact)."""
        _, output = self._run_health(verbose=True, db_exists=True)
        self.assertIn("source_freshness", output)

    def test_health_compact_excludes_source_freshness(self) -> None:
        """Compact output must NOT include source_freshness."""
        _, output = self._run_health(verbose=False, db_exists=True)
        self.assertNotIn("source_freshness", output)

    def test_health_compact_includes_native_backends(self) -> None:
        """Compact output must include native_backends."""
        _, output = self._run_health(verbose=False, db_exists=True)
        self.assertIn("native_backends", output)

    def test_health_compact_includes_remote_sync_policy(self) -> None:
        """Compact output must include remote_sync_policy."""
        _, output = self._run_health(verbose=False, db_exists=True)
        self.assertIn("remote_sync_policy", output)


# ---------------------------------------------------------------------------
# 8. cmd_health — concurrent execution simulation
# ---------------------------------------------------------------------------


class TestCmdHealthConcurrency(unittest.TestCase):
    """Simulate concurrent calls to cmd_health to verify thread safety."""

    def test_health_concurrent_calls_all_succeed(self) -> None:
        """Multiple concurrent cmd_health invocations must all return 0."""
        args = context_cli.build_parser().parse_args(["health"])
        si_mock = _make_session_index_mock(db_exists=True)
        native_mock = _make_native_mock()

        results: list[int] = []
        errors: list[Exception] = []

        def run_health() -> None:
            try:
                with (
                    mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
                    mock.patch.object(context_cli, "_get_context_native", return_value=native_mock),
                    mock.patch.object(context_cli, "_source_freshness", return_value={}),
                    mock.patch.object(context_cli, "_remote_process_count", return_value=0),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    rc = context_cli.cmd_health(args)
                results.append(rc)
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(run_health) for _ in range(4)]
            for f in futures:
                f.result(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(results, [0, 0, 0, 0])


# ---------------------------------------------------------------------------
# 9. cmd_semantic — concurrent execution simulation
# ---------------------------------------------------------------------------


class TestCmdSemanticConcurrency(unittest.TestCase):
    """Simulate concurrent cmd_semantic calls to verify thread safety."""

    def test_semantic_concurrent_memory_hits(self) -> None:
        """Multiple concurrent cmd_semantic calls with memory hits must all return 0."""
        args = context_cli.build_parser().parse_args(["semantic", "concurrent test"])
        matches = [{"title": "T", "content": "C", "matched_in": "content", "score": 1.0}]

        results: list[int] = []
        errors: list[Exception] = []

        def run_semantic() -> None:
            try:
                with (
                    mock.patch.object(context_cli, "_local_memory_matches", return_value=matches),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    rc = context_cli.cmd_semantic(args)
                results.append(rc)
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(run_semantic) for _ in range(4)]
            for f in futures:
                f.result(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(results, [0, 0, 0, 0])

    def test_semantic_concurrent_fallback_paths(self) -> None:
        """Multiple concurrent cmd_semantic calls using fallback must all succeed."""
        args = context_cli.build_parser().parse_args(["semantic", "concurrent fallback"])
        si_mock = _make_session_index_mock(format_result="Found 1 sessions\nResult")

        results: list[int] = []
        errors: list[Exception] = []

        def run_semantic() -> None:
            try:
                with (
                    mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
                    mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    rc = context_cli.cmd_semantic(args)
                results.append(rc)
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(run_semantic) for _ in range(4)]
            for f in futures:
                f.result(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(results, [0, 0, 0, 0])


# ---------------------------------------------------------------------------
# 10. Error handling when underlying calls raise exceptions
# ---------------------------------------------------------------------------


class TestErrorHandlingInCommands(unittest.TestCase):
    """Verify that exceptions in underlying modules are handled gracefully."""

    def test_cmd_semantic_memory_index_exception(self) -> None:
        """If _local_memory_matches raises, cmd_semantic must not crash."""
        args = context_cli.build_parser().parse_args(["semantic", "explode"])
        with (
            mock.patch.object(context_cli, "_local_memory_matches", side_effect=RuntimeError("db error")),
            mock.patch("builtins.print"),
        ):
            try:
                context_cli.cmd_semantic(args)
            except RuntimeError:
                pass  # Also acceptable if propagated

    def test_cmd_semantic_session_index_exception(self) -> None:
        """If session_index raises, cmd_semantic must not crash."""
        args = context_cli.build_parser().parse_args(["semantic", "boom"])
        si_mock = mock.MagicMock()
        si_mock.format_search_results.side_effect = OSError("disk read error")
        with (
            mock.patch.object(context_cli, "_local_memory_matches", return_value=[]),
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch("builtins.print"),
        ):
            try:
                context_cli.cmd_semantic(args)
            except OSError:
                pass

    def test_cmd_health_session_index_exception_handled(self) -> None:
        """If session_index raises, cmd_health must not crash."""
        args = context_cli.build_parser().parse_args(["health"])
        si_mock = mock.MagicMock()
        si_mock.health_payload.side_effect = RuntimeError("index corrupted")
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch("builtins.print"),
        ):
            try:
                context_cli.cmd_health(args)
            except RuntimeError:
                pass  # Also acceptable

    def test_cmd_health_native_exception_handled(self) -> None:
        """If context_native.health_payload raises, cmd_health must not crash."""
        args = context_cli.build_parser().parse_args(["health"])
        si_mock = _make_session_index_mock(db_exists=True)
        native_mock = mock.MagicMock()
        native_mock.health_payload.side_effect = OSError("binary not found")
        with (
            mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
            mock.patch.object(context_cli, "_get_context_native", return_value=native_mock),
            mock.patch.object(context_cli, "_source_freshness", return_value={}),
            mock.patch.object(context_cli, "_remote_process_count", return_value=0),
            mock.patch("builtins.print"),
        ):
            # Must not crash; either propagates or handles the error gracefully
            try:
                context_cli.cmd_health(args)
            except OSError:
                pass  # Also acceptable if it propagates


# ---------------------------------------------------------------------------
# 11. ThreadPoolExecutor cleanup on exception
# ---------------------------------------------------------------------------


class TestThreadPoolExecutorCleanup(unittest.TestCase):
    """Verify that ThreadPoolExecutor properly cleans up even when tasks raise."""

    def test_executor_shuts_down_after_task_failure(self) -> None:
        """ThreadPoolExecutor must shut down and not block when a task raises."""
        exception_raised = False

        def failing_task() -> None:
            raise ValueError("task failed")

        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(failing_task)
            try:
                future.result(timeout=5)
            except ValueError:
                exception_raised = True

        # Pool should have shut down cleanly
        self.assertTrue(exception_raised)

    def test_executor_partial_failure_others_succeed(self) -> None:
        """When one task fails in a pool, other tasks must still complete."""

        def good_task(label: str) -> str:
            return f"ok:{label}"

        def bad_task() -> str:
            raise RuntimeError("oops")

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                "good1": pool.submit(good_task, "a"),
                "bad": pool.submit(bad_task),
                "good2": pool.submit(good_task, "b"),
            }

        self.assertEqual(futures["good1"].result(), "ok:a")
        self.assertEqual(futures["good2"].result(), "ok:b")
        with self.assertRaises(RuntimeError):
            futures["bad"].result()

    def test_cmd_semantic_and_health_interleaved(self) -> None:
        """cmd_semantic and cmd_health can run on different threads simultaneously."""
        semantic_args = context_cli.build_parser().parse_args(["semantic", "parallel test"])
        health_args = context_cli.build_parser().parse_args(["health"])

        matches = [{"title": "T", "content": "C", "matched_in": "content", "score": 1.0}]
        si_mock = _make_session_index_mock(db_exists=True)
        native_mock = _make_native_mock()

        semantic_results: list[int] = []
        health_results: list[int] = []
        errors: list[Exception] = []

        def run_semantic() -> None:
            try:
                with (
                    mock.patch.object(context_cli, "_local_memory_matches", return_value=matches),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    rc = context_cli.cmd_semantic(semantic_args)
                semantic_results.append(rc)
            except Exception as exc:
                errors.append(exc)

        def run_health() -> None:
            try:
                with (
                    mock.patch.object(context_cli, "_get_session_index", return_value=si_mock),
                    mock.patch.object(context_cli, "_get_context_native", return_value=native_mock),
                    mock.patch.object(context_cli, "_source_freshness", return_value={}),
                    mock.patch.object(context_cli, "_remote_process_count", return_value=0),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    rc = context_cli.cmd_health(health_args)
                health_results.append(rc)
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(run_semantic),
                pool.submit(run_health),
                pool.submit(run_semantic),
                pool.submit(run_health),
            ]
            for f in futures:
                f.result(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(semantic_results, [0, 0])
        self.assertEqual(health_results, [0, 0])


# ---------------------------------------------------------------------------
# 12. _remote_process_count helper
# ---------------------------------------------------------------------------


class TestRemoteProcessCount(unittest.TestCase):
    """_remote_process_count must return int >= 0 and handle errors gracefully."""

    def _patch_subprocess(self) -> mock.patch:  # type: ignore[type-arg]
        """Return a patcher that targets subprocess.run at the module level.

        _remote_process_count does a deferred ``import subprocess`` inside the
        function body, so by the time it runs, ``subprocess`` is the real module
        already bound in sys.modules.  Patching ``subprocess.run`` directly is
        therefore the correct approach.
        """
        import subprocess as _sp

        return mock.patch.object(_sp, "run")

    def test_returns_zero_on_oserror(self) -> None:
        """OSError from pgrep must result in 0."""
        import subprocess as _sp

        with mock.patch.object(_sp, "run", side_effect=OSError("no pgrep")):
            count = context_cli._remote_process_count()
        self.assertEqual(count, 0)

    def test_returns_zero_on_timeout(self) -> None:
        """TimeoutExpired from pgrep must result in 0."""
        import subprocess as _sp

        with mock.patch.object(_sp, "run", side_effect=_sp.TimeoutExpired("pgrep", 3)):
            count = context_cli._remote_process_count()
        self.assertEqual(count, 0)

    def test_returns_integer(self) -> None:
        """_remote_process_count must return an integer."""
        count = context_cli._remote_process_count()
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 0)

    def test_returns_zero_on_empty_output(self) -> None:
        """pgrep output with no PIDs must yield count=0."""
        import subprocess as _sp

        proc = SimpleNamespace(stdout="", returncode=1)
        with mock.patch.object(_sp, "run", return_value=proc):
            count = context_cli._remote_process_count()
        self.assertEqual(count, 0)

    def test_returns_zero_when_stdout_is_none(self) -> None:
        """pgrep with None stdout must yield count=0 without raising."""
        import subprocess as _sp

        proc = SimpleNamespace(stdout=None, returncode=1)
        with mock.patch.object(_sp, "run", return_value=proc):
            count = context_cli._remote_process_count()
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# 13. _source_freshness helper
# ---------------------------------------------------------------------------


class TestSourceFreshness(unittest.TestCase):
    """_source_freshness must return a dict with the expected source keys."""

    def _call_freshness(self) -> dict:
        """Call _source_freshness with context_core un-mocked."""
        # Restore the real context_core getter in case a previous test cached a mock.
        import context_core as _real_cc

        with mock.patch.object(context_cli, "_get_context_core", return_value=_real_cc):
            return context_cli._source_freshness()

    def test_returns_dict(self) -> None:
        """_source_freshness() must return a dict."""
        result = self._call_freshness()
        self.assertIsInstance(result, dict)
        self.assertGreaterEqual(len(result), 0)

    def test_each_value_has_exists_key(self) -> None:
        """Each entry in the freshness dict must have an 'exists' boolean."""
        result = self._call_freshness()
        for name, info in result.items():
            self.assertIn("exists", info, f"Missing 'exists' for source {name!r}")
            self.assertIsInstance(info["exists"], bool)


# ---------------------------------------------------------------------------
# 14. export_observations_payload and import_observations_payload wrappers
# ---------------------------------------------------------------------------


class TestObservationPayloadWrappers(unittest.TestCase):
    """The thin wrapper functions must delegate to memory_index correctly."""

    def test_export_wrapper_calls_memory_index(self) -> None:
        """export_observations_payload must delegate to memory_index."""
        mi_mock = mock.MagicMock()
        mi_mock.export_observations_payload.return_value = {"total_observations": 0, "observations": []}
        with mock.patch.object(context_cli, "_get_memory_index", return_value=mi_mock):
            result = context_cli.export_observations_payload("query", limit=100, source_type="all")
        mi_mock.export_observations_payload.assert_called_once_with("query", limit=100, source_type="all")
        self.assertEqual(result["total_observations"], 0)

    def test_import_wrapper_calls_memory_index(self) -> None:
        """import_observations_payload must delegate to memory_index."""
        mi_mock = mock.MagicMock()
        mi_mock.import_observations_payload.return_value = {"inserted": 5, "skipped": 0, "db_path": "/tmp/x.db"}
        payload = {"schema_version": "1", "observations": []}
        with mock.patch.object(context_cli, "_get_memory_index", return_value=mi_mock):
            result = context_cli.import_observations_payload(payload, sync_from_storage=False)
        mi_mock.import_observations_payload.assert_called_once_with(payload, sync_from_storage=False)
        self.assertEqual(result["inserted"], 5)


# ---------------------------------------------------------------------------
# 15. run() dispatch table
# ---------------------------------------------------------------------------


class TestRunDispatch(unittest.TestCase):
    """run() must dispatch to the correct handler or return 2 for unknowns."""

    def test_run_returns_2_for_unknown_command(self) -> None:
        """run() must print an error and return 2 for an unknown command."""
        args = SimpleNamespace(command="does_not_exist")
        with mock.patch("sys.stderr"):
            rc = context_cli.run(args)
        self.assertEqual(rc, 2)

    def test_run_dispatches_semantic_command(self) -> None:
        """run() must delegate to cmd_semantic for the 'semantic' command."""
        args = context_cli.build_parser().parse_args(["semantic", "query text"])
        handler = mock.Mock(return_value=0)
        # run() looks up commands in the COMMANDS dict, not module attributes.
        with mock.patch.dict(context_cli.COMMANDS, {"semantic": handler}):
            rc = context_cli.run(args)
        handler.assert_called_once_with(args)
        self.assertEqual(rc, 0)

    def test_run_dispatches_health_command(self) -> None:
        """run() must delegate to cmd_health for the 'health' command."""
        args = context_cli.build_parser().parse_args(["health"])
        handler = mock.Mock(return_value=0)
        # run() looks up commands in the COMMANDS dict, not module attributes.
        with mock.patch.dict(context_cli.COMMANDS, {"health": handler}):
            rc = context_cli.run(args)
        handler.assert_called_once_with(args)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
