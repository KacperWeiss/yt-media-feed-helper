---
name: taxonomy-edit
description: Edits and validates the yt-media-feed-helper files catalog/taxonomy.yaml and catalog/channels.yaml WITHOUT MCP — adding dimensions and values, synonyms, term aliases, hierarchy, manual overrides (source user). Use when the user wants to change categories, add synonyms (e.g. EU5, RON) or validate the catalog files (e.g. "add a category", "add a synonym", "validate the catalog", "dodaj kategorię", "dodaj synonim").
---

> `<workspace>` = `${user_config.workspace_dir}`; if empty, the project directory. The same path is shown in the SessionStart status line (`workspace …`) and in the `workspace` field of `quota_status`/`auth_status`.

# Editing the taxonomy and catalog (no MCP, no network)

Files in the workspace (default: the project directory): `catalog/taxonomy.yaml`, `catalog/channels.yaml`.
This skill does NOT call `mcp__*` tools. For data from YouTube/Steam → the `feed-guide` skill.

## Procedure
1. Read the file (`Read`) and make the change with the `Edit` tool (preserve comments and indentation).
   Every edit of `catalog/*.yaml` (including `--fix-synonyms`) triggers a permission prompt with a preview of the change (guard hook) —
   this is intentional: the model cannot change the catalog on its own. In bypass/auto modes the edit is blocked (switch to normal mode).
2. Editing rules:
   - New value: add `- key: <Name>` under `dimensions.<dimension>.values` (optionally `description`, `parent`, `synonyms: [...]`).
   - Hierarchy: `parent: <parent key>` (a filter on the parent includes its descendants).
   - Category synonyms: `synonyms: [EU5, Europa Universalis V]`; query-word aliases: the `aliases` section
     (`strategy: {dimension: specialization, value: Strategy}`; Polish aliases such as `strategia` may stay alongside);
     content-term synonyms: `term_aliases` (e.g. `Rzeczpospolita Obojga Narodów: [RON, Rzplita, PLC, Commonwealth]`).
   - Manual override of a channel's category: in `channels.yaml` the value `{value: X, source: user, locked: true, set_at: <ISO>}`.
     The agent will never overwrite it. "Supporter" (subscription) is allowed only with `source: user`.
   - After every taxonomy change, increment `taxonomy_version` by 1.
3. **Always** validate after editing:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/skills/taxonomy-edit/scripts/validate_catalog.py" --workspace "<workspace>"
   ```
   - `--json` — machine-readable output; `--fix-synonyms` — replaces synonyms with canonical keys in `channels.yaml`.
   - Exit code 1 = errors → fix and validate again. Pass warnings (e.g. `STALE_CATEGORIZATION`) on to the user.

Validation rules (the same as in the MCP server): required dimensions for `approved`, `single` cardinality, values must
exist (synonym → warning), `source` ∈ {agent, user} (agent: `confidence` ∈ [0,1] + `rationale`),
"Supporter" only set manually, a warning for channels categorized under an older taxonomy version.
