# FAQ / 常见问题

> [index.md](index.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md) · [CONFIGURATION.md](CONFIGURATION.md)

---

## Installation / 安装

**Q: `pip install contextgo` fails with "externally-managed-environment". / 报此错误怎么办？**

Use `pipx`. PEP 668 blocks direct `pip install` on macOS Homebrew Python 3.12+ and many Linux distros.
请改用 `pipx`，PEP 668 限制了直接安装。

```bash
brew install pipx && pipx ensurepath   # macOS
# sudo apt install pipx               # Debian/Ubuntu
pipx install "contextgo[vector]"
```

---

**Q: `ImportError: No module named 'model2vec'` when running `vector-sync`.**

Reinstall with the vector extra: `pipx install "contextgo[vector]"`. Without it, search falls back to FTS5/LIKE automatically — vector dependencies are optional.

重装包含向量扩展：`pipx install "contextgo[vector]"`。不安装亦可，搜索自动降级为 FTS5/LIKE。

---

## Search / 搜索

**Q: `contextgo search` returns no results for sessions I know exist. / 搜索已有会话无结果。**

1. Run `contextgo health` to trigger an index refresh. / 执行 `contextgo health` 刷新索引。
2. Verify source dirs exist: `~/.codex/sessions/`, `~/.claude/projects/`, `~/.zsh_history`.
3. Run `contextgo sources` to confirm detected platforms. / 运行 `contextgo sources` 确认探测到的平台。
4. Verify `CONTEXTGO_STORAGE_ROOT` if using a custom path.

---

**Q: Hybrid search returns different results than keyword search. / 混合搜索结果与关键词搜索不同，正常吗？**

Expected. Hybrid search combines vector similarity with BM25 via RRF — it surfaces semantically related content without exact keyword matches. Use `contextgo search` for exact terms, `contextgo semantic` for concept recall.

符合预期。混合搜索能找到语义相关但不含精确词的内容。精确词用 `search`，概念召回用 `semantic`。

---

## Database / 数据库

**Q: `sqlite3.OperationalError: database is locked`. / 数据库锁定错误。**

WAL mode allows concurrent reads; a lock error means another process holds a write lock.
Check for stale daemon processes (`ps aux | grep context_daemon`) and kill if stuck.
Transient locks resolve automatically via exponential backoff in `sqlite_retry.py`.

WAL 模式支持并发读，锁错误通常因 daemon 进程卡住所致。检查并重启 daemon；短暂竞争由 `sqlite_retry.py` 自动处理。

---

**Q: How do I move the database? / 如何迁移数据库？**

```bash
export CONTEXTGO_STORAGE_ROOT=/data/contextgo
contextgo health   # creates directory structure / 首次运行自动建目录
```

---

## Native Backend / 原生后端

**Q: `contextgo health` reports "native backend unavailable". Is it required? / 必须安装原生后端吗？**

No. Rust/Go backends are optional acceleration. ContextGO falls back to Python transparently.
不必须。Python 路径自动兜底，大量会话时原生后端能提升吞吐。

---

**Q: How do I build the native binaries? / 如何编译原生二进制？**

```bash
# Go (requires Go 1.21+)
cd native/session_scan_go && go build -o session_scan_go .

# Rust (requires Rust 1.75+)
cd native/session_scan
CARGO_TARGET_DIR="${CONTEXTGO_NATIVE_TARGET_DIR:-$HOME/.cache/contextgo/target}" cargo build --release
```

---

## Vector Search / 向量搜索

**Q: `vector-sync` is slow on first run. / 首次运行很慢？**

Expected: downloads the `potion-base-8M` model (~30 MB) and embeds all sessions. Incremental syncs with no changes take ~6 ms afterward.
正常：首次下载约 30 MB 模型并全量嵌入，后续增量同步约 6 ms。

---

**Q: How do I reset the vector index? / 如何重置向量索引？**

```bash
rm ~/.contextgo/index/vector_index.db && contextgo vector-sync
```

---

## Secret Redaction / 密钥脱敏

**Q: Will ContextGO store my API keys? / 会存储 API 密钥吗？**

ContextGO applies `<private>` redaction at ingest time. AWS, GitHub, Stripe, HuggingFace, SendGrid, Twilio, and 12+ other token patterns are stripped automatically. Inspect `~/.contextgo/raw/` to verify.

写入前自动 `<private>` 脱敏，覆盖 AWS、GitHub、Stripe 等 12+ 种 Token 模式。可查看 `~/.contextgo/raw/` 确认实际内容。
