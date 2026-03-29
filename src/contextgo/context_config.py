#!/usr/bin/env python3
"""Shared configuration helpers for ContextGO.

All runtime configuration is read from environment variables. This module
provides typed accessors with explicit defaults and validation so that the
rest of the codebase never calls ``os.environ`` directly.

Environment variables recognised by this module:

``CONTEXTGO_STORAGE_ROOT``
    Absolute (or ``~``-prefixed) path to the storage root directory.
    Defaults to ``~/.contextgo``. The resolved path must be absolute and
    have at least three components (e.g. ``/home/user/.contextgo``) to
    prevent accidental use of top-level directories.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeVar

    _N = TypeVar("_N", int, float)

__all__ = [
    "env_bool",
    "env_float",
    "env_int",
    "env_str",
    "storage_root",
]

# Minimum path depth required for the storage root (prevents "/" or "/tmp").
_MIN_STORAGE_ROOT_PARTS = 3


def env_str(*names: str, default: str = "") -> str:
    """Return the first non-empty value found in *names*, or *default*.

    A variable is considered empty when it is unset, the empty string, or
    contains only whitespace.
    """
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value
    return default


def _parse_numeric(
    type_: type[_N],
    names: tuple[str, ...],
    default: _N,
    minimum: _N | None,
) -> _N:
    """Parse a numeric env var, log on failure, and apply an optional floor."""
    import logging

    logger = logging.getLogger(__name__)

    raw = env_str(*names, default=str(default))
    try:
        value: _N = type_(raw)
    except ValueError:
        logger.warning(
            "cannot parse %r as %s for %s; using default %s",
            raw,
            type_.__name__,
            " / ".join(names),
            default,
        )
        value = default
    return max(minimum, value) if minimum is not None else value


def env_int(*names: str, default: int, minimum: int | None = None) -> int:
    """Return an integer configuration value from environment variables.

    Falls back to *default* when no variable is set or the value cannot be
    parsed. Clamps to *minimum* when provided.
    """
    return _parse_numeric(int, names, default, minimum)


def env_float(*names: str, default: float, minimum: float | None = None) -> float:
    """Return a float configuration value from environment variables.

    Falls back to *default* when no variable is set or the value cannot be
    parsed. Clamps to *minimum* when provided.
    """
    return _parse_numeric(float, names, default, minimum)


def env_bool(*names: str, default: bool = False) -> bool:
    """Return a boolean configuration value from environment variables.

    Truthy strings: ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case-insensitive).
    Anything else, including an unset variable, resolves to *default*.
    """
    raw = env_str(*names, default="1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def storage_root(default_home_name: str = ".contextgo") -> Path:
    """Return the resolved storage root path.

    Reads ``CONTEXTGO_STORAGE_ROOT`` (defaults to ``~/<default_home_name>``).
    The resolved path must be absolute and have at least
    ``_MIN_STORAGE_ROOT_PARTS`` components to guard against short paths like
    ``"/"`` or ``"/tmp"`` being used as the storage root.

    Raises:
        ValueError: If the resolved path is not absolute or is too short.
    """
    env_val = os.environ.get("CONTEXTGO_STORAGE_ROOT")
    raw = env_val if env_val and env_val.strip() else str(Path.home() / default_home_name)
    resolved = Path(os.path.expanduser(raw)).resolve()

    if not resolved.is_absolute():
        raise ValueError(f"CONTEXTGO_STORAGE_ROOT resolved to a non-absolute path: {resolved}")
    if len(resolved.parts) < _MIN_STORAGE_ROOT_PARTS:
        raise ValueError(
            f"CONTEXTGO_STORAGE_ROOT resolved to a suspiciously short path "
            f"({resolved}). Refusing to use a top-level directory as the storage root."
        )
    return resolved
