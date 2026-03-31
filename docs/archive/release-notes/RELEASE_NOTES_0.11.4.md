# Release Notes — ContextGO 0.11.4 / 发布说明

## Highlights / 亮点

- **Read-only environment support**: `search` and `health` now work on read-only
  filesystems by gracefully skipping sync instead of failing with
  `OperationalError`. / 只读环境支持——搜索和健康检查在只读文件系统中优雅降级
- **Security hardening**: Path traversal guards, new secret patterns, and
  `0600` file permissions on new SQLite databases. / 安全加固——路径遍历防护、
  新增密钥模式、数据库文件权限收紧
- **Exception observability**: Broad `except Exception` clauses replaced with
  specific types or augmented with `logger.exception()`. / 异常可观测性——
  宽泛异常捕获替换为具体类型或增加日志

## Added / 新增

- `_try_sync()` in `session_index.py` — best-effort sync that checks
  `os.access(W_OK)` before writing; read operations degrade gracefully in
  read-only environments. / 读路径解耦同步写入
- Secret redaction patterns: Anthropic API keys (`sk-ant-*`), GitLab tokens
  (`glpat-*`), npm tokens (`npm_*`) in both daemon and memory index. / 新增
  Anthropic/GitLab/npm 密钥脱敏
- `make clean-native` target for Rust/Go build artifact cleanup. / 新增
  native 构建产物清理目标
- `.gitignore`: Added `src/artifacts/`. / 防止生成产物被提交

## Fixed / 修复

- **API.md auth**: Root page (`/`) correctly documented as requiring token. /
  文档修正——根页面认证说明
- **API.md `db_path`**: Removed stale `db_path` field from examples. / 移除
  已过滤字段引用
- **Makefile `test`**: Now runs full `tests/` directory instead of hardcoded
  file list. / 测试覆盖完整目录
- **Exception handling**: `memory_viewer.py`, `context_cli.py`,
  `context_daemon.py` — replaced silent swallows with logged exceptions. /
  异常不再被静默吞掉

## Security / 安全

- New SQLite databases created with `0600` permissions (owner-only). / 新数据库
  文件仅 owner 可读写
- Three additional secret patterns in sanitization pipeline. / 脱敏管线新增
  三种密钥模式

## Repo Cleanliness / 仓库整洁

- Cleaned `__pycache__/`, `native/session_scan/target/`, `src/artifacts/` from
  working tree. / 清理工作树中的构建产物
- `make clean-all` now covers native artifacts. / 全量清理覆盖 native 产物
