# Troubleshooting & Integration Gotchas

This document summarizes known issues, integration blind spots, and troubleshooting steps for the recall-first + OpenViking + GSD ecosystem, including the legacy timeline compatibility layer.

> **Note**: All paths referenced below are standard/relative forms. Actual deployment paths vary based on your environment configurations.

## 1. OpenViking Server Crash Loop (`litellm` Dependency)

**Symptom:**
`com.openviking.server` (or equivalent systemd service) is stuck in a crash loop (`spawn scheduled`).
Logs indicate:
```text
ModuleNotFoundError: No module named 'litellm.llms.base_llm.skills'
```

**Root Cause:**
OpenViking requires the `skills` module from `litellm`, which was refactored or removed in `litellm` versions >= `1.81.0`.

**Fix:**
Downgrade or pin `litellm` to `<1.81.0` within the OpenViking virtual environment before starting the server.
```bash
# Example
/path/to/openviking_env/bin/pip install "litellm<1.81.0"
```
*Tip: Always restart the daemon or launch agent after modifying the environment.*

## 2. Aline Watcher/Worker Failures (`realign` Import Error)

**Symptom:**
If you rely on `Aline`'s hooks for capturing events (e.g., Claude Code), you might notice that recent conversations aren't indexed.
Logs for `aline_watcher_launchd.err` show:
```text
ModuleNotFoundError: No module named 'realign'
```
*(Even when using an isolated runner like `uv tool`.)*

**Root Cause:**
When starting the watcher/worker module via `python -m realign.watcher_daemon`, Python may fail to resolve the package if the wrapper scripts do not properly set the working directory or `PYTHONPATH` to the top layer of the `.venv/lib/python3.XX/site-packages`.

**Fix:**
Ensure that your `LaunchAgent` plist or `systemd` service explicitly includes `PYTHONPATH` pointing to the `site-packages` directory where `realign` is installed.
```xml
<key>EnvironmentVariables</key>
<dict>
    <key>PYTHONPATH</key>
    <string>/path/to/aline-ai/lib/python3.x/site-packages</string>
</dict>
```

## 3. MCP Configuration Blind Spots across Terminals

A unified context system is only as good as its integrations. Terminals often have different MCP config locations and syntax:

### Claude Code (`claude`)
- **Config file**: `~/.claude/settings.json`
- **Gotcha**: Ensure you declare the MCP block inside `mcpServers` alongside existing keys like `hooks` or `model`.

### OpenCode
- **Config file**: `~/.config/opencode/opencode.json` (or `~/.opencode/opencode.json`)
- **Gotcha**: Requires an array structure under `"command"`. Watch out for legacy or broken paths if you renamed your skills directory.

### OpenClaw
- **Config file**: `~/.openclaw/workspace/config/mcporter.json`
- **Gotcha**: Do not write MCP objects straight to a root JSON; it uses standard `mcpServers` format wrapped in `mcporter.json`.

### Antigravity / Gemini
- **Config file**: `~/.gemini/antigravity/mcp_config.json`
- Configuration handles generic MCPs cleanly but relies on accurate script targets (`openviking_mcp.py`).

## 4. Hidden Config Override (Path Drift Still Happens)

Even after fixing `~/.claude/settings.json`, some clients may still read other persisted config files first.

**Common hidden sources to verify:**
- `~/.claude.json` (global Claude CLI state; can include `mcpServers`)
- `~/.codex/config.toml`
- `~/.gemini/antigravity/mcp_config.json`

**Symptom:**
MCP list/health still points to an old script path (for example a removed `~/.gemini/.../openviking_mcp.py`) even though your primary config is already fixed.

**Fix:**
Ensure all active client config sources reference the same absolute script path.

## 5. General Diagnosis Advice
1. **Healthcheck Command**: Always run the included `context_healthcheck.sh --deep`. It probes `/health` and forces a dummy query against `/api/v1/search/find`.
2. **Reviewing Logs**: Keep an eye on `.context_system/logs/` or `journalctl --user -u viking-daemon`.
3. **Empty Searches?**: If the legacy compatibility search finds nothing for "today", verify the actual JSONL sources (like `history.jsonl`) are being actively modified by your terminals. Sometimes terminals change their implicit storage paths.
