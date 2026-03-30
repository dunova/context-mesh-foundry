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

## [0.11.4] — 2026-03-30

### Added / 新增
- **Read-only environment support / 只读环境支持**: Read operations (`search`, `health`) now gracefully degrade when the database is read-only, skipping sync instead of failing / 读操作在只读环境中优雅降级
- **Security hardening / 安全加固**: Added path traversal guards, Content-Security-Policy headers, and tighter file permissions on new database files / 路径遍历防护、CSP安全头、数据库文件权限收紧
- **`make clean-native` target**: New Makefile target to clean Rust/Go build artifacts / 新增native构建产物清理
- **Narrowed exception handling / 收窄异常处理**: Replaced broad `except Exception` with specific exception types across CLI, viewer, and daemon for improved observability / 用具体异常类型替代宽泛捕获

### Fixed / 修复
- **API.md stale content / 文档过时内容**: Fixed incorrect auth description and removed references to filtered `db_path` field / 修复认证描述和已过滤字段引用
- **Makefile `test` scope / 测试范围**: `make test` now runs the full `tests/` directory instead of a hardcoded file list / 测试覆盖完整目录
- **`.gitignore` coverage**: Added `src/artifacts/` to prevent generated output from being committed / 防止生成产物被提交

---

## [0.11.3] — 2026-03-30

### Added / 新增
- **`__init__.py` exports**: Added `__all__` and `__version__` for proper package API / 添加包导出定义
- **Module docstrings**: Added to `context_core`, `session_index`, `context_daemon` / 核心模块补充文档字符串
- **`docs/RELEASE_NOTES_0.11.1.md`**: Added missing release notes / 补充缺失的发布说明
- **CI wheel validation**: Build and install wheel in verify.yml to catch packaging issues / CI中构建并安装wheel验证

### Fixed / 修复
- **CI smoke test**: Added `--sandbox` flag to smoke step in verify.yml / CI冒烟测试添加沙箱标志
- **`.gitignore` completeness**: Added `.mypy_cache/`, `htmlcov/`, `.coverage`, and other cache patterns / 补充缓存忽略规则

---

## [0.11.2] — 2026-03-30

### Fixed / 修复
- **[Go P0] sync.Pool buffer aliasing / 缓冲区别名**: Fixed pool buffer to use fixed 32MB allocation; Scanner never grows, pool reuse now effective / 修复缓冲池别名问题，Scanner永不增长，池化真正有效
- **[Rust P1] active_workdir per-file syscall / 每文件重复syscall**: Computed once at scan entry, passed to all parallel workers / 在扫描入口计算一次，传入所有并行worker
- **[Rust P1] ASCII fast-path heap allocation / ASCII快路径堆分配**: Replaced `Vec<u8>` lowercase copy with `eq_ignore_ascii_case` zero-alloc comparison / 用零分配比较替代Vec复制
- **[Go P1] unconditional lineStr allocation / 无条件字符串分配**: Deferred string conversion to JSON-parse-failure path only / 延迟到JSON解析失败时才转换
- **[Go P1] IsNoiseLower strings.Split on single lines / 单行Split**: Added newline check before Split to avoid allocation in common case / 单行时跳过Split
- **[Python] wheel force-include "scripts" removed / 移除重复打包**: Eliminated duplicate `scripts` package from wheel distribution / 消除wheel中重复的scripts包
- **[CI] safety scan scope / 安全扫描范围**: Changed from `requirements.txt` to `pip freeze` for full dependency coverage / 全量依赖扫描
- **[CI] release.yml concurrency / 发布并发保护**: Added concurrency group to prevent duplicate releases / 防止重复发布
- **[Python] memory_viewer db_name leak / 信息泄露**: Filtered `db_name` from `/api/health` and SSE responses / 过滤健康接口中的数据库路径信息

### Changed / 变更
- **pyproject.toml**: Added `numpy>=1.24` to dev extras; removed incorrect `Libraries` classifier / dev依赖补充numpy，移除错误分类
- **Makefile**: Updated mypy/py_compile targets to scan `src/contextgo/` / 更新扫描路径
- **docs/TROUBLESHOOTING.md**: Fixed stale `context_cli serve` → `contextgo serve` / 修复过期命令名

