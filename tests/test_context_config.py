#!/usr/bin/env python3
"""Unit tests for context_config module."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure scripts/ is on the path
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from context_config import env_bool, env_float, env_int, env_str, storage_root  # noqa: E402


class TestEnvStr(unittest.TestCase):
    """Tests for env_str()."""

    def test_returns_default_when_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_CCTG_MISSING_VAR_", None)
            result = env_str("_CCTG_MISSING_VAR_", default="fallback")
        self.assertEqual(result, "fallback")

    def test_returns_set_value(self) -> None:
        with patch.dict(os.environ, {"_CCTG_TEST_VAR_": "hello"}):
            result = env_str("_CCTG_TEST_VAR_", default="fallback")
        self.assertEqual(result, "hello")

    def test_skips_empty_string(self) -> None:
        with patch.dict(os.environ, {"_CCTG_EMPTY_": ""}):
            result = env_str("_CCTG_EMPTY_", default="default_val")
        self.assertEqual(result, "default_val")

    def test_skips_whitespace_only(self) -> None:
        with patch.dict(os.environ, {"_CCTG_BLANK_": "   "}):
            result = env_str("_CCTG_BLANK_", default="mydefault")
        self.assertEqual(result, "mydefault")

    def test_first_non_empty_wins(self) -> None:
        with patch.dict(os.environ, {"_CCTG_A_": "", "_CCTG_B_": "winner"}):
            result = env_str("_CCTG_A_", "_CCTG_B_", default="loser")
        self.assertEqual(result, "winner")

    def test_default_empty_string(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_CCTG_NOPE_", None)
            result = env_str("_CCTG_NOPE_")
        self.assertEqual(result, "")


class TestEnvInt(unittest.TestCase):
    """Tests for env_int()."""

    def test_parses_integer(self) -> None:
        with patch.dict(os.environ, {"_CCTG_INT_": "42"}):
            result = env_int("_CCTG_INT_", default=0)
        self.assertEqual(result, 42)

    def test_falls_back_on_invalid(self) -> None:
        with patch.dict(os.environ, {"_CCTG_INT_BAD_": "not_a_number"}):
            result = env_int("_CCTG_INT_BAD_", default=99)
        self.assertEqual(result, 99)

    def test_default_when_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_CCTG_INT_MISS_", None)
            result = env_int("_CCTG_INT_MISS_", default=7)
        self.assertEqual(result, 7)

    def test_minimum_clamp(self) -> None:
        with patch.dict(os.environ, {"_CCTG_INT_LOW_": "1"}):
            result = env_int("_CCTG_INT_LOW_", default=10, minimum=5)
        self.assertEqual(result, 5)

    def test_minimum_not_applied_when_above(self) -> None:
        with patch.dict(os.environ, {"_CCTG_INT_HIGH_": "20"}):
            result = env_int("_CCTG_INT_HIGH_", default=10, minimum=5)
        self.assertEqual(result, 20)

    def test_negative_value(self) -> None:
        with patch.dict(os.environ, {"_CCTG_INT_NEG_": "-3"}):
            result = env_int("_CCTG_INT_NEG_", default=0)
        self.assertEqual(result, -3)

    def test_zero_value(self) -> None:
        with patch.dict(os.environ, {"_CCTG_INT_ZERO_": "0"}):
            result = env_int("_CCTG_INT_ZERO_", default=10)
        self.assertEqual(result, 0)


class TestEnvFloat(unittest.TestCase):
    """Tests for env_float()."""

    def test_parses_float(self) -> None:
        with patch.dict(os.environ, {"_CCTG_F_": "3.14"}):
            result = env_float("_CCTG_F_", default=0.0)
        self.assertAlmostEqual(result, 3.14)

    def test_parses_integer_string_as_float(self) -> None:
        with patch.dict(os.environ, {"_CCTG_F2_": "5"}):
            result = env_float("_CCTG_F2_", default=0.0)
        self.assertAlmostEqual(result, 5.0)

    def test_falls_back_on_invalid(self) -> None:
        with patch.dict(os.environ, {"_CCTG_FBAD_": "xyz"}):
            result = env_float("_CCTG_FBAD_", default=2.71)
        self.assertAlmostEqual(result, 2.71)

    def test_default_when_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_CCTG_FMISS_", None)
            result = env_float("_CCTG_FMISS_", default=1.5)
        self.assertAlmostEqual(result, 1.5)

    def test_minimum_clamp(self) -> None:
        with patch.dict(os.environ, {"_CCTG_FMIN_": "0.1"}):
            result = env_float("_CCTG_FMIN_", default=1.0, minimum=0.5)
        self.assertAlmostEqual(result, 0.5)

    def test_minimum_not_applied_when_above(self) -> None:
        with patch.dict(os.environ, {"_CCTG_FHIGH_": "1.0"}):
            result = env_float("_CCTG_FHIGH_", default=0.0, minimum=0.5)
        self.assertAlmostEqual(result, 1.0)


class TestEnvBool(unittest.TestCase):
    """Tests for env_bool()."""

    def _check(self, value: str, expected: bool) -> None:
        with patch.dict(os.environ, {"_CCTG_BOOL_": value}):
            result = env_bool("_CCTG_BOOL_", default=False)
        self.assertEqual(result, expected, f"env_bool({value!r}) should be {expected}")

    def test_true_values(self) -> None:
        for val in ("1", "true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"):
            self._check(val, True)

    def test_false_values(self) -> None:
        for val in ("0", "false", "False", "FALSE", "no", "No", "off", "Off"):
            self._check(val, False)

    def test_default_false_when_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_CCTG_BOOLMISS_", None)
            result = env_bool("_CCTG_BOOLMISS_", default=False)
        self.assertFalse(result)

    def test_default_true_when_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_CCTG_BOOLMISS2_", None)
            result = env_bool("_CCTG_BOOLMISS2_", default=True)
        self.assertTrue(result)

    def test_empty_string_uses_default(self) -> None:
        with patch.dict(os.environ, {"_CCTG_BEMPTY_": ""}):
            result = env_bool("_CCTG_BEMPTY_", default=True)
        self.assertTrue(result)


class TestStorageRoot(unittest.TestCase):
    """Tests for storage_root()."""

    def test_default_uses_home_contextgo(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTGO_STORAGE_ROOT", None)
            path = storage_root()
        self.assertTrue(path.is_absolute())
        self.assertTrue(str(path).endswith(".contextgo"))

    def test_custom_env_var(self) -> None:
        custom = str(Path.home() / ".custom" / "contextgo")
        with patch.dict(os.environ, {"CONTEXTGO_STORAGE_ROOT": custom}):
            path = storage_root()
        self.assertEqual(path, Path(custom).resolve())

    def test_tilde_expansion(self) -> None:
        with patch.dict(os.environ, {"CONTEXTGO_STORAGE_ROOT": "~/.myapp/contextgo"}):
            path = storage_root()
        self.assertTrue(path.is_absolute())
        self.assertNotIn("~", str(path))

    def test_too_short_path_raises_value_error(self) -> None:
        # Use a single-component path that stays short even after macOS
        # symlink resolution (``/tmp`` resolves to ``/private/tmp`` on macOS,
        # gaining an extra component and no longer triggering the guard).
        with patch.dict(os.environ, {"CONTEXTGO_STORAGE_ROOT": "/x"}), self.assertRaises(ValueError):
            storage_root()

    def test_root_path_raises_value_error(self) -> None:
        with patch.dict(os.environ, {"CONTEXTGO_STORAGE_ROOT": "/"}), self.assertRaises(ValueError):
            storage_root()

    def test_valid_deep_path_accepted(self) -> None:
        # Use the real home directory to avoid macOS ``/home`` -> ``/System/Volumes/Data/home``
        # resolution which changes the expected string representation.
        expected = str(Path.home() / ".contextgo_test_deep")
        with patch.dict(os.environ, {"CONTEXTGO_STORAGE_ROOT": expected}):
            path = storage_root()
        self.assertEqual(str(path), expected)

    def test_custom_default_home_name(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTGO_STORAGE_ROOT", None)
            path = storage_root(default_home_name=".mycontextgo")
        self.assertTrue(str(path).endswith(".mycontextgo"))

    def test_non_absolute_resolved_path_raises_value_error(self) -> None:
        """Cover line 121: resolved path is not absolute (requires mock)."""
        from unittest.mock import patch as _patch

        relative_path = Path("relative/path/only")

        with patch.dict(os.environ, {"CONTEXTGO_STORAGE_ROOT": "/home/user/.contextgo"}):
            with _patch.object(Path, "resolve", return_value=relative_path):
                with self.assertRaises(ValueError):
                    storage_root()


if __name__ == "__main__":
    unittest.main()
