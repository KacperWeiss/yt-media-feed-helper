---
name: catalog-workflow
description: Channel cataloging procedure for yt-media-feed-helper — syncing YouTube subscriptions, categorizing channels in waves of channel-categorizer subagents, and having the user review and approve the proposals. Use for /yt-media-feed-helper:catalog-sync, /categorize, /review-categories, or when the user asks to (re)categorize, tag or classify channels (e.g. "categorize my channels", "review the proposals", "skategoryzuj kanały").
---

# Channel cataloging

Tools: `mcp__plugin_yt-media-feed-helper_ytfeed__*` (abbreviated below to the tool name).
Catalog changes are **local** (`catalog/*.yaml`). Nothing is ever written to YouTube.

## Sync
1. `catalog_sync_subscriptions(dry_run=true)` → show: the number of new channels (with titles, max 20), lost channels, and the quota cost.
2. Write only when the user passed `--apply` or confirmed in chat: `catalog_sync_subscriptions(dry_run=false)`.
   A native permission prompt (hook) will appear — the user approves it, not you.
3. Suggest `/yt-media-feed-helper:categorize uncategorized` for the new channels.

## Categorization (waves)
1. Channel selection:
   - `uncategorized` → `catalog_query(status="uncategorized", fields=["channel_id","title"], limit=100)`;
     **continue with `cursor=page.next_cursor` while `page.has_more`** (the catalog can hold hundreds of channels);
   - `all` → `catalog_query(status="any")` plus channels whose `taxonomy_version_at_categorization` is older than the current version;
   - `@handle` / `UC…` → the specified channels.
2. `taxonomy_get` — remember `version`.
3. **Waves**: in ONE message, at most `parallelism.categorizer_wave_size` (default 5) `Agent` tool calls
   with `subagent_type: "yt-media-feed-helper:channel-categorizer"` — one channel per subagent.
   Subagent prompt: `{"channel_id": "UC…", "taxonomy_version": N, "hint": "<user hint or null>"}`.
   Start the next wave only after receiving the results of the previous one (hook R15 blocks exceeding the limit).
4. Collect the one-line JSON results from the subagents; note errors/`flags`.
5. At the end: `catalog_list_proposals(format="table")` → show the table and invite the user to `/yt-media-feed-helper:review-categories`.

## Review and approval
1. `catalog_list_proposals(format="table")` — show the table UNCHANGED (⚠ = confidence < 0.6, ★ = new value, 🔒 = locked).
2. Collect the user's decisions in chat. Syntax (example):
   `1-20 OK; 7: communication=Media partner; 12: reject; new tag 'Crusader Kings' OK; 15: subscription=Supporter`
3. Build the arguments:
   - "OK" → `{"proposal_id": …, "action": "accept"}`;
   - correction → `{"proposal_id": …, "action": "accept_with_changes", "overrides": {"communication": ["Media partner"]}}`
     (overrides are saved as `source: user, locked: true` — the agent will never overwrite them);
   - "reject" → `{"action": "reject"}`;
   - new taxonomy values only when explicitly accepted → `approve_new_values: [{"dimension": "specialization", "value": "Crusader Kings", "parent": "Grand strategy"}]`;
   - manual-only values (e.g. `subscription=Supporter`) do NOT go through `overrides` (the server rejects them) — set them
     separately with `catalog_set_manual(channel_id, set={"subscription": ["Supporter"]})`.
4. Call `catalog_commit(...)`. **Never** without having shown the table and obtained the user's decisions in chat. The permission
   prompt shows the full content of the decisions (channel, values, corrections) — the user approves exactly what they see.
   In bypass/auto permission modes the hook blocks the commit — ask the user to switch to normal mode.
5. Show `diff_summary` and `warnings`. A channel missing required dimensions stays `pending`, and its proposal returns
   to the table marked `⚠ missing: <dimension>` (`needs_input`) — **ask the user** for the missing values and commit
   again as `accept_with_changes` with `overrides`.
6. Direct editing of `catalog/*.yaml` (Write/Edit/shell) always requires approval (hook) — prefer the MCP tools.

## Manual corrections
- Single channel: `catalog_set_manual(channel_id, set={"subscription": ["Supporter"]})` (permission prompt).
- Removing a lock so the agent can propose again: `catalog_set_manual(channel_id, unlock=["specialization"])`.
- Bulk YAML editing without MCP → the `taxonomy-edit` skill.