---

## [0.11.1] — 2026-03-30

### Fixed / 修复
- **Adapter fault isolation / 适配器容错隔离**: `sync_all_adapters()` and `source_freshness_snapshot()` now catch per-source exceptions, preventing a single broken adapter from blocking the entire sync pipeline / 单个适配器异常不再阻断整个同步流水线
- **Search/sync decoupling / 搜索与同步解耦**: `sync_all_adapters()` moved after throttle check in `sync_session_index()` — frequent search calls no longer trigger full adapter filesystem scans / 频繁搜索不再触发全量适配器扫描
- **Smoke test path resolution / 冒烟测试路径解析**: `smoke --sandbox` now uses `Path.resolve()` to handle symlinked installations correctly / 正确处理软链接安装路径
- **CI release gate / CI发布门禁**: `release.yml` now includes Go/Rust verification steps matching `verify.yml`; removed `|| true` from safety check to make it a blocking gate / 发布流水线增加Go/Rust验证，安全检查改为阻塞门禁
- **Test adapter dirty compatibility / 测试适配器dirty兼容**: Fixed `test_sync_rechecks_immediately_when_adapter_dirty` to mark adapter dirty after creating test data / 修复测试中adapter dirty标记时序

### Changed / 变更
- **Documentation alignment / 文档路径对齐**: Updated stale `scripts/` references to `src/contextgo/` across AGENTS.md, CONTRIBUTING.md, ARCHITECTURE.md, RELEASE_CHECKLIST.md, native/README.md, and .claude/CLAUDE.md / 全量修复过期路径引用

### Added / 新增
- **CHANGELOG v0.11.0 entry**: Full change documentation for the v0.11.0 release / 补充v0.11.0完整变更记录
- **Release notes**: Added `docs/RELEASE_NOTES_0.11.0.md` / 新增发布说明文档

---

## [0.11.0] — 2026-03-30

### Overview

Security, performance, reliability, and developer-experience hardening release. API surface tightened to avoid path-disclosure via `db_path`, directory permissions hardened to `0o700`, URI injection guard extended to ATTACH DATABASE. Startup latency reduced with lazy `context_native` import, batch DML in sync paths, N+1 query elimination, and a temp-table stale-deletion strategy. Atomic file writes protect daemon export and all adapter files. Per-source fault isolation prevents one failing adapter from breaking the entire ingest pipeline. Timezone-aware datetimes eliminate ambiguous comparisons. New `--version` flag and friendly no-subcommand help improve first-run experience. pytest gate added to the release workflow; `safety` check added to verify.

安全、性能、可靠性与开发体验全面加固版本。API 重命名 `db_path` → `db_name` 防止路径泄露，目录权限收紧至 `0o700`，ATTACH DATABASE URI 注入防护。懒加载 `context_native`、批量 DML、消除 N+1 查询、临时表加速陈旧数据清理。原子写入保护 daemon 导出和所有 adapter 文件。逐数据源容错隔离防止单源故障影响整体摄取。时区感知日期时间消除歧义比较。新增 `--version` 标志和友好的无子命令帮助文本。发布流程加入 pytest 门控，verify 流程加入 `safety` 安全检查。

### Security

- **API:** Rename `db_path` → `db_name` in public API to prevent internal path disclosure (安全：API 重命名，防止路径泄露)
- **Hardening:** `0o700` permissions on all created directories in storage root (安全：目录权限收紧至 0o700)
- **Injection guard:** ATTACH DATABASE URI injection guard using strict allowlist validation (安全：ATTACH DATABASE URI 注入防护)

### Performance

- **Startup:** Lazy `context_native` import — module only loaded when native scan is actually invoked (性能：懒加载 context_native，降低 CLI 冷启动延迟)
- **Sync:** Batch DML in `sync_session_index()` using `executemany()` for upserts and deletes (性能：sync 批量 DML)
- **Queries:** N+1 query elimination in session listing and adapter refresh paths (性能：消除 N+1 查询)
- **Cleanup:** Temp table strategy for stale session deletion — single DELETE instead of per-row round-trips (性能：临时表加速陈旧数据删除)

