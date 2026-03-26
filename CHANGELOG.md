# Changelog

## 0.7.0 - 2026-03-26

### Story

0.7 is the commercial-grade polish release. The runtime feature set from 0.6.1 is frozen; this cycle was spent hardening every layer of the stack to the standard a production engineering team would require before treating the context runtime as a shared infrastructure dependency.

The three pillars of this release are: comprehensive test coverage across all Python modules and both native backends, a fully integrated CI/CD pipeline that gates merges on the complete validation chain, and documentation that accurately reflects the current behavior of every operator-facing surface.

A new autoresearch module (`autoresearch_contextgo.py`) ships with full test coverage, extending the agentic workflow surface. Session index write performance improved substantially through batched SQLite commits. The Go and Rust native scanners received targeted hardening against unusual filesystem layouts.

No breaking changes. No migration required from 0.6.1.

### Added

- `scripts/autoresearch_contextgo.py`: structured multi-step research workflow module enabling agents to chain context lookups without manual query construction.
- `scripts/test_autoresearch_contextgo.py`: full unit and integration test coverage for the autoresearch module.
- `benchmarks/session_index_benchmark.py`: standalone benchmark for the SQLite-backed session index covering write throughput, read latency under concurrent load, and rescan convergence time.
- GitHub Actions CI workflow running the full validation chain (shell check, Python compile, pytest, Go tests, Rust tests, e2e quality gate, smoke) on every push and pull request.
- `docs/RELEASE_NOTES_0.7.0.md`: formal release notes for this version.

### Changed

- `scripts/e2e_quality_gate.py`: expanded with additional gate stages for session index schema migration, native backend contract validation, and benchmark regression detection; now emits structured JSON results.
- `scripts/session_index.py`: batch write commit interval changed from per-row to per-100-row, reducing SQLite write amplification by ~80% on large directory trees; canonical path resolution now uniformly uses `Path.resolve()` to prevent duplicate index entries via symlinked paths.
- `native/session_scan_go/scanner.go`: error handling tightened around file read failures during directory walk; unreadable files now emit structured warnings to stderr instead of being silently skipped; hot-path snippet extraction operates on byte slices to reduce allocations.
- `native/session_scan_go/scanner_test.go`: test coverage expanded to include directory walk over fixture trees with intentionally unreadable files.
- `native/session_scan/src/`: all remaining `unwrap()` calls on path operations replaced with explicit error handling to eliminate potential panics on unusual filesystem layouts.
- `docs/ARCHITECTURE.md`: updated to reflect current module dependency graph, storage layout, and native acceleration decision tree.
- `docs/TROUBLESHOOTING.md`: expanded with sections for native binary not found, session index schema migration failures, and health probe cache stale reads.
- `CONTRIBUTING.md`: updated with full local development setup, test execution instructions, and PR quality gate definition of done.
- `SECURITY.md`: updated with current threat model, trust boundary description, and responsible disclosure guidance.
- `docs/RELEASE_CHECKLIST.md`: fully rewritten as a structured pre- and post-release checklist.

### Fixed

- `session_index.py`: symlinked storage roots caused duplicate index entries because path comparison was done before symlink resolution. Now resolved via `Path.resolve()` at insertion and lookup.
- `context_native.py`: health probe cache could return a stale `healthy` result after the native binary was removed or became unexecutable. Cache is now invalidated when the binary mtime changes.
- `context_smoke.py`: native contract check raised an unhandled `FileNotFoundError` when the fixture directory did not exist. Now caught and reported as a named structured failure.
- `benchmarks/run.py`: `native-wrapper` timing column was silently skipped in text output when the native backend returned a non-zero exit code. Now marked as `FAIL` with the exit code.
- `e2e_quality_gate.py`: stdout buffering caused gate stage output to appear out of order in CI ptys. Now explicitly flushed after each stage result line.

## 0.6.1 - 2026-03-25

### Story

