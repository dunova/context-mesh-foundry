#!/usr/bin/env python3
"""Helpers for invoking native Context Mesh prototypes."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

try:
    from context_config import env_int, env_str
except ImportError:  # pragma: no cover
    from .context_config import env_int, env_str  # type: ignore[import-not-found]


REPO_ROOT = Path(__file__).resolve().parents[1]
NATIVE_ROOT = REPO_ROOT / "native"
RUST_PROJECT = NATIVE_ROOT / "session_scan"
GO_PROJECT = NATIVE_ROOT / "session_scan_go"
DEFAULT_TARGET_DIR = env_str("CONTEXT_MESH_NATIVE_TARGET_DIR", default="/tmp/context_mesh_target")
NATIVE_HEALTH_CACHE_TTL_SEC = env_int("CONTEXT_MESH_NATIVE_HEALTH_CACHE_TTL_SEC", default=30, minimum=0)
NATIVE_HEALTH_CACHE_PATH = Path(DEFAULT_TARGET_DIR) / "native_health_cache.json"


@dataclass
class NativeRunResult:
    backend: str
    returncode: int
    stdout: str
    stderr: str
    command: list[str]
    error: str | None = None
    _payload_cache: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _payload_error: str | None = field(default=None, init=False, repr=False)

    def json_payload(self) -> dict[str, Any] | None:
        if self._payload_cache is not None:
            return self._payload_cache
        text = (self.stdout or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except Exception as exc:
            self._payload_error = str(exc)
            snippet = self._find_json_snippet(text)
            if snippet:
                try:
                    payload = json.loads(snippet)
                    self._payload_cache = payload
                    return payload
                except Exception as nested:
                    self._payload_error = f"{self._payload_error}; fallback parse failed: {nested}"
                    return None
            return None
        if isinstance(payload, dict):
            self._payload_cache = payload
            return payload
        self._payload_error = "payload is not an object"
        return None

    def error_details(self) -> list[str]:
        errors: list[str] = []
        if self.error:
            errors.append(self.error)
        payload = self.json_payload()
        if isinstance(payload, dict):
            raw_errors = payload.get("errors")
            if isinstance(raw_errors, list):
                for item in raw_errors:
                    if isinstance(item, str) and item:
                        errors.append(item)
                    elif item is not None:
                        errors.append(str(item))
        if self._payload_error:
            errors.append(self._payload_error)
        if self.returncode != 0 and not errors:
            errors.append(f"native backend exited with code {self.returncode}")
        return errors

    def _find_json_snippet(self, text: str) -> str | None:
        start = text.find("{")
        if start == -1:
            return None
        end = text.rfind("}")
        if end == -1 or end <= start:
            return None
        return text[start : end + 1]


def extract_matches(result: NativeRunResult) -> list[dict[str, Any]]:
    return [item.metadata for item in parse_native_matches(result)]


def inventory_items(result: NativeRunResult) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for match in parse_native_matches(result):
        items.append((match.source, match.path))
    return items


def _normalize_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return []
    return [item for item in matches if isinstance(item, dict)]


@dataclass(frozen=True)
class NativeMatch:
    source: str
    path: Path
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NativeMatch" | None:
        source = str(raw.get("source") or "").strip()
        path_text = str(raw.get("path") or "").strip()
        if not source or not path_text:
            return None
        return cls(source=source, path=Path(path_text), metadata=raw)


def parse_native_matches(result: NativeRunResult) -> list[NativeMatch]:
    payload = result.json_payload()
    if not isinstance(payload, dict):
        return []
    out: list[NativeMatch] = []
    for raw in _normalize_matches(payload):
        match = NativeMatch.from_dict(raw)
        if match:
            out.append(match)
    return out


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
    limit: int | None,
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
    if limit is not None:
        cmd.extend(["--limit", str(max(1, limit))])
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
    limit: int | None,
) -> tuple[list[str], Path, dict[str, str]]:
    cmd = ["go", "run", ".", "--threads", str(max(1, threads))]
    if codex_root:
        cmd.extend(["--codex-root", codex_root])
    if claude_root:
        cmd.extend(["--claude-root", claude_root])
    if query:
        cmd.extend(["--query", query])
    if limit is not None:
        cmd.extend(["--limit", str(max(1, limit))])
    if json_output:
        cmd.append("--json")
    return cmd, GO_PROJECT, os.environ.copy()


def _execute_native_command(
    *,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    backend: str,
) -> NativeRunResult:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return NativeRunResult(
            backend=backend,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            command=cmd,
        )
    except subprocess.TimeoutExpired as exc:
        message = f"native backend timed out after {timeout} seconds"
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if stderr:
            stderr = f"{stderr.rstrip()}\n{message}"
        else:
            stderr = message
        return NativeRunResult(
            backend=backend,
            returncode=-1,
            stdout=stdout,
            stderr=stderr,
            command=cmd,
            error=message,
        )
    except FileNotFoundError as exc:
        message = f"{cmd[0]} not found: {exc}"
        return NativeRunResult(
            backend=backend,
            returncode=-1,
            stdout="",
            stderr=message,
            command=cmd,
            error=message,
        )
    except Exception as exc:  # pragma: no cover
        message = f"native backend failed: {exc}"
        return NativeRunResult(
            backend=backend,
            returncode=-1,
            stdout="",
            stderr=message,
            command=cmd,
            error=message,
        )


def run_native_scan(
    *,
    backend: str = "auto",
    codex_root: str | None = None,
    claude_root: str | None = None,
    threads: int = 4,
    release: bool = True,
    query: str | None = None,
    json_output: bool = False,
    limit: int | None = None,
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
            limit=limit,
        )
    else:
        cmd, cwd, env = _build_go_cmd(
            codex_root=codex_root,
            claude_root=claude_root,
            threads=threads,
            query=query,
            json_output=json_output,
            limit=limit,
        )

    return _execute_native_command(cmd=cmd, cwd=cwd, env=env, timeout=timeout, backend=chosen)


def _load_health_cache() -> dict[str, Any] | None:
    if NATIVE_HEALTH_CACHE_TTL_SEC <= 0 or not NATIVE_HEALTH_CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(NATIVE_HEALTH_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    cached_at = float(payload.get("cached_at") or 0)
    if cached_at <= 0:
        return None
    if (time.time() - cached_at) > NATIVE_HEALTH_CACHE_TTL_SEC:
        return None
    cached_payload = payload.get("payload")
    if isinstance(cached_payload, dict):
        return cached_payload
    return None


def _store_health_cache(payload: dict[str, Any]) -> None:
    if NATIVE_HEALTH_CACHE_TTL_SEC <= 0:
        return
    try:
        NATIVE_HEALTH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        NATIVE_HEALTH_CACHE_PATH.write_text(
            json.dumps({"cached_at": int(time.time()), "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        return


def main() -> int:
    result = run_native_scan()
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def health_payload(*, probe: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available_backends": available_backends(),
        "default_target_dir": DEFAULT_TARGET_DIR,
    }
    if not probe:
        payload["probe_mode"] = "disabled-by-default"
        return payload
    cached = _load_health_cache()
    if cached is not None:
        return cached
    for backend in payload["available_backends"]:
        try:
            result = run_native_scan(backend=backend, json_output=True, limit=1, release=(backend == "rust"), timeout=120)
            payload[backend] = {
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "has_json": isinstance(result.json_payload(), dict),
            }
        except Exception as exc:
            payload[backend] = {"ok": False, "error": str(exc)}
    payload["probe_mode"] = "executed"
    _store_health_cache(payload)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
