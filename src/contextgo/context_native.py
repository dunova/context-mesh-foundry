#!/usr/bin/env python3
"""Rust/Go backend orchestration for ContextGO native session scanning."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from context_config import env_int, env_str
except ImportError:  # pragma: no cover
    from .context_config import env_int, env_str  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Repository layout
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
NATIVE_ROOT: Path = REPO_ROOT / "native"
RUST_PROJECT: Path = NATIVE_ROOT / "session_scan"
GO_PROJECT: Path = NATIVE_ROOT / "session_scan_go"


# ---------------------------------------------------------------------------
# Runtime configuration (resolved once at import time)
# ---------------------------------------------------------------------------


def _default_native_target_dir() -> str:
    """Return a user-owned build-artifact cache directory.

    Prefers ``XDG_CACHE_HOME`` when set; falls back to ``~/.cache`` so that
    builds from different OS users never share artifacts.
    """
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return str(base / "contextgo" / "target")


DEFAULT_TARGET_DIR: str = env_str(
    "CONTEXTGO_NATIVE_TARGET_DIR",
    default=_default_native_target_dir(),
)

# TTL of 0 disables the on-disk health-probe cache entirely.
NATIVE_HEALTH_CACHE_TTL_SEC: int = env_int(
    "CONTEXTGO_NATIVE_HEALTH_CACHE_TTL_SEC",
    default=30,
    minimum=0,
)

NATIVE_HEALTH_CACHE_PATH: Path = Path(DEFAULT_TARGET_DIR) / "native_health_cache.json"
RUST_RELEASE_BIN: Path = Path(DEFAULT_TARGET_DIR) / "release" / "session_scan"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class NativeRunResult:
    """Encapsulates the outcome of a single native backend invocation."""

    backend: str
    returncode: int
    stdout: str
    stderr: str
    command: list[str]
    error: str | None = None

    # Private parse cache — not part of the public interface.
    # _payload_parsed distinguishes "not yet parsed" from "parsed but None".
    _payload_cache: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _payload_parsed: bool = field(default=False, init=False, repr=False)
    _payload_error: str | None = field(default=None, init=False, repr=False)

    def json_payload(self) -> dict[str, Any] | None:
        """Parse stdout as JSON and return the top-level object, or ``None``.

        The result is memoised after the first call.  If stdout is not valid
        JSON at the top level a best-effort search for an embedded ``{…}``
        block is attempted, which handles backends that emit diagnostic lines
        before or after the actual payload.
        """
        if self._payload_parsed:
            return self._payload_cache

        self._payload_parsed = True

        text = (self.stdout or "").strip()
        if not text:
            return None

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            self._payload_error = str(exc)
            snippet = self._find_json_snippet(text)
            if snippet is not None:
                try:
                    parsed = json.loads(snippet)
                except json.JSONDecodeError as nested:
                    self._payload_error = f"{self._payload_error}; fallback parse failed: {nested}"
                    return None
            else:
                return None

        if not isinstance(parsed, dict):
            self._payload_error = "top-level JSON value is not an object"
            return None

        self._payload_cache = parsed
        return parsed

    def error_details(self) -> list[str]:
        """Return a list of human-readable error strings for this result."""
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
        """Extract the outermost ``{…}`` substring from *text*, or ``None``."""
        start = text.find("{")
        if start == -1:
            return None
        end = text.rfind("}")
        if end <= start:
            return None
        return text[start : end + 1]


@dataclass(frozen=True)
class NativeMatch:
    """A single match record returned by a native backend."""

    source: str
    path: Path
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NativeMatch | None:
        """Construct a NativeMatch from a raw payload dict, or return ``None``."""
        source = str(raw.get("source") or "").strip()
        path_text = str(raw.get("path") or "").strip()
        if not source or not path_text:
            return None
        return cls(source=source, path=Path(path_text), metadata=raw)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def parse_native_matches(result: NativeRunResult) -> list[NativeMatch]:
    """Return all valid NativeMatch records from a NativeRunResult."""
    payload = result.json_payload()
    if not isinstance(payload, dict):
        return []
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return []
    out: list[NativeMatch] = []
    for raw in matches:
        if not isinstance(raw, dict):
            continue
        match = NativeMatch.from_dict(raw)
        if match is not None:
            out.append(match)
    return out


def extract_matches(result: NativeRunResult) -> list[dict[str, Any]]:
    """Return the raw metadata dict for every match in *result*."""
    return [m.metadata for m in parse_native_matches(result)]


def inventory_items(result: NativeRunResult) -> list[tuple[str, Path]]:
    """Return ``(source, path)`` pairs for every match in *result*."""
    return [(m.source, m.path) for m in parse_native_matches(result)]


# ---------------------------------------------------------------------------
# Backend discovery
# ---------------------------------------------------------------------------


def available_backends() -> list[str]:
    """Return the backends that are both installed on PATH and have source trees.

    Checks for ``cargo`` (Rust) and ``go`` (Go) on PATH, and verifies that
    the corresponding source directory exists under ``native/``.
    """
    backends: list[str] = []
    if shutil.which("cargo") and RUST_PROJECT.exists():
        backends.append("rust")
    if shutil.which("go") and GO_PROJECT.exists():
        backends.append("go")
    return backends


def resolve_backend(requested: str) -> str:
    """Resolve *requested* to a concrete backend name.

    Passing ``"auto"`` selects Rust when available, then Go.  Any other value
    is validated against the installed set and returned as-is.

    Raises:
        RuntimeError: if the requested backend is unavailable, or if
            ``"auto"`` finds no installed backend.
    """
    backends = available_backends()

    if requested == "auto":
        for candidate in ("rust", "go"):
            if candidate in backends:
                return candidate
        raise RuntimeError("no native backend is available (neither cargo nor go found on PATH)")

    if requested not in backends:
        raise RuntimeError(f"requested native backend '{requested}' is not available (installed: {backends or 'none'})")
    return requested


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------


def _rust_binary_is_fresh() -> bool:
    """Return ``True`` when the pre-built Rust release binary is up-to-date.

    Compares the binary mtime against ``Cargo.toml`` and every ``*.rs``
    source file.  Returns ``False`` if the binary does not exist.
    """
    if not RUST_RELEASE_BIN.exists():
        return False
    binary_mtime = RUST_RELEASE_BIN.stat().st_mtime
    candidates: list[Path] = [
        RUST_PROJECT / "Cargo.toml",
        *list((RUST_PROJECT / "src").rglob("*.rs")),
    ]
    return all(not candidate.exists() or candidate.stat().st_mtime <= binary_mtime for candidate in candidates)


def _append_scan_flags(
    cmd: list[str],
    *,
    codex_root: str | None,
    claude_root: str | None,
    threads: int,
    query: str | None,
    limit: int | None,
    json_output: bool,
) -> None:
    """Append common scanner CLI flags to *cmd* in-place."""
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
    """Return ``(argv, cwd, env)`` for a Rust backend invocation.

    When *release* is True and a fresh pre-built binary is found it is invoked
    directly.  Otherwise ``cargo run`` is used, which also handles first-time
    compilation.
    """
    env: dict[str, str] = os.environ.copy()
    env.setdefault("CONTEXTGO_ACTIVE_WORKDIR", str(Path.cwd()))

    if release and _rust_binary_is_fresh():
        cmd: list[str] = [str(RUST_RELEASE_BIN)]
        _append_scan_flags(
            cmd,
            codex_root=codex_root,
            claude_root=claude_root,
            threads=threads,
            query=query,
            limit=limit,
            json_output=json_output,
        )
        return cmd, REPO_ROOT, env

    cmd = ["cargo", "run"]
    if release:
        cmd.append("--release")
    cmd.extend(["--manifest-path", str(RUST_PROJECT / "Cargo.toml"), "--"])
    _append_scan_flags(
        cmd,
        codex_root=codex_root,
        claude_root=claude_root,
        threads=threads,
        query=query,
        limit=limit,
        json_output=json_output,
    )
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
    """Return ``(argv, cwd, env)`` for a Go backend invocation."""
    cmd: list[str] = ["go", "run", "."]
    _append_scan_flags(
        cmd,
        codex_root=codex_root,
        claude_root=claude_root,
        threads=threads,
        query=query,
        limit=limit,
        json_output=json_output,
    )
    env: dict[str, str] = os.environ.copy()
    env.setdefault("CONTEXTGO_ACTIVE_WORKDIR", str(Path.cwd()))
    return cmd, GO_PROJECT, env


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------


def _decode_process_stream(raw: bytes | str | None) -> str:
    """Safely decode a stream value that may be bytes, str, or None."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode(errors="replace")
    return raw


