---
description: Categorize channels with waves of channel-categorizer subagents (proposals for review)
argument-hint: "[uncategorized|all|@handle ...|UC... ...] [--wave N]"
---
Use the `catalog-workflow` skill, section "Categorization (waves)". Arguments: $ARGUMENTS (default `uncategorized`).
- One channel = one `yt-media-feed-helper:channel-categorizer` subagent; at most N (default 5, `--wave N`) `Agent` calls in one message; start the next wave only after the results arrive.
- At the end show the table from `catalog_list_proposals(format="table")` and invite the user to `/yt-media-feed-helper:review-categories`.
- Do not call `catalog_commit` in this command.
