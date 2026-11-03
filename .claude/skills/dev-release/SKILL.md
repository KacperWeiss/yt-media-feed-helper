---
name: dev-release
description: Release checklist for the yt-media-feed-helper plugin and marketplace — version bump, dependency lock and hash files, full verification (tests, plugin validation, no-env launch check, Mermaid), changelog, tag. Use when the maintainer asks to prepare, cut or publish a release or bump the plugin version.
---

# Releasing yt-media-feed-helper

Installed copies are pinned to `version`, so users get changes only after a bump. Do not push or tag remotely unless the
maintainer explicitly asks in this session.

## 1. Pre-flight
```powershell
git status --short          # clean, on main, up to date with origin
git log --oneline <last-tag>..HEAD
```
Read the commits; classify as fix / feature / breaking (renamed taxonomy values, tool names, settings or report format
are breaking for existing users).

## 2. Version
Bump the same SemVer in **both**:
- `yt-media-feed-helper/.claude-plugin/plugin.json` → `version` (this one wins at install time)
- `.claude-plugin/marketplace.json` → `plugins[0].version`

## 3. Dependencies (only if `server/requirements.txt` changed or a dependency needs a security update)
1. Fresh venv via the launcher's own path so it matches what users get:
   `python -I yt-media-feed-helper/server/launch.py install --force --data-dir "$env:TEMP\ytfeed-rel"`.
2. Regenerate `server/requirements.lock` from that venv (`pip freeze`, keep the header comment, update the date and
   Python/OS line).
3. Regenerate `server/requirements.hashes.txt` from the lock with `pip download` + `pip hash` for the platform in its
   header (Windows x64 / the CPython version stated there); keep `[sdist]` markers for packages without a wheel.
   Verify: install with `YTFEED_STRICT_DEPS=1` into another fresh data dir must succeed.
4. Any change to these files changes the venv stamp → users get a rebuilt venv automatically.

## 4. Verify everything
```powershell
.venv\Scripts\python.exe -m pytest yt-media-feed-helper/tests -q
claude plugin validate --strict yt-media-feed-helper
claude plugin validate --strict .
git grep -n -I -P "D:[\\/](?!proj[\\/]|Downloads[\\/])|C:[\\/]Users[\\/](?!u[\\/]|someuser[\\/]|\.\.\.)" -- . ":!*.lock" ":!*hashes.txt"   # must print nothing (neutral examples allowed)
```
- If the maintainer has `docs-internal/tools/noenv_e2e.py`: run it (real launch with no `YTFEED_*` variables).
- Docs with Mermaid changed: validate the diagrams (`docs-internal/tools/mermaid_check.mjs` or GitHub preview).
- Smoke test in a real session: `claude --plugin-dir .\yt-media-feed-helper` → `/yt-media-feed-helper:doctor`, `/mcp`.

## 5. Notes and tag
- Release notes: user-visible changes, breaking changes with the manual migration step (e.g. renamed taxonomy values →
  edit `catalog/taxonomy.yaml` / `channels.yaml`), new settings.
- Commit `Release vX.Y.Z`, create an annotated tag `vX.Y.Z` locally.
- Ask the maintainer before `git push origin main --follow-tags`. After pushing, users update with
  `claude plugin marketplace update yt-media-feed-helper-marketplace` + `claude plugin update yt-media-feed-helper@yt-media-feed-helper-marketplace`.
