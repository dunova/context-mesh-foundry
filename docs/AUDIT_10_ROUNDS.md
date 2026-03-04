# Context Mesh Foundry - 10 轮进化迭代（2026-03-05）

## 目标

1. 用 `recall` 取代 `onecontext` 上游依赖，保留兼容调用入口。  
2. 胶水层改为低功耗默认路径，避免 Claude 并发与 Antigravity 多 agent 场景下后台阻塞。  
3. 形成可复现的“轻量健康检查 + 回归验证 + 发布”闭环。

## 轮次记录

| 轮次 | 审计方式 | 主要发现 | 处理结果 |
|---|---|---|---|
| 1 | 零基线扫描 | 仍有旧 `onecontext/aline` 假设、健康脚本带重操作 | 进入改造队列 |
| 2 | MCP 核心改造 | `openviking_mcp.py` 需 recall-first | 已切换 recall-first + onecontext shim 兼容 |
| 3 | 热点 I/O 优化 | 本地资源扫描每次 `os.walk`，健康探针重复执行重命令 | 增加本地扫描缓存、健康缓存与节流 |
| 4 | 健康脚本重构 | `context_healthcheck.sh` 存在杀进程/重检查/旧栈强耦合 | 改为 lite 默认，deep 才启用可选旧栈探针 |
| 5 | 并发冲突审计 | `viking_daemon` 可能多实例并发读写 | 增加单实例锁，重复启动立即退出 |
| 6 | 主循环阻塞审计 | 高负载时循环阶段可能挤压，错误后重试风暴 | 增加循环预算、错误退避、抖动、索引同步节流 |
| 7 | 部署参数审计 | 部署脚本未注入新低功耗参数 | `unified_context_deploy.sh` 注入 budget/backoff/index-sync 参数 |
| 8 | 模板一致性审计 | launchd 模板仍含旧健康参数 | 模板更新为 lite 健康语义 |
| 9 | 回归与压力验证 | 需验证语法、并发查询、单实例锁、MCP可用性 | 全部通过（见验证结论） |
| 10 | 发布收口 | 文档仍偏旧架构描述 | README 与审计文档更新完成 |

## 本次关键改动

1. `scripts/openviking_mcp.py`。  
• 切换为 recall-first（保留 `onecontext` 兼容命令）。  
• 新增本地扫描缓存：`OPENVIKING_LOCAL_SCAN_CACHE_TTL_SEC` 等参数。  
• 新增健康缓存：避免频繁调用 `recall --health` 造成重复开销。  

2. `scripts/viking_daemon.py`。  
• 新增单实例锁：`~/.context_system/logs/viking_daemon.lock`。  
• 新增循环预算与退避：`VIKING_CYCLE_BUDGET_SEC`、`VIKING_ERROR_BACKOFF_MAX_SEC`、`VIKING_LOOP_JITTER_SEC`。  
• 索引同步节流：`VIKING_INDEX_SYNC_MIN_INTERVAL_SEC`，避免高频 `sync_index_from_storage()` 阻塞。  

3. `scripts/context_healthcheck.sh`。  
• 默认仅执行非侵入式 lite 检查，不再进行进程清理类重操作。  
• `--deep` 模式下执行可选探针，且不将 openviking 离线视为 recall-lite 阻断。  

4. 部署与模板。  
• `scripts/unified_context_deploy.sh` 注入新低功耗参数。  
• `templates/launchd/com.openviking.daemon.plist` 增加预算/退避/抖动参数。  
• `templates/launchd/com.context.healthcheck.plist` 去除过时 MCP 修剪参数。  

## 回归命令（本次执行）

1. `python3 -m py_compile scripts/*.py`  
2. `bash -n scripts/*.sh`  
3. `bash scripts/context_healthcheck.sh --deep`  
4. `/Users/dunova/.openviking_env/bin/python` 直调 `scripts/openviking_mcp.py`：  
   • `context_system_health()`  
   • `search_onecontext_history()`  
   • `query_viking_memory()`  
5. 并发检索压测：`onecontext search ...` 8 并发  
6. 单实例锁验证：并发启动两次 `python3 scripts/viking_daemon.py`

## 结论

10 轮进化迭代已完成。当前默认链路为 recall-lite，胶水层具备单实例、预算、退避、缓存与轻量健康检查能力。  
在高并发场景下，最关键的“多实例互抢与重试风暴”风险已被压制。
