# Changelog

## 0.5.0 - 2026-03-25

### Story

- 本地单体 `contextmesh` 运行时已经沉淀出统一 CLI，所有上下文采集、语义检索与守护进程操作都发生在单机边界内，远端依赖默认关闭。
- Benchmark 结果驱动 Native 迁移：在 Python monolith 里先跑 `benchmarks/`、收集瓶颈，再用 Rust/Go 替换热点，实现性能递增而无需修改 CLI。
- 旧桥接（`recall-lite`、`openviking`、`aline`）仍然可被引用，但当前版本强调的商业叙事是“本地优先、无 MCP、单体可控”。

### Added

- standalone `contextmesh` runtime with unified CLI:
  - `search`
  - `semantic`
  - `save`
  - `export`
  - `import`
  - `serve`
  - `maintain`
  - `health`
- built-in session index backed by local SQLite
- benchmark harness under [`benchmarks/`](/Volumes/AI/GitHub/context-mesh-foundry/benchmarks)
- Rust session-scan prototype under [`native/session_scan/`](/Volumes/AI/GitHub/context-mesh-foundry/native/session_scan)

### Changed

- converged the mainline into a local-first monolith
- switched deployment defaults to `contextmesh` service names
- disabled remote sync by default for lower overhead and more predictable local behavior
-明确化本地部署目录与服务名的变更，使运维侧清楚这一版本聚焦“单机可控”的部署体验。
- isolated legacy bridges under [`scripts/legacy/`](/Volumes/AI/GitHub/context-mesh-foundry/scripts/legacy)
- normalized package-safe imports so the runtime works in both script mode and package mode

### Fixed

- viewer runtime config propagation through the canonical server entrypoint
- session index rescan behavior with short sync windows
- several wrapper/path assumptions left from earlier incremental refactors
