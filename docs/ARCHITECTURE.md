# Context Mesh Foundry 1.0 架构

## 组件分层

1. 采集层。  
• `viking_daemon.py` 监听终端历史。  
• 对输入执行脱敏与 `<private>` 区块剔除。  

2. 索引层。  
• `memory_index.py` 将历史 Markdown 归一到 `observations` 索引表。  
• 统一输出 ID、时序、详情，支撑三层检索。  

3. 检索层。  
• `openviking_mcp.py` 暴露 MCP 工具。  
• recall-first 走精确检索，OpenViking 走语义检索，legacy shim 只做回退。  

4. 交互层。  
• `memory_viewer.py` 提供 `search/timeline/batch` API。  
• SSE 事件流可实时看索引状态。  

5. 运维层。  
• `context_healthcheck.sh` 巡检进程、端口、日志、权限、索引。  
• `templates/launchd` 与 `templates/systemd-user` 支持常驻。  

## 数据流

1. 会话输入 -> `viking_daemon.py`。  
2. 脱敏/私密过滤 -> 历史文件落盘。  
3. `memory_index.py` 同步索引。  
4. MCP 调用 `search -> timeline -> get_observations`。  
5. 必要时并行调用 `search_onecontext_history` + `query_viking_memory`。  

## 设计要点

1. 先索引后详情，避免一次拉全量文本。  
2. 统一 ID 语义，便于跨终端追溯。  
3. 写入前过滤而非读出后过滤，降低泄露面。  
4. 索引与语义库并存，兼顾精确与召回。  
