---
name: dev-add-mcp-tool
description: Checklist for adding, renaming, removing or changing the parameters of a tool in the plugin's `ytfeed` MCP server (yt-media-feed-helper) so code, guard hooks, agent tool lists, skills, docs and tests stay in sync. Use when developing this repo and the task touches `server/ytfeed/tools/*.py`, `TOOL_ORDER`, or an MCP tool's name/arguments/return shape.
---

# Adding or changing a `ytfeed` MCP tool

A tool is defined once in Python but referenced in ~8 other places. Work through every step; skipping one is how
integrations broke during the original build (wrong tool names in agents, missing guard rules, stale counts).

## 1. Decide the safety class first
- **Read-only external call** (YouTube/Steam GET) → allowed silently; must reserve quota (see existing `yt_*` tools).
- **Local write** (catalog, proposals, reports) → the guard must `ask` the main agent, `deny` subagents and bypass/auto.
- **External write** → not allowed in this plugin. Stop and discuss with the maintainer.

## 2. Server code (`yt-media-feed-helper/server/ytfeed/`)
1. Implement in the matching `tools/<group>_tools.py` with the `@tool` decorator (`tools/registry.py` turns exceptions
   into the `{"ok": false, "error": {code, message, hint}}` envelope). Use `ToolError(code, message, hint)` for expected errors.
2. Strict argument types (`StrictBool`, `StrictInt`) for anything the guard reasons about (see S-05 in `docs/SECURITY.md`).
3. Docstring = the tool description the model reads: English, precise, **≤ 400 chars** (tested), include quota cost.
4. Keep results compact (field selection, limits, truncation, cursors); external text goes into `untrusted` via `sanitize.py`;
   new evidence gets a `ref` through the evidence registry if reports may cite it.
5. Quota: reserve before the API call, adjust after (`quota.py`); decide whether it counts against the report budget.
6. Add the name to `TOOL_ORDER` in `server.py` and update the count in the module docstring.

## 3. Guard hook (`yt-media-feed-helper/hooks/scripts/guard_pretool.py`)
- Local write → add a rule in the ytfeed section next to `catalog_commit` / `catalog_set_manual` (`ask` with a readable
  summary of what will change; ambiguous argument values → `ask`).
- Restricted to one subagent type → mirror the `catalog_propose` / `CATEGORIZER` rule.
- Add hook tests in `tests/test_hooks_guard.py` for main agent, subagent, and `bypassPermissions`/`auto`.

## 4. Agents, skills, commands (prompts)
- Add `mcp__plugin_yt-media-feed-helper_ytfeed__<tool>` to the `tools:` frontmatter of **only** the agents that need it
  (least privilege — subagents never get write tools, Bash, Write/Edit, WebFetch or Agent).
- Mention it where the workflow uses it: `skills/feed-guide/SKILL.md` (+ `references/quota-costs.md` for cost),
  `skills/catalog-workflow` or `skills/report-workflow`, and the relevant `commands/*.md`.

## 5. Tests
- `tests/test_mcp_server.py`: update `EXPECTED_TOOLS` and both `len(tools) == N` assertions.
- Behaviour tests on the mock transport; add fixtures under `tests/fixtures/` (via `tests/fixtures/make_fixtures.py` if the
  fixture is generated there).
- If the tool can write: a regression test in `tests/test_security_regression.py`.

## 6. Docs and counts
Find every stale count/list and update it:
```powershell
git grep -n -I -E "\b<OLD_COUNT>\b tools|Tools \(<OLD_COUNT>\)|exposes <OLD_COUNT> tools"
```
Typical places: `docs/ARCHITECTURE.md` §2.1 (tool table), `docs/SECURITY.md` §defenses, `yt-media-feed-helper/README.md`
intro, `server/ytfeed/server.py` docstring.

## 7. Verify
```powershell
.venv\Scripts\python.exe -m pytest yt-media-feed-helper/tests -q
claude plugin validate --strict yt-media-feed-helper
```
Then try it in a real session: `claude --plugin-dir .\yt-media-feed-helper`, `/mcp` → `ytfeed` shows the new count.
