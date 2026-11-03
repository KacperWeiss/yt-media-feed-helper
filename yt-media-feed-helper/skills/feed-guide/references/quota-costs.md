# YouTube Data API quota — costs and budget

Daily limit by default 10,000 units (bucket `default`), reset at midnight Pacific Time (America/Los_Angeles).
Reserve of 500 units: below it only zero-cost tools work (`QUOTA_RESERVE_REACHED`).
Method costs (`catalog/config.yaml → quota.costs`, can be overridden):

| API method | units | Used by |
|---|---|---|
| `channels.list` | 1 | `yt_channel_profile`, `catalog_sync_subscriptions` (write) |
| `playlistItems.list` | 1 / page (50) | `yt_channel_profile`, `yt_recent_uploads` |
| `videos.list` | 1 / 50 videos | `yt_recent_uploads`, `yt_video_details` |
| `commentThreads.list` | 1 / page (100) | `yt_comments` |
| `subscriptions.list` | 1 / page (50) | `catalog_sync_subscriptions` |
| `search.list` | 100 (or 1 call in the `search` bucket when `enabled: true`) | `yt_search` |

Cache: profiles 7 days, uploads 1 h, details/comments 6 h, search 1 h (a cache hit = 0 units).

## Report budget
- Default **2000 units per report, hard limit**.
- `quota_estimate(plan, begin_report=true)` opens the budget; every API call is deducted from it;
  `report_save` closes the budget and writes `quota_units` into the report frontmatter.
- Exceeding it → `REPORT_BUDGET_EXCEEDED`. Ask the user; with consent: `quota_estimate(begin_report=true, budget_units=N)`
  (N > 2000 triggers a native permission prompt).

## Typical plans
- Q1 (official announcements, ~9 channels, news): ~10–15 units.
- Q3 (DLC reception, ~15 channels, ~16 videos with comments): ~50–70 units.
- `args_hint` in `quota_estimate`: `{"channels": 9, "videos": 45}`, `{"max_threads": 100}`, `{"subscriptions": 300, "new": 20}`.