def _execute_native_command(
    *,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    backend: str,
) -> NativeRunResult:
    """Run *cmd* and return a NativeRunResult regardless of exit status.

    All execution failures are captured in the returned result; this function
    never raises.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return NativeRunResult(
            backend=backend,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            command=cmd,
        )
    except subprocess.TimeoutExpired as exc:
        message = f"native backend timed out after {timeout}s"
        stdout = _decode_process_stream(exc.stdout)
        stderr_prefix = _decode_process_stream(exc.stderr).rstrip()
        stderr = f"{stderr_prefix}\n{message}".lstrip() if stderr_prefix else message
        return NativeRunResult(
            backend=backend,
            returncode=-1,
            stdout=stdout,
            stderr=stderr,
            command=cmd,
            error=message,
        )
    except FileNotFoundError as exc:
        message = f"binary not found — {cmd[0]}: {exc}"
        return NativeRunResult(
            backend=backend,
            returncode=-1,
            stdout="",
            stderr=message,
            command=cmd,
            error=message,
        )
    except PermissionError as exc:
        message = f"permission denied executing {cmd[0]}: {exc}"
        return NativeRunResult(
            backend=backend,
            returncode=-1,
            stdout="",
            stderr=message,
            command=cmd,
            error=message,
        )
    except OSError as exc:
        message = f"OS error executing {cmd[0]}: {exc}"
        return NativeRunResult(
            backend=backend,
            returncode=-1,
            stdout="",
            stderr=message,
            command=cmd,
            error=message,
        )


# ---------------------------------------------------------------------------
# Public scan entry point
# ---------------------------------------------------------------------------


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
    """Invoke the selected native backend and return the result.

    Args:
        backend: ``"auto"``, ``"rust"``, or ``"go"``.
        codex_root: Override for the Codex workspace root path.
        claude_root: Override for the Claude workspace root path.
        threads: Worker thread count forwarded to the backend.
        release: Build the Rust backend in release mode.
        query: Optional text filter forwarded to the backend.
        json_output: Request JSON-formatted output from the backend.
        limit: Cap the number of results returned by the backend.
        timeout: Wall-clock timeout in seconds for the subprocess.

    Returns:
        :class:`NativeRunResult` with stdout/stderr and the subprocess exit
        code.

    Raises:
        RuntimeError: if no suitable backend can be resolved.
    """
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


# ---------------------------------------------------------------------------
# Health-probe TTL cache (on-disk, mode 0600)
# ---------------------------------------------------------------------------


def _load_health_cache() -> dict[str, Any] | None:
    """Return the cached health payload if it is present and has not expired.

    Returns ``None`` when the cache is disabled (TTL == 0), missing, corrupt,
    or older than :data:`NATIVE_HEALTH_CACHE_TTL_SEC` seconds.
    """
    if NATIVE_HEALTH_CACHE_TTL_SEC <= 0:
        return None
    if not NATIVE_HEALTH_CACHE_PATH.exists():
        return None

    try:
        raw = NATIVE_HEALTH_CACHE_PATH.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(envelope, dict):
        return None

    try:
        cached_at = float(envelope.get("cached_at") or 0)
    except (TypeError, ValueError):
        return None

    if cached_at <= 0 or (time.time() - cached_at) > NATIVE_HEALTH_CACHE_TTL_SEC:
        return None

    inner = envelope.get("payload")
    return inner if isinstance(inner, dict) else None


def _store_health_cache(payload: dict[str, Any]) -> None:
    """Persist *payload* to the health cache file with mode ``0600``.

    Silently skips writes when the TTL is disabled or the filesystem is
    unavailable.  The cache directory is created on demand with mode ``0700``.
    """
    if NATIVE_HEALTH_CACHE_TTL_SEC <= 0:
        return

    envelope = json.dumps(
        {"cached_at": int(time.time()), "payload": payload},
        ensure_ascii=False,
    )
    cache_dir = NATIVE_HEALTH_CACHE_PATH.parent

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    with contextlib.suppress(OSError):
        os.chmod(cache_dir, 0o700)

    try:
        fd = os.open(
            str(NATIVE_HEALTH_CACHE_PATH),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(envelope)
    except OSError:
        return


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------


def health_payload(*, probe: bool = False) -> dict[str, Any]:
    """Return a health status dict describing the available native backends.

    When *probe* is ``False`` (the default) only static availability
    information is returned — no subprocesses are spawned.  When *probe* is
    ``True`` each available backend is invoked with a minimal scan to verify
    end-to-end operation; the result is cached on disk for
    :data:`NATIVE_HEALTH_CACHE_TTL_SEC` seconds.

    Args:
        probe: Execute a live probe of each backend when ``True``.

    Returns:
        Dict containing ``"available_backends"``, per-backend status entries,
        and ``"probe_mode"``.
    """
    payload: dict[str, Any] = {
        "available_backends": available_backends(),
        "default_target_dir": DEFAULT_TARGET_DIR,
    }

    if not probe:
        payload["probe_mode"] = "disabled"
        return payload

    cached = _load_health_cache()
    if cached is not None:
        return cached

    for backend in payload["available_backends"]:
        try:
            result = run_native_scan(
                backend=backend,
                json_output=True,
                limit=1,
                release=(backend == "rust"),
                timeout=120,
            )
            payload[backend] = {
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "has_json": isinstance(result.json_payload(), dict),
            }
        except (OSError, RuntimeError) as exc:
            payload[backend] = {"ok": False, "error": str(exc)}

    payload["probe_mode"] = "executed"
    _store_health_cache(payload)
    return payload


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Run a native scan with default settings and stream output to stdout/stderr."""
    result = run_native_scan()
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
