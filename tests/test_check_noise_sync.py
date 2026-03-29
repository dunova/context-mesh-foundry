#!/usr/bin/env python3
"""Unit tests for check_noise_sync module."""

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

import check_noise_sync  # noqa: E402


class TestExtractStringArray(unittest.TestCase):
    """Tests for the internal _extract_string_array helper."""

    def test_returns_empty_when_pattern_missing(self) -> None:
        result = check_noise_sync._extract_string_array("no match here", "MISSING_PATTERN[")
        self.assertEqual(result, [])

    def test_extracts_strings_from_bracket_block(self) -> None:
        source = 'var Arr = []string{"alpha", "beta", "gamma"}'
        result = check_noise_sync._extract_string_array(source, "var Arr = []string{")
        self.assertIn("alpha", result)
        self.assertIn("beta", result)
        self.assertIn("gamma", result)

    def test_extracts_strings_from_square_bracket_block(self) -> None:
        source = 'const MARKERS: &[&str] = &["foo", "bar"]'
        result = check_noise_sync._extract_string_array(source, "const MARKERS: &[&str] = &[")
        self.assertIn("foo", result)
        self.assertIn("bar", result)

    def test_returns_empty_for_unclosed_bracket(self) -> None:
        source = 'var Arr = []string{"unclosed"'
        result = check_noise_sync._extract_string_array(source, "var Arr = []string{")
        self.assertEqual(result, [])

    def test_block_start_not_bracket(self) -> None:
        # pattern ends with a char that is not '[' or '{'
        source = "some_pattern_x_value"
        result = check_noise_sync._extract_string_array(source, "some_pattern_x")
        self.assertEqual(result, [])


class TestExtractRustMarkers(unittest.TestCase):
    def test_extracts_rust_noise_markers(self) -> None:
        source = 'const NOISE_MARKERS: &[&str] = &["rust_marker_1", "rust_marker_2"];'
        result = check_noise_sync.extract_rust_markers(source)
        self.assertIn("rust_marker_1", result)
        self.assertIn("rust_marker_2", result)

    def test_returns_empty_when_absent(self) -> None:
        result = check_noise_sync.extract_rust_markers("no markers here")
        self.assertEqual(result, [])


class TestExtractRustPrefixes(unittest.TestCase):
    def test_extracts_rust_noise_prefixes(self) -> None:
        source = 'const NOISE_PREFIXES: &[&str] = &["prefix_a", "prefix_b"];'
        result = check_noise_sync.extract_rust_prefixes(source)
        self.assertIn("prefix_a", result)
        self.assertIn("prefix_b", result)

    def test_returns_empty_when_absent(self) -> None:
        result = check_noise_sync.extract_rust_prefixes("nothing")
        self.assertEqual(result, [])


class TestExtractGoMarkers(unittest.TestCase):
    def test_extracts_go_default_noise_markers(self) -> None:
        source = 'var DefaultNoiseMarkers = []string{"go_mark_1", "go_mark_2"}'
        result = check_noise_sync.extract_go_markers(source)
        self.assertIn("go_mark_1", result)
        self.assertIn("go_mark_2", result)

    def test_returns_empty_when_absent(self) -> None:
        result = check_noise_sync.extract_go_markers("no go markers")
        self.assertEqual(result, [])


class TestExtractGoPrefixes(unittest.TestCase):
    def test_extracts_go_default_noise_prefixes(self) -> None:
        source = 'var DefaultNoisePrefixes = []string{"go_pre_1", "go_pre_2"}'
        result = check_noise_sync.extract_go_prefixes(source)
        self.assertIn("go_pre_1", result)
        self.assertIn("go_pre_2", result)

    def test_returns_empty_when_absent(self) -> None:
        result = check_noise_sync.extract_go_prefixes("empty")
        self.assertEqual(result, [])


class TestDecodeStringEscapes(unittest.TestCase):
    def test_decodes_newline_escape(self) -> None:
        self.assertEqual(check_noise_sync._decode_string_escapes("a\\nb"), "a\nb")

    def test_decodes_tab_escape(self) -> None:
        self.assertEqual(check_noise_sync._decode_string_escapes("a\\tb"), "a\tb")

    def test_decodes_carriage_return(self) -> None:
        self.assertEqual(check_noise_sync._decode_string_escapes("a\\rb"), "a\rb")

    def test_decodes_backslash(self) -> None:
        self.assertEqual(check_noise_sync._decode_string_escapes("a\\\\b"), "a\\b")

    def test_decodes_quote(self) -> None:
        self.assertEqual(check_noise_sync._decode_string_escapes('\\"'), '"')

    def test_no_escapes_unchanged(self) -> None:
        self.assertEqual(check_noise_sync._decode_string_escapes("plain"), "plain")


class TestNormalizeExtracted(unittest.TestCase):
    def test_decodes_list_of_strings(self) -> None:
        result = check_noise_sync._normalize_extracted(["a\\nb", "c\\td"])
        self.assertEqual(result, ["a\nb", "c\td"])


