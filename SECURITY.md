# Security Policy

## Scope

This policy covers the ContextGO monorepo runtime. The primary entry points are:

- `src/contextgo/context_cli.py` - unified CLI
- `src/contextgo/context_daemon.py` - background sync daemon
- `src/contextgo/context_maintenance.py` - maintenance utility
- `src/contextgo/session_index.py` - session indexing
- `src/contextgo/memory_index.py` - memory/observation indexing
- `src/contextgo/memory_viewer.py` - local HTTP viewer server
- Native backends: `native/session_scan` (Rust), `native/session_scan_go` (Go)

Out-of-scope: third-party dependencies, downstream forks, or deployment infrastructure not included in this repository.

## Supported Versions

Only the latest commit on the `main` branch is actively supported with security fixes. We do not backport fixes to older releases.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

To report a vulnerability privately:

1. Report via GitHub Security Advisories at https://github.com/dunova/ContextGO/security/advisories/new
2. If you prefer, contact the maintainers directly via the repository's **Security** tab (**Report a vulnerability** button).
3. Encrypt sensitive reports using the maintainer's public PGP key if one is published in the repository.

### What to include

Please provide:

- A clear description of the vulnerability and affected component(s)
- The version or commit hash where you observed the issue
- Step-by-step reproduction instructions (proof-of-concept code or commands)
- The potential impact and attack scenario
- Any suggested mitigations or patches

### Response timeline

- **Acknowledgement**: within 72 hours of receipt
- **Initial triage**: within 7 days
- **Fix or mitigation**: within 30 days for critical/high severity; 90 days for medium/low
- **Public disclosure**: coordinated with the reporter after a fix is available

We follow responsible disclosure: if a fix is delayed beyond the agreed timeline, the reporter may publish after giving 7 days notice.

## Architecture and Security Controls

### Local-first design

By default ContextGO operates entirely on the local filesystem. No network requests are made unless explicitly configured:

- `CONTEXTGO_ENABLE_REMOTE_SYNC=0` (daemon remote sync, default: off)
- `CONTEXTGO_ENABLE_REMOTE_MEMORY_HTTP=0` (CLI HTTP export, default: off)
- The memory viewer binds to `127.0.0.1` by default and requires a token when bound to any non-loopback address.

### HTTPS enforcement

The daemon refuses to start if `CONTEXTGO_REMOTE_URL` is set to a non-localhost HTTP URL. Only `https://` is accepted for remote hosts. This is enforced at module import time in `context_daemon.py`.

### Storage root validation

`context_config.storage_root()` validates that the resolved path is absolute and has at least three path components (e.g. `/home/user/.contextgo`) to prevent accidentally using `/`, `/tmp`, or similarly dangerous roots.

### File permission hardening

- Storage root, pending directory, log directory: `0700`
- Memory markdown files, SQLite index databases, lock files, health cache: `0600`
- These permissions are set at creation time using `os.open(..., 0o600)` / `os.chmod(...)`.

### Symlink safety

The daemon checks that the configured storage root is not a symlink to a directory owned by another user. Source history files (shell history, JSONL files) that are symlinks are silently skipped via `SessionTracker._is_safe_source()`.

### Private data filtering

All content written to the index passes through `strip_private_blocks()` (removes `<private>...</private>` blocks) and the daemon's `SECRET_REPLACEMENTS` list, which redacts common credential patterns (API keys, tokens, passwords, PEM blocks, OAuth tokens, AWS access key IDs).

### Token comparison

The memory viewer uses `hmac.compare_digest` for `X-Context-Token` header comparison to prevent timing-based token enumeration attacks.

### CORS origin validation

The memory viewer parses the `Origin` request header with `urllib.parse.urlparse` and checks only the **hostname** component against the loopback allowlist (`127.0.0.1`, `localhost`, `::1`). This prevents substring-bypass attacks such as `http://evil127.0.0.1.attacker.com` that would pass a naive `in`-based string check.

### SQL parameterization

All SQLite queries use parameterized placeholders (`?`) for user-supplied values. WHERE clauses are constructed from hardcoded predicate strings; user input flows only through bind parameters. Dynamic `IN (...)` placeholders are built from `",".join("?" for _ in items)` so the number of `?` tokens always matches the bind arguments without interpolating any user data into the SQL string.

### subprocess safety

All `subprocess.run` / `subprocess.Popen` calls pass arguments as lists (never `shell=True`). Command arguments derived from user input (query strings, paths) are passed as separate list elements, never interpolated into shell strings.

### Native build artifacts

Rust and Go build artifacts default to `~/.cache/contextgo/target` (a user-owned directory) rather than a shared `/tmp` path to prevent TOCTOU races from other users on multi-tenant systems. Override with `CONTEXTGO_NATIVE_TARGET_DIR`.

### Content-Security-Policy

The HTML viewer page is served with a restrictive `Content-Security-Policy` header (`default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'self'`). All JSON API endpoints receive `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.

## Contributor Guidelines

- **No secrets in commits**: API keys, tokens, passwords, or machine-specific absolute paths must never appear in committed files. Replace with `XXX` and document in the PR description.
- **No pickle / eval / exec**: Deserializing untrusted data with `pickle`, or executing dynamic code via `eval`/`exec`, is prohibited. All external data is parsed as JSON.
- **Dependency review**: New third-party dependencies require explicit justification. Prefer stdlib where feasible.
- **Environment variable documentation**: Any new `CONTEXTGO_*` environment variable must be added to `.env.example` with a description before merging.
- **Verification commands** (run before each PR and after security-relevant changes):

  ```
  contextgo health
  contextgo smoke
  python3 scripts/smoke_installed_runtime.py
  python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
  bash -n scripts/*.sh
  python3 -m pytest tests/test_context_core.py
  ```

## Known Limitations and Non-Goals

- The local memory viewer (`contextgo serve`) is intended for single-user localhost use only. It is not hardened against adversarial clients on the network. Do not expose it to untrusted networks even with a token.
- Shell history indexing reads `~/.zsh_history` and `~/.bash_history`. These files may contain sensitive commands. The daemon applies secret-pattern redaction before indexing, but this is best-effort and not a substitute for managing shell history hygiene.
- The tool does not encrypt data at rest. Filesystem-level encryption (e.g. FileVault, LUKS) is recommended for sensitive environments.
