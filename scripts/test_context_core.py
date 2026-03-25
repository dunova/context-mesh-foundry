#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import context_core


class ContextCoreTests(unittest.TestCase):
    def test_write_memory_markdown_then_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            conversations = Path(tmpdir) / "resources" / "shared" / "conversations"
            path = context_core.write_memory_markdown(
                "core-unit",
                "unique_token_context_core_unit",
                ["unit", "core"],
                conversations_root=conversations,
            )
            self.assertTrue(path.exists())
            matches = context_core.local_memory_matches(
                "unique_token_context_core_unit",
                shared_root=conversations.parent,
                limit=3,
                uri_prefix="local://",
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["matched_in"], "content")

if __name__ == "__main__":
    unittest.main()