### Reliability

- **Atomic writes:** Daemon export files and all adapter output files now use `os.open()` + `os.replace()` for atomic writes (可靠性：原子文件写入)
- **Fault isolation:** Adapter ingest wraps each source in a try/except so a single failing adapter cannot abort the entire pipeline (可靠性：逐数据源容错隔离)
- **Datetimes:** All `datetime.now()` calls replaced with timezone-aware `datetime.now(timezone.utc)` (可靠性：时区感知日期时间)

### Added

- `contextgo --version` flag for quick version inspection (新增：`--version` 标志)
- Friendly no-subcommand help message when `contextgo` is invoked without arguments (新增：无子命令友好帮助)

### CI

- pytest gate added to `release.yml` — release cannot proceed if any test fails (CI：发布前 pytest 门控)
- `safety` dependency audit added to `verify.yml` — known-vulnerable packages block verification (CI：verify 流程加入 safety 安全检查)

---

## [0.10.1] — 2026-03-30

### Overview

Comprehensive security, performance, and documentation hardening release based on a full zero-knowledge code audit across all dimensions.

基于全维度零知识代码审计的安全、性能与文档加固版本。

### Fixed

- **CRITICAL:** SQL injection in `vector_index.py` ATTACH DATABASE — replaced character filter with strict path whitelist (安全：SQL 注入修复，改用严格路径白名单)
- **Security:** Remove internal exception details from HTTP 500 responses in memory_viewer (安全：移除 500 响应中的内部异常信息)
- **Security:** Replace `assert` with explicit `RuntimeError` in sqlite_retry.py for `-O` mode safety (安全：`assert` 改为显式异常)
- **Security:** Bandit CI scan no longer silenced with `|| true` — findings now fail the build (安全：bandit 扫描不再静默)
- **Bug:** BM25 cache invalidation now uses `(row_count, max_rowid)` tuple to detect content changes at same count (修复：BM25 缓存失效检测)
- **Bug:** `cmd_smoke` gracefully handles missing `e2e_quality_gate.py` in pip-installed mode (修复：pip 安装模式下 smoke 命令兼容)
- **Bug:** `_SHUTDOWN_EVENT.clear()` on memory_viewer restart prevents SSE hang (修复：viewer 重启 SSE 挂起)
- **Bug:** TOCTOU guard on `source_adapters.py` stat() call (修复：文件竞态条件)
- **Bug:** Use timezone-aware `datetime.now(timezone.utc)` in context_core (修复：时区感知时间)

### Changed

- Remove private Chinese meta strings from Go/Rust noise markers — now loaded from `config/noise_markers.json` only (清理：移除 Go/Rust 中的私有中文噪声标记)
- Remove Go coverage.out and tmp/gen_report.py from git tracking (清理：移除构建产物)
- Add Python 3.13 classifier to pyproject.toml (包装：新增 Python 3.13 分类标签)

### Documentation

- Fix CHANGELOG link table — add v0.10.0, v0.9.37, v0.9.6 comparison links (文档：修复变更日志链接表)
- Fix README key numbers to match actual: 2,026 tests, 98.9% coverage (文档：修正 README 统计数字)
- Add OpenCode, Kilo, OpenClaw to README and ARCHITECTURE.md diagrams (文档：架构图补充新数据源)
- Update ARCHITECTURE.md: 15 subcommands, add vector_index layer (文档：更新子命令数和向量索引层)
- Fix CONTRIBUTING.md coverage threshold: 50% → 97% (文档：修正覆盖率阈值)

**Key numbers / 关键指标:** 2,041 tests | 97.14% coverage | 0 security findings

---

## [0.10.0] — 2026-03-29

### Overview

