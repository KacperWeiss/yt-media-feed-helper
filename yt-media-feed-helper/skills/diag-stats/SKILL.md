---
name: diag-stats
description: Statistics from the yt-media-feed-helper hook diagnostic logs (logs/diag-*.jsonl, guard-*.jsonl) — tool calls per agent/subagent, p50/p95 durations, result sizes, errors, quota cost, block decisions, subagent parallelism. Use for /yt-media-feed-helper:diag or questions about what the agent did (e.g. "show diagnostics", "which tools were slow", "what got blocked", "statystyki diagnostyczne").
---

> `<workspace>` = `${user_config.workspace_dir}`; if empty, the project directory. The same path is shown in the SessionStart status line (`workspace …`) and in the `workspace` field of `quota_status`/`auth_status`.

# Diagnostic statistics (no MCP)

Logs: `<workspace>/logs/diag-YYYY-MM-DD.jsonl` (hook events) and `guard-YYYY-MM-DD.jsonl` (deny/ask decisions).
Outside a workspace, logs go to the plugin data directory (`${CLAUDE_PLUGIN_DATA}/logs`). The guard log (`guard-*`) is ALWAYS written to `${CLAUDE_PLUGIN_DATA}/logs` (the source of truth); the workspace holds only a copy — if they differ, trust the data directory.

Run:
```
python "${CLAUDE_PLUGIN_ROOT}/skills/diag-stats/scripts/diag_stats.py" --logs "<workspace>/logs" --since 1d --by tool --format md
```
Options: `--since 1d|7d|24h`, `--session <ID>`, `--by tool|agent|event`, `--top 10`, `--format md|json`,
`--archive-older-than 30` (only lists archiving candidates; never deletes anything without the user's consent).

Output: tables — call counts, p50/p95 `duration_ms`, total `result_bytes` (≈ tokens: bytes/4), errors, deny/ask decisions
from the guard, the most expensive tools by `quota_cost`, and the maximum number of concurrently active subagents
(from SubagentStart/Stop). Show the user the Markdown table and 2–3 conclusions. The logs contain no result content
(metadata only, secrets redacted).
