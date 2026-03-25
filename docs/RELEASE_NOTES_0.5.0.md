# Context Mesh Foundry 0.5.0

## Summary

`0.5.0` 把 Context Mesh Foundry 定位为可商用的本地单体产品，所有 context/agent/守护进程操作都在本地单一执行路径完成，远端依赖默认关闭，部署途中不再需要 MCP 或容器。

默认运行时行为：

- 本地优先，局限在本机资源与 SQLite 索引，Benchmark 驱动性能证据由工程团队掌控。
- 无 MCP、无 Docker，唯一的外部依赖是用户机器本身。
- 统一 CLI (`contextmesh`) 与 canonical entrypoints 保持稳定，任何 native 迁移都必须保留相同的操作体验。
- 建议用 `python3 scripts/context_cli.py health`、`python3 scripts/context_cli.py smoke` 与 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 将状态验证纳入发布流程。

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
- README/CHANGELOG/docs/RELEASE_NOTES_0.5.0.md 统一强调商业价值、FAQ、安装矩阵与 native 路线，并围绕 `python3 scripts/context_cli.py health`、`smoke` 与 `native-scan --backend auto --threads 4` 命令构建验证流程。

## Product Direction

The release strategy is deliberately staged:

1. converge Python into a stable local monolith
2. benchmark real hotspots
3. replace only hot paths in Rust or Go
4. keep the operator-facing product stable throughout while recording benchmark data before every native swap

## 安装矩阵

1. **Linux (x86_64/ARM64)**：准备 Python 3.11、SQLite、bash，`git clone https://github.com/dunova/context-mesh-foundry.git && cd context-mesh-foundry && cp .env.example .env && bash scripts/unified_context_deploy.sh`，再用 `python3 scripts/context_cli.py health`、`python3 scripts/context_cli.py smoke` 与 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 验证守护进程与 native 后端。
2. **macOS (Intel/Apple Silicon)**：确认 `/opt/homebrew/bin` 在 PATH，系统 Python 3.11 或 `pyenv` 安装好后与 Linux 同步运行上面的脚本，必要时用 `brew install sqlite` 补齐 sqlite3，并按顺序运行 `python3 scripts/context_cli.py health`、`python3 scripts/context_cli.py smoke` 与 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 做验证。
3. **Windows (WSL2 / PowerShell)**：在 WSL 2 (Ubuntu 22.04+) 内运行 `git clone https://github.com/dunova/context-mesh-foundry.git && cd context-mesh-foundry && cp .env.example .env && bash scripts/unified_context_deploy.sh`，使用 `python3 scripts/context_cli.py health`、`python3 scripts/context_cli.py native-scan --backend auto --threads 4` 做健康检查，避免跨环境权限冲突。

## Native 路线

1. 先用 `python -m benchmarks --query <真实业务>` 量化 Python 单体的 latency/throughput，并把结果写入 release notes。
2. 把瓶颈抽象成 `native/session_scan`（Rust）或 `native/session_scan_go`（Go）原型，保持 `context_cli.py native-scan --backend <auto|rust|go>` 入口不变。
3. 每次 Native 替换都保持相同 CLI 参数，先用 `python3 scripts/context_cli.py native-scan --backend auto --threads 4` 复测，再用 `cargo run --release` 或 `go run` 校验性能，并随后运行 `python3 scripts/context_cli.py health` 与 `python3 scripts/context_cli.py smoke` 确保守护进程、viewer 与扫描链路都正常。
4. 发布前先跑 `python3 -m benchmarks --iterations 1 --warmup 0 --query perf` 记录对比数据，把结果写入 `docs/RELEASE_NOTES_0.5.0.md` 与 `CHANGELOG.md`，让商业客户看到迁移前后指标与验证命令。

## FAQ

1. **有没有平台安装的速查表？** 见上面安装矩阵，所有平台都围绕 `bash scripts/unified_context_deploy.sh` 和 `python3 scripts/context_cli.py health` 这两条命令构建，Rust 工具链只在需要 native 原型时附加。
2. **Native 迁移会破坏已有 CLI 吗？** 不会，它始终通过 `context_cli.py native-scan` 入口；工程师只需要在 `benchmarks/` 及 `cargo run --release`/`go run` 间比较指标，就能在保留操作一致性的情况下升级。
3. **如何确认部署与 Native 替换都正常？** 每次部署或 native 替换后依次运行 `python3 scripts/context_cli.py health`、`python3 scripts/context_cli.py smoke`、`python3 scripts/context_cli.py native-scan --backend auto --threads 4`，再对比 `cargo run --release` 与 `go run` 的输出，把 benchmark & 验证命令写到 release notes 中以供客户复盘。

## Recommended Post-Release Checks

```bash
python3 scripts/context_cli.py health
python3 scripts/context_cli.py smoke
python3 scripts/context_cli.py native-scan --backend auto --threads 4
python3 scripts/e2e_quality_gate.py
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
python3 scripts/smoke_installed_runtime.py
```

## Upgrade Note

If you were previously running older local services such as `recall-lite`, `openviking`, `aline`, or older daemon/log names, remove those remnants and redeploy via:

```bash
bash scripts/unified_context_deploy.sh
```