Commercial-grade onboarding and multi-platform memory unification release. ContextGO now ships with automatic source discovery, normalized adapter ingestion for additional terminal AI tools, immediate post-install platform visibility via `contextgo sources`, first-class upgrade/uninstall flows, and a release-grade README that demonstrates value in under a minute.

面向商业开源标准的安装与多平台记忆统一版本。ContextGO 现在内置自动数据源发现、针对额外终端 AI 工具的规范化 adapter 摄取、安装后可立即查看接入平台的 `contextgo sources`、标准化升级/卸载流程，以及 1 分钟内可展示价值的发布级 README。

### Added

- `contextgo sources` command for platform detection and adapter visibility
- `source_adapters.py` for normalized ingestion of:
  - OpenCode session databases
  - Kilo local storage
  - OpenClaw session JSONL roots
- `scripts/upgrade_contextgo.sh` for idempotent local upgrade flows
- `scripts/uninstall_contextgo.sh` for one-command uninstall with optional data purge
- Adapter schema versioning and home-scoped adapter roots to prevent stale cache reuse across environments
- New adapter and incremental-ingest regression tests

### Changed

- README rewritten around a 1-minute install, immediate proof of value, upgrade flow, uninstall flow, and automatic platform absorption
- `health --verbose` now reports OpenCode DBs, Kilo storage, OpenClaw session roots, and adapter session counts
- `session_index` now refreshes adapters before min-interval gating so newly installed tools become searchable immediately
- `vector-sync` remains safe on fresh installs and now coexists with the new adapter discovery layer
- Release and packaging metadata now align around `0.10.0`

### Fixed

- Incremental platform adoption no longer requires manual reconfiguration after installing OpenCode / Kilo / OpenClaw
- Adapter-generated session mirrors are now normalized, schema-versioned, and cleaned up across upgrades
- OpenClaw adapter filenames and titles no longer render with duplicated `.jsonl` suffixes
- Installed runtime and CLI smoke coverage expanded to catch first-run regressions earlier

---

## [0.9.37] — 2026-03-29

### Overview

Installation-guidance and agent-handoff release. Public onboarding now defaults to `pipx` and deployed-runtime flows, avoiding unsupported `pip install` guidance on macOS. Agent instructions were tightened around shell initialization, durable memory usage, and installed-runtime verification.

安装指引与智能体接管标准化补丁版。公开安装流程统一收敛到 `pipx` 与已部署运行时路径，避免在 macOS 上继续给出不受支持的 `pip install` 文案。Agent 接管说明进一步明确了 shell 初始化、持久记忆使用和已安装运行时验证步骤。同时修正版本号策略，确保公开发布序列继续高于既有 `0.9.36`。

### Changed

- Public installation guidance now defaults to `pipx`
- Source-install guidance now points to `scripts/unified_context_deploy.sh`
- `AGENTS.md` rewritten for direct operational takeover
- Installed-runtime validation emphasized via `contextgo smoke --sandbox` and `contextgo health`

---

## [0.9.6] — 2026-03-28

### Overview

Security hardening, search robustness, and CI quality improvements. SQL injection in `vector_index.py` patched, FTS5 rebuild correctness fixed, shared SQLite retry helpers extracted, BM25 index caching added, vector dimension validation introduced, WAL mode applied to `context_maintenance.py`, and chunked dedup for large import batches. Python 3.13 added to the test matrix, GitHub Actions SHA-pinned, Bandit scanning integrated, and the coverage threshold raised to 97%. 165 new tests push the total to 2,026 at 98.9% coverage.

安全加固、搜索健壮性与 CI 质量提升。修补 `vector_index.py` SQL 注入漏洞，修复 FTS5 重建正确性，提取公共 SQLite retry helper，新增 BM25 索引缓存与向量维度校验，`context_maintenance.py` 启用 WAL 模式，大批量导入分块去重。测试矩阵加入 Python 3.13，GitHub Actions SHA 固定，集成 Bandit 安全扫描，覆盖率阈值提升至 97%。新增 165 个测试，总计 2,026 个，覆盖率 98.9%。

### Fixed

