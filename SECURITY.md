# Security Policy

## Supported Scope

This repository now ships a standalone local runtime, not just integration helpers.

Supported primary surface:

- `scripts/context_cli.py`
- `scripts/context_daemon.py`
- `scripts/context_server.py`
- `scripts/context_maintenance.py`
- `scripts/session_index.py`
- `scripts/memory_index.py`

Legacy wrappers and files under `scripts/legacy/` are best-effort compatibility surfaces and are lower priority than the canonical mainline.

## Reporting a Vulnerability

Please do **not** open a public issue for secrets exposure or active exploitation paths.

Report privately with:
- affected script/path
- reproduction steps
- impact
- suggested mitigation (if any)

## Local Secret Hygiene

Before sharing logs or configs:
- remove API keys / tokens / passwords
- redact hostnames and internal IPs if needed
- avoid uploading `ov.conf` unless fully scrubbed

## Threat Model (Practical)

This repo assumes:
- a trusted local machine
- untrusted input inside terminal histories and prompts
- need to avoid accidental secret propagation into shared memory

Controls included:
- shell-history secret redaction patterns in the daemon
- file permission checks (`0600`) for written memory artifacts
- local-first default mode with remote sync disabled unless explicitly enabled
- `trust_env=False` on HTTP clients that still exist for optional remote paths

## Security Expectations For Contributors

- Prefer local-only execution paths when adding features.
- Do not make remote sync, MCP, or external services mandatory for the default path.
- Keep benchmarks and tests free of production secrets.
- Treat viewer endpoints as local tools; do not broaden exposure without an explicit security pass.
