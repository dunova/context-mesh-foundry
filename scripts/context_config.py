#!/usr/bin/env python3
"""Shared configuration helpers for Context Mesh."""

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
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_float(*names: str, default: float, minimum: float | None = None) -> float:
    raw = env_str(*names, default=str(default))
    try:
        value = float(raw)
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_bool(*names: str, default: bool = False) -> bool:
    raw = env_str(*names, default="1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def storage_root(default_home_name: str = ".unified_context_data") -> Path:
    return Path(
        os.path.expanduser(
            env_str(
                "CONTEXT_MESH_STORAGE_ROOT",
                "UNIFIED_CONTEXT_STORAGE_ROOT",
                "OPENVIKING_STORAGE_ROOT",
                default=str(Path.home() / default_home_name),
            )
        )
    )