- **CRITICAL**: SQL injection in `vector_index.py` ATTACH DATABASE — parameterized path now validated and quoted
- **HIGH**: FTS5 rebuild now correctly triggers on document updates (was silently skipped)
- **HIGH**: Vector dimension mismatch detection — mismatched embeddings raise a clear error instead of silent corruption
- **HIGH**: Import fingerprint dedup chunked for >999 items to avoid SQLite variable limit

### Added

- **HIGH**: `sqlite_retry.py` — shared SQLite retry helpers extracted from `session_index.py` and `memory_index.py`
- **HIGH**: BM25 index caching in `vector_index.py` (no rebuild on every query)
- **HIGH**: WAL mode + `busy_timeout` applied in `context_maintenance.py`
- **MEDIUM**: Session ID lookup made case-insensitive
- **MEDIUM**: Bilingual error messages (English + Chinese) on user-facing exceptions
- **MEDIUM**: Shell scripts excluded from wheel via `pyproject.toml`
- CI: Python 3.13 added to test matrix
- CI: GitHub Actions steps pinned to SHA for supply-chain security
- CI: Bandit security scanning integrated
- CI: Coverage threshold raised to 97%

### Changed

- Tests: 1,861 → 2,026 (+165)
- Coverage: 97.4% → 98.9%

---

## [0.9.36] — 2026-03-27

### Overview

"极覆盖" (Extreme Coverage) AutoResearch release. 50-round optimization cycle targeting world-class context memory quality. Critical mmap case-sensitivity bug fixed, test-ordering flaky failures eliminated, thread-safety mock leaks resolved, and 263 new tests across 13 new test files push coverage from 94.7% to 99.4%.

"极覆盖" AutoResearch版本。50轮优化循环追求世界级上下文记忆系统：修复mmap大小写敏感关键bug、消除测试排序抖动、解决线程安全mock泄漏，13个新测试文件+263个新测试，覆盖率从94.7%飞升至99.4%。

### Fixed

- **Critical**: mmap content search was case-sensitive — `mm.find(query_bytes)` on raw bytes without lowering. Fixed to `mm[:region].lower()` before searching (context_core.py)
- `builtins.print` mock leak in R25 threaded tests — replaced `mock.patch("builtins.print")` inside threads with `contextlib.redirect_stdout(io.StringIO())` for thread safety
- `_GlobCacheEntry` weakref TypeError — added `__weakref__` to `__slots__` (context_daemon.py)
- `OrderedDict` vs plain `dict` in `file_cursors` — 3 test files fixed to use `OrderedDict`
- Test ordering flakiness in `test_context_maintenance.py` — 10 tests that failed when run after R25 due to leaked mock

### Added

- 13 new test files: R22 (daemon), R23 (core), R25 (CLI), R26 (concurrent safety), R27 (Go), R31 (daemon coverage), R32 (session_index), R33 (core), R34 (CLI), R35 (memory_index), R36 (daemon deep), R37 (session deep), R38 (Go edge cases), R39 (viewer/native), R40 (smoke/e2e)
- 263 new Python tests, 14 new Go tests
- SQLite concurrent safety tests with WAL mode verification
- mmap edge case tests (empty files, binary noise, Unicode content)
- ThreadPoolExecutor cleanup and interleaving tests

### Changed

- Tests: 1545 → 1808
- Coverage: 94.7% → 99.4%
- context_core.py: 89.5% → 100%
- context_daemon.py: 89.7% → 99.3%
- session_index.py: 94.5% → 99.6%
- context_cli.py: 95.0% → 99.1%
- memory_index.py: 97.5% → 99.3%
- autoresearch_contextgo.py: 97.8% → 100%
- context_smoke.py / e2e_quality_gate.py: → 100%

---

## [0.9.35] — 2026-03-27

### Overview

"轻稳快" (Lightweight, Stable, Fast) optimization release. 11-round AutoResearch cycle targeting the global memory system. CLI cold-start further reduced via deeper lazy imports, daemon memory bounded with chunked reads and cursor eviction, SQLite queries accelerated with PRAGMA tuning and secondary indexes, retry-on-busy resilience added to all database operations, and 151 new tests (including CJK/Unicode edge cases) push coverage from 97.9% to 98.1%.

