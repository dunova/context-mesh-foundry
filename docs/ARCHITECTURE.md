# Context Mesh 单体产品架构

## 组件概览

1. **采集层**  
   `scripts/context_daemon.py` 是 canonical 守护入口（不再依赖 OpenViking/mcp），负责收集终端会话、shell 历史并在写入前完成 `<private>` 过滤。它与 `scripts/context_core.py`、`scripts/context_maintenance.py` 协同处理同步、清理与落盘逻辑。

2. **索引层**  
   `scripts/session_index.py` 构建 session 索引、`scripts/memory_index.py` 构建 memory/observation 索引；默认落盘在 `scripts/context_config.py` 定义的 storage root（默认 `~/.unified_context_data`），索引文件出了用户目录就不会用默认流程读/写。

3. **检索与服务层**  
   `scripts/context_cli.py` 是唯一 canonical CLI，承载：
   - `health`
   - `search`
   - `semantic`
   - `save`
   - `export`
   - `import`
   - `serve`
   - `maintain`
   `scripts/context_server.py` 提供 viewer 服务入口，默认只监听本地回环地址；任何监听调整必须附 smoke/benchmark 覆盖。

4. **运维验证层**  
   `scripts/context_healthcheck.sh`、`scripts/context_smoke.py`、`scripts/smoke_installed_runtime.py` 以及 `benchmarks/run.py` 统一保障 local-first 路线的安装态可用性和性能。Smoke 脚本依次调用 `context_cli health`/`quality gate`/`semantic`/`serve`，benchmark harness 驱动 `context_cli health`/`search`、`session_index.sync` 基准。这些检查依赖 `scripts/context_config.storage_root()`（默认 `~/.unified_context_data` 或由 `CONTEXT_MESH_STORAGE_ROOT`/`UNIFIED_CONTEXT_STORAGE_ROOT` 覆盖）与安装态 `INSTALL_ROOT=~/.local/share/context-mesh-foundry/scripts`，发布包必须确保 `storage_root` 可写并在 `INSTALL_ROOT` 下提供 `context_cli.py`、`e2e_quality_gate.py`、`benchmarks/run.py` 等入口脚本。

## 数据流

1. 终端/AI 历史由 `context_daemon` 捕获并脱敏，原始内容写入 storage root（默认 `~/.unified_context_data`，可通过 `CONTEXT_MESH_STORAGE_ROOT` 等环境变量覆盖）。  
2. 本地索引同步由 `session_index` 与 `memory_index` 负责，写入 sqlite/datastore 文件后再由 `context_cli` 读取。  
3. `context_cli` 在本地索引上执行精确检索、语义补洞、导入导出与健康检查，`context_server` 提供 viewer API，`context_maintenance` 实现定期清理。  
4. Smoke/benchmark 脚本调用 canonical CLI/daemon 验证健康与性能，无需远程依赖；仅当用户显式开启同步模块时才触发外部 HTTP。

## 设计原则

- **本地优先**：默认主链不依赖 MCP、Docker 或外部 recall 服务，所有路径落在 storage root 下。  
- **统一入口**：用户体验集中在 `context_cli` / `context_daemon` / `context_server` / `context_maintenance`。  
- **兼容隔离**：历史兼容实现收敛到 `scripts/legacy/`，不进入默认运行路径。  
- **Smoke 与 benchmark 融入验证**：任何变更都应通过 `scripts/context_smoke.py`、`scripts/smoke_installed_runtime.py` 及 `python3 -m benchmarks --iterations 1 --warmup 0 --query benchmark` 验证健康与性能。  
- **渐进提速**：优先用 Python 主链稳定交付，再用 benchmark 数据驱动 Rust/Go 热路径替换，保持 smoke/benchmark 可复现性。
