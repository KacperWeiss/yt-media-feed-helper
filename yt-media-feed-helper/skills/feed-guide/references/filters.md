# Catalog filters — details

## `catalog_query` semantics
- `filters`: `{dimension: [values]}` — AND between dimensions, OR within a dimension.
- `exclude`: `{dimension: [values]}` — a channel is dropped if it has any of the values (NOT).
- `min_confidence`: applies to `source: agent` values; `source: user` values always satisfy it.
- `status`: `approved` (default) | `pending` | `uncategorized` | `any`.
- `text`: match (case- and diacritics-insensitive) against title, handle, notes, channel_id.
- `fields`: `channel_id, title, handle, categories_compact, status, uploads_playlist, categories, notes, subscription_lost_at`.
- `limit` ≤ 100 + `cursor` (from `page.next_cursor`; do not change parameters between pages).

## Hierarchy
A value with a `parent` (e.g. `Europa Universalis` → `Grand strategy` → `Strategy`). A filter on the parent includes
all descendants. A filter on a descendant does NOT include the parent.

## Synonyms and aliases
- Value `synonyms` (e.g. `EU5`, `Europa Universalis V`) — normalized to the key.
- `aliases` (taxonomy section) — a word from the query → (dimension, value), e.g. `strategy → specialization: Strategy`
  (Polish aliases such as `strategia` map to the same value).
- `term_aliases` — a dictionary of content terms for `match_terms` (does not apply to channel categories).

## Examples
| Question | filters |
|---|---|
| official announcements in the strategy category | `{"specialization": ["Strategy"], "communication": ["Official", "Community Manager"]}` |
| official information about Sanderson's books | `{"specialization": ["Brandon Sanderson / Cosmere"], "content": ["Books"], "communication": ["Official", "Community Manager"]}` |
| reception of the EU5 DLC | `{"specialization": ["Europa Universalis"]}` |
| Polish EU5 fans | `{"specialization": ["Europa Universalis"], "language_region": ["PL", "multi"]}` |