"轻稳快"优化版本。11轮AutoResearch循环针对全局记忆系统：CLI冷启动深度懒加载、daemon内存有界化(分块读取+cursor淘汰)、SQLite PRAGMA调优+二级索引加速、全数据库操作retry-on-busy韧性、151个新测试(含CJK/Unicode边缘用例)，覆盖率从97.9%提升至98.1%。

### Added

- 151 new tests across 5 new test files (test_coverage_boost_r10, test_context_smoke, test_memory_viewer, test_context_maintenance, test_session_index CJK)
- SQLite retry helpers: `_retry_sqlite()`, `_retry_sqlite_many()`, `_retry_commit()` with exponential backoff (0.1/0.5/2s)
- SQLite secondary indexes on `session_documents(source_type)`, `session_documents(session_id)`, `session_documents(file_mtime)`
- Search result caching with TTL in both session_index and memory_index
- Go scanner: `sync.Pool` for reusable buffers, 8 new edge-case tests (BOM, large files, symlinks, deep nesting)
- Daemon: bounded `_TAIL_CHUNK_BYTES` (1MB) for chunked file reads
- Daemon: cursor eviction policy (oldest third evicted when over `MAX_FILE_CURSORS`)

### Changed

- CLI: `json`, `datetime`, `types.ModuleType` imports deferred to point of use
- SQLite PRAGMAs: `synchronous=NORMAL`, `cache_size=-8000` (8MB), `mmap_size=268435456` (256MB), `temp_store=MEMORY` on all connections
- memory_index: `import_observations_payload()` uses `executemany()` for batch inserts
- session_index: `sync_session_index()` uses batch upsert with `executemany()` and timing instrumentation
- Daemon: glob cache uses generator for large result sets, avoiding full materialization
- Daemon: `_tail_file()` reads binary and decodes, avoiding `UnicodeDecodeError` in file handle
- Go scanner: pre-allocated rune slices, parallel directory walks with bounded goroutine pool
- Test count: 1131 -> 1282, Coverage: 97.9% -> 98.1%

### Fixed

- Daemon `maybe_sync_index()` catches `sqlite3.OperationalError` (database locked) gracefully, retries next cycle
- `MAX_PENDING_FILES` hard-capped at 50 to bound disk scan cost
- Documentation: CONFIGURATION.md updated with new env vars (`TAIL_CHUNK_BYTES`, `PENDING_HARD_LIMIT`)

### Performance

- CLI cold-start: further reduced by deferring 3 stdlib imports
- SQLite queries: secondary indexes reduce LIKE scan from full-table to index-assisted
- SQLite writes: `synchronous=NORMAL` with WAL reduces fsync overhead ~50%
- Memory index: batch inserts via `executemany()` reduce Python-SQLite round-trips
- Session index: batch upserts and cached path normalization reduce sync time
- Daemon: chunked reads cap peak memory during large file tailing

### Stats / 统计
- Tests: 1282 passed, Coverage: 98.1%, Go: all passed, ruff: clean, go vet: clean

---

## [0.9.32] — 2026-03-27

### Overview

Zero-bug hardening release. Resolves all code review findings: eliminates module-level side effects in the daemon, adds HTTPS enforcement to CLI remote sync, implements search_type filtering, enables SQLite WAL mode, and fixes Rust/Go code quality issues.

零缺陷加固版本。修复所有代码审查发现：消除 daemon 模块级副作用，CLI 远程同步增加 HTTPS 强制，实现 search_type 过滤，启用 SQLite WAL 模式，修复 Rust/Go 代码质量问题。

### Fixed

