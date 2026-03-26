#!/usr/bin/env python3
"""Canonical memory viewer server entrypoint.

This module is the single entry point for launching the ContextGO viewer
server.  It delegates all implementation to memory_viewer.py and exposes
apply_runtime_config() so callers can override host/port/token after import
without touching environment variables directly.
"""

from __future__ import annotations

try:
    import memory_viewer as _viewer
except ImportError:  # pragma: no cover
    from . import memory_viewer as _viewer  # type: ignore[import-not-found]


HOST: str = _viewer.HOST
PORT: int = _viewer.PORT
VIEWER_TOKEN: str = _viewer.VIEWER_TOKEN


def apply_runtime_config(host: str, port: int, token: str) -> None:
    """Override host, port, and auth token at runtime before serve_forever."""
    global HOST, PORT, VIEWER_TOKEN
    HOST = host
    PORT = port
    VIEWER_TOKEN = token
    _viewer.HOST = host
    _viewer.PORT = port
    _viewer.VIEWER_TOKEN = token


def main() -> None:
    """Start the ContextGO viewer server (blocks until interrupted)."""
    return _viewer.main()


if __name__ == "__main__":
    main()
