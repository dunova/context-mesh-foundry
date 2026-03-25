# Session Scan 原型

最小化高性能原型，扫描 Codex/Claude 会话文件并输出统计信息。

## 构建
1. 从仓库根目录运行 `cd native/session_scan`。
2. 执行 `cargo build`（或 `CARGO_TARGET_DIR=/tmp/context_mesh_target cargo build` 以避免当前文件系统锁限制）。

## 运行
```bash
cd native/session_scan
./target/debug/session_scan --codex-root ~/.codex/sessions --claude-root ~/.claude/projects --threads 4
```

可通过 `--codex-root` / `--claude-root` 指定目录，`--threads` 控制 rayon 线程数。

## 当前能力与限制
- 并行扫描 `.json` / `.jsonl` 文件，提取会话 ID 与时间戳，统计行数与体积。
- 仅输出每个源的汇总、示例会话，不写入数据库，也不支持全文检索。
- 未集成 session_index 的 sqlite 逻辑，仅作为渐进重写的性能原型。
