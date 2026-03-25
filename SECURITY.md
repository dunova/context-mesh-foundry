# 安全政策

## 适用范围
- 本仓库仅提供 contextmesh 单体运行时，核心脚本包括 `scripts/context_cli.py`、`scripts/context_daemon.py`、`scripts/context_maintenance.py`、`scripts/session_index.py`、`scripts/memory_index.py`。非必要情况下不再维护 OpenViking、Aline、MCP 等历史兼容路线。
- 默认路径面向本地部署，远程同步或云 recall 功能必须通过明确开关开启且在文档/配置中说明。

## 报告流程
1. 私下联系安全负责人（若无统一渠道，可在 PR/issue 中@团队安全负责人或直接在公司内部安全邮箱提交）。  
2. 提供受影响模块、复现步骤、影响范围、建议缓解方案。  
3. 若怀疑暴露敏感信息，优先使用加密/私有渠道递交，避免在公开 issue 中泄露细节。

## 重要控制
- 本地优先：默认不会在运行时依赖外部服务，所有默认配置都在本机文件系统操作。  
- 配置最小化：不读取全局环境变量除非在 `scripts/context_cli.py` 中明确声明；新增环境变量要同步更新文档。  
- 运行时日志、索引结果文件的默认权限为 `0600`，避免意外泄露。  
- 所有写入历史/记忆的路径必须通过脱敏流程（`context_daemon` 中的 `<private>` 过滤）处理。

## 贡献者守则
- 禁止在提交中包含 secrets（API key、token、密码）或机器/用户专属路径，必要时替换为 `XXX` 并在 PR 描述中说明。  
- 向 `scripts/legacy`、`openviking_*`、`realign` 等遗留模块添加功能前需先评估是否能迁移至主链，再行变更。  
- 安全验证命令：`python3 scripts/context_cli.py health`、`bash -n scripts/*.sh`、`python3 -m pytest scripts/test_context_core.py` 等命令应在相关改动后运行并附上结果摘要。
