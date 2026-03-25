# Context Mesh Foundry 0.5.0

## Summary

`0.5.0` is the first release where Context Mesh Foundry behaves like a standalone product rather than a loose collection of integration scripts.

The default runtime is now:

- local-first
- MCP-free
- Docker-free
- centered on a unified CLI
- packaged around canonical entrypoints

## Highlights

- Unified CLI:
  - `search`
  - `semantic`
  - `save`
  - `export`
  - `import`
  - `serve`
  - `maintain`
  - `health`
- Built-in local session index backed by SQLite
- Canonical daemon / server / maintenance entrypoints
- Legacy code isolated behind thin wrappers and archived under `scripts/legacy/`
- Remote sync disabled by default
- Benchmark harness added
- First Rust hot-path prototype added

## Product Direction

The release strategy is deliberately staged:

1. converge Python into a stable local monolith
2. benchmark real hotspots
3. replace only hot paths in Rust or Go
4. keep the operator-facing product stable throughout

## Recommended Post-Release Checks

```bash
python3 scripts/context_cli.py health
python3 scripts/e2e_quality_gate.py
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
```

## Upgrade Note

If you were previously running older local services such as `recall-lite`, `openviking`, `aline`, or older daemon/log names, remove those remnants and redeploy via:

```bash
bash scripts/unified_context_deploy.sh
```