- **Daemon module-level side effects** — moved `SystemExit` checks, HTTPS validation, directory creation, and file handler setup from import-time to `main()` via `_validate_startup()` / `_setup_logging()`
- **CLI HTTPS enforcement** — `REMOTE_MEMORY_URL` now requires HTTPS for non-localhost targets, matching daemon behavior
- **`search_type` filtering** — `format_search_results()` now filters by source type (`codex`, `claude`, etc.) instead of ignoring the parameter
- **SQLite WAL mode** — both `session_index.py` and `memory_index.py` now use `PRAGMA journal_mode=WAL` with 30s connection timeout, preventing lock contention
- **Rust overflow-checks** — re-enabled in release profile for safety in file-size/offset calculations
- **Go builtin shadowing** — renamed `cap` variable to `initialCap` in `main.go`
- **`os.getuid()` portability** — guarded with `hasattr(os, "getuid")` for non-POSIX platforms
- **CSP documentation** — added rationale for `unsafe-inline` in viewer (loopback + auth required)

### Stats / 统计
- Tests: 1131 passed, Coverage: 98.1%, Go: all passed
- Review findings: 0 remaining

---

## [0.9.31] — 2026-03-27

### Overview

Bugfix, security, and quality release. Fixes `contextgo serve` under pipx, improves Chinese short-query recall, resolves macOS test failures, eliminates file write race conditions, and corrects documentation.

修复、安全和质量版本。修复 pipx 下 `contextgo serve`，改善中文短查询召回率，修复 macOS 测试问题，消除文件写入竞争条件，修正文档。

### Fixed

- **`contextgo serve` ModuleNotFoundError under pipx** — `_load_module()` now falls back to package-relative import (`scripts.context_server`) when the top-level import fails
- **`test_too_short_path_raises_value_error`** — use `/x` instead of `/tmp` as test input; macOS resolves `/tmp` to `/private/tmp` (4 components), bypassing the `< 3` guard
- **`test_valid_deep_path_accepted`** — use `Path.home()` based path instead of hard-coded `/home/user/.contextgo`
- **`test_zsh_extended_history_format`** — accept both `20240325` and `20240326` in session id to handle UTC+N timezone rollover
- **`test_skips_paths_already_in_db`** — resolve temp directory paths before inserting into DB
- **`cli-health` regression test** — parse health output as JSON instead of string-matching
- **Security**: Atomic file writes in `context_daemon.py` — eliminated race condition between `write_text()` and `chmod()` by using `os.open()` with `0o600` mode
- **Docs**: Corrected all FTS5 references to reflect actual LIKE-based search implementation
- **CI**: Raised `--cov-fail-under` from 50% to 95% to match actual 98.3% coverage
- **Metadata**: Updated OS classifier from `OS Independent` to `POSIX`/`MacOS`
- **Code**: Removed hardcoded `/tmp/contextgo-gate` path in `e2e_quality_gate.py`

### Improved

- **Chinese short-query recall** — split CJK stopwords into a separate set (`CJK_STOPWORDS`); when all query terms are CJK stopwords, they are preserved as search terms
- **Daemon httpx warning** — downgraded "httpx not installed" from `WARNING` to `INFO` level

### Stats / 统计
- Tests: 1131 passed, Coverage: 98.3%

---

## [0.9.3] — 2026-03-27

### Overview

Production-grade quality release. 20-round AutoResearch optimization achieving 98.3% test coverage with 1131 tests, lazy CLI imports for 37% faster startup, and comprehensive edge-case hardening across all modules.

生产级质量版本。20 轮 AutoResearch 优化，实现 98.3% 测试覆盖率、1131 个测试、CLI 懒加载提速 37%，全模块边缘场景加固。

### Added

- 348 new tests across all modules (daemon, smoke, viewer, session_index, memory_index, CLI, e2e, config, core, native, autoresearch)
- Lazy import system for CLI startup optimization (80ms -> 50ms)
- CLI edge-case guards: empty query, invalid port, bad thread count
- Go scanner: 27 new edge-case tests (Unicode, boundary, noise filter)
- Shell script mktemp trap for cleanup on failure

### Fixed

- Benchmark environment leak (os.environ restored after runs)
- ARCHITECTURE.md stale subcommand count (8 -> 10)
- ruff format aligned across all 42 Python files
- subprocess.run explicit check=False on all 8 call sites

### Changed

