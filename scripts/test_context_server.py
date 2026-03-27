#!/usr/bin/env python3
"""Unit tests for context_server module."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


class TestContextServer(unittest.TestCase):
    def setUp(self) -> None:
        # Provide a fake memory_viewer module so context_server can import
        self._fake_viewer = mock.MagicMock()
        self._fake_viewer.HOST = "127.0.0.1"
        self._fake_viewer.PORT = 37242
        self._fake_viewer.VIEWER_TOKEN = ""
        self._fake_viewer.main = mock.MagicMock()

        # Save original modules for restoration
        self._orig_cs = sys.modules.get("context_server")
        self._orig_viewer = sys.modules.get("memory_viewer")

        # Ensure a clean import each test by removing cached module
        for mod in ("context_server", "memory_viewer"):
            sys.modules.pop(mod, None)

        with mock.patch.dict("sys.modules", {"memory_viewer": self._fake_viewer}):
            import context_server as cs

            self._cs = cs

    def tearDown(self) -> None:
        # Restore original modules to avoid polluting other test files
        sys.modules.pop("context_server", None)
        if self._orig_viewer is not None:
            sys.modules["memory_viewer"] = self._orig_viewer
        else:
            sys.modules.pop("memory_viewer", None)

    def test_apply_runtime_config_sets_viewer_attributes(self) -> None:
        viewer = self._fake_viewer
        self._cs.apply_runtime_config("0.0.0.0", 9999, "my-token")
        self.assertEqual(viewer.HOST, "0.0.0.0")
        self.assertEqual(viewer.PORT, 9999)
        self.assertEqual(viewer.VIEWER_TOKEN, "my-token")

    def test_apply_runtime_config_empty_token(self) -> None:
        viewer = self._fake_viewer
        self._cs.apply_runtime_config("127.0.0.1", 8080, "")
        self.assertEqual(viewer.HOST, "127.0.0.1")
        self.assertEqual(viewer.PORT, 8080)
        self.assertEqual(viewer.VIEWER_TOKEN, "")

    def test_main_delegates_to_viewer_main(self) -> None:
        self._cs.main()
        self._fake_viewer.main.assert_called_once()

    def test_apply_runtime_config_and_then_main(self) -> None:
        self._cs.apply_runtime_config("localhost", 12345, "tok")
        self._cs.main()
        self.assertEqual(self._fake_viewer.HOST, "localhost")
        self.assertEqual(self._fake_viewer.PORT, 12345)
        self._fake_viewer.main.assert_called_once()

    def test_all_exported_symbols_present(self) -> None:
        self.assertIn("apply_runtime_config", self._cs.__all__)
        self.assertIn("main", self._cs.__all__)


if __name__ == "__main__":
    unittest.main()
