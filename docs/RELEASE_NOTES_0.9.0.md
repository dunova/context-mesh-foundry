# ContextGO 0.9.0 Release Notes

**Release date:** 2026-03-26
**Release type:** Complete rewrite — commercial-grade milestone

---

## English Version

### Highlights

ContextGO 0.9.0 is the definitive commercial-grade release.

Over one hundred rounds of AutoResearch-guided optimization, this cycle performed a complete rewrite of every Python module, extended native CJK safety to both the Go and Rust backends, shipped PyPI packaging, added a project logo and Code of Conduct, introduced a canonical GitHub label taxonomy, hardened every shell script to `shellcheck` clean, and polished the documentation suite to production standard.

Where 0.7.0 established the commercial baseline and proved the runtime was deployable, 0.9.0 proves it is *maintainable at scale*: every public interface carries type annotations, every module is lint-clean with zero suppressions, every native hot path is safe on multi-byte CJK input, and `pip install contextgo` works end-to-end.

This release is suitable for adoption as shared context infrastructure in any engineering team working with multi-agent AI coding workflows.

---

### Breaking Changes

None.

All CLI commands, environment variable names, configuration keys, install paths, service labels, and index schemas from 0.7.0 are unchanged. Upgrade is a drop-in replacement.

---

### What Shipped

#### Complete Python Codebase Rewrite

Every module in `scripts/` was rewritten or substantially overhauled:

- Dead code and unreachable branches eliminated
- All public functions carry complete type annotations
- Docstrings updated to reflect actual current behavior
- Ruff lint enforced with zero suppression directives
- Import ordering normalized throughout: stdlib → third-party → local

#### Native CJK Safety

Multi-byte CJK content in session files previously caused silent truncation or runtime panics in both native backends. This release eliminates those failure modes:

- **Go scanner:** all string operations on session content converted from raw byte slices to `[]rune`; snippet boundaries are codepoint-aware
- **Rust scanner:** all slice index operations replaced with `get()` / `get_mut()` idioms; no remaining `unwrap()` on variable-length paths

#### Rust LTO + Strip

The Rust release profile now enables thin LTO and symbol stripping:

- Binary size: ~4.2 MB → ~2.7 MB (~35% reduction)
- Cold-start latency: ~18% reduction
- No change to CLI interface or output contract

#### PyPI Packaging

`pyproject.toml` is fully wired with hatchling dynamic versioning from the `VERSION` file. The `contextgo` entry-point CLI installs correctly via `pip install contextgo`. The `dev` extras include `pytest-cov` for coverage-gated CI.

#### Repository Front Door

- Project logo and visual identity banner added to `docs/media/` and embedded in README
- `CODE_OF_CONDUCT.md`: Contributor Covenant 2.1 adopted
- `.github/labels.yml`: canonical issue and PR label taxonomy, importable via `gh label import`
- Coverage badge generated on CI and embedded in README
- All bilingual documentation updated to reflect 0.9.0 module surface

#### Shell Hardening

All `.sh` scripts across `scripts/` and the project root now:

- Start with `#!/usr/bin/env bash`
- Declare `set -euo pipefail` at the top
- Pass `shellcheck` at error level with no suppressions

#### Batch SQLite Commit Hardening

Session index batch writes now use an explicit `BEGIN EXCLUSIVE` transaction with rollback on failure, preventing partial-write WAL corruption on SIGKILL or power loss. Batch size is configurable via `CONTEXTGO_INDEX_BATCH_SIZE` (default: 100).

---

### New Features

**PyPI distribution**
- `pip install contextgo` installs the `contextgo` CLI entry point
- Hatchling build backend reads version from `VERSION` file
- `pyproject.toml` wired for `hatch build`, `hatch publish`, and `pip install -e .[dev]`

**Project identity**
- Logo and banner assets in `docs/media/`
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1)
- `.github/labels.yml` canonical label set

**Native CJK safety**
- Go: rune-slice snippet extraction and noise filtering
- Rust: bounds-checked indexing throughout all path and string operations

**Rust performance profile**
- `[profile.release]` updated with `lto = "thin"` and `strip = "symbols"`

**Coverage reporting**
- pytest-cov integrated; XML and terminal coverage reports generated on every CI run
- Coverage badge embedded in README

---

### Improvements

**Code quality**
- Complete rewrite of all Python modules: type-annotated, lint-clean, dead-code-free
- Zero ruff suppression directives across the entire codebase
- Shell scripts fully shellcheck-clean

**Native performance**
- Rust binary ~35% smaller, ~18% faster cold-start after LTO + strip
- Go CJK-heavy session scan ~14% faster (eliminates re-encoding passes)
- ASCII-only paths: <2% overhead from rune-slice conversion

