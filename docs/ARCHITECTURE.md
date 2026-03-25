# Context Mesh Foundry 1.0 架构

## 组件分层

1. 采集层。  
• `viking_daemon.py` 监听终端历史。  
• 对输入执行脱敏与 `<private>` 区块剔除。  

2. 索引层。  
• `memory_index.py` 将历史 Markdown 归一到 `observations` 索引表。  
• `session_index.py` 将本机 AI / Shell 会话归一到 `session_index.db`。  
• 统一输出 ID、时序、详情，支撑三层检索。  

3. 检索层。  
• `context_cli.py` 作为统一入口，承接搜索、导入导出、viewer、maintenance。  
• `openviking_mcp.py` 仅保留为 optional legacy 兼容层。  
• 默认主链：`session_index.py` 精确检索 + `memory_index.py` 本地记忆检索。  

4. 交互层。  
• `memory_viewer.py` 提供 `search/timeline/batch` API，但默认由 `context_cli.py serve` 拉起。  
• SSE 事件流可实时看索引状态。  

5. 运维层。  
• `context_healthcheck.sh` 巡检进程、端口、日志、权限、索引。  
• `templates/launchd` 与 `templates/systemd-user` 支持常驻。  

## 数据流

1. 会话输入 -> `viking_daemon.py`。  
2. 脱敏/私密过滤 -> 历史文件落盘。  
3. `memory_index.py` / `session_index.py` 同步索引。  
4. `context_cli.py` 或 legacy wrapper 调用索引/检索能力。  
5. 必要时再走 MCP 兼容层或 OpenViking 语义检索。  

## 设计要点

1. 先索引后详情，避免一次拉全量文本。  
2. 统一 ID 语义，便于跨终端追溯。  
3. 写入前过滤而非读出后过滤，降低泄露面。  
4. 默认主链无 MCP、无外部 recall 依赖，保证轻量和可移植。  
