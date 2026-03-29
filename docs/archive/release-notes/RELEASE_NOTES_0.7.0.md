# ContextGO 0.7.0 Release Notes

**Release date:** 2026-03-26
**Release type:** Commercial-grade polish

---

## English Version

### Highlights

0.7.0 is the commercial-grade polish release for ContextGO.

Where 0.6.1 closed the gap between experimental codebase and deployable product, 0.7.0 hardens every layer: comprehensive test coverage, structured CI/CD validation, tightened native code paths, documented security posture, a fully verified end-to-end quality gate, and a unified product-facing surface across README, release page, and repository metadata.

No new runtime features are introduced. The focus is correctness, auditability, and confidence: every path that ships has a test, every test has a deterministic pass condition, and every operator-facing document reflects the current behavior of the system.

This release is suitable for deployment in multi-developer teams where the context runtime is a shared infrastructure dependency.

---

### Breaking Changes

None. All CLI commands, environment variables, configuration keys, install paths, and service labels from 0.6.1 remain unchanged.

---

### What Shipped

- Bilingual README with separated Chinese and English sections
- Bilingual architecture document with Mermaid diagrams
- Bilingual GitHub release body
- Aligned repository metadata, description, and topics
- Media guidance and preview assets
- CI workflow aligned with current verification matrix
- Autoresearch module and full test suite
- Extended e2e quality gate with structured JSON output
- Session index benchmark

### New Features

**Autoresearch module**
- `scripts/autoresearch_contextgo.py`: structured multi-step research workflows over the local context index
- `scripts/test_autoresearch_contextgo.py`: full unit and integration test coverage

**Extended e2e quality gate**
- `scripts/e2e_quality_gate.py` expanded with session index schema migration, native backend contract validation, and benchmark regression detection
- Emits structured JSON results for CI artifact pass/fail determination

**Session index benchmark**
- `benchmarks/session_index_benchmark.py`: write throughput, read latency, and rescan convergence time tracking across versions

---

### Improvements

**Code quality**
- All Python modules pass lint with zero errors
- Import ordering normalized: stdlib, third-party, local
- Dead code and unreachable branches removed
- Type annotations added to all public functions

**Test coverage**
- Coverage raised across all test modules with previously untested edge cases
- All tests are deterministic: no real filesystem paths, network access, or wall-clock timing

**CI/CD pipeline**
- GitHub Actions workflow runs full validation on every push and PR
- Shell syntax, Python compile, unit tests, Go tests, Rust tests, e2e quality gate, smoke
- Failure in any stage blocks merge

**Native code hardening**
- Go scanner: error handling tightened around file read failures
- Rust scanner: bounds-checked slice indexing, eliminated remaining `unwrap()` panics

**Documentation**
- Architecture, troubleshooting, contributing, security, and release checklist fully updated
- All doc cross-links verified against current file layout

---

### Bug Fixes

- Fixed: `session_index.py` canonical path logic incorrectly resolved symlinked storage roots
- Fixed: `context_native.py` health probe cache returned stale results after binary removal
- Fixed: `context_smoke.py` native contract check raised unhandled `FileNotFoundError`
- Fixed: `benchmarks/run.py` silently skipped native-wrapper timing on non-zero exit
- Fixed: `e2e_quality_gate.py` buffered output appeared out of order in CI

---

### Performance

- Session index rescan: batch commit per-100-row reduces write amplification ~80%
- Go scanner: byte-slice snippet extraction avoids redundant allocation (~12% throughput gain)
- Health probe TTL cache confirmed stable; default 30s TTL retained

---

### Verification

```bash
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py benchmarks/*.py
python3 -m pytest scripts/test_context_cli.py scripts/test_context_core.py \
  scripts/test_context_native.py scripts/test_context_smoke.py \
  scripts/test_session_index.py scripts/test_autoresearch_contextgo.py
python3 scripts/e2e_quality_gate.py
python3 scripts/context_cli.py health
python3 scripts/context_cli.py smoke
python3 scripts/smoke_installed_runtime.py
python3 -m benchmarks --mode both --iterations 1 --warmup 0 --query benchmark --format text
cd native/session_scan_go && go test ./...
cd native/session_scan && CARGO_INCREMENTAL=0 cargo test
```

---

### Upgrade Path

No migration steps required from 0.6.1. After replacing scripts and binaries in the install root, run `python3 scripts/context_cli.py health` to confirm. Session index schema is unchanged; no rescan required.

---

## 中文版

### 概述

`0.7.0` 是 ContextGO 的商业化收口版本。

这一版的重点不是增加运行时新特性，而是把仓库、文档、发布面、验证链路和 GitHub 展示层彻底对齐到一个完整的产品形态。每条路径都有测试，每个测试都有确定性通过条件，每份文档都反映系统当前行为。

### 本次发布包含

- README 改为中英分离双语版
- 架构文档补双语架构图
- GitHub Release 页改为中英双语正文
- 仓库 description / topics 全面对齐
- 补齐媒体素材规范与首页预览素材
- CI workflow 对齐当前仓库实际测试矩阵
- 新增 autoresearch 模块及完整测试套件
- 扩展 e2e 质量门禁，输出结构化 JSON
- 新增 session index 基准测试

### 为什么这是 0.7.0

这次变化已经超出补丁修复的范围：

- 仓库首页从内部工程说明变为产品首页
- 发布页从临时说明变为正式对外发布面
- 验证矩阵与仓库真实状态完全对齐
- 文档、素材、发布、仓库元信息形成统一 front door

### 关键命令

```bash
python3 scripts/context_cli.py health
python3 scripts/context_cli.py smoke
python3 scripts/context_cli.py native-scan --backend auto --threads 4
python3 scripts/smoke_installed_runtime.py
python3 -m benchmarks --mode both --iterations 1 --warmup 0 --query benchmark --format text
```

### 产品定位

ContextGO 0.7.0 适合作为多 agent AI 编码团队直接部署的本地上下文运行时：

- 本地优先，默认无 MCP
- 默认无 Docker，无云向量依赖
- 单 CLI，单验证链
- Rust / Go 热路径渐进替换

---

## Contributors

This release was produced by the ContextGO core team. Contributions in the form of benchmark data, bug reports, and real-world deployment feedback from early operators informed the prioritization of this cycle.
