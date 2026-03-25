# Context Mesh 单体产品架构

## 组件概览

1. **采集层**  
   `scripts/context_daemon.py` 是 canonical 守护入口，默认调度 `scripts/viking_daemon.py` 实现；它负责收集终端会话与 shell 历史，并在写入前完成 `<private>` 过滤与脱敏。

2. **索引层**  
   `scripts/session_index.py` 负责会话索引，`scripts/memory_index.py` 负责记忆/观察索引。默认落盘到：
   - `~/.unified_context_data/index/session_index.db`
   - `~/.unified_context_data/index/memory_index.db`

3. **检索与服务层**  
   `scripts/context_cli.py` 是唯一 canonical CLI，承载：
   - `search`
   - `semantic`
   - `save`
   - `export`
   - `import`
   - `serve`
   - `maintain`
   - `health`

   `scripts/context_server.py` 提供 viewer 服务入口，默认只监听本地回环地址。

4. **运维层**  
   `scripts/context_healthcheck.sh`、`scripts/unified_context_deploy.sh` 以及 `templates/{launchd,systemd-user}` 负责安装、巡检、常驻启动和日志观测。

## 数据流

1. 终端/AI 历史由 `context_daemon` 捕获并脱敏。
2. 原始内容落入本地存储目录。
3. `session_index` / `memory_index` 在本地文件之上构建时序索引与内容索引。
4. `context_cli` 在本地索引上执行精确检索、语义补洞、导入导出和健康检查。
5. 只有用户显式开启远程同步时，才会触发可选外部 HTTP 路径。

## 设计原则

- **本地优先**：默认主链不依赖 MCP、Docker 或外部 recall 服务。
- **统一入口**：用户面集中在 `context_cli` / `context_daemon` / `context_server` / `context_maintenance`。
- **兼容隔离**：历史兼容实现收敛到 `scripts/legacy/`，不进入默认路径。
- **渐进提速**：先用 Python 主链稳定交付，再用 benchmark 驱动 Rust/Go 热路径替换。
