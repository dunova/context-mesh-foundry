#!/usr/bin/env python3
"""Canonical memory viewer server entrypoint."""

try:
    import memory_viewer as _viewer
except ImportError:  # pragma: no cover
    from . import memory_viewer as _viewer  # type: ignore[import-not-found]


HOST = _viewer.HOST
PORT = _viewer.PORT
VIEWER_TOKEN = _viewer.VIEWER_TOKEN


def apply_runtime_config(host: str, port: int, token: str) -> None:
    global HOST, PORT, VIEWER_TOKEN
    HOST = host
    PORT = port
    VIEWER_TOKEN = token
    _viewer.HOST = host
    _viewer.PORT = port
    _viewer.VIEWER_TOKEN = token


def main():
    return _viewer.main()


if __name__ == "__main__":
    main()
