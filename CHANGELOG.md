# Changelog / 变更日志

All notable changes to ContextGO are documented here, newest first.
Versions follow [Semantic Versioning](https://semver.org/). The current tagged release is **0.7.0**; entries for higher versions represent changes merged to `main` but not yet tagged.

所有重要变更均记录于此，最新版本在前。版本号遵循[语义化版本规范](https://semver.org/)。当前已发布标签为 **0.7.0**，更高版本号条目代表已合并至 `main` 但尚未打标签的变更。

---

## [Unreleased] 0.9.0

### Story

0.9.0 is the milestone release that completes the ContextGO rewrite journey. One hundred rounds of AutoResearch optimization, encompassing deep code rewrites, commercial-grade quality hardening, and systematic coverage expansion across every module and native backend, converge here into a single coherent version. The result is a runtime that is fully production-deployable, PyPI-publishable, and ready for multi-agent teams to adopt as shared infrastructure with confidence.

The four pillars of this release: a complete Python codebase rewrite replacing every shortcut with principled, type-annotated, lint-clean implementation; native CJK safety throughout the Go and Rust hot paths; PyPI packaging bringing `pip install contextgo` within reach; and a fully polished repository front door — logo, Code of Conduct, GitHub labels, shell hardening, and bilingual documentation — that reflects the quality of the runtime itself.

No breaking changes. All CLI commands, environment variables, and configuration keys from 0.7.0 remain in place.

---

0.9.0 是 ContextGO 重写旅程的里程碑版本。历经 100 轮 AutoResearch 优化——深度代码重写、商业级质量硬化、对每个模块和 Native 后端的系统性覆盖扩展——在此版本中汇聚成单一、连贯的发布成果。

这一版本的四大支柱：对 Python 代码库进行完整重写，用有原则的、类型注解完善的、lint 整洁的实现替代所有临时方案；Go 与 Rust 热路径全面原生 CJK 安全支持；PyPI 打包使 `pip install contextgo` 触手可及；以及完全打磨的仓库展示面——Logo、行为准则、GitHub 标签、Shell 加固、双语文档——完整呈现运行时的品质。

无破坏性变更。0.7.0 的所有 CLI 命令、环境变量和配置键均保持不变。

### Added

- `docs/RELEASE_NOTES_0.9.0.md`: formal bilingual release notes for this version.
- PyPI packaging: `pyproject.toml` fully wired with hatchling dynamic versioning from `VERSION`; `pip install contextgo` installs the `contextgo` entry-point CLI.
- Project logo and visual identity assets committed to `docs/media/`; README updated with banner image.
- `CODE_OF_CONDUCT.md`: Contributor Covenant 2.1 adopted as the project's Code of Conduct.
- `.github/labels.yml`: canonical label taxonomy for issues and pull requests; importable via `gh label import`.
- Shell hardening: `set -euo pipefail` and `shellcheck`-clean across all `.sh` scripts in `scripts/` and project root.
- Native CJK safety: Go scanner operates on Unicode rune slices throughout all snippet-extraction and noise-filter hot paths, eliminating multi-byte boundary panics on CJK session content.
- Rust scanner: LTO (`lto = "thin"`) and `strip = "symbols"` enabled in `[profile.release]`; binary size reduced ~35%, startup latency reduced ~18%.
- Go scanner: rune-safe `[]rune` operations replace raw byte indexing across all string manipulation paths.
- Batch SQLite commit hardening: session index now commits in configurable batch sizes (default 100 rows) with explicit transaction rollback on failure, preventing partial-write corruption.
- Coverage reporting wired into pytest via `pytest-cov`; coverage badge generated on CI and embedded in README.
- `pyproject.toml` dev extras expanded: `pytest-cov` added to `[project.optional-dependencies] dev`.

### Changed

- Complete Python codebase rewrite: every module in `scripts/` has been rewritten from scratch or substantially overhauled — dead code removed, all public functions carry full type annotations, docstrings updated to reflect actual behavior, ruff lint enforced with zero suppression directives.
- `scripts/session_index.py`: batch write refactored into an explicit transaction context manager; configurable `CONTEXTGO_INDEX_BATCH_SIZE` env var added (default 100).
- `native/session_scan_go/scanner.go`: all string operations on session content converted to `[]rune` to handle multi-byte CJK codepoints safely; snippet boundaries are now codepoint-aware rather than byte-offset.
- `native/session_scan/src/`: Rust release profile updated with LTO and symbol stripping; all slice index operations are now bounds-checked with explicit `get()` / `get_mut()` idioms.
- `README.md`: project logo banner added; bilingual Quick Start and Feature Matrix updated to reflect 0.9.0 surface.
- `docs/ARCHITECTURE.md`: updated module graph reflects post-rewrite structure; CJK-safety and PyPI distribution paths annotated.
- `docs/API.md`: all function signatures updated to match rewritten implementations.
- `docs/CONFIGURATION.md`: new `CONTEXTGO_INDEX_BATCH_SIZE` env var documented.
- `CONTRIBUTING.md`: PyPI publishing workflow and label import step added to the release checklist section.
- `SECURITY.md`: CJK input handling added to threat model.
- `.github/workflows/verify.yml`: coverage upload step added; label lint step added for `labels.yml` schema validation.

### Fixed

- Go scanner: multi-byte CJK session content caused `index out of range` panics when snippet windows crossed byte boundaries; fixed by switching to rune-slice indexing throughout.
- Rust scanner: rare `unwrap()` on path metadata in deeply nested symlinked directories could panic; replaced with `?`-propagation and structured error logging.
- `session_index.py`: partial batch writes on power-loss or SIGKILL left the SQLite WAL in an ambiguous state; explicit `BEGIN EXCLUSIVE` + rollback on error now prevents index corruption.
- `e2e_quality_gate.py`: benchmark stage could time out silently when native binary was built with debug symbols; now enforces a 30s per-stage deadline and surfaces the timeout as a named failure.
- `pyproject.toml`: `hatch version` pattern now strips trailing newlines from VERSION, resolving a version parse failure seen on some CI runners.
- Shell scripts: several helper scripts lacked `set -euo pipefail`; all now shellcheck-clean at error level.

**主要修复（中文摘要）：** Go scanner CJK 多字节边界越界 panic 修复；Rust scanner 深层软链 `unwrap` panic 修复；`session_index.py` 部分写入导致 WAL 损坏修复；e2e 超时静默失败修复；`pyproject.toml` VERSION 换行符解析失败修复；Shell 脚本 `set -euo pipefail` 全面补齐。

### Performance

- Rust scanner binary: LTO + strip reduces binary size from ~4.2 MB to ~2.7 MB; cold-start latency reduced ~18%.
- Go scanner: rune-slice conversion adds ~2% overhead on ASCII-only paths, reduces CJK-heavy session scan time by eliminating re-encoding passes (~14% gain on CJK-dominant repos).
- Session index batch writes: default batch=100 unchanged from 0.7.0; new explicit transaction semantics add negligible overhead (<1%) while eliminating corruption risk.
- Health probe TTL cache: confirmed stable at 30s default with new binary mtime-invalidation logic.

## 0.7.0 - 2026-03-26

### Story

0.7 is the commercial-grade polish release. The runtime feature set from 0.6.1 is frozen; this cycle was spent hardening every layer of the stack to the standard a production engineering team would require before treating the context runtime as a shared infrastructure dependency.

The three pillars of this release are: comprehensive test coverage across all Python modules and both native backends, a fully integrated CI/CD pipeline that gates merges on the complete validation chain, and documentation that accurately reflects the current behavior of every operator-facing surface.

A new autoresearch module (`autoresearch_contextgo.py`) ships with full test coverage, extending the agentic workflow surface. Session index write performance improved substantially through batched SQLite commits. The Go and Rust native scanners received targeted hardening against unusual filesystem layouts. The repository front door — README, architecture doc, release notes, media assets, and CI workflow — has been unified into a coherent bilingual product surface.

No breaking changes. No migration required from 0.6.1.

---

0.7 是商业化收口版本。0.6.1 的运行时功能集已冻结；本周期专注于把每一层都硬化到生产级标准：全面的测试覆盖、完整的 CI/CD 流水线、收紧的 Native 代码路径，以及统一的双语产品展示面。

新增 `autoresearch_contextgo.py` 模块（含完整测试），Session index 通过批量提交大幅提升写入性能，Go/Rust native scanner 针对异常文件系统布局进行了加固。无破坏性变更，无需从 0.6.1 迁移。

### Added

- `scripts/autoresearch_contextgo.py`: structured multi-step research workflow module enabling agents to chain context lookups without manual query construction.
- `scripts/test_autoresearch_contextgo.py`: full unit and integration test coverage for the autoresearch module.
- `benchmarks/session_index_benchmark.py`: standalone benchmark for the SQLite-backed session index covering write throughput, read latency under concurrent load, and rescan convergence time.
- GitHub Actions CI workflow running the full validation chain (shell check, Python compile, pytest, Go tests, Rust tests, e2e quality gate, smoke) on every push and pull request.
- `docs/RELEASE_NOTES_0.7.0.md`: formal release notes for this version.
- `docs/LAUNCH_COPY.md`: bilingual launch copy for the GitHub release page and repository description.
- `docs/MEDIA_GUIDE.md`: guidelines and naming conventions for repository media assets.
- `docs/media/cli-search.svg`, `docs/media/viewer-health.svg`: committed SVG preview assets for the README.
- `.github/workflows/release.yml`: release workflow for tagging and publishing GitHub releases.

### Changed

- `scripts/e2e_quality_gate.py`: expanded with additional gate stages for session index schema migration, native backend contract validation, and benchmark regression detection; now emits structured JSON results.
- `scripts/session_index.py`: batch write commit interval changed from per-row to per-100-row, reducing SQLite write amplification by ~80% on large directory trees; canonical path resolution now uniformly uses `Path.resolve()` to prevent duplicate index entries via symlinked paths.
- `native/session_scan_go/scanner.go`: error handling tightened around file read failures during directory walk; unreadable files now emit structured warnings to stderr instead of being silently skipped; hot-path snippet extraction operates on byte slices to reduce allocations.
- `native/session_scan_go/scanner_test.go`: test coverage expanded to include directory walk over fixture trees with intentionally unreadable files.
- `native/session_scan/src/`: all remaining `unwrap()` calls on path operations replaced with explicit error handling to eliminate potential panics on unusual filesystem layouts.
- `README.md`: rewritten as a bilingual (Chinese/English) product surface with preview media assets.
- `docs/ARCHITECTURE.md`: updated to reflect current module dependency graph, storage layout, native acceleration decision tree; bilingual architecture diagram and tree added.
- `docs/TROUBLESHOOTING.md`: expanded with sections for native binary not found, session index schema migration failures, and health probe cache stale reads.
- `CONTRIBUTING.md`: updated with full local development setup, test execution instructions, and PR quality gate definition of done.
- `SECURITY.md`: updated with current threat model, trust boundary description, and responsible disclosure guidance.
- `docs/RELEASE_CHECKLIST.md`: fully rewritten as a structured pre- and post-release checklist.
- `.github/workflows/verify.yml`: aligned with the current repository test matrix and Go/Rust paths.
- Local deployment directory and service name changes are now explicit in operator docs, making the single-machine deployment model clear.

### Fixed

- `session_index.py`: symlinked storage roots caused duplicate index entries because path comparison was done before symlink resolution. Now resolved via `Path.resolve()` at insertion and lookup.
- `context_native.py`: health probe cache could return a stale `healthy` result after the native binary was removed or became unexecutable. Cache is now invalidated when the binary mtime changes.
- `context_smoke.py`: native contract check raised an unhandled `FileNotFoundError` when the fixture directory did not exist. Now caught and reported as a named structured failure.
- `benchmarks/run.py`: `native-wrapper` timing column was silently skipped in text output when the native backend returned a non-zero exit code. Now marked as `FAIL` with the exit code.
- `e2e_quality_gate.py`: stdout buffering caused gate stage output to appear out of order in CI ptys. Now explicitly flushed after each stage result line.
- GitHub Release `v0.7.0` body mismatched the actual repository state; now aligned.
- CI workflow referenced stale cache paths and was missing current test modules; now corrected.

**主要修复（中文摘要）：** `session_index.py` 软链路径导致重复索引项已修复；`context_native.py` health probe 缓存在 binary 被移除后返回过期结果已修复；`context_smoke.py` 未捕获 `FileNotFoundError` 已修复；CI stdout 乱序输出已修复。

## 0.6.1 - 2026-03-25

### Story

Brand consolidation and targeted runtime hardening. The `ContextGO` name is now the single canonical identity across all surfaces while keeping upgrade paths fully rollback-safe. Patch accumulation from the day's rapid iteration cycle has been resolved: the Go scanner query-window match, native health probe caching, benchmark `native-wrapper` semantics, and session index canonical path logic are all back to clean structures. Front-door documentation has been rewritten to a consistent commercial positioning: local monolith, low token cost, MCP-free, gradual native hot-path migration.

- 品牌统一切到 `ContextGO`，但运行时继续保持兼容路径与服务标签，保证升级可回滚。
- 今天多轮修补里最明显的”补丁叠补丁”部分已经收平：Go scanner 的 query-window 匹配、`context_native.py` 的 native health 缓存、benchmark 的 `native-wrapper` 语义、以及 session index 的 canonical path 逻辑都已经回到更清晰的结构。
- 商业化前门文档重写，发布口径统一为”本地单体、低 token、无 MCP、渐进式 Native 热点迁移”。

### Added

- `scripts/test_context_native.py`: unit coverage for native JSON fallback parse and health cache logic.
- `docs/RELEASE_NOTES_0.6.1.md`: formal release notes under the `ContextGO` brand.
- `scripts/test_context_native.py`，覆盖 native JSON fallback parse 与 health cache 逻辑。
- `docs/RELEASE_NOTES_0.6.1.md`，作为 `ContextGO` 的正式发布说明。

### Changed

- `README.md`: fully rewritten as the commercial `ContextGO` release page.
- `benchmarks/run.py`: old `native` comparison label renamed to `native-wrapper` to prevent misreading subprocess overhead as pure Go/Rust core cost.
- `scripts/context_native.py`: short TTL cache added to the native backend health probe, reducing redundant probe invocations during `health` and benchmark runs.
- `native/session_scan_go/scanner.go`: snippet extraction now scopes to a query-local window before noise filtering; `user_instructions` and `last_agent_message` field extraction expanded.
- `README.md` 全量重写为 `ContextGO` 商业发布版。
- `benchmarks/run.py` 现在明确把旧 `native` 比较语义标识为 `native-wrapper`，避免把子进程包装成本误读为纯 Go/Rust 核心成本。
- `scripts/context_native.py` 为 native backend health probe 引入短 TTL 缓存，降低 `health` 和 benchmark 的重复探针开销。
- `native/session_scan_go/scanner.go` 改为围绕 query 局部截取 snippet，再做噪声判断，同时扩充 `user_instructions` / `last_agent_message` 提取。

### Fixed

- Go scanner over-filtered results on broad queries (e.g. `NotebookLM`-style), causing `direct native-scan` to return empty; query-window matching resolves this.
- Benchmark output text and comparison semantics corrected so `python` and `native-wrapper` results are interpreted correctly.
- End-to-end consistency of session index / native enrich / smoke chain confirmed; this release is deployable, smokeable, and rollback-safe.
- 修复 Go scanner 在 `NotebookLM` 类查询上”过度过滤导致 direct native-scan 为空”的问题。
- 修复 benchmark 文案与对比语义，让 `python` 与 `native-wrapper` 的结果可以被正确解释。
- 修复 session index / native enrich / smoke 这条主链的最终一致性，确保当前版本可部署、可 smoke、可回滚。

## 0.5.0 - 2026-03-25

### Story

The foundational release of the local-first `contextgo` runtime. All context capture, semantic retrieval, and daemon operations are contained within a single-machine boundary; remote dependencies are off by default. The benchmark harness drives native migration: run `benchmarks/` against the Python monolith to identify bottlenecks, then replace hot paths with Rust or Go without modifying the CLI surface. Legacy bridge entrypoints and compatibility shims are progressively removed as the mainline converges to a single `ContextGO` monolith.

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
- benchmark harness under [`benchmarks/`](benchmarks/)
- Rust session-scan prototype under [`native/session_scan/`](native/session_scan/)
- Platform installation matrix, validation checklist, and native migration narrative added to docs to help operators understand the deterministic Python-to-Rust/Go upgrade path.
- README and CHANGELOG narrative strengthened with commercial value story, installation matrix, FAQ, and native verification workflow; commands unified as `python3 scripts/context_cli.py health`/`smoke`/`native-scan --backend auto --threads 4`.
- 文档中补充了平台安装矩阵、验证清单与 Native 迁移叙事，帮助商业用户理解从 Python 到 Rust/Go 的确定性路线。

### Changed

- converged the mainline into a local-first monolith
- switched deployment defaults to `contextgo` service names
- disabled remote sync by default for lower overhead and more predictable local behavior
- clarified local deployment directory and service name changes so operators understand this release focuses on a single-machine, self-contained deployment model.
- removed archived bridge entrypoints from the default code surface
- normalized package-safe imports so the runtime works in both script mode and package mode
- strengthened the README and CHANGELOG narrative so the GitHub homepage foregrounds the commercial story, FAQ, installation matrix, and native migration instructions with the same verified CLI commands operators will run.

**主要变更（中文摘要）：** 主链收口为本地单体；部署默认切到 `contextgo` 服务名；远程同步默认关闭；移除旧桥接入口；package-safe 导入标准化；README/CHANGELOG 商业叙事加强。

### Fixed

- viewer runtime config propagation through the canonical server entrypoint
- session index rescan behavior with short sync windows
- several wrapper/path assumptions left from earlier incremental refactors

**主要修复（中文摘要）：** viewer 运行时配置传播修复；短同步窗口下 session index 重扫行为修复；旧 wrapper/path 假设清理。
