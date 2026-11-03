---
name: dev-change-contract
description: Procedure for changing a cross-cutting contract in yt-media-feed-helper — taxonomy dimensions/values, report section keys and headings (`<!-- section:KEY -->`), report language rules, or plugin settings / environment variables (YTFEED_*, userConfig). These values are duplicated across templates, server code, hooks, agent and skill prompts, fixtures, tests and docs. Use when developing this repo and renaming or adding any of them.
---

# Changing a contract shared by code and prompts

Contracts here are plain strings repeated in many files; nothing breaks at import time, so a missed copy surfaces only as
a wrong report or a silent guard gap. Always: **grep first, change everything in one pass, run the full suite.**

## A. Taxonomy dimensions and values
Source of truth for defaults: `yt-media-feed-helper/templates/taxonomy.default.yaml` (dimension `label`, `values[].key`,
flags `official` / `semi_official` / `parent` / `synonyms` / `steam_appids`, `agent_settable`, `aliases`, `term_aliases`).

1. Find every copy of the old value:
   ```powershell
   git grep -n -I -F "<Old value>" -- yt-media-feed-helper docs README.md
   ```
2. Update, in order: template → `tests/fixtures/catalog/channels.yaml` → code literals (e.g.
   `server/ytfeed/tools/catalog_tools.py` `subscription_value`, `catalog/rules.py`) → `config.default.yaml`
   (`window_days_by_content` keys) → agents (`channel-categorizer.md` rules and output example) → skills
   (`feed-guide` + `references/filters.md`, `catalog-workflow`, `taxonomy-edit`, `catalog-export`) → `commands/*.md`
   → tests → `docs/ARCHITECTURE.md` §2.7 and the plugin README examples.
3. Prefer reading values from the taxonomy over new hard-coded literals.
4. Rules to preserve: `Supporter` is manual-only (`source: user`); the agent may set only `agent_settable` subscription
   values; `communication` is single-valued; Polish aliases stay next to English ones.
5. Existing user catalogs are not migrated automatically — if a value is renamed, mention the manual edit in the release
   notes (users can fix with the `taxonomy-edit` skill + `validate_catalog.py`).

## B. Report section keys, headings, report language
Keys: `tldr, findings, official, semi_official, creator_voices, community_reception, steam, discrepancies, methodology`
(+ generated `sources`).

Places that must agree:
- `server/ytfeed/reports.py` — `SECTION_KEYS`, `HEADING_KEYWORDS` (EN + PL fallback), cited vs numeric-only sections,
  `SOURCES_HEADING`, required `methodology`.
- `skills/reports-browse/scripts/reports_index.py` — lint by marker + heading fallback.
- `agents/summarizer.md`, `skills/feed-guide/references/report-format.md`, `skills/report-workflow/SKILL.md`,
  `commands/report.md` (format, marker lines, `language` passed to `report_save`).
- `agents/scout.md` (what facts/stance data it returns for each section).
- Tests: `test_evidence_and_reports.py`, `test_round2.py`, `test_e2e_mock.py`, `test_security_regression.py`.
- `docs/ARCHITECTURE.md` §2.5 and §7.2.

Keep backward compatibility in the validator (old headings without markers still parse) unless the maintainer agrees to
break it. If a Mermaid diagram changes, validate it (`;` and `<…>` break GitHub rendering — use `#59;`, `#lt;`/`#gt;`).

## C. Settings and environment variables
Precedence everywhere: `YTFEED_*` env var > plugin option (`CLAUDE_PLUGIN_OPTION_*`) > file/default.

Places that must agree:
- `server/ytfeed/paths.py` `SETTINGS` and its stdlib copy in `server/launch.py` `SETTINGS` (+ `SECRET_SETTINGS`).
- `.claude-plugin/plugin.json` `userConfig` (mark secrets `"sensitive": true`).
- `yt-media-feed-helper/.mcp.json` `env`: map options as `CLAUDE_PLUGIN_OPTION_<KEY>: ${user_config.<key>}` — **never**
  map an option onto a `YTFEED_*` name (it would override the user's environment variable). MCP servers do not receive
  `CLAUDE_PLUGIN_OPTION_*` automatically; hooks do.
- Hooks: `hooks/scripts/_common.py` (`workspace`, `budget_baseline`, `secret_files`, `SECRET_ENV_VARS`) and
  `guard_pretool.py` `SECRETS` for any secret setting.
- `launch.py doctor` (`settings_report`) — secrets shown only as "set (SOURCE)".
- Tests: `tests/test_env_config.py`; docs: plugin README §3 table, `docs/SECURITY.md`.
- Re-run the no-env-vars end-to-end check if the maintainer has `docs-internal/tools/noenv_e2e.py`.

## Finish
```powershell
.venv\Scripts\python.exe -m pytest yt-media-feed-helper/tests -q
claude plugin validate --strict yt-media-feed-helper
git grep -n -I -F "<Old value>"     # must return only intentional leftovers (e.g. PL aliases)
```
