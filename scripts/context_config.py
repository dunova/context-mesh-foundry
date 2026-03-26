#!/usr/bin/env python3
"""Shared configuration helpers for ContextGO.

All runtime configuration is read from environment variables.  This module
provides typed accessors with explicit defaults and validation so that the
rest of the codebase never calls ``os.environ`` directly.

Environment variables recognised by this module:

``CONTEXTGO_STORAGE_ROOT``
    Absolute (or ``~``-prefixed) path to the storage root directory.
    Defaults to ``~/.contextgo``.  The resolved path must be absolute and
    have at least three components (e.g. ``/home/user/.contextgo``) to
    prevent accidental use of top-level directories.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

__all__ = [
    "env_bool",
    "env_float",
    "env_int",
    "env_str",
    "storage_root",
]

logger = logging.getLogger(__name__)

# Minimum number of path components required for the storage root.
# e.g.  /  home  user  .contextgo  → 4 parts, well above the threshold.
_MIN_STORAGE_ROOT_PARTS = 3


def env_str(*names: str, default: str = "") -> str:
    """Return the first non-empty value from *names*, or *default*.

    Args:
        *names: One or more environment variable names checked in order.
        default: Fallback value when none of the variables are set or
            non-empty.

    Returns:
        The resolved string value.
    """
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value
    return default


def env_int(
    *names: str,
    default: int,
    minimum: int | None = None,
) -> int:
    """Return an integer configuration value from environment variables.

    If the resolved string cannot be parsed as an integer, a warning is logged
    and *default* is used instead.

    Args:
        *names: One or more environment variable names checked in order.
        default: Fallback value when no variable is set, non-empty, or
            parseable as an integer.
        minimum: When provided, the returned value is clamped to this lower
            bound.

    Returns:
        The resolved integer value.
    """
    raw = env_str(*names, default=str(default))
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "env_int: cannot parse %r as int for %s; using default %d",
            raw,
            " / ".join(names),
            default,
        )
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_float(
    *names: str,
    default: float,
    minimum: float | None = None,
) -> float:
    """Return a float configuration value from environment variables.

    If the resolved string cannot be parsed as a float, a warning is logged
    and *default* is used instead.

    Args:
        *names: One or more environment variable names checked in order.
        default: Fallback value when no variable is set, non-empty, or
            parseable as a float.
        minimum: When provided, the returned value is clamped to this lower
            bound.

    Returns:
        The resolved float value.
    """
    raw = env_str(*names, default=str(default))
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "env_float: cannot parse %r as float for %s; using default %g",
            raw,
            " / ".join(names),
            default,
        )
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_bool(*names: str, default: bool = False) -> bool:
    """Return a boolean configuration value from environment variables.

    The strings ``"1"``, ``"true"``, ``"yes"``, and ``"on"``
    (case-insensitive) are treated as ``True``; everything else as ``False``.

    Args:
        *names: One or more environment variable names checked in order.
        default: Fallback value when none of the variables are set or
            non-empty.

    Returns:
        The resolved boolean value.
    """
    raw = env_str(*names, default="1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def storage_root(default_home_name: str = ".contextgo") -> Path:
    """Return the resolved storage root path.

    The path is taken from ``CONTEXTGO_STORAGE_ROOT`` (or
    ``~/<default_home_name>`` by default).  The resolved value must be an
    absolute path with at least three components (e.g.
    ``/home/user/.contextgo``) so that an accidental short value such as
    ``"/"`` or ``"/tmp"`` cannot silently become the storage root.

    Args:
        default_home_name: Subdirectory name under ``~`` used when
            ``CONTEXTGO_STORAGE_ROOT`` is not set.

    Returns:
        The resolved, absolute storage root path.

    Raises:
        ValueError: If the resolved path is not absolute or has fewer than
            :data:`_MIN_STORAGE_ROOT_PARTS` components.
    """
    raw = env_str(
        "CONTEXTGO_STORAGE_ROOT",
        default=str(Path.home() / default_home_name),
    )
    resolved = Path(os.path.expanduser(raw)).resolve()

    if not resolved.is_absolute():
        raise ValueError(f"CONTEXTGO_STORAGE_ROOT resolved to a non-absolute path: {resolved}")
    if len(resolved.parts) < _MIN_STORAGE_ROOT_PARTS:
        raise ValueError(
            f"CONTEXTGO_STORAGE_ROOT resolved to a suspiciously short path"
            f" ({resolved}). Refusing to use a top-level directory as the"
            " storage root."
        )

    return resolved
