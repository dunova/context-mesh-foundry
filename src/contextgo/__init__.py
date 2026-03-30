"""ContextGO — local-first context & memory runtime for multi-agent AI coding teams."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "main",
    "run",
]

__author__ = "Dunova"
__license__ = "AGPL-3.0-only"
__email__ = "contact@dunova.io"

try:
    __version__ = version("contextgo")
except PackageNotFoundError:
    __version__ = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()


def __getattr__(name: str) -> object:
    """Lazy-import heavy submodules to keep import time low."""
    if name in ("main", "run"):
        from contextgo.context_cli import main, run  # noqa: PLC0415

        globals()["main"] = main
        globals()["run"] = run
        return globals()[name]
    raise AttributeError(f"module 'contextgo' has no attribute {name!r}")
