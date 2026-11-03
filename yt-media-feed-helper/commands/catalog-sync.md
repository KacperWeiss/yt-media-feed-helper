---
description: Sync YouTube subscriptions into the local catalog (dry-run first)
argument-hint: "[--apply]"
---
Use the `catalog-workflow` skill, section "Sync". Arguments: $ARGUMENTS
1. Call `catalog_sync_subscriptions(dry_run=true)` and show a summary (new / lost / quota cost).
2. If the user passed `--apply` or confirms in chat, call it with `dry_run=false` (a permission prompt will appear).
3. Suggest `/yt-media-feed-helper:categorize uncategorized` for the new channels.
