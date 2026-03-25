# 故障排查

## 1. 首次索引构建慢

- **现象**：`python3 scripts/context_cli.py health` 或 `search` 第一次运行较慢。
- **原因**：`session_index.py` 需要扫描本机历史并建立 SQLite 索引。
- **解决**：
  - 首次运行结束后再试一次
  - 检查 `~/.unified_context_data/index/session_index.db`
  - 用 `python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark` 观察基准结果

## 2. viewer 无法访问

- **现象**：`context_cli.py serve` 启动后无法打开 `/api/health`
- **原因**：常见于端口冲突、索引首次同步较慢，或旧安装版本仍在运行
- **解决**：
  - 先运行 `python3 scripts/context_cli.py health`
  - 再运行 `bash scripts/unified_context_deploy.sh`
  - 使用 `python3 scripts/smoke_installed_runtime.py` 做安装态烟测

## 3. 搜索结果为空

- **现象**：`context_cli search ...` 没有命中最近会话
- **原因**：本地历史文件路径变化，或 daemon 尚未写入/索引尚未刷新
- **解决**：
  - 检查 `~/.codex/sessions`、`~/.claude/projects`、`~/.zsh_history`
  - 运行 `python3 scripts/context_cli.py health`
  - 再运行 `python3 scripts/e2e_quality_gate.py`

## 4. 权限或路径问题

- **现象**：访问本地索引/记忆目录时出现权限错误
- **解决**：
  - 确认目录位于 `~/.unified_context_data`
  - 确认运行用户与文件归属一致
  - 必要时重新部署：

```bash
bash scripts/unified_context_deploy.sh
```

## 5. 发布前检查

发布前建议至少执行：

```bash
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py scripts/test_session_index.py
python3 scripts/e2e_quality_gate.py
python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark
python3 scripts/smoke_installed_runtime.py
```
