---
name: report-workflow
description: Report orchestration for yt-media-feed-helper — parsing the question into filters and a mode (news/reception), selecting channels from the catalog, the quota budget, splitting channels into groups for scout subagents, steam-checker for games, summarizer and saving a cited report. Use for /yt-media-feed-helper:report or questions about the latest news, announcements, patches or community reception of YouTube creators and games (e.g. "what's new from official EU5 channels", "how did fans receive the DLC", "co nowego", "jak odebrano DLC", "raport").
---

# Report — orchestration

You are the orchestrator. Informational mode: you change nothing except saving the report. Tools:
`mcp__plugin_yt-media-feed-helper_ytfeed__*`. Subagents: `yt-media-feed-helper:scout`, `yt-media-feed-helper:steam-checker`,
`yt-media-feed-helper:summarizer`.

## Algorithm
1. **Parse** the question → `query_spec`:
   `{"mode": "news|reception", "filters": {...}, "exclude": {...}, "window": {"since": "14d"}, "topic_terms": [...], "language": "PL|EN|any", "report_language": "<ISO 639-1>", "games": [...], "question": "<original>"}`.
   - `news` = announcements/information; `reception` = reception/opinions.
   - `language` = the language of the content/comments to analyze (e.g. "Polish fans" → `PL`); `report_language` = the
     language the user wrote the question in (e.g. `en`, `pl`) — the report and the chat answer are written in it.
   - Default window by `content`: Gaming 14 d; Books/Movies/Anime/Politics 30 d; `--window` overrides it.
   - Terms → `taxonomy_get` (aliases, synonyms, hierarchy). Critical ambiguity → ONE question to the user.
2. `catalog_query(filters, exclude)` → channels. 0 channels → tell the user; suggest loosening the filters or
   `yt_search` (~100 units) — call it only if the user agrees or passed `--search`.
3. **Budget**: `quota_estimate(plan, begin_report=true)` (default 2000 units, hard limit; expires after 2 h).
   If the plan does not fit → reduce `videos_per_channel` / the number of channels / comments and tell the user; a higher budget only
   with consent (`budget_units=N` → permission prompt). **Any interruption of the report** (cancellation, summarizer error,
   3× `VALIDATION_FAILED`) → `quota_estimate(plan=[], end_report=true)`, so the budget does not burden later operations.
4. **Games**: if a game is in scope (content=Gaming), first run `steam-checker` with `step: "identify"`: appids from the
   specialization's `steam_appids`, or if there are none, by leaf specialization names (e.g. "Europa Universalis") or a game
   named explicitly in the question.
   If steam-checker returns `needs_user_choice` with `suggested`, **ask the user**, showing the suggestion as the default
   answer ("Do you mean Europa Universalis V? [Y/n] or choose: …", in the user's language); never pick the game yourself.
   Offer to save the resolved appid: `catalog_commit(decisions=[], set_steam_appids=[{"value": "<specialization>", "appids": [N]}])`
   (permission prompt) — later reports will not need to look it up again.
5. **Scouts**: `news` mode — groups of ≤ 8 channels; `reception` mode — groups of ≤ 3–4 channels (scout context limit), with
   `comments_videos_max: 4`. Group by language and specialization; at most `scout_max_parallel` (5) `Agent` calls in one message,
   in parallel with `steam-checker` (`step: "full"`).
   **Reception in a specific language** (e.g. "Polish fans"): add a SEPARATE scout group with channels having
   `communication ∈ {Official, Community Manager}` in that specialization, WITHOUT the `language_region` filter and with
   `language_hint` = that language (fans comment under official EN videos).
6. Pass the collected subagent JSON outputs to the `summarizer` **verbatim, without summarizing or rewriting numbers/refs**
   (subagent outputs only, not raw data), together with the question and `query_spec` (including `report_language`) → `report_save`.
7. **Chat**: answer in the report language — `chat_summary` (3–5 bullets, each linking to a report section anchor) + the report
   path + quota usage (`quota_units`).

## Scout brief (subagent prompt)
```json
{"brief": "<user's question>", "mode": "reception", "group": "eu5-pl-creators",
 "channel_ids": ["UC…"], "channel_meta": {"UC…": "Official"},
 "window": {"since": "2026-10-19T00:00:00Z", "until": null},
 "match_terms": ["Rzeczpospolita Obojga Narodów", "RON", "Unia Lubelska", "Commonwealth", "PLC", "eventy", "patch"],
 "budget_units": 300, "videos_per_channel": 5, "comments_per_video": 100, "comments_videos_max": 4, "language_hint": "PL", "t0": "2026-10-28T00:00:00Z"}
```

## Expanding `match_terms`
PL/EN synonyms from the taxonomy (`term_aliases`, `synonyms`) + your own variants and inflected forms, e.g.
"RON / Rzeczpospolita Obojga Narodów / Rzeczypospolitej / Commonwealth / PLC / Polish-Lithuanian Commonwealth / unia lubelska".
The tools expand aliases automatically (`expand_aliases=true`), but add inflected forms yourself.

## Thresholds and special cases
- Few results (< 3 videos) → widen the window (×2) once; still few → suggest `yt_search` (never call it without consent).
- 0 official channels in the catalog → `yt_search(type="channel")` (with consent / `--search`); offer to add a matching channel
  with `catalog_add_channel(channel)` (permission prompt), then `/yt-media-feed-helper:categorize <UC…>`.
- Without `since`, `yt_recent_uploads` derives the window from the channels' content (`window.source="content"`).
- "Latest change" cannot be determined → ask the user for the version/date or report the candidates.
- Unreleased DLC → no DLC reviews (note it); reception from YouTube comments and the base game's reviews.
- Small sample in the target language (< 30 statements) → the summarizer must state this clearly.

## Change questions and sampling
- Question about "the latest change" (e.g. a patch, an event): determine T0 (steam-checker `identify` / news) and pass
  `"t0": "<ISO>"` to the scouts — the summarizer will show the before/after-T0 distribution.
- Scouts in reception mode use `yt_comments(order="mixed")` (≈ 70% relevance + 30% newest).

## Chat answer format (in the report language)
```
<3–5 bullets, each with a link to a report section: (→ reports/YYYY-MM-DD-<topic>.md#anchor)>
Report: reports/YYYY-MM-DD-<topic>.md · quota: N units (budget from quota_status)
```
Anchors come from `anchors` in the `report_save` result (headings are in the report language, so anchors are too).
