#!/usr/bin/env python3
"""Canonical maintenance entrypoint."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


def _load_legacy_module(name: str) -> ModuleType:
    try:
        return import_module(f"legacy.{name}")
    except (ImportError, ValueError) as first_exc:  # pragma: no cover - fallback when running locally
        fallback_pkg = __package__ or "scripts"
        try:
            return import_module(f".legacy.{name}", package=fallback_pkg)
        except (ImportError, ValueError) as second_exc:
            raise ImportError(
                f"Cannot load legacy module '{name}' via legacy.{name} or {fallback_pkg}.legacy.{name}"
            ) from second_exc


def _expose_public_api(module: ModuleType) -> list[str]:
    public_names = [key for key in vars(module) if not key.startswith("__")]
    globals().update({name: getattr(module, name) for name in public_names})
    return getattr(module, "__all__", public_names)


LEGACY_MODULE = _load_legacy_module("onecontext_maintenance")
__all__ = _expose_public_api(LEGACY_MODULE)


if __name__ == "__main__":
    raise SystemExit(main())