**Documentation**
- `docs/API.md`: all signatures updated to match rewritten implementations
- `docs/CONFIGURATION.md`: new `CONTEXTGO_INDEX_BATCH_SIZE` env var documented
- `docs/ARCHITECTURE.md`: module graph updated for post-rewrite structure
- `CONTRIBUTING.md`: PyPI publish workflow and label import step added
- `SECURITY.md`: CJK input handling added to threat model

**CI/CD**
- Coverage upload step added to `verify.yml`
- Label lint step validates `labels.yml` schema on every PR
- All native build steps verified against post-strip binaries

---

### Bug Fixes

- **Go scanner CJK panic:** `index out of range` when snippet windows crossed multi-byte CJK boundaries; fixed by switching all string operations to rune-slice indexing.
- **Rust scanner symlink panic:** rare `unwrap()` on path metadata in deeply nested symlinked directories; replaced with `?`-propagation and structured error logging.
- **Session index WAL corruption:** partial batch writes on SIGKILL left the SQLite WAL in an ambiguous state; `BEGIN EXCLUSIVE` + explicit rollback now prevents index corruption.
- **e2e quality gate silent timeout:** benchmark stage could time out without reporting failure when native binary carried debug symbols; 30-second per-stage deadline with named failure now enforced.
- **pyproject.toml VERSION parse:** `hatch version` pattern failed on some CI runners due to trailing newline in VERSION; pattern now strips whitespace before version match.
- **Shell strict mode gaps:** several helper scripts lacked `set -euo pipefail`; now uniformly applied and shellcheck-verified.

---

### Performance Summary

| Component | Metric | 0.7.0 | 0.9.0 | Delta |
|---|---|---|---|---|
| Rust binary size | MB | ~4.2 | ~2.7 | -35% |
| Rust cold-start | latency | baseline | -18% | -18% |
| Go CJK scan | throughput | baseline | +14% | +14% |
| Session index batch write | corruption risk | low | eliminated | fixed |
| Health probe cache | TTL | 30s | 30s | stable |

---

### Verification

```bash
# Syntax checks
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py benchmarks/*.py

# Unit and integration tests with coverage
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py \
  scripts/test_context_native.py scripts/test_context_smoke.py \
  scripts/test_session_index.py scripts/test_autoresearch_contextgo.py \
  --cov=scripts --cov-report=term-missing

# End-to-end quality gate
python3 scripts/e2e_quality_gate.py

# Smoke tests (sandboxed)
python3 scripts/context_cli.py smoke --sandbox
python3 scripts/smoke_installed_runtime.py

# Health check
python3 scripts/context_cli.py health
bash scripts/context_healthcheck.sh

# Native backends
cd native/session_scan_go && go test ./...
cd native/session_scan && cargo test
cd native/session_scan && cargo build --release  # verify LTO + strip

# Benchmarks
python3 -m benchmarks --mode both --iterations 1 --warmup 0 --query benchmark --format text

# PyPI packaging
pip install -e .[dev]
contextgo health
```

---

### Upgrade Path

No migration steps required from 0.7.0 or 0.6.1.

1. Replace scripts and native binaries in the install root.
2. Run `python3 scripts/context_cli.py health` to confirm.
3. Session index schema is unchanged; no rescan required.
4. Optionally set `CONTEXTGO_INDEX_BATCH_SIZE` to tune write batch size (default: 100).

---

## 中文版

### 概述

`0.9.0` 是 ContextGO 商业化里程碑版本，也是完整重写周期的终点。

历经超过 100 轮 AutoResearch 优化，本版本完成了 Python 代码库的完整重写、原生 CJK 安全支持全覆盖、PyPI 打包上线、项目 Logo 与行为准则落地、GitHub 标签体系建立、Shell 脚本全面加固，以及文档套件的最终打磨。

0.7.0 证明了运行时可以部署。0.9.0 证明了它**可以在规模上持续维护**：每个公开接口均有类型注解，每个模块均通过 lint 且无抑制指令，每个 Native 热路径在多字节 CJK 输入下均安全，`pip install contextgo` 端到端可用。

无破坏性变更。从 0.7.0 直接替换文件即可升级。

---

### 核心亮点

#### Python 代码库完整重写

`scripts/` 下所有模块均经过完整重写或实质性重构：

- 删除所有死代码与不可达分支
- 所有公开函数携带完整类型注解
- docstring 更新为反映当前实际行为
- ruff lint 全通过，零抑制指令
- 导入顺序全面统一：标准库 → 第三方 → 本地

#### 原生 CJK 安全

此前 Go 与 Rust 两个 Native 后端均存在 CJK 多字节内容导致的截断或 panic 问题。本版本彻底消除：

- **Go scanner：** 所有 session 内容的字符串操作从原始字节切片改为 `[]rune`；snippet 边界按码点（codepoint）计算
- **Rust scanner：** 所有切片索引操作替换为 `get()` / `get_mut()` 惯用写法；可变长度路径不再有任何 `unwrap()`

