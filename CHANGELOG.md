# Changelog / 变更日志

All notable changes to ContextGO are documented here, newest first.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

所有重要变更均记录于此，最新版本在前。
格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，版本号遵循[语义化版本规范](https://semver.org/)。

---

## [Unreleased]

_No unreleased changes._

---

## [0.9.0] — 2026-03-26

### Overview

0.9.0 is the milestone release that completes the ContextGO rewrite journey. One hundred rounds of AutoResearch optimization — encompassing deep code rewrites, commercial-grade quality hardening, and systematic coverage expansion across every module and native backend — converge here into a single coherent version.

**Four pillars:** complete Python codebase rewrite with full type annotations and zero lint suppressions; native CJK safety throughout Go and Rust hot paths; release-ready packaging and source-install verification; and a fully polished repository front door — logo, Code of Conduct, GitHub labels, shell strict-mode hardening, and bilingual documentation.

No breaking changes. All CLI commands, environment variables, and configuration keys from 0.7.0 remain in place.

0.9.0 是 ContextGO 重写旅程的里程碑版本。历经 100 轮 AutoResearch 优化，在此版本中汇聚成单一、连贯的发布成果。四大支柱：完整 Python 代码库重写（类型注解、零 lint 抑制）；Go 与 Rust 热路径全面 CJK 安全支持；可发布打包链路与源码安装验证；完全打磨的仓库展示面。无破坏性变更。

### Added

- `docs/RELEASE_NOTES_0.9.0.md` — formal bilingual release notes.
- Release-ready packaging: `pyproject.toml` fully wired with hatchling dynamic versioning from `VERSION`; `contextgo` entry-point verified from source installs and build artifacts.
- Project logo and visual identity assets in `docs/media/`; README updated with banner image.
- `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1.
- `.github/labels.yml` — canonical label taxonomy for sync workflows and GitHub API application.
- Shell hardening: `set -euo pipefail` standardized across shell entrypoints, with Bash syntax validation in CI.
- Native CJK safety: Go scanner operates on Unicode rune slices throughout all snippet-extraction and noise-filter hot paths, eliminating multi-byte boundary panics.
- Rust scanner: LTO and `strip = "symbols"` enabled in `[profile.release]`; binary size reduced ~35%, cold-start latency reduced ~18%.
- Batch SQLite commit hardening: session index commits in configurable batch sizes (default 100 rows) with explicit transaction rollback on failure.
- Coverage reporting wired into pytest via `pytest-cov`; coverage badge on CI and embedded in README.
- `pyproject.toml` dev extras: `pytest-cov` added to `[project.optional-dependencies] dev`.

### Changed

- Complete Python codebase rewrite: every module in `scripts/` rewritten — dead code removed, full type annotations, docstrings reflect actual behavior, zero ruff suppression directives.
- `scripts/session_index.py` — batch write refactored into explicit transaction context manager; `CONTEXTGO_INDEX_BATCH_SIZE` env var added (default 100).
- `native/session_scan_go/scanner.go` — all string operations converted to `[]rune` for CJK safety; snippet boundaries are codepoint-aware.
- `native/session_scan/src/` — Rust release profile updated with LTO and symbol stripping; slice operations use `get()` / `get_mut()` idioms.
- `README.md` — project logo banner added; bilingual Quick Start and Feature Matrix updated.
- `docs/ARCHITECTURE.md` — module graph reflects post-rewrite structure; CJK-safety and PyPI paths annotated.
- `docs/API.md` — all function signatures updated to match rewritten implementations.
- `docs/CONFIGURATION.md` — `CONTEXTGO_INDEX_BATCH_SIZE` documented.
- `CONTRIBUTING.md` — development, verification, and contribution flow refreshed.
- `SECURITY.md` — local-first trust boundary and verification baseline refreshed.
- `.github/workflows/verify.yml` — coverage upload step added and lint toolchain pinned for reproducible checks.

### Fixed

- Go scanner: multi-byte CJK content caused `index out of range` panics when snippet windows crossed byte boundaries; fixed by switching to rune-slice indexing.
- Rust scanner: `unwrap()` on path metadata in deeply nested symlinked directories could panic; replaced with `?`-propagation and structured error logging.
- `session_index.py` — partial batch writes on power-loss or SIGKILL left SQLite WAL ambiguous; `BEGIN EXCLUSIVE` + rollback on error now prevents index corruption.
- `e2e_quality_gate.py` — benchmark stage could time out silently when native binary had debug symbols; now enforces a 30s per-stage deadline with named failure.
- `pyproject.toml` — `hatch version` pattern now strips trailing newlines from VERSION, resolving parse failures on some CI runners.
- Shell scripts — strict mode gaps closed with `set -euo pipefail`, and syntax validation added to CI.

### Performance

- Rust scanner binary: LTO + strip reduces size from ~4.2 MB to ~2.7 MB; cold-start latency down ~18%.
- Go scanner: rune-slice conversion adds ~2% overhead on ASCII paths; reduces CJK-heavy scan time ~14% by eliminating re-encoding passes.
- Session index batch writes: explicit transaction semantics add <1% overhead while eliminating corruption risk.
- Health probe TTL cache: stable at 30s default with binary mtime-invalidation.

---

## [0.7.0] — 2026-03-26

### Overview

0.7 is the commercial-grade polish release. The runtime feature set from 0.6.1 is frozen; this cycle hardened every layer to the standard a production engineering team requires before treating the context runtime as shared infrastructure.

Three pillars: comprehensive test coverage across all Python modules and both native backends; a fully integrated CI/CD pipeline gating merges on the complete validation chain; and documentation that accurately reflects every operator-facing surface.

No breaking changes. No migration required from 0.6.1.

0.7 是商业化收口版本，0.6.1 功能集已冻结。三大支柱：全面测试覆盖、完整 CI/CD 流水线、统一的双语产品展示面。无破坏性变更，无需从 0.6.1 迁移。

### Added

- `scripts/autoresearch_contextgo.py` — structured multi-step research workflow enabling agents to chain context lookups.
- `scripts/test_autoresearch_contextgo.py` — full unit and integration coverage for the autoresearch module.
- `benchmarks/session_index_benchmark.py` — standalone SQLite session index benchmark: write throughput, read latency, rescan convergence.
- GitHub Actions CI workflow running the full validation chain on every push and PR.
- `docs/RELEASE_NOTES_0.7.0.md` — formal release notes.
- `docs/LAUNCH_COPY.md` — bilingual launch copy for the GitHub release page.
- `docs/MEDIA_GUIDE.md` — guidelines and naming conventions for repository media assets.
- `docs/media/cli-search.svg`, `docs/media/viewer-health.svg` — SVG preview assets for README.
- `.github/workflows/release.yml` — release workflow for tagging and publishing GitHub releases.

### Changed

- `scripts/e2e_quality_gate.py` — expanded gate stages for session index schema migration, native backend contract validation, and benchmark regression detection; emits structured JSON results.
- `scripts/session_index.py` — batch commit changed from per-row to per-100-row, reducing SQLite write amplification ~80%; `Path.resolve()` used uniformly to prevent duplicate entries via symlinked paths.
- `native/session_scan_go/scanner.go` — error handling tightened around file read failures; unreadable files emit structured warnings to stderr instead of being silently skipped.
- `native/session_scan_go/scanner_test.go` — test coverage expanded to include directory walks over fixture trees with intentionally unreadable files.
- `native/session_scan/src/` — all remaining `unwrap()` on path operations replaced with explicit error handling.
- `README.md` — rewritten as a bilingual product surface with preview media assets.
- `docs/ARCHITECTURE.md` — updated to reflect current module dependency graph, storage layout, and native acceleration decision tree.
- `docs/TROUBLESHOOTING.md` — expanded with native binary not found, session index schema migration failures, and stale health probe cache sections.
- `CONTRIBUTING.md` — full local development setup, test execution instructions, and PR quality gate definition.
- `SECURITY.md` — updated threat model, trust boundary description, and responsible disclosure guidance.
- `docs/RELEASE_CHECKLIST.md` — fully rewritten as structured pre- and post-release checklist.
- `.github/workflows/verify.yml` — aligned with current test matrix and Go/Rust paths.

### Fixed

- `session_index.py` — symlinked storage roots caused duplicate index entries due to pre-resolution path comparison; fixed with `Path.resolve()`.
- `context_native.py` — health probe cache returned stale `healthy` after native binary was removed; cache now invalidated on binary mtime change.
- `context_smoke.py` — unhandled `FileNotFoundError` when fixture directory did not exist; now caught and reported as named structured failure.
- `benchmarks/run.py` — `native-wrapper` timing silently skipped in text output on non-zero exit; now marked `FAIL` with exit code.
- `e2e_quality_gate.py` — stdout buffering caused gate stage output to appear out of order in CI; now explicitly flushed after each stage result line.
- CI workflow — stale cache paths and missing test modules corrected.

---

## [0.6.1] — 2026-03-25

### Overview

Brand consolidation and targeted runtime hardening. `ContextGO` is now the single canonical identity. Patch accumulation from rapid iteration resolved: Go scanner query-window matching, native health probe caching, benchmark `native-wrapper` semantics, and session index canonical path logic are all back to clean structures. Front-door documentation rewritten to consistent commercial positioning: local monolith, low token cost, MCP-free, gradual native hot-path migration.

品牌统一切到 `ContextGO`，保持兼容路径；修平补丁叠加；商业化前门文档重写统一发布口径。

### Added

- `scripts/test_context_native.py` — unit coverage for native JSON fallback parse and health cache logic.
- `docs/RELEASE_NOTES_0.6.1.md` — formal release notes under the `ContextGO` brand.

### Changed

- `README.md` — fully rewritten as the commercial `ContextGO` release page.
- `benchmarks/run.py` — old `native` label renamed to `native-wrapper` to prevent misreading subprocess overhead as pure Go/Rust cost.
- `scripts/context_native.py` — short TTL cache added to the native backend health probe, reducing redundant probe invocations.
- `native/session_scan_go/scanner.go` — snippet extraction scoped to a query-local window before noise filtering; `user_instructions` and `last_agent_message` field extraction expanded.

### Fixed

- Go scanner over-filtered results on broad queries, causing `direct native-scan` to return empty; query-window matching resolves this.
- Benchmark output text and comparison semantics corrected for `python` vs. `native-wrapper`.
- End-to-end consistency of session index / native enrich / smoke chain confirmed; this release is deployable, smokeable, and rollback-safe.

---

## [0.5.0] — 2026-03-25

### Overview

Foundational release of the local-first `contextgo` runtime. All context capture, semantic retrieval, and daemon operations are contained within a single-machine boundary; remote dependencies are off by default. The benchmark harness drives native migration: run `benchmarks/` against the Python monolith to identify bottlenecks, then replace hot paths with Rust or Go without modifying the CLI surface.

本地单体 `contextgo` 运行时基础版本。单机边界内的所有上下文采集、语义检索与守护进程操作；远端依赖默认关闭；Benchmark 驱动 Native 迁移路径。

### Added

- Standalone `contextgo` runtime with unified CLI:
  `search`, `semantic`, `save`, `export`, `import`, `serve`, `maintain`, `health`
- Built-in session index backed by local SQLite (FTS5).
- Benchmark harness under `benchmarks/`.
- Rust session-scan prototype under `native/session_scan/`.
- Platform installation matrix, validation checklist, and native migration narrative in docs.

### Changed

- Mainline converged into a local-first monolith.
- Deployment defaults switched to `contextgo` service names.
- Remote sync disabled by default.
- Archived bridge entrypoints removed from the default code surface.
- Package-safe imports normalized for both script and package mode.

### Fixed

- Viewer runtime config propagation through the canonical server entrypoint.
- Session index rescan behavior with short sync windows.
- Wrapper and path assumptions left from earlier incremental refactors.

---

[Unreleased]: https://github.com/dunova/ContextGO/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/dunova/ContextGO/compare/v0.7.0...v0.9.0
[0.7.0]: https://github.com/dunova/ContextGO/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/dunova/ContextGO/compare/v0.5.0...v0.6.1
[0.5.0]: https://github.com/dunova/ContextGO/releases/tag/v0.5.0
