# 基准测试脚手架

本目录提供一个可重复执行的 Python 基准套件，用来衡量主链核心路径的性能：

- `context_cli.py health`（包含 session index 同步）
- `context_cli.py search`（精确查找）
- `session_index.sync_session_index`（强制重建本地索引）

### 用法

```bash
python -m benchmarks.run [--iterations 3] [--warmup 1] [--query benchmark]
```

脚本会在一次临时的 `HOME` 环境下生成样本 `.codex`、`.claude`、`.zsh_history` 等数据，从而避免依赖实际用户目录。输出会包含每个用例的平均/最小时延以及示例 JSON/文本。
