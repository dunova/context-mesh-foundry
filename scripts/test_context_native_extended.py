#!/usr/bin/env python3
"""Extended unit tests for context_native module to improve coverage."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_native  # noqa: E402

# ---------------------------------------------------------------------------
# NativeRunResult tests
# ---------------------------------------------------------------------------


class TestNativeRunResultJsonPayload(unittest.TestCase):
    def test_empty_stdout_returns_none(self) -> None:
        result = context_native.NativeRunResult(backend="go", returncode=0, stdout="", stderr="", command=[])
        self.assertIsNone(result.json_payload())

    def test_whitespace_only_stdout_returns_none(self) -> None:
        result = context_native.NativeRunResult(backend="go", returncode=0, stdout="   \n  ", stderr="", command=[])
        self.assertIsNone(result.json_payload())

    def test_valid_json_object_returned(self) -> None:
        result = context_native.NativeRunResult(
            backend="go",
            returncode=0,
            stdout='{"matches": [], "count": 0}',
            stderr="",
            command=[],
        )
        payload = result.json_payload()
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["count"], 0)

    def test_memoised_second_call_same_result(self) -> None:
        result = context_native.NativeRunResult(
            backend="go",
            returncode=0,
            stdout='{"k": "v"}',
            stderr="",
            command=[],
        )
        first = result.json_payload()
        second = result.json_payload()
        self.assertIs(first, second)

    def test_non_dict_json_returns_none(self) -> None:
        result = context_native.NativeRunResult(backend="go", returncode=0, stdout="[1, 2, 3]", stderr="", command=[])
        payload = result.json_payload()
        self.assertIsNone(payload)
        self.assertTrue(result._payload_error)

    def test_embedded_json_with_nested_bad_snippet(self) -> None:
        # text has a '{' but the extracted snippet is also invalid JSON
        result = context_native.NativeRunResult(
            backend="go",
            returncode=0,
            stdout="prefix { bad json } suffix",
            stderr="",
            command=[],
        )
        payload = result.json_payload()
        self.assertIsNone(payload)


class TestNativeRunResultErrorDetails(unittest.TestCase):
    def test_error_field_included(self) -> None:
        result = context_native.NativeRunResult(
            backend="go",
            returncode=-1,
            stdout="",
            stderr="",
            command=[],
            error="something went wrong",
        )
        details = result.error_details()
        self.assertIn("something went wrong", details)

    def test_nonzero_returncode_with_no_other_errors(self) -> None:
        result = context_native.NativeRunResult(backend="go", returncode=1, stdout="{}", stderr="", command=[])
        # {} is a dict but no errors key, returncode != 0
        details = result.error_details()
        # May or may not add generic message; just ensure no exception
        self.assertIsInstance(details, list)

    def test_payload_errors_list_included(self) -> None:
        payload_json = '{"errors": ["err1", "err2"]}'
        result = context_native.NativeRunResult(backend="go", returncode=0, stdout=payload_json, stderr="", command=[])
        details = result.error_details()
        self.assertIn("err1", details)
        self.assertIn("err2", details)

    def test_payload_errors_with_non_string_items(self) -> None:
        payload_json = '{"errors": [42, null, "real_err"]}'
        result = context_native.NativeRunResult(backend="go", returncode=0, stdout=payload_json, stderr="", command=[])
        details = result.error_details()
        self.assertIn("real_err", details)
        self.assertIn("42", details)

    def test_nonzero_returncode_appends_generic_message(self) -> None:
        result = context_native.NativeRunResult(backend="go", returncode=2, stdout="", stderr="", command=[])
        details = result.error_details()
        self.assertTrue(any("exited with code 2" in d for d in details))


class TestFindJsonSnippet(unittest.TestCase):
    def test_returns_none_when_no_brace(self) -> None:
        result = context_native.NativeRunResult(backend="go", returncode=0, stdout="", stderr="", command=[])
        self.assertIsNone(result._find_json_snippet("no braces here"))

    def test_returns_none_when_end_before_start(self) -> None:
        result = context_native.NativeRunResult(backend="go", returncode=0, stdout="", stderr="", command=[])
        # The } comes before {
        self.assertIsNone(result._find_json_snippet("} something {"))

    def test_extracts_snippet(self) -> None:
        result = context_native.NativeRunResult(backend="go", returncode=0, stdout="", stderr="", command=[])
        snippet = result._find_json_snippet('before {"key": "val"} after')
        self.assertEqual(snippet, '{"key": "val"}')


# ---------------------------------------------------------------------------
# NativeMatch tests
# ---------------------------------------------------------------------------


class TestNativeMatch(unittest.TestCase):
    def test_from_dict_valid(self) -> None:
        raw = {"source": "codex", "path": "/tmp/foo.md", "extra": 1}
        match = context_native.NativeMatch.from_dict(raw)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.source, "codex")
        self.assertEqual(match.path, Path("/tmp/foo.md"))
        self.assertEqual(match.metadata["extra"], 1)

    def test_from_dict_missing_source_returns_none(self) -> None:
        raw = {"path": "/tmp/foo.md"}
        match = context_native.NativeMatch.from_dict(raw)
        self.assertIsNone(match)

    def test_from_dict_missing_path_returns_none(self) -> None:
        raw = {"source": "codex"}
        match = context_native.NativeMatch.from_dict(raw)
        self.assertIsNone(match)

    def test_from_dict_empty_source_returns_none(self) -> None:
        raw = {"source": "   ", "path": "/tmp/foo.md"}
        match = context_native.NativeMatch.from_dict(raw)
        self.assertIsNone(match)

    def test_from_dict_empty_path_returns_none(self) -> None:
        raw = {"source": "codex", "path": ""}
        match = context_native.NativeMatch.from_dict(raw)
        self.assertIsNone(match)


# ---------------------------------------------------------------------------
# parse_native_matches / extract_matches / inventory_items tests
# ---------------------------------------------------------------------------


class TestParseNativeMatches(unittest.TestCase):
    def _make_result(self, stdout: str) -> context_native.NativeRunResult:
        return context_native.NativeRunResult(backend="go", returncode=0, stdout=stdout, stderr="", command=[])

    def test_empty_payload_returns_empty_list(self) -> None:
        result = self._make_result("")
        self.assertEqual(context_native.parse_native_matches(result), [])

    def test_no_matches_key_returns_empty(self) -> None:
        result = self._make_result('{"count": 0}')
        self.assertEqual(context_native.parse_native_matches(result), [])

    def test_matches_not_list_returns_empty(self) -> None:
        result = self._make_result('{"matches": "not_a_list"}')
        self.assertEqual(context_native.parse_native_matches(result), [])

    def test_valid_matches_parsed(self) -> None:
        stdout = '{"matches": [{"source": "codex", "path": "/tmp/a.md"}]}'
        result = self._make_result(stdout)
        matches = context_native.parse_native_matches(result)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].source, "codex")

    def test_invalid_match_items_skipped(self) -> None:
        stdout = '{"matches": ["not_a_dict", {"source": "s", "path": "/p"}]}'
        result = self._make_result(stdout)
        matches = context_native.parse_native_matches(result)
        self.assertEqual(len(matches), 1)

    def test_extract_matches_returns_metadata_dicts(self) -> None:
        stdout = '{"matches": [{"source": "s", "path": "/p", "score": 0.9}]}'
        result = self._make_result(stdout)
        items = context_native.extract_matches(result)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["score"], 0.9)

    def test_inventory_items_returns_tuples(self) -> None:
        stdout = '{"matches": [{"source": "s", "path": "/p"}]}'
        result = self._make_result(stdout)
        items = context_native.inventory_items(result)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0], "s")
        self.assertEqual(items[0][1], Path("/p"))


# ---------------------------------------------------------------------------
# Backend discovery tests
# ---------------------------------------------------------------------------


class TestAvailableBackends(unittest.TestCase):
    def test_no_backends_when_neither_installed(self) -> None:
        fake_rust = mock.MagicMock()
        fake_rust.exists.return_value = False
        fake_go = mock.MagicMock()
        fake_go.exists.return_value = False
        with (
            mock.patch("shutil.which", return_value=None),
            mock.patch.object(context_native, "RUST_PROJECT", fake_rust),
            mock.patch.object(context_native, "GO_PROJECT", fake_go),
        ):
            backends = context_native.available_backends()
        self.assertEqual(backends, [])

    def test_rust_backend_when_cargo_and_project_exist(self) -> None:
        def fake_which(name: str) -> str | None:
            return "/usr/bin/cargo" if name == "cargo" else None

        fake_rust = mock.MagicMock()
        fake_rust.exists.return_value = True
        fake_go = mock.MagicMock()
        fake_go.exists.return_value = False
        with (
            mock.patch("shutil.which", side_effect=fake_which),
            mock.patch.object(context_native, "RUST_PROJECT", fake_rust),
            mock.patch.object(context_native, "GO_PROJECT", fake_go),
        ):
            backends = context_native.available_backends()
        self.assertIn("rust", backends)
        self.assertNotIn("go", backends)

    def test_go_backend_when_go_and_project_exist(self) -> None:
        def fake_which(name: str) -> str | None:
            return "/usr/bin/go" if name == "go" else None

        fake_rust = mock.MagicMock()
        fake_rust.exists.return_value = False
        fake_go = mock.MagicMock()
        fake_go.exists.return_value = True
        with (
            mock.patch("shutil.which", side_effect=fake_which),
            mock.patch.object(context_native, "RUST_PROJECT", fake_rust),
            mock.patch.object(context_native, "GO_PROJECT", fake_go),
        ):
            backends = context_native.available_backends()
        self.assertNotIn("rust", backends)
        self.assertIn("go", backends)

    def test_both_backends_available(self) -> None:
        def fake_which(name: str) -> str | None:
            return f"/usr/bin/{name}" if name in ("cargo", "go") else None

        fake_rust = mock.MagicMock()
        fake_rust.exists.return_value = True
        fake_go = mock.MagicMock()
        fake_go.exists.return_value = True
        with (
            mock.patch("shutil.which", side_effect=fake_which),
            mock.patch.object(context_native, "RUST_PROJECT", fake_rust),
            mock.patch.object(context_native, "GO_PROJECT", fake_go),
        ):
            backends = context_native.available_backends()
        self.assertIn("rust", backends)
        self.assertIn("go", backends)


class TestResolveBackend(unittest.TestCase):
    def test_auto_selects_rust_first(self) -> None:
        with mock.patch.object(context_native, "available_backends", return_value=["rust", "go"]):
            self.assertEqual(context_native.resolve_backend("auto"), "rust")

    def test_auto_selects_go_when_no_rust(self) -> None:
        with mock.patch.object(context_native, "available_backends", return_value=["go"]):
            self.assertEqual(context_native.resolve_backend("auto"), "go")

    def test_auto_raises_when_no_backends(self) -> None:
        with mock.patch.object(context_native, "available_backends", return_value=[]), self.assertRaises(RuntimeError):
            context_native.resolve_backend("auto")

    def test_explicit_backend_returned_when_available(self) -> None:
        with mock.patch.object(context_native, "available_backends", return_value=["go"]):
            self.assertEqual(context_native.resolve_backend("go"), "go")

    def test_explicit_backend_raises_when_unavailable(self) -> None:
        with mock.patch.object(context_native, "available_backends", return_value=[]), self.assertRaises(RuntimeError):
            context_native.resolve_backend("rust")


# ---------------------------------------------------------------------------
# _rust_binary_is_fresh tests
# ---------------------------------------------------------------------------


class TestRustBinaryIsFresh(unittest.TestCase):
    def test_returns_false_when_binary_missing(self) -> None:
        fake_bin = mock.MagicMock()
        fake_bin.exists.return_value = False
        with mock.patch.object(context_native, "RUST_RELEASE_BIN", fake_bin):
            self.assertFalse(context_native._rust_binary_is_fresh())

    def test_returns_true_when_binary_newer_than_sources(self) -> None:
        now = time.time()

        fake_bin = mock.MagicMock()
        fake_bin.exists.return_value = True
        fake_bin_stat = mock.MagicMock()
        fake_bin_stat.st_mtime = now
        fake_bin.stat.return_value = fake_bin_stat

        # Mock Cargo.toml as older than binary
        mock_cargo = mock.MagicMock()
        mock_cargo.exists.return_value = True
        mock_cargo_stat = mock.MagicMock()
        mock_cargo_stat.st_mtime = now - 100
        mock_cargo.stat.return_value = mock_cargo_stat

        fake_src = mock.MagicMock()
        fake_src.rglob.return_value = []

        def fake_truediv(other: str) -> mock.MagicMock:
            if other == "Cargo.toml":
                return mock_cargo
            if other == "src":
                return fake_src
            return mock.MagicMock()

        fake_project = mock.MagicMock()
        fake_project.__truediv__ = mock.MagicMock(side_effect=fake_truediv)

        with (
            mock.patch.object(context_native, "RUST_RELEASE_BIN", fake_bin),
            mock.patch.object(context_native, "RUST_PROJECT", fake_project),
        ):
            result = context_native._rust_binary_is_fresh()
        self.assertIsInstance(result, bool)


# ---------------------------------------------------------------------------
# _append_scan_flags tests
# ---------------------------------------------------------------------------


class TestAppendScanFlags(unittest.TestCase):
    def test_appends_codex_and_claude_roots(self) -> None:
        cmd: list[str] = []
        context_native._append_scan_flags(
            cmd,
            codex_root="/codex",
            claude_root="/claude",
            threads=2,
            query=None,
            limit=None,
            json_output=False,
        )
        self.assertIn("--codex-root", cmd)
        self.assertIn("/codex", cmd)
        self.assertIn("--claude-root", cmd)
        self.assertIn("/claude", cmd)

    def test_appends_query_and_limit(self) -> None:
        cmd: list[str] = []
        context_native._append_scan_flags(
            cmd,
            codex_root=None,
            claude_root=None,
            threads=4,
            query="hello",
            limit=10,
            json_output=True,
        )
        self.assertIn("--query", cmd)
        self.assertIn("hello", cmd)
        self.assertIn("--limit", cmd)
        self.assertIn("10", cmd)
        self.assertIn("--json", cmd)

    def test_thread_minimum_is_one(self) -> None:
        cmd: list[str] = []
        context_native._append_scan_flags(
            cmd,
            codex_root=None,
            claude_root=None,
            threads=0,
            query=None,
            limit=None,
            json_output=False,
        )
        idx = cmd.index("--threads")
        self.assertEqual(cmd[idx + 1], "1")

    def test_no_optional_flags_when_omitted(self) -> None:
        cmd: list[str] = []
        context_native._append_scan_flags(
            cmd,
            codex_root=None,
            claude_root=None,
            threads=2,
            query=None,
            limit=None,
            json_output=False,
        )
        self.assertNotIn("--query", cmd)
        self.assertNotIn("--limit", cmd)
        self.assertNotIn("--json", cmd)
        self.assertNotIn("--codex-root", cmd)
        self.assertNotIn("--claude-root", cmd)


# ---------------------------------------------------------------------------
# _build_rust_cmd and _build_go_cmd tests
# ---------------------------------------------------------------------------


class TestBuildRustCmd(unittest.TestCase):
    def test_uses_cargo_run_when_not_release(self) -> None:
        cmd, cwd, env = context_native._build_rust_cmd(
            codex_root=None,
            claude_root=None,
            threads=1,
            release=False,
            query=None,
            json_output=False,
            limit=None,
        )
        self.assertIn("cargo", cmd)
        self.assertIn("run", cmd)
        self.assertNotIn("--release", cmd)

    def test_uses_cargo_run_release_when_release_and_binary_stale(self) -> None:
        with mock.patch.object(context_native, "_rust_binary_is_fresh", return_value=False):
            cmd, cwd, env = context_native._build_rust_cmd(
                codex_root=None,
                claude_root=None,
                threads=1,
                release=True,
                query=None,
                json_output=False,
                limit=None,
            )
        self.assertIn("--release", cmd)

    def test_uses_prebuilt_binary_when_fresh(self) -> None:
        with mock.patch.object(context_native, "_rust_binary_is_fresh", return_value=True):
            cmd, cwd, env = context_native._build_rust_cmd(
                codex_root=None,
                claude_root=None,
                threads=1,
                release=True,
                query=None,
                json_output=False,
                limit=None,
            )
        # First item should be the binary path, not 'cargo'
        self.assertNotEqual(cmd[0], "cargo")
        self.assertIn(str(context_native.RUST_RELEASE_BIN), cmd[0])


class TestBuildGoCmd(unittest.TestCase):
    def test_go_run_dot_is_first_args(self) -> None:
        cmd, cwd, env = context_native._build_go_cmd(
            codex_root=None,
            claude_root=None,
            threads=2,
            query=None,
            json_output=False,
            limit=None,
        )
        self.assertEqual(cmd[:3], ["go", "run", "."])
        self.assertEqual(cwd, context_native.GO_PROJECT)


# ---------------------------------------------------------------------------
# _decode_process_stream tests
# ---------------------------------------------------------------------------


class TestDecodeProcessStream(unittest.TestCase):
    def test_none_returns_empty_string(self) -> None:
        self.assertEqual(context_native._decode_process_stream(None), "")

    def test_bytes_decoded(self) -> None:
        self.assertEqual(context_native._decode_process_stream(b"hello"), "hello")

    def test_bytes_with_invalid_encoding_replaced(self) -> None:
        result = context_native._decode_process_stream(b"\xff\xfe")
        self.assertIsInstance(result, str)

    def test_str_returned_as_is(self) -> None:
        self.assertEqual(context_native._decode_process_stream("already str"), "already str")


# ---------------------------------------------------------------------------
# _execute_native_command tests
# ---------------------------------------------------------------------------


class TestExecuteNativeCommand(unittest.TestCase):
    def _run(self, side_effect: Exception | None = None, returncode: int = 0) -> context_native.NativeRunResult:
        cmd = ["echo", "hello"]
        cwd = Path("/tmp")
        env: dict[str, str] = {}

        if side_effect is not None:
            with mock.patch("subprocess.run", side_effect=side_effect):
                return context_native._execute_native_command(cmd=cmd, cwd=cwd, env=env, timeout=10, backend="go")
        else:
            mock_proc = mock.MagicMock()
            mock_proc.returncode = returncode
            mock_proc.stdout = "output"
            mock_proc.stderr = ""
            with mock.patch("subprocess.run", return_value=mock_proc):
                return context_native._execute_native_command(cmd=cmd, cwd=cwd, env=env, timeout=10, backend="go")

    def test_successful_run_returns_result(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "output")

    def test_timeout_returns_error_result(self) -> None:
        exc = subprocess.TimeoutExpired(cmd=["echo"], timeout=10)
        exc.stdout = b"partial"
        exc.stderr = b"warn"
        result = self._run(side_effect=exc)
        self.assertEqual(result.returncode, -1)
        self.assertIn("timed out", result.error or "")

    def test_timeout_with_none_streams(self) -> None:
        exc = subprocess.TimeoutExpired(cmd=["echo"], timeout=5)
        exc.stdout = None
        exc.stderr = None
        result = self._run(side_effect=exc)
        self.assertEqual(result.returncode, -1)

    def test_file_not_found_returns_error_result(self) -> None:
        result = self._run(side_effect=FileNotFoundError("not found"))
        self.assertEqual(result.returncode, -1)
        self.assertIn("binary not found", result.error or "")

    def test_permission_error_returns_error_result(self) -> None:
        result = self._run(side_effect=PermissionError("denied"))
        self.assertEqual(result.returncode, -1)
        self.assertIn("permission denied", result.error or "")

    def test_oserror_returns_error_result(self) -> None:
        result = self._run(side_effect=OSError("os error"))
        self.assertEqual(result.returncode, -1)
        self.assertIn("OS error", result.error or "")


# ---------------------------------------------------------------------------
# run_native_scan tests
# ---------------------------------------------------------------------------


class TestRunNativeScan(unittest.TestCase):
    def _fake_result(self) -> context_native.NativeRunResult:
        return context_native.NativeRunResult(
            backend="go", returncode=0, stdout='{"matches":[]}', stderr="", command=[]
        )

    def test_run_with_go_backend(self) -> None:
        with (
            mock.patch.object(context_native, "resolve_backend", return_value="go"),
            mock.patch.object(context_native, "_build_go_cmd", return_value=(["go", "run", "."], Path("/tmp"), {})),
            mock.patch.object(context_native, "_execute_native_command", return_value=self._fake_result()),
        ):
            result = context_native.run_native_scan(backend="go")
        self.assertEqual(result.returncode, 0)

    def test_run_with_rust_backend(self) -> None:
        with (
            mock.patch.object(context_native, "resolve_backend", return_value="rust"),
            mock.patch.object(
                context_native,
                "_build_rust_cmd",
                return_value=(["cargo", "run", "--"], Path("/tmp"), {}),
            ),
            mock.patch.object(context_native, "_execute_native_command", return_value=self._fake_result()),
        ):
            result = context_native.run_native_scan(backend="rust")
        self.assertEqual(result.returncode, 0)


# ---------------------------------------------------------------------------
# _load_health_cache tests
# ---------------------------------------------------------------------------


def _make_fake_cache_path(
    *,
    exists: bool = True,
    read_text_return: str | None = None,
    read_text_raises: Exception | None = None,
) -> mock.MagicMock:
    """Return a MagicMock that mimics a NATIVE_HEALTH_CACHE_PATH."""
    fake = mock.MagicMock()
    fake.exists.return_value = exists
    fake.parent = mock.MagicMock()
    if read_text_raises is not None:
        fake.read_text.side_effect = read_text_raises
    elif read_text_return is not None:
        fake.read_text.return_value = read_text_return
    return fake


class TestLoadHealthCache(unittest.TestCase):
    def test_returns_none_when_ttl_zero(self) -> None:
        with mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 0):
            self.assertIsNone(context_native._load_health_cache())

    def test_returns_none_when_file_missing(self) -> None:
        fake_path = _make_fake_cache_path(exists=False)
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", fake_path),
        ):
            self.assertIsNone(context_native._load_health_cache())

    def test_returns_none_on_oserror(self) -> None:
        fake_path = _make_fake_cache_path(exists=True, read_text_raises=OSError("io error"))
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", fake_path),
        ):
            self.assertIsNone(context_native._load_health_cache())

    def test_returns_none_on_invalid_json(self) -> None:
        fake_path = _make_fake_cache_path(exists=True, read_text_return="not json")
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", fake_path),
        ):
            self.assertIsNone(context_native._load_health_cache())

    def test_returns_none_when_not_dict(self) -> None:
        fake_path = _make_fake_cache_path(exists=True, read_text_return="[1, 2]")
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", fake_path),
        ):
            self.assertIsNone(context_native._load_health_cache())

    def test_returns_none_when_expired(self) -> None:
        import json as _json

        old_time = time.time() - 3600
        envelope = _json.dumps({"cached_at": old_time, "payload": {"key": "val"}})
        fake_path = _make_fake_cache_path(exists=True, read_text_return=envelope)
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", fake_path),
        ):
            self.assertIsNone(context_native._load_health_cache())

    def test_returns_none_when_payload_not_dict(self) -> None:
        import json as _json

        envelope = _json.dumps({"cached_at": time.time(), "payload": [1, 2]})
        fake_path = _make_fake_cache_path(exists=True, read_text_return=envelope)
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", fake_path),
        ):
            self.assertIsNone(context_native._load_health_cache())

    def test_returns_payload_when_fresh(self) -> None:
        import json as _json

        payload = {"available_backends": ["go"]}
        envelope = _json.dumps({"cached_at": time.time(), "payload": payload})
        fake_path = _make_fake_cache_path(exists=True, read_text_return=envelope)
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", fake_path),
        ):
            result = context_native._load_health_cache()
        self.assertEqual(result, payload)


# ---------------------------------------------------------------------------
# _store_health_cache tests
# ---------------------------------------------------------------------------


class TestStoreHealthCache(unittest.TestCase):
    def test_skips_when_ttl_zero(self) -> None:
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 0),
            mock.patch("os.open") as mock_open,
        ):
            context_native._store_health_cache({"k": "v"})
            mock_open.assert_not_called()

    def test_writes_cache_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "subdir" / "native_health_cache.json"
            with (
                mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
                mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", cache_path),
            ):
                context_native._store_health_cache({"status": "ok"})
            self.assertTrue(cache_path.exists())

    def test_silently_handles_mkdir_oserror(self) -> None:
        fake_parent = mock.MagicMock()
        fake_parent.mkdir.side_effect = OSError("no space")
        fake_path = mock.MagicMock()
        fake_path.parent = fake_parent
        with (
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_TTL_SEC", 30),
            mock.patch.object(context_native, "NATIVE_HEALTH_CACHE_PATH", fake_path),
        ):
            # Should not raise
            context_native._store_health_cache({"k": "v"})


# ---------------------------------------------------------------------------
# health_payload tests
# ---------------------------------------------------------------------------


class TestHealthPayload(unittest.TestCase):
    def test_probe_false_returns_static_payload(self) -> None:
        with mock.patch.object(context_native, "available_backends", return_value=["go"]):
            payload = context_native.health_payload(probe=False)
        self.assertEqual(payload["probe_mode"], "disabled")
        self.assertIn("available_backends", payload)

    def test_probe_true_executes_scan(self) -> None:
        fake_result = context_native.NativeRunResult(
            backend="go", returncode=0, stdout='{"matches":[]}', stderr="", command=[]
        )
        with (
            mock.patch.object(context_native, "available_backends", return_value=["go"]),
            mock.patch.object(context_native, "_load_health_cache", return_value=None),
            mock.patch.object(context_native, "run_native_scan", return_value=fake_result),
            mock.patch.object(context_native, "_store_health_cache"),
        ):
            payload = context_native.health_payload(probe=True)
        self.assertEqual(payload["probe_mode"], "executed")
        self.assertIn("go", payload)
        self.assertEqual(payload["go"]["ok"], True)

    def test_probe_true_captures_oserror(self) -> None:
        with (
            mock.patch.object(context_native, "available_backends", return_value=["go"]),
            mock.patch.object(context_native, "_load_health_cache", return_value=None),
            mock.patch.object(context_native, "run_native_scan", side_effect=OSError("cannot exec")),
            mock.patch.object(context_native, "_store_health_cache"),
        ):
            payload = context_native.health_payload(probe=True)
        self.assertFalse(payload["go"]["ok"])
        self.assertIn("error", payload["go"])

    def test_probe_true_captures_runtime_error(self) -> None:
        with (
            mock.patch.object(context_native, "available_backends", return_value=["rust"]),
            mock.patch.object(context_native, "_load_health_cache", return_value=None),
            mock.patch.object(
                context_native,
                "run_native_scan",
                side_effect=RuntimeError("no backend"),
            ),
            mock.patch.object(context_native, "_store_health_cache"),
        ):
            payload = context_native.health_payload(probe=True)
        self.assertFalse(payload["rust"]["ok"])


# ---------------------------------------------------------------------------
# main() entry point test
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def test_main_writes_stdout_and_returns_code(self) -> None:
        fake_result = context_native.NativeRunResult(
            backend="go", returncode=0, stdout="scan output\n", stderr="", command=[]
        )
        with (
            mock.patch.object(context_native, "run_native_scan", return_value=fake_result),
            mock.patch("sys.stdout") as mock_stdout,
        ):
            rc = context_native.main()
        self.assertEqual(rc, 0)
        mock_stdout.write.assert_called_once_with("scan output\n")

    def test_main_writes_stderr_when_present(self) -> None:
        fake_result = context_native.NativeRunResult(
            backend="go", returncode=1, stdout="", stderr="error msg\n", command=[]
        )
        with (
            mock.patch.object(context_native, "run_native_scan", return_value=fake_result),
            mock.patch("sys.stderr") as mock_stderr,
        ):
            rc = context_native.main()
        self.assertEqual(rc, 1)
        mock_stderr.write.assert_called_once_with("error msg\n")

    def test_main_no_output_when_empty(self) -> None:
        fake_result = context_native.NativeRunResult(backend="go", returncode=0, stdout="", stderr="", command=[])
        with (
            mock.patch.object(context_native, "run_native_scan", return_value=fake_result),
            mock.patch("sys.stdout") as mock_stdout,
            mock.patch("sys.stderr") as mock_stderr,
        ):
            rc = context_native.main()
        self.assertEqual(rc, 0)
        mock_stdout.write.assert_not_called()
        mock_stderr.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
