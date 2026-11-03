---
name: catalog-export
description: Exports the yt-media-feed-helper channel catalog to a Markdown table or CSV (with dimension filters and column selection), e.g. for review or a spreadsheet — no MCP and no network. Use when the user asks for a list, table, overview or export of channels from the catalog (e.g. "export the catalog", "list my channels as CSV", "zestawienie kanałów", "lista kanałów z katalogu").
---

> `<workspace>` = `${user_config.workspace_dir}`; if empty, the project directory. The same path is shown in the SessionStart status line (`workspace …`) and in the `workspace` field of `quota_status`/`auth_status`.

# Catalog export (no MCP)

```
python "${CLAUDE_PLUGIN_ROOT}/skills/catalog-export/scripts/export_catalog.py" --workspace "<workspace>" --format md \
  --filter content=Gaming --filter language_region=PL --columns title,handle,communication,specialization
```
- `--format md|csv`, `--filter dimension=value` (repeatable; AND between dimensions, OR for comma-separated values:
  `--filter communication=Official,Community Manager`), `--status approved|pending|uncategorized|any` (default `any`),
  `--columns` (`channel_id,title,handle,status,<any dimension>,notes,url`), `--out <file>` (e.g. `reports/catalog-export.csv`).
- The filter matches exact keys or taxonomy synonyms (no hierarchy — for full filtering use `catalog_query` via MCP).
- Without `--out` the result goes to stdout — show it to the user. Write files only inside the workspace's `reports/` directory.