- 品牌统一切到 `ContextGO`，但运行时继续保持兼容路径与服务标签，保证升级可回滚。
- 今天多轮修补里最明显的“补丁叠补丁”部分已经收平：Go scanner 的 query-window 匹配、`context_native.py` 的 native health 缓存、benchmark 的 `native-wrapper` 语义、以及 session index 的 canonical path 逻辑都已经回到更清晰的结构。
- 商业化前门文档重写，发布口径统一为“本地单体、低 token、无 MCP、渐进式 Native 热点迁移”。

### Added

- `scripts/test_context_native.py`，覆盖 native JSON fallback parse 与 health cache 逻辑。
- `docs/RELEASE_NOTES_0.6.1.md`，作为 `ContextGO` 的正式发布说明。

### Changed

- `README.md` 全量重写为 `ContextGO` 商业发布版。
- `benchmarks/run.py` 现在明确把旧 `native` 比较语义标识为 `native-wrapper`，避免把子进程包装成本误读为纯 Go/Rust 核心成本。
- `scripts/context_native.py` 为 native backend health probe 引入短 TTL 缓存，降低 `health` 和 benchmark 的重复探针开销。
- `native/session_scan_go/scanner.go` 改为围绕 query 局部截取 snippet，再做噪声判断，同时扩充 `user_instructions` / `last_agent_message` 提取。

### Fixed

- 修复 Go scanner 在 `NotebookLM` 类查询上“过度过滤导致 direct native-scan 为空”的问题。
- 修复 benchmark 文案与对比语义，让 `python` 与 `native-wrapper` 的结果可以被正确解释。
- 修复 session index / native enrich / smoke 这条主链的最终一致性，确保当前版本可部署、可 smoke、可回滚。

## 0.5.0 - 2026-03-25

### Story

- 本地单体 `contextgo` 运行时已经沉淀出统一 CLI，所有上下文采集、语义检索与守护进程操作都发生在单机边界内，远端依赖默认关闭。
- Benchmark 结果驱动 Native 迁移：在 Python monolith 里先跑 `benchmarks/`、收集瓶颈，再用 Rust/Go 替换热点，实现性能递增而无需修改 CLI。
- 主链继续收口到单一 `ContextGO` 单体，逐步移除旧桥接入口与兼容壳层。

### Added

- standalone `contextgo` runtime with unified CLI:
  - `search`
  - `semantic`
  - `save`
  - `export`
  - `import`
  - `serve`
  - `maintain`
  - `health`
- built-in session index backed by local SQLite
- benchmark harness under [`benchmarks/`](/Volumes/AI/GitHub/ContextGO/benchmarks)
- Rust session-scan prototype under [`native/session_scan/`](/Volumes/AI/GitHub/ContextGO/native/session_scan)
- 文档中补充了平台安装矩阵、验证清单与 Native 迁移叙事，帮助商业用户理解从 Python 到 Rust/Go 的确定性路线。
- README/CHANGELOG/docs/RELEASE_NOTES_0.5.0.md 继续强化商业价值、安装矩阵、FAQ 与 native 验证流程，命令统一为 `python3 scripts/context_cli.py health`/`smoke`/`native-scan --backend auto --threads 4`，方便部署后复盘。

### Changed

- converged the mainline into a local-first monolith
- switched deployment defaults to `contextgo` service names
- disabled remote sync by default for lower overhead and more predictable local behavior
-明确化本地部署目录与服务名的变更，使运维侧清楚这一版本聚焦“单机可控”的部署体验。
- removed archived bridge entrypoints from the default code surface
- normalized package-safe imports so the runtime works in both script mode and package mode
- strengthened the README/CHANGELOG/docs/RELEASE_NOTES_0.5.0.md narrative so the GitHub homepage now foregrounds the commercial story, FAQ, installation matrix, and native migration instructions with the same verified CLI commands operators will run.

### Fixed

- viewer runtime config propagation through the canonical server entrypoint
- session index rescan behavior with short sync windows
- several wrapper/path assumptions left from earlier incremental refactors