#### Rust LTO + 体积压缩

Rust release profile 启用 thin LTO 与 strip symbols：

- 二进制体积：~4.2 MB → ~2.7 MB（减少约 35%）
- 冷启动延迟：降低约 18%
- CLI 接口与输出契约不变

#### PyPI 打包

`pyproject.toml` 通过 hatchling 从 `VERSION` 文件动态读取版本。`pip install contextgo` 可正确安装 `contextgo` CLI 入口点。

#### 仓库展示面

- 项目 Logo 与视觉识别资产加入 `docs/media/`，嵌入 README
- `CODE_OF_CONDUCT.md`：采用 Contributor Covenant 2.1
- `.github/labels.yml`：可通过 `gh label import` 导入的规范标签集
- 覆盖率徽章由 CI 生成并嵌入 README

#### Shell 加固

所有 `.sh` 脚本现在：

- 均以 `#!/usr/bin/env bash` 开头
- 顶部声明 `set -euo pipefail`
- shellcheck 错误级别零抑制通过

#### SQLite 批量提交加固

session index 的批量写入现在使用显式 `BEGIN EXCLUSIVE` 事务，失败时回滚，防止 SIGKILL 或断电导致的 WAL 损坏。批量大小可通过 `CONTEXTGO_INDEX_BATCH_SIZE` 配置（默认：100）。

---

### 主要修复

- **Go scanner CJK panic：** snippet 窗口跨越 CJK 多字节边界时 `index out of range`；改用 rune-slice 索引后修复。
- **Rust scanner 软链 panic：** 深层嵌套软链目录下路径元数据 `unwrap()` 偶发 panic；改为 `?` 传播与结构化错误日志。
- **session index WAL 损坏：** SIGKILL 导致部分批量写入留下歧义 WAL；`BEGIN EXCLUSIVE` + 显式回滚后消除。
- **e2e 超时静默失败：** benchmark 阶段在 native binary 含调试符号时可能静默超时；现在强制每阶段 30 秒上限，以命名失败形式上报。
- **pyproject.toml VERSION 解析失败：** 部分 CI runner 上 VERSION 文件尾换行导致 `hatch version` 解析失败；pattern 更新后修复。
- **Shell strict mode 缺失：** 若干辅助脚本缺少 `set -euo pipefail`；现在全面补齐并经 shellcheck 验证。

---

### 性能一览

| 组件 | 指标 | 0.7.0 | 0.9.0 | 变化 |
|---|---|---|---|---|
| Rust 二进制体积 | MB | ~4.2 | ~2.7 | -35% |
| Rust 冷启动延迟 | 相对 | 基准 | -18% | -18% |
| Go CJK 扫描吞吐量 | 相对 | 基准 | +14% | +14% |
| Session index 批量写入 | 损坏风险 | 低 | 消除 | 修复 |
| Health probe 缓存 TTL | 秒 | 30 | 30 | 稳定 |

---

### 升级方式

无需从 0.7.0 或 0.6.1 执行任何迁移步骤。

1. 用新版 scripts 和 native binary 替换 install root 中的对应文件。
2. 运行 `python3 scripts/context_cli.py health` 确认正常。
3. session index schema 不变，无需重新扫描。
4. 可选：通过 `CONTEXTGO_INDEX_BATCH_SIZE` 调整写入批次大小（默认 100）。

---

### 关键命令

```bash
# 健康检查
python3 scripts/context_cli.py health

# Smoke 测试（沙盒模式，不写入 ~/.contextgo）
python3 scripts/context_cli.py smoke --sandbox

# Native 扫描
python3 scripts/context_cli.py native-scan --backend auto --threads 4

# 带覆盖率的单元测试
python3 -m pytest scripts/ --cov=scripts --cov-report=term-missing

# 基准测试
python3 -m benchmarks --mode both --iterations 1 --warmup 0 --query benchmark --format text

# PyPI 安装验证
pip install -e .[dev]
contextgo health
```

---

### 产品定位

ContextGO 0.9.0 是面向多 agent AI 编码团队的本地优先上下文运行时的里程碑版本：

- **本地优先**：默认无 MCP、无 Docker、无云向量依赖
- **零成本上下文**：本地 SQLite FTS5，token 开销极低
- **Native 热路径**：Rust / Go 渐进式替换，性能递增无需修改 CLI
- **生产就绪**：完整 CI/CD、pytest-cov、shellcheck-clean、PyPI 可发布
- **CJK 安全**：Go 与 Rust 均以码点安全方式处理多字节 session 内容

---

## Contributors

0.9.0 was produced by the ContextGO core team through one hundred rounds of AutoResearch-guided systematic optimization. Special acknowledgment to all early operators whose benchmark data, bug reports, and real-world CJK deployment feedback shaped the prioritization of this cycle.
