# 发布清单

- [ ] `rg` 搜寻是否留有 secrets、硬编码主机路径或本地用户配置。  
- [ ] `bash -n scripts/*.sh` 保证所有 Shell 脚本语法正确。  
- [ ] `python3 -m py_compile scripts/*.py` 与 `python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_session_index.py`。  
- [ ] `python3 scripts/e2e_quality_gate.py` 或等效的集成质量门。  
- [ ] `python3 scripts/context_cli.py health` 或 `bash scripts/context_healthcheck.sh --deep` 验证主链健康状态。  
- [ ] README 与文档同步更新任何新增环境变量、配置项或行为变更（特别是 `docs/ARCHITECTURE.md`、`CONTRIBUTING.md`、`docs/TROUBLESHOOTING.md`）。  
- [ ] GitHub Actions / CI 检查通过，并留存关键步骤输出便于复盘。  
- [ ] 发布说明包含新特性/风险/迁移注意事项，并在 PR 中列出验证命令与结果。
