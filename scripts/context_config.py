#!/usr/bin/env python3
"""Shared configuration helpers for ContextGO."""

from __future__ import annotations

import os
from pathlib import Path


def env_str(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip() != "":
            return str(value)
    return default


def env_int(*names: str, default: int, minimum: int | None = None) -> int:
    raw = env_str(*names, default=str(default))
    try:
        value = int(raw)
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_float(*names: str, default: float, minimum: float | None = None) -> float:
    raw = env_str(*names, default=str(default))
    try:
        value = float(raw)
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_bool(*names: str, default: bool = False) -> bool:
    raw = env_str(*names, default="1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def storage_root(default_home_name: str = ".contextgo") -> Path:
    """Return the resolved storage root path.

    The path is taken from CONTEXTGO_STORAGE_ROOT (or ~/.contextgo by
    default).  We validate that the resolved value is an absolute path with
    at least three components (e.g. /home/user/.contextgo) so that an
    accidental short value like '/' or '/tmp' cannot become the storage root
    and cause data to be scattered across the filesystem.
    """
    raw = env_str(
        "CONTEXTGO_STORAGE_ROOT",
        default=str(Path.home() / default_home_name),
    )
    resolved = Path(os.path.expanduser(raw)).resolve()
    if not resolved.is_absolute():
        raise ValueError(f"CONTEXTGO_STORAGE_ROOT resolved to a non-absolute path: {resolved}")
    if len(resolved.parts) < 3:
        raise ValueError(
            f"CONTEXTGO_STORAGE_ROOT resolved to a suspiciously short path ({resolved}). "
            "Refusing to use a top-level directory as the storage root."
        )
    return resolved
