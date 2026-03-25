# 发布清单

- [ ] `rg` 搜寻是否留有 secrets、硬编码主机路径或本地用户配置。  
- [ ] 确认 `scripts/context_config.storage_root()`（默认 `~/.contextgo`）及任何 `CONTEXTGO_STORAGE_ROOT` 覆盖路径可读写且仍归当前用户所有。  
- [ ] `bash -n scripts/*.sh` 保证所有 Shell 脚本语法正确。  
- [ ] `python3 -m py_compile scripts/*.py` 与 `python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_context_native.py scripts/test_context_smoke.py scripts/test_session_index.py`。  
- [ ] `python3 scripts/e2e_quality_gate.py` 或等效的集成质量门。  
- [ ] `python3 scripts/context_cli.py health` 或 `bash scripts/context_healthcheck.sh --deep` 验证主链健康状态。  
- [ ] `python3 scripts/context_cli.py smoke`、`python3 scripts/smoke_installed_runtime.py` 与 `python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark`，确保 smoke/benchmark 在本地与安装态均可复现。
- [ ] `cd native/session_scan_go && go test ./...` 与 `cd native/session_scan && CARGO_INCREMENTAL=0 cargo test`，确保两条 native 热路径可复现。
- [ ] 安装态 `INSTALL_ROOT=~/.local/share/contextgo/scripts` 至少包含 `context_cli.py` 与 `e2e_quality_gate.py`，并同时保留 `context_healthcheck.sh`、`benchmarks/run.py` 等运维入口，确保 smoke、healthcheck 与 benchmark 都能在安装态复现。
- [ ] README 与文档同步更新任何新增环境变量、配置项或行为变更（特别是 `docs/ARCHITECTURE.md`、`CONTRIBUTING.md`、`docs/TROUBLESHOOTING.md`）。
- [ ] GitHub Actions / CI 检查通过，并留存关键步骤输出便于复盘。  
- [ ] 发布说明包含新特性/风险/迁移注意事项，并在 PR 中列出验证命令与结果。  
- [ ] 验证发布包在 `~/.local/share/contextgo/scripts`（`scripts/smoke_installed_runtime.py` 的默认 `INSTALL_ROOT`）下包含 `context_cli.py`、`e2e_quality_gate.py`，以及 `context_healthcheck.sh`、`benchmarks/run.py` 等运维入口，并确保相关路径可读。
