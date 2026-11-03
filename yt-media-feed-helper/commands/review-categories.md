---
description: Review category proposals and commit the user's decisions (catalog_commit)
argument-hint: "[pending|all]"
---
Use the `catalog-workflow` skill, section "Review and approval". Arguments: $ARGUMENTS (default `pending`).
1. Show the table `catalog_list_proposals(format="table")`.
2. Collect the user's decisions in chat (e.g. `1-5 OK; 3: communication=Media partner; 6: reject; new tag X OK`).
3. Build `decisions` and `approve_new_values`, show them to the user in brief, then call `catalog_commit` (native permission prompt).
