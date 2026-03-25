# Session Scan 原型

最小化高性能原型，扫描 Codex/Claude 会话文件并输出结构化统计信息。

## 目标
- 在多线程环境下快速走访 `.json` / `.jsonl` 会话文件，提取会话元数据。
- 为后续分析提供干净的聚合摘要、示例会话与可选的 JSON 报表。
- 保持接口可扩展，便于未来对 session_index、全文索引或其他源的集成。

## 构建
1. 从仓库根目录运行 `cd native/session_scan`。
2. 执行 `cargo build`（或 `CARGO_TARGET_DIR=/tmp/context_mesh_target cargo build` 以避免当前文件系统锁限制）。
3. 本地测试时若遇到锁文件错误，可通过 `CARGO_INCREMENTAL=0` 关闭增量编译（例如 `CARGO_INCREMENTAL=0 cargo test`）。

## 运行
```bash
cd native/session_scan
./target/debug/session_scan \
  --codex-root ~/.codex/sessions \
  --claude-root ~/.claude/projects \
  --threads 4 \
  --query "agent" \
  --limit 50 \
  --json
```

`--codex-root` / `--claude-root` 设置会话目录，`--threads` 控制 rayon 线程池。`--query` 会在提取文本时过滤，`--limit` 限制最终返回数量，`--json` 切换为可序列化结构。查询为空则返回全部会话摘要。

## 输出说明
- **命令行模式**：默认输出每个源的会话数量、行数/字节总和和首个示例会话（含时间戳与路径），并在解析异常时列出前几个错误。
- **JSON 模式**：结构体包含文件数、查询、耗时、根级聚合、匹配列表与错误日志（见下节）。

## JSON 输出结构
```json
{
  "files_scanned": 42,
  "query": "agent",
  "duration_ms": 1530,
  "aggregates": [
    {
      "label": "codex_session",
      "session_count": 4,
      "total_lines": 320,
      "total_bytes": 102400,
      "sample": {
        "session_id": "abc123",
        "path": "/home/.codex/sessions/abc123.jsonl",
        "first_timestamp": "2025-03-24T12:00:00Z",
        "last_timestamp": "2025-03-24T12:05:00Z",
        "snippet": "..."
      }
    }
  ],
  "matches": [ ... ],
  "errors": []
}
```

`aggregates` 按照传入 `roots` 顺序列出每个源的行/字节汇总与示例，`matches` 与 CLI 展示字段一致，`errors` 收集所有解析失败的报错字符串。

## 扩展与过滤
- `--query` 可对 JSON 内容（prompt/output 等字段）做子串匹配，结果会尽可能返回首个命中片段。
- `--limit` 适用于调试或可视化消费，只保留指定数量的 `matches`。
- 若需添加新源，请在 `Scanner::from_args` 中新增 `SourceRoot`，逻辑会自动参与聚合与 JSON 报表。

## 测试
- 进入 `native/session_scan` 执行 `CARGO_INCREMENTAL=0 cargo test`。
- 该命令会运行各个辅助函数的单元测试（payload/key 提取、噪声过滤、聚合逻辑）；在不支持锁的文件系统，建议始终设置 `CARGO_INCREMENTAL=0`。
