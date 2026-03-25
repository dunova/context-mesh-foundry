# Context Mesh Foundry 0.5.0

## Summary

`0.5.0` 把 Context Mesh Foundry 定位为可商用的本地单体产品，所有 context/agent/守护进程操作都在本地单一执行路径完成，远端依赖默认关闭，部署途中不再需要 MCP 或容器。

默认运行时行为：

- 本地优先，局限在本机资源与 SQLite 索引，Benchmark 驱动性能证据由工程团队掌控。
- 无 MCP、无 Docker，唯一的外部依赖是用户机器本身。
- 统一 CLI (`contextmesh`) 与 canonical entrypoints 保持稳定，任何 native 迁移都必须保留相同的操作体验。

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
- Remote sync disabled by default to prioritize predictable local behavior
- Benchmark harness added so operators can reproduce latency/throughput before native migration
- First Rust hot-path prototype added, showing a concrete Native 迁移路线 without breaking the CLI

## Product Direction

The release strategy is deliberately staged:

1. converge Python into a stable local monolith
2. benchmark real hotspots
3. replace only hot paths in Rust or Go
4. keep the operator-facing product stable throughout while recording benchmark data before every native swap

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
