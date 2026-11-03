---
name: reports-browse
description: Browses saved yt-media-feed-helper reports in reports/ without MCP — listing, searching by topic/date, regenerating index.md, citation lint (does every bullet link to a source). Use when the user asks about earlier reports or wants to check a report (e.g. "show my reports", "find the EU5 report", "lint the report", "poprzednie raporty").
---

> `<workspace>` = `${user_config.workspace_dir}`; if empty, the project directory. The same path is shown in the SessionStart status line (`workspace …`) and in the `workspace` field of `quota_status`/`auth_status`.

# Browsing reports (no MCP, no network)

```
python "${CLAUDE_PLUGIN_ROOT}/skills/reports-browse/scripts/reports_index.py" --workspace "<workspace>" list
python "${CLAUDE_PLUGIN_ROOT}/skills/reports-browse/scripts/reports_index.py" --workspace "<workspace>" index
python "${CLAUDE_PLUGIN_ROOT}/skills/reports-browse/scripts/reports_index.py" --workspace "<workspace>" lint [file ...]
python "${CLAUDE_PLUGIN_ROOT}/skills/reports-browse/scripts/reports_index.py" --workspace "<workspace>" show --topic eu5
```
- `list` — table (date, title, file, number of sources from the frontmatter).
- `index` — regenerates `reports/index.md`.
- `lint` — offline: bullets/paragraphs without a link in claim sections, links outside YouTube/Steam, missing
  "Methodology and limitations" section (`<!-- section:methodology -->`). Exit code 1 = problems found.
- `show --topic` — the TL;DR of matching reports (for a quick chat answer with a link to the file).

This skill does not fetch new data from YouTube/Steam — use `report-workflow` for that.
