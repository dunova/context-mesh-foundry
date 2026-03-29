#!/usr/bin/env python3
"""ContextGO viewer server entry point.

This is the single entry point for launching the ContextGO memory viewer
server.  Runtime configuration can be overridden programmatically via
:func:`apply_runtime_config` before calling :func:`main`, which is useful
in tests or when embedding the server.

Example::

    import context_server

    context_server.apply_runtime_config("127.0.0.1", 38000, "<your-token-here>")
    context_server.main()  # blocks until KeyboardInterrupt
"""

from __future__ import annotations

__all__ = ["apply_runtime_config", "main"]

try:
    import memory_viewer as _viewer
except ImportError:  # pragma: no cover
    from . import memory_viewer as _viewer  # type: ignore[import-not-found]


def apply_runtime_config(host: str, port: int, token: str) -> None:
    """Override the server's host, port, and auth token before :func:`main`.

    Must be called *before* :func:`main`.  Changes are applied directly to
    the ``memory_viewer`` module so there is a single source of truth.

    Args:
        host:  IP address or hostname to bind (e.g. ``"127.0.0.1"``).
        port:  TCP port number (1–65535).
        token: Bearer token checked via ``X-Context-Token`` header.
               Pass an empty string to disable token auth (loopback-only).
    """
    _viewer.HOST = host
    _viewer.PORT = port
    _viewer.VIEWER_TOKEN = token


def main() -> None:
    """Start the ContextGO viewer server (blocks until interrupted)."""
    _viewer.main()


if __name__ == "__main__":
    main()