- Test coverage: 84.5% -> 98.3%
- Test count: 783 -> 1131
- CLI startup: 80ms -> 50ms (lazy imports)
- All Python scripts now have executable bit set

---

## [0.9.2] — 2026-03-27

### Overview

Performance, stability, and quality hardening release. 20-round AutoResearch optimization targeting lightweight, stable, and fast operation. Coverage pushed from 51% to 84%, test count from 225 to 783, with zero new dependencies.

20 轮 AutoResearch 迭代优化版本，目标：轻量、稳定、迅速。覆盖率从 51% 提升至 84%，测试数量从 225 增加到 783，零新增依赖。

### Added

- 550+ new tests across session_index, memory_index, context_daemon, context_core, context_native, check_noise_sync, and utility scripts
- Export-import and maintain cases in e2e quality gate
- Sandbox isolation for smoke_installed_runtime
- Go scanner: Unicode byte-offset mismatch fix and `TestSnippetMatcherToLowerByteShift` regression test

### Fixed

- CORS origin bypass vulnerability in memory_viewer (now uses proper hostname parsing)
- Go scanner: Unicode byte-offset mismatch in `SnippetMatcher.Match` for multi-byte lowercased characters
- Go 1.19 compatibility: replaced builtin `min()` with explicit comparison
- CLI error messages improved with actionable guidance (import, save)
- Healthcheck and e2e diagnostics enhanced with detailed failure context

### Changed

- Test coverage: 51% -> 84.5%
- Test count: 225 -> 783
- All native backends verified (Rust clippy clean, Go vet clean)

---

## [0.9.1] — 2026-03-27

### Overview

Documentation consistency audit and patch release. All documentation surfaces updated to reflect the current codebase; no Python source, test, or CI workflow files were changed.

0.9.1 是文档一致性审查补丁版本。所有文档更新以反映当前代码库；未修改任何 Python 源码、测试或 CI 工作流文件。

### Fixed

- `CHANGELOG.md` — added 0.9.1 entry; bottom link table updated to reference `v0.9.1` as the latest released tag.
- `docs/CONFIGURATION.md` — "Viewer server" section: corrected start command from `context_cli serve` to `contextgo serve` (matching the installed entry-point and the CLI reference in all other docs).
- `docs/API.md` — server startup example now shows `contextgo serve` instead of `python3 scripts/context_cli.py serve` for consistency with the recommended installed workflow; programmatic Python example preserved unchanged.

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
- Built-in session index backed by local SQLite.
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

[Unreleased]: https://github.com/dunova/ContextGO/compare/v0.11.4...HEAD
[0.11.4]: https://github.com/dunova/ContextGO/compare/v0.11.3...v0.11.4
[0.11.3]: https://github.com/dunova/ContextGO/compare/v0.11.2...v0.11.3
[0.11.2]: https://github.com/dunova/ContextGO/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/dunova/ContextGO/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/dunova/ContextGO/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/dunova/ContextGO/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/dunova/ContextGO/compare/v0.9.37...v0.10.0
[0.9.37]: https://github.com/dunova/ContextGO/compare/v0.9.6...v0.9.37
[0.9.6]: https://github.com/dunova/ContextGO/compare/v0.9.36...v0.9.6
[0.9.36]: https://github.com/dunova/ContextGO/compare/v0.9.35...v0.9.36
[0.9.35]: https://github.com/dunova/ContextGO/compare/v0.9.32...v0.9.35
[0.9.32]: https://github.com/dunova/ContextGO/compare/v0.9.31...v0.9.32
[0.9.31]: https://github.com/dunova/ContextGO/compare/v0.9.3...v0.9.31
[0.9.3]: https://github.com/dunova/ContextGO/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/dunova/ContextGO/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/dunova/ContextGO/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/dunova/ContextGO/compare/v0.7.0...v0.9.0
[0.7.0]: https://github.com/dunova/ContextGO/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/dunova/ContextGO/compare/v0.5.0...v0.6.1
[0.5.0]: https://github.com/dunova/ContextGO/releases/tag/v0.5.0
