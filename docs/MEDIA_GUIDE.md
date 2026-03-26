# Media Guide

Guidelines for preparing screenshots and visual assets for GitHub, release pages, and social platforms.

The goal is for a first-time visitor to understand three things within 10 seconds:

1. It runs
2. It has a unified CLI
3. It is built for multi-agent team delivery

---

## Recommended assets

### 1. CLI search screenshot

Suggested command:

```bash
python3 scripts/context_cli.py search "NotebookLM" --limit 3 --literal
```

The screenshot should show a session hit, a timestamp, and a snippet. This demonstrates that the tool is functional and that context retrieval is concrete and visible.

### 2. Smoke / health screenshot

Suggested commands:

```bash
python3 scripts/context_cli.py health
python3 scripts/context_cli.py smoke
```

Show the health status and a passing smoke result. This demonstrates that the project has a validation chain, not just features.

### 3. Viewer screenshot

Suggested commands:

```bash
python3 scripts/context_cli.py serve
# then open http://127.0.0.1:37677 in a browser
# or show the /api/health or /api/search response
```

This demonstrates that the project provides a local visualization surface in addition to the CLI.

### 4. Architecture diagram

Export the Mermaid diagram from `docs/ARCHITECTURE.md` as a static PNG or SVG. This gives first-time visitors a quick map of the Capture / Index / Search / Viewer / Smoke / Native layers.

---

## Placement recommendations

### README

Two images is enough:

1. CLI search screenshot
2. Viewer screenshot

Keep the README page length reasonable. Do not add more than two inline images.

### Release page

Suggested order:

1. Architecture diagram or overview image
2. CLI search screenshot
3. Viewer screenshot

### Social platforms

- X: one clean CLI or architecture image
- Reddit: one to two images
- GitHub Release: up to three images

---

## Style guidelines

- Use a dark terminal background for all terminal screenshots
- Keep image widths consistent across assets
- Remove unrelated file paths, private information, and cluttered scrollbars
- Show passing / hit / runnable state wherever possible
- Use static images rather than low-quality GIFs

---

## File naming and location

Place all media assets in `docs/media/`:

```text
docs/media/
├── cli-search.png
├── cli-smoke.png
├── viewer-search.png
└── architecture.png
```

Two SVG assets are already committed and can be used in the README immediately:

- `docs/media/cli-search.svg`
- `docs/media/viewer-health.svg`

---

## Minimum viable asset set

If producing only one set of assets, prioritize these three:

- `cli-search.png`
- `viewer-search.png`
- `architecture.png`

当前仓库已内置两张静态 SVG 素材，可直接先用于 README：

- [docs/media/cli-search.svg](media/cli-search.svg)
- [docs/media/viewer-health.svg](media/viewer-health.svg)
