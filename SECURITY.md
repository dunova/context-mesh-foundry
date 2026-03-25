# 安全政策

## 适用范围
- 本仓库仅维护 ContextGO 单体运行时，主线入口为 `scripts/context_cli.py`、`scripts/context_daemon.py`、`scripts/context_maintenance.py`、`scripts/session_index.py`、`scripts/memory_index.py`。默认不再依赖旧桥接层，统一路径指向本地 `contextgo` 安装与 smoke/benchmark 环境。
- 默认运行链路 local-first，任何远程/云 recall、部署入口必须显式开启且在文档中说明，并提供本地 smoke/benchmark 覆盖路径（`python3 scripts/context_smoke.py`、`python3 scripts/smoke_installed_runtime.py`、`python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark`）。`context_smoke.py` 与 benchmark 依赖 `scripts/context_config.storage_root()`（默认 `~/.contextgo` 或由 `CONTEXTGO_STORAGE_ROOT` 覆盖），请先确认路径归属当前用户、可写且不映射历史数据树。

## 报告流程
1. 私下联系安全负责人（若无统一渠道，可在 PR/issue 中@团队安全负责人或直接在公司内部安全邮箱提交）。  
2. 提供受影响模块、复现步骤、影响范围、建议缓解方案。  
3. 若怀疑暴露敏感信息，优先使用加密/私有渠道递交，避免在公开 issue 中泄露细节。

## 重要控制
- 本地优先：默认不会在运行时依赖外部服务，所有默认配置都在本机文件系统操作。  
- 配置最小化：不读取全局环境变量除非在 `scripts/context_cli.py` 中明确声明；新增环境变量要同步更新文档并在 smoke/benchmark 中验证。任何对 `CONTEXTGO_STORAGE_ROOT` 的调整必须确保 `scripts/context_config.storage_root()` 仍指向当前用户可访问的 `~/.contextgo`（或显式设置的路径），并通过 `bash scripts/context_healthcheck.sh`（可附 `--deep` 探测）确认权限与运行环境。
- 运行时日志、索引结果文件的默认权限为 `0600`，避免意外泄露。  
- 所有写入历史/记忆的路径必须通过脱敏流程（`context_daemon` 中的 `<private>` 过滤）处理。  
- smoke/benchmark 覆盖：变更后需运行 `python3 scripts/context_smoke.py`、`python3 scripts/smoke_installed_runtime.py`、`python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark`，验证默认 `context_cli`/`context_daemon`/`context_server` 健康。Smoke 会在确认 `scripts/context_config.storage_root()` 可写之后，从 `INSTALL_ROOT=~/.local/share/contextgo/scripts` 调用 `context_cli.py` 与 `e2e_quality_gate.py`；发布产物应在该 INSTALL_ROOT 同时保留 `context_healthcheck.sh`、`benchmarks/run.py` 等运维入口，避免健康检查与 benchmark 流程断裂。

## 贡献者守则
- 禁止在提交中包含 secrets（API key、token、密码）或机器/用户专属路径，必要时替换为 `XXX` 并在 PR 描述中说明。  
- 新功能优先落在主链脚本，向 `scripts/legacy` 等遗留模块添加功能需先评估是否能迁移至主链。  
- 安全验证命令：`python3 scripts/context_cli.py health`、`python3 scripts/context_smoke.py`、`python3 scripts/smoke_installed_runtime.py`、`python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark` 及 `bash -n scripts/*.sh`、`python3 -m pytest scripts/test_context_core.py`，并在 PR 中汇总结果。
