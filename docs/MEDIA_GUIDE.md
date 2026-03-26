# Media Guide

## 目标

为 ContextGO 准备一组更适合 GitHub 首页、Release 页、社交平台传播的截图素材。

目标不是“做花”，而是让访客在 10 秒内看懂三件事：

1. 它真的能跑  
2. 它真的有统一 CLI  
3. 它真的适合多 agent 团队交付

## 推荐素材清单

### 1. CLI Search 截图

建议内容：

- `python3 scripts/context_cli.py search "NotebookLM" --limit 3 --literal`
- 画面里能看到：
  - 命中会话
  - 时间戳
  - snippet

目的：

- 证明它不是空壳概念仓库
- 证明“上下文命中”是具体、可见的

### 2. Smoke / Health 截图

建议内容：

- `python3 scripts/context_cli.py health`
- `python3 scripts/context_cli.py smoke`

画面重点：

- 健康状态
- smoke pass
- 结果简洁、可运维

目的：

- 证明这不是只有 feature、没有验证链路的仓库

### 3. Viewer 截图

建议内容：

- `python3 scripts/context_cli.py serve`
- 浏览器打开 viewer 首页
- 或直接展示 `/api/health` / `/api/search`

目的：

- 证明它不仅有 CLI，也有可展示的本地可视化面

### 4. Architecture 图

建议内容：

- README 里的 Mermaid 架构图导出成静态图
- 或者基于同一结构画成更干净的 PNG/SVG

目的：

- 让首次访客快速理解：
  - Capture
  - Index
  - Search
  - Viewer
  - Smoke
  - Native hot paths

## 推荐放置位置

### README

放 2 张就够：

1. CLI Search 截图
2. Viewer 截图

不要一口气塞太多图，避免首页过长。

### Release 页

建议顺序：

1. 一张总览图或架构图
2. 一张 CLI 检索图
3. 一张 viewer 图

### 社交平台

- X：只放一张最干净的 CLI / 架构图
- Reddit：可以放 1 到 2 张
- GitHub Release：最多 3 张

## 风格建议

- 终端截图尽量统一深色背景
- 宽度尽量一致
- 去掉无关路径、隐私信息、杂乱滚动条
- 优先展示“通过 / 命中 / 可运行”的状态
- 不要用低质量 GIF 代替静态图

## 命名建议

建议统一放在后续新目录，例如：

```text
docs/media/
├── cli-search.png
├── cli-smoke.png
├── viewer-search.png
└── architecture.png
```

## 最小可交付素材包

如果只做最小一版，先准备这 3 个：

- `cli-search.png`
- `viewer-search.png`
- `architecture.png`
