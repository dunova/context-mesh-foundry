# 故障排查

## 1. 首次索引构建慢

- **现象**：`python3 scripts/context_cli.py health` 或 `search` 第一次运行耗时明显。
- **原因**：`session_index.py` 扫描本机历史并建立 SQLite 索引需要完整数据。
- **解决**：
  - 完成一次 `context_cli.py health` 后再发起 search，索引会在后台追加。
  - 确认 storage root（默认 `~/.unified_context_data`）下存在 `index/session_index.db` 和 `index/memory_index.db`。
  - 运行 `python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark` 获取基准并排查是否受限于 IO 或 CPU。
  - 若依赖 `CONTEXT_MESH_STORAGE_ROOT` 等自定义目录，先在上下文脚本里 `print(storage_root())` 确认路径，再重建索引。

## 2. viewer 无法访问

- **现象**：`python3 scripts/context_cli.py serve` 启动后打开 `http://127.0.0.1:38880/api/health` 时失败。
- **原因**：端口冲突、health 未就绪、旧版本进程未退出或 `context_server` 绑定本地以外地址。
- **解决**：
  - 先用 `python3 scripts/context_cli.py health` 验证 CLI 健康，确认 `context_smoke.py`（包含 serve + viewer health 查询）通过。
  - 确认没有旧服务占用端口：`lsof -iTCP:38880` / `ps` 后 kill 再重启。
  - 若是已安装运行时（`~/.local/share/context-mesh-foundry`），执行 `python3 scripts/smoke_installed_runtime.py`，观察 viewer 访问与 quality gate 结果。

## 3. 搜索结果为空

- **现象**：`context_cli search ...` 没有命中最近会话。
- **原因**：`context_daemon` 尚未写入、新历史落在未索引目录、`session_index` 未刷新，或 clin_path 指向非默认 storage。
- **解决**：
  - 确认 `~/.unified_context_data`（或 `CONTEXT_MESH_STORAGE_ROOT` 覆盖路径）下的 `raw/`、`index/` 有更新文件。
  - 检查常见来源（如 `~/.codex/sessions/`、`~/.claude/projects/`、`~/.zsh_history`、`~/.bash_history`）是否出现在 `context_daemon` 抓取目录。
  - 运行 `python3 scripts/context_cli.py health` + `python3 scripts/context_smoke.py` 验证写入/semantic pipeline。
  - 用 `python3 scripts/e2e_quality_gate.py` 或 `python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark` 检查 index/CLI 的整体可用性。

## 4. 权限或路径问题

- **现象**：访问本地索引/记忆目录时出现权限错误。
- **解决**：
  - 确认目录位于 `scripts/context_config.py` 计算的 storage root（默认 `~/.unified_context_data`）下，`ls -ld $(storage_root)` 验证拥有者。
  - `stat` 输出确认 `index/`、`raw/` 的权限与当前用户一致。
  - 若路径被移动或清空，先用 `python3 scripts/context_daemon.py --reset`（需确认参数）或 `bash scripts/context_healthcheck.sh --local` 重建，再运行 `python3 scripts/context_smoke.py` 验证。

## 5. 发布前检查

发布前建议至少执行：

```
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_session_index.py
python3 scripts/e2e_quality_gate.py
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
python3 scripts/context_smoke.py
python3 scripts/smoke_installed_runtime.py
bash scripts/context_healthcheck.sh --local
```

上述命令依赖 `scripts/context_config.storage_root()`（默认 `~/.unified_context_data`），请确认当前用户可以读写该目录，并在 `CONTEXT_MESH_STORAGE_ROOT` / `UNIFIED_CONTEXT_STORAGE_ROOT` 替换被启用时同步更新。安装态 `scripts/smoke_installed_runtime.py` 会从 `~/.local/share/context-mesh-foundry/scripts` 载入 `context_cli.py`、`e2e_quality_gate.py` 与 `benchmarks/run.py`，发布前务必在该 `INSTALL_ROOT` 下存在这些入口脚本并验证 smoke 启动成功。
