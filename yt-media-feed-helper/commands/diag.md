---
description: Statistics from the hook diagnostic logs (tools, subagents, durations, quota, blocks)
argument-hint: "[1d|7d] [--session ID] [--by agent|tool|event]"
---

> `<workspace>` = `${user_config.workspace_dir}`; if empty, the project directory. The same path is shown in the SessionStart status line (`workspace …`) and in the `workspace` field of `quota_status`/`auth_status`.

Use the `diag-stats` skill. Arguments: $ARGUMENTS (default `--since 1d --by tool`).
Run `python "${CLAUDE_PLUGIN_ROOT}/skills/diag-stats/scripts/diag_stats.py" --logs "<workspace>/logs" ...`
and show the Markdown table + 2–3 conclusions (e.g. subagent parallelism, the most expensive tools, blocks).
