---
description: Report on the latest news or community reception (YouTube + Steam) with cited sources
argument-hint: "<question in natural language> [--window 14d] [--search]"
---
Use the `report-workflow` skill. Question and options: $ARGUMENTS
- `--window` overrides the time window (default by topic: Gaming 14 d, Books/Movies/Anime/Politics 30 d).
- `--search` = the user's consent to search outside the catalog (`yt_search`, ~100 units, counted against the budget).
- Report quota budget: 2000 units (hard limit; higher only with the user's consent).
- The report and the chat answer are written in the language of the question (quotes stay in the original language, with a
  translation when it differs); pass that language (ISO 639-1) as `language` to `report_save`.
Chat answer: a TL;DR of 3-5 bullets in the report language, each linking to a report section anchor + the report path + quota usage.
