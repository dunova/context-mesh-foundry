#!/usr/bin/env python3
"""AutoResearch R39 — targeted coverage tests for context_native.py main().

Covers context_native.main() execution paths (stdout/stderr routing,
return code handling).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import context_native  # noqa: E402


class TestContextNativeMain(unittest.TestCase):
    """Exercise context_native.main() paths."""

    def test_main_function_returns_zero_on_success(self) -> None:
        """context_native.main() returns run_native_scan's returncode."""
        mock_result = mock.MagicMock()
        mock_result.stdout = "output line\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with (
            mock.patch.object(context_native, "run_native_scan", return_value=mock_result),
            mock.patch("sys.stdout") as mock_stdout,
        ):
            rc = context_native.main()

        self.assertEqual(rc, 0)
        mock_stdout.write.assert_called_once_with("output line\n")

    def test_main_function_returns_nonzero_on_failure(self) -> None:
        """context_native.main() returns the scan's non-zero returncode."""
        mock_result = mock.MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error text\n"
        mock_result.returncode = 1

        with (
            mock.patch.object(context_native, "run_native_scan", return_value=mock_result),
            mock.patch("sys.stderr") as mock_stderr,
        ):
            rc = context_native.main()

        self.assertEqual(rc, 1)
        mock_stderr.write.assert_called_once_with("error text\n")

    def test_main_empty_stdout_stderr(self) -> None:
        """main() with empty stdout/stderr does not call write."""
        mock_result = mock.MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0

        with mock.patch.object(context_native, "run_native_scan", return_value=mock_result):
            rc = context_native.main()

        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
