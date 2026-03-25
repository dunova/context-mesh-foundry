# Contributing

## Principles

- Prefer the standalone `contextmesh` mainline over legacy compatibility paths.
- Keep the default path local-first, quiet, predictable, and MCP-free.
- Optimize for recovery, observability, and low operator surprise.
- No secrets, tokens, or machine-specific absolute paths in commits.
- New work should land behind canonical entrypoints:
  - `scripts/context_cli.py`
  - `scripts/context_daemon.py`
  - `scripts/context_server.py`
  - `scripts/context_maintenance.py`

## Change Strategy

- Treat `scripts/legacy/` as archived compatibility code, not the preferred integration surface.
- Avoid re-introducing direct dependencies on external recall/MCP stacks into the default path.
- Prefer `CONTEXT_MESH_*` env vars for new configuration. Legacy names may remain only for compatibility.
- If you touch a hotspot, add or update a benchmark under [`benchmarks/`](/Volumes/AI/GitHub/context-mesh-foundry/benchmarks).

## Local Validation

```bash
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_session_index.py
python3 scripts/e2e_quality_gate.py
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
```

If you modify daemon, viewer, deploy, or legacy wrappers, also do one real local smoke test against the installed runtime under:

`/Users/<you>/.local/share/context-mesh-foundry`

## Style

- Shell: POSIX-ish bash, `set -euo pipefail`
- Python: stdlib first, small targeted dependencies
- Rust/Go: prefer small, isolated hot-path prototypes before widening scope
- Comments: only for non-obvious operational logic
