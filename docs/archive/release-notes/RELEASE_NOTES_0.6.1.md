# ContextGO 0.6.1

## Summary

`0.6.1` 是今天这一轮大收口后的正式商业发布版。
它的目标不是“再加功能”，而是把多轮快速迭代留下的边缘补丁收平，形成一个更像成品的本地单体运行时。

本版完成了三件关键事：

1. 品牌统一到 `ContextGO`
2. Native 与 benchmark 语义收口到更诚实、更稳定的状态
3. 文档、部署、已安装运行时 smoke、主链健康检查再对齐一次

## What Shipped

- 重写了 README，统一商业叙事、安装路径、命令矩阵与 FAQ。
- 默认运行时目录切换到 `~/.local/share/contextgo`，服务标签切换到 `com.contextgo.*`。
- 重新梳理了 Go scanner 的 query 窗口与噪声规则，减少 direct native-scan 的空结果。
- 给 `context_native.py` 增加了 native health probe 缓存，避免 `health` 被重复 probe 拉慢。
- benchmark 里把原“native”重新定义为 `native-wrapper`，明确它测的是子进程包装层成本，而不是纯 Go/Rust 核心执行时间。

## Release Positioning

ContextGO 现在适合作为一个可交付的本地产品，而不只是内部实验仓库：

- 可直接在研发团队机器上部署
- 可作为私有上下文运行时使用
- 可在不引入 MCP/向量云服务的前提下支持高频 AI 编码工作流
- 可作为后续 Rust/Go 深度重写前的稳定基线

## GitHub Release Summary

ContextGO 0.6.1 是一个面向多 agent AI 编码团队的本地优先上下文运行时发布版。

它把搜索、记忆、viewer、health、smoke 与 Rust/Go 热路径统一到一条默认无 MCP、无 Docker、无云向量依赖的 CLI 上，重点不是“再加更多编排”，而是把上下文系统做成一个可交付、可审计、可回滚的本地产品。

This release turns ContextGO into a cleaner commercial baseline for local AI team workflows: one CLI, one validation chain, one local trust boundary, and gradual Rust/Go acceleration without changing how operators use the system.

## Key Commands

```bash
python3 scripts/context_cli.py health
python3 scripts/context_cli.py smoke
python3 scripts/context_cli.py native-scan --backend auto --threads 4
python3 scripts/smoke_installed_runtime.py
python3 -m benchmarks --mode both --iterations 1 --warmup 0 --query benchmark --format text
```

## Verification

本轮发布前已覆盖：

```bash
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py benchmarks/*.py
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_context_native.py scripts/test_context_smoke.py scripts/test_session_index.py
python3 scripts/e2e_quality_gate.py
python3 scripts/context_cli.py smoke
python3 scripts/smoke_installed_runtime.py
cd native/session_scan_go && go test ./...
cd native/session_scan && CARGO_INCREMENTAL=0 cargo test
```

补充说明：

- `context_cli.py smoke` 现在已经包含 `native_scan` 合同校验，会分别对 Rust / Go backend 做最小 fixture 实跑，不再只看 CLI 健康。
- Go 原生扫描与 Rust 原生扫描都已经统一到当前调用侧 workdir 语义，避免把当前仓库优化线程误当成历史召回结果。

## Migration Note

品牌已改为 `ContextGO`，但为了平滑升级，以下兼容项暂时保留：

- 安装目录：`~/.local/share/contextgo`
- systemd/launchd 标签：`com.contextgo.daemon` / `com.contextgo.healthcheck`
- 环境变量前缀：`CONTEXTGO_*`

这样可以确保旧安装态不必一次性重做，也方便出现问题时快速回滚。

## Decision on Vector API

本版结论是：**默认不需要向量 API**。

原因：

- 当前主链已经有可用的精确索引、本地记忆、结构化 snippet 和 SQLite 回退。
- 向量 API 会显著增加 token、网络、运维与隐私边界成本。
- 对当前产品定位而言，先把本地索引做到稳、快、准，比尽快接向量层更重要。

只有在以下场景下，才值得作为可选增强评估向量层：

- 超长文本块的弱语义召回
- 同义表达非常多的跨项目知识搜索
- 跨语言、跨领域、低关键词重合的深语义发现

默认发布版仍坚持：本地优先、无 MCP、低 token、低 surprise。
