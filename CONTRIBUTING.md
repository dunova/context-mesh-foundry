# 贡献指南

## 核心理念
- 以 ContextGO 单体产品为视角，所有变更必须服务于 `scripts/context_cli.py`、`scripts/context_daemon.py`、`scripts/context_maintenance.py` 等核心入口，不再围绕历史兼容或分支架构。  
- 默认链路面向本地运行：本地索引、健康检查与可观测性优先，远程/外部路径仅在明确开启的功能中出现。  
- 安装态 smoke/benchmark 是健康阈值：`scripts/context_smoke.py` 与 `scripts/smoke_installed_runtime.py` 负责覆盖本地与安装态的 `context_cli`、quality gate、导入导出、viewer/serve 路径，确保默认运行链路在 smoke 下可用。Smoke 依赖 `scripts/context_config.storage_root()`（默认 `~/.contextgo`，可由 `CONTEXTGO_STORAGE_ROOT` 覆盖）可读写，并要求安装态 `INSTALL_ROOT=~/.local/share/contextgo/scripts` 下至少存在 `context_cli.py` 与 `e2e_quality_gate.py`；发布产物同时还应保留 `context_healthcheck.sh`、`benchmarks/run.py` 等运维入口，避免健康检查与 benchmark 流程失效。
- 始终将快速恢复、静默失败与最小 surprise 作为衡量标准，避免在默认路径上引入额外依赖、网络拨测或多主机同步。  
- 不在提交中带入 secrets、绝对主机路径或特定用户配置。  

## 工作流程
- 所有新工作以 `main` 分支的 ContextGO 版本为目标，避免回退到 `scripts/legacy`、旧桥接层或其他历史路径；这些路径仅保留备份状态，无需主动更新。  
- 改动前请 `git pull` 保持 workspace 与远端对齐，合并完成后通过 `git status` 确认改动范围。  
- 任何涉及默认入口逻辑的改动（如 `context_cli`、`context_daemon`、`session_index`、`memory_index`）都应附带清晰描述、单元/集成验证命令与观察指标。  
- 小型修复可以直接提交 PR；跨模块改动建议先开讨论 issue 以协调影响面。  

## 本地验证与安装态 Smoke
在提交前至少覆盖以下路径，优先在本地运行后再验证安装态：

```
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_session_index.py
python3 scripts/e2e_quality_gate.py
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
python3 scripts/context_smoke.py
python3 scripts/smoke_installed_runtime.py
bash scripts/context_healthcheck.sh
```

上述命令依赖 `CONTEXTGO_STORAGE_ROOT` 默认指向 `~/.contextgo`，因此请先验证 `scripts/context_config.py` 中的 storage root 与测试用户一致，再运行 smoke/benchmark。  
额外注意：`python3 scripts/smoke_installed_runtime.py` 默认从 `INSTALL_ROOT=~/.local/share/contextgo/scripts` 载入 `context_cli.py` 与 `e2e_quality_gate.py`；若显式设置了 `CONTEXTGO_INSTALL_ROOT`，脚本也会跟随该目录探测。发布产物还应在默认 tree 下保留 `context_healthcheck.sh`、`benchmarks/run.py` 等运维入口，并确认 smoke 启动前路径可读。

如改动仅影响 docs/配置，可跳过 smoke 但需在 PR 中说明原因。  

## 代码风格与习惯
- Shell 脚本遵循 `set -euo pipefail`，优先轻量命令组合，不依赖非标准工具。  
- Python 优先使用标准库，依赖外部包时需在 `requirements.txt` 中声明并在 CI 中验证。  
- Rust/Go 代码应保持单一功能模块，先用小型 prototypes 证明后再扩展。  
- 注释仅用于解释非显而易见的运维或运行时逻辑，逻辑清晰页面尽量让代码自说明。  
- Benchmarks 放在 `benchmarks/`，调优或热点改动请在同一目录下补齐基线对比。  

## 贡献前检查清单
- 确认没引入 secrets / 绝对路径，可使用 `rg` 搜散列关键词（如 `AKIA`、`password`）。  
- README 与文档同步更新新增环境变量或行为变化。  
- 若改动会影响 release 流程，通知 release owner 并在 PR 中提及相关健康检查步骤。  
