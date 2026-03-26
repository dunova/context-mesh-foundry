#!/usr/bin/env python3
"""ContextGO viewer server entry point.

This module is the single entry point for launching the ContextGO memory
viewer server.  All HTTP handling is implemented in ``memory_viewer``; this
shim exposes :func:`apply_runtime_config` so callers can override the
host, port, and auth token programmatically (e.g. in tests or when
embedding the server) without mutating environment variables.

Example::

    import context_server

    context_server.apply_runtime_config("127.0.0.1", 38000, "secret-token")
    context_server.main()  # blocks until KeyboardInterrupt
"""

from __future__ import annotations

__all__ = [
    "HOST",
    "PORT",
    "VIEWER_TOKEN",
    "apply_runtime_config",
    "main",
]

try:
    import memory_viewer as _viewer
except ImportError:  # pragma: no cover
    from . import memory_viewer as _viewer  # type: ignore[import-not-found]

# Re-export the current values so callers can read them without importing
# memory_viewer directly.  These are kept in sync by apply_runtime_config.
HOST: str = _viewer.HOST
PORT: int = _viewer.PORT
VIEWER_TOKEN: str = _viewer.VIEWER_TOKEN


def apply_runtime_config(host: str, port: int, token: str) -> None:
    """Override the server's host, port, and auth token before :func:`main`.

    Propagates the new values to ``memory_viewer`` module globals so they
    are picked up when the server starts.  Must be called *before*
    :func:`main`.

    Args:
        host: IP address or hostname to bind (e.g. ``"127.0.0.1"``).
        port: TCP port number (1–65535).
        token: Bearer token checked via ``X-Context-Token`` header.  Pass an
            empty string to disable token authentication (loopback-only).
    """
    global HOST, PORT, VIEWER_TOKEN  # noqa: PLW0603
    HOST = host
    PORT = port
    VIEWER_TOKEN = token
    _viewer.HOST = host
    _viewer.PORT = port
    _viewer.VIEWER_TOKEN = token


def main() -> None:
    """Start the ContextGO viewer server (blocks until interrupted)."""
    _viewer.main()


if __name__ == "__main__":
    main()
