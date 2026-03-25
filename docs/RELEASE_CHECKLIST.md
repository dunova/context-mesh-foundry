# 发布清单

- [ ] `rg` 搜寻是否留有 secrets、硬编码主机路径或本地用户配置。  
- [ ] 确认 `scripts/context_config.storage_root()`（默认 `~/.unified_context_data`）及任何 `CONTEXT_MESH_STORAGE_ROOT` / `UNIFIED_CONTEXT_STORAGE_ROOT` 覆盖路径可读写且仍归当前用户所有。  
- [ ] `bash -n scripts/*.sh` 保证所有 Shell 脚本语法正确。  
- [ ] `python3 -m py_compile scripts/*.py` 与 `python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_session_index.py`。  
- [ ] `python3 scripts/e2e_quality_gate.py` 或等效的集成质量门。  
- [ ] `python3 scripts/context_cli.py health` 或 `bash scripts/context_healthcheck.sh --deep` 验证主链健康状态。  
- [ ] `python3 scripts/context_smoke.py`、`python3 scripts/smoke_installed_runtime.py` 与 `python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark`，确保 smoke/benchmark 在本地与安装态均可复现。  
- [ ] README 与文档同步更新任何新增环境变量、配置项或行为变更（特别是 `docs/ARCHITECTURE.md`、`CONTRIBUTING.md`、`docs/TROUBLESHOOTING.md`）。  
- [ ] GitHub Actions / CI 检查通过，并留存关键步骤输出便于复盘。  
- [ ] 发布说明包含新特性/风险/迁移注意事项，并在 PR 中列出验证命令与结果。  
- [ ] 验证发布包在 `~/.local/share/context-mesh-foundry/scripts`（`scripts/smoke_installed_runtime.py` 的 `INSTALL_ROOT`）下包含 `context_cli.py`、`e2e_quality_gate.py`、`benchmarks/run.py` 等 smoke/benchmark 入口，并确保 smoke 启动前路径可读。
