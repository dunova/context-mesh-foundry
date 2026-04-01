# Migration Guide / 版本迁移指南

> [index.md](index.md) · [CHANGELOG.md](../.github/CHANGELOG.md) · [CONFIGURATION.md](CONFIGURATION.md)

---

## 0.10.x → 0.11.x

### Breaking changes / 破坏性变更

#### 1. Runtime package moved: `scripts/` → `src/contextgo/`

All Python runtime modules moved from `scripts/` to `src/contextgo/`. The 21 symlinks that previously bridged the two locations were removed in 0.11.5.

**Impact:** Any code that imports directly from `scripts/` will break.

**Fix:**

```python
# Before / 旧写法
import scripts.session_index as si

# After / 新写法
from contextgo.session_index import ...
# or, from a source checkout:
import sys; sys.path.insert(0, "src")
from contextgo.session_index import ...
```

Shell scripts invoking `python3 scripts/context_cli.py` should be updated:

```bash
# Before / 旧写法
python3 scripts/context_cli.py search "auth"

# After / 新写法
contextgo search "auth"
# or from source:
python3 src/contextgo/context_cli.py search "auth"
```

所有 Python 运行时模块已从 `scripts/` 迁移到 `src/contextgo/`，0.11.5 删除了桥接符号链接。直接从 `scripts/` 导入的代码需更新路径。

---

#### 2. API field renamed: `db_path` → `db_name`

The `/api/health` and SSE event responses previously included a `db_path` field exposing the absolute database path. It was renamed to `db_name` (basename only) in 0.11.0 to prevent path disclosure.

**Fix:** Update any client code that reads `response["db_path"]` to use `response["db_name"]`.

`/api/health` 和 SSE 响应中的 `db_path` 字段已重命名为 `db_name`（仅返回文件名），客户端代码需相应更新。

---

#### 3. `sqlite_retry` parameter change

`sqlite_retry.py` replaced bare `assert` statements with explicit `RuntimeError`. Code running Python with the `-O` (optimize) flag that relied on assertions being evaluated will need no changes — the behavior is now always enforced, not silently bypassed.

`sqlite_retry.py` 将 `assert` 替换为显式 `RuntimeError`，在 `-O` 优化模式下行为更安全可靠。无需代码修改，但语义略有变化：过去用 `-O` 运行可能绕过检查，现在不会。

---

### New module: `secret_redaction`

0.11.x adds 12+ new token patterns (Stripe, HuggingFace, SendGrid, Twilio, AWS variants, GitHub `ghs_`/`ghr_`). Runs automatically at ingest time — no configuration required.

0.11.x 新增 12+ 种 Token 脱敏模式，摄取时自动运行，无需配置。

---

### Upgrade procedure / 升级步骤

```bash
# From PyPI / 从 PyPI 升级
pipx upgrade contextgo || pipx install "contextgo[vector]"
eval "$(contextgo shell-init)"
contextgo health && contextgo sources

# From source checkout / 从源码升级
git pull origin main && bash scripts/upgrade_contextgo.sh

# Verify / 验证
contextgo --version && contextgo smoke --sandbox
```

---

## 0.9.x → 0.10.x

### Source adapter layer added / 新增数据源适配器层

0.10.0 introduced `source_adapters.py` for automatic discovery and normalized ingestion of OpenCode, Kilo, and OpenClaw session data.

**No action required** for existing Codex/Claude/shell users — the adapter layer is additive and runs transparently on `health`, `sources`, and `search`.

To see which platforms were detected after upgrading:

```bash
contextgo sources
```

0.10.0 新增 `source_adapters.py`，自动发现并摄取 OpenCode、Kilo、OpenClaw 数据。对现有 Codex/Claude/Shell 用户无需任何操作，适配器层透明叠加。升级后运行 `contextgo sources` 查看检测到的平台。

---

### `vector-sync` on fresh installs / 全新安装时的 `vector-sync`

`vector-sync` in 0.10.x and later is safe to run on machines with no existing `session_index.db`. It initializes a clean local index automatically.

0.10.x 起 `vector-sync` 可在无 `session_index.db` 的全新环境安全运行，会自动初始化索引。

---

## Data compatibility / 数据兼容性

SQLite databases (`session_index.db`, `memory_index.db`, `vector_index.db`) are forward-compatible within the 0.10–0.11 range. No manual schema migration is required.

If the adapter cache schema changes during an upgrade, ContextGO detects the version mismatch and refreshes the cache automatically on next startup.

`session_index.db`、`memory_index.db`、`vector_index.db` 在 0.10–0.11 范围内向前兼容，无需手动迁移。Adapter 缓存 schema 变更时系统自动检测并刷新，无需手动干预。