class TestCompare(unittest.TestCase):
    def test_in_sync_returns_empty(self) -> None:
        result = check_noise_sync.compare("label", ["a", "b"], ["a", "b"])
        self.assertEqual(result, [])

    def test_missing_from_source_detected(self) -> None:
        result = check_noise_sync.compare("TestLabel", ["a", "b"], ["a"])
        self.assertTrue(any("MISSING" in msg for msg in result))

    def test_extra_in_source_detected(self) -> None:
        result = check_noise_sync.compare("TestLabel", ["a"], ["a", "extra"])
        self.assertTrue(any("NOT in config" in msg for msg in result))

    def test_both_missing_and_extra_detected(self) -> None:
        result = check_noise_sync.compare("TestLabel", ["a", "b"], ["a", "extra"])
        self.assertTrue(any("MISSING" in msg for msg in result))
        self.assertTrue(any("NOT in config" in msg for msg in result))


class TestLoadConfig(unittest.TestCase):
    def test_loads_valid_config(self) -> None:
        data = {"native_noise_markers": ["m1"], "noise_prefixes": ["p1"]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            tmp_path = Path(f.name)
        try:
            with mock.patch.object(check_noise_sync, "CONFIG_PATH", tmp_path):
                result = check_noise_sync.load_config()
            self.assertEqual(result["native_noise_markers"], ["m1"])
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_exits_when_config_missing(self) -> None:
        missing = Path("/nonexistent_path_xyz/noise_markers.json")
        with mock.patch.object(check_noise_sync, "CONFIG_PATH", missing), self.assertRaises(SystemExit) as ctx:
            check_noise_sync.load_config()
        self.assertEqual(ctx.exception.code, 2)

    def test_exits_on_invalid_json(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{ invalid json }")
            tmp_path = Path(f.name)
        try:
            with mock.patch.object(check_noise_sync, "CONFIG_PATH", tmp_path), self.assertRaises(SystemExit) as ctx:
                check_noise_sync.load_config()
            self.assertEqual(ctx.exception.code, 2)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestMain(unittest.TestCase):
    def _make_sources(
        self,
        markers: list[str],
        prefixes: list[str],
    ) -> tuple[str, str]:
        """Build minimal Rust and Go source strings with the given markers."""
        quoted_m = ", ".join(f'"{m}"' for m in markers)
        quoted_p = ", ".join(f'"{p}"' for p in prefixes)
        rust = f"const NOISE_MARKERS: &[&str] = &[{quoted_m}];\nconst NOISE_PREFIXES: &[&str] = &[{quoted_p}];\n"
        go = f"var DefaultNoiseMarkers = []string{{{quoted_m}}}\nvar DefaultNoisePrefixes = []string{{{quoted_p}}}\n"
        return rust, go

    def test_main_returns_0_when_in_sync(self) -> None:
        config = {"native_noise_markers": ["noise_a"], "noise_prefixes": ["pre_b"]}
        rust_src, go_src = self._make_sources(["noise_a"], ["pre_b"])

        with (
            mock.patch.object(check_noise_sync, "load_config", return_value=config),
            mock.patch.object(
                check_noise_sync,
                "RUST_PATH",
                mock.MagicMock(read_text=mock.MagicMock(return_value=rust_src)),
            ),
            mock.patch.object(
                check_noise_sync,
                "GO_PATH",
                mock.MagicMock(read_text=mock.MagicMock(return_value=go_src)),
            ),
        ):
            result = check_noise_sync.main()
        self.assertEqual(result, 0)

    def test_main_returns_1_when_out_of_sync(self) -> None:
        config = {"native_noise_markers": ["noise_a", "noise_b"], "noise_prefixes": ["pre_b"]}
        # Only include noise_a, missing noise_b
        rust_src, go_src = self._make_sources(["noise_a"], ["pre_b"])

        with (
            mock.patch.object(check_noise_sync, "load_config", return_value=config),
            mock.patch.object(
                check_noise_sync,
                "RUST_PATH",
                mock.MagicMock(read_text=mock.MagicMock(return_value=rust_src)),
            ),
            mock.patch.object(
                check_noise_sync,
                "GO_PATH",
                mock.MagicMock(read_text=mock.MagicMock(return_value=go_src)),
            ),
        ):
            result = check_noise_sync.main()
        self.assertEqual(result, 1)

    def test_main_empty_config_sections_in_sync(self) -> None:
        config = {"native_noise_markers": [], "noise_prefixes": []}
        rust_src, go_src = self._make_sources([], [])

        with (
            mock.patch.object(check_noise_sync, "load_config", return_value=config),
            mock.patch.object(
                check_noise_sync,
                "RUST_PATH",
                mock.MagicMock(read_text=mock.MagicMock(return_value=rust_src)),
            ),
            mock.patch.object(
                check_noise_sync,
                "GO_PATH",
                mock.MagicMock(read_text=mock.MagicMock(return_value=go_src)),
            ),
        ):
            result = check_noise_sync.main()
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
