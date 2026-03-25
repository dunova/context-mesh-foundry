#!/usr/bin/env python3
"""Helpers for invoking native Context Mesh prototypes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

try:
    from context_config import env_str
except ImportError:  # pragma: no cover
    from .context_config import env_str  # type: ignore[import-not-found]


REPO_ROOT = Path(__file__).resolve().parents[1]
NATIVE_ROOT = REPO_ROOT / "native"
RUST_PROJECT = NATIVE_ROOT / "session_scan"
GO_PROJECT = NATIVE_ROOT / "session_scan_go"
DEFAULT_TARGET_DIR = env_str("CONTEXT_MESH_NATIVE_TARGET_DIR", default="/tmp/context_mesh_target")


@dataclass
class NativeRunResult:
    backend: str
    returncode: int
    stdout: str
    stderr: str

    def json_payload(self) -> dict[str, Any] | None:
        text = (self.stdout or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None


def extract_matches(result: NativeRunResult) -> list[dict[str, Any]]:
    payload = result.json_payload()
    if not isinstance(payload, dict):
        return []
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return []
    out: list[dict[str, Any]] = []
    for item in matches:
        if isinstance(item, dict):
            out.append(item)
    return out


def inventory_items(result: NativeRunResult) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for item in extract_matches(result):
        source = str(item.get("source") or "").strip()
        path = str(item.get("path") or "").strip()
        if source and path:
            items.append((source, Path(path)))
    return items


def available_backends() -> list[str]:
    backends: list[str] = []
    if shutil.which("cargo") and RUST_PROJECT.exists():
        backends.append("rust")
    if shutil.which("go") and GO_PROJECT.exists():
        backends.append("go")
    return backends


def resolve_backend(requested: str) -> str:
    backends = available_backends()
    if requested != "auto":
        if requested not in backends:
            raise RuntimeError(f"native backend unavailable: {requested}")
        return requested
    if "rust" in backends:
        return "rust"
    if "go" in backends:
        return "go"
    raise RuntimeError("no native backend available")


def _build_rust_cmd(
    *,
    codex_root: str | None,
    claude_root: str | None,
    threads: int,
    release: bool,
    query: str | None,
    json_output: bool,
) -> tuple[list[str], Path, dict[str, str]]:
    cmd = ["cargo", "run"]
    if release:
        cmd.append("--release")
    cmd.extend(["--manifest-path", str(RUST_PROJECT / "Cargo.toml"), "--"])
    if codex_root:
        cmd.extend(["--codex-root", codex_root])
    if claude_root:
        cmd.extend(["--claude-root", claude_root])
    cmd.extend(["--threads", str(max(1, threads))])
    if query:
        cmd.extend(["--query", query])
    if json_output:
        cmd.append("--json")
    env = os.environ.copy()
    env.setdefault("CARGO_TARGET_DIR", DEFAULT_TARGET_DIR)
    return cmd, REPO_ROOT, env


def _build_go_cmd(
    *,
    codex_root: str | None,
    claude_root: str | None,
    threads: int,
    query: str | None,
    json_output: bool,
) -> tuple[list[str], Path, dict[str, str]]:
    cmd = ["go", "run", ".", "--threads", str(max(1, threads))]
    if codex_root:
        cmd.extend(["--codex-root", codex_root])
    if claude_root:
        cmd.extend(["--claude-root", claude_root])
    if query:
        cmd.extend(["--query", query])
    if json_output:
        cmd.append("--json")
    return cmd, GO_PROJECT, os.environ.copy()


def run_native_scan(
    *,
    backend: str = "auto",
    codex_root: str | None = None,
    claude_root: str | None = None,
    threads: int = 4,
    release: bool = True,
    query: str | None = None,
    json_output: bool = False,
    timeout: int = 300,
) -> NativeRunResult:
    chosen = resolve_backend(backend)
    if chosen == "rust":
        cmd, cwd, env = _build_rust_cmd(
            codex_root=codex_root,
            claude_root=claude_root,
            threads=threads,
            release=release,
            query=query,
            json_output=json_output,
        )
    else:
        cmd, cwd, env = _build_go_cmd(
            codex_root=codex_root,
            claude_root=claude_root,
            threads=threads,
            query=query,
            json_output=json_output,
        )

    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return NativeRunResult(
        backend=chosen,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def main() -> int:
    result = run_native_scan()
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
