---
name: dev-package
description: Builds a minimal distributable package of the yt-media-feed-helper marketplace — only the marketplace manifest, user instructions (READMEs, docs/SECURITY.md, docs/ARCHITECTURE.md), license and the plugin's runtime files (no tests, dev tooling, CLAUDE.md, .claude/, docs-internal/) — as a staged folder plus a reproducible zip with SHA256SUMS, then validates it. Use when the maintainer asks to package, bundle, export, zip or ship the plugin, or to prepare a release artifact.
---

# Packaging yt-media-feed-helper

The script does all the work and checks; your job is to run it, read its report, and fix the cause of any problem
(never hand-edit the output).

## Run
From the repo root, with the dev venv (it has the plugin dependencies needed for the smoke test):
```powershell
.venv\Scripts\python.exe -I .claude\skills\dev-package\scripts\package.py
```
Options: `--out <dir>` (default `dist/`, gitignored), `--ref <git-ref>` for rewritten links (default `v<version>` tag if it
exists, else `main`), `--allow-dirty`, `--skip-validate`, `--skip-smoke`, `--python <exe>`.
Do **not** run it with the shell's working directory inside `dist/` — Windows then cannot replace the old staged folder.

## What goes in (allowlist in the script)
| Packaged | Why |
|---|---|
| `.claude-plugin/marketplace.json` | so the folder/zip can be added as a marketplace |
| `README.md`, `LICENSE`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md` | user instructions, license, security policy |
| `yt-media-feed-helper/**` except `tests/` | the plugin: manifest, `.mcp.json`, agents, commands, skills (+ scripts), hooks, server, templates, README, LICENSE |
| `yt-media-feed-helper/tests/fixtures/{youtube,steam}/` | **runtime data**: mock mode reads them (`sources/mock.py`) |

Only tracked files are considered (`git ls-files`), so caches, venvs, logs, tokens and `docs-internal/` can never leak;
a denylist double-checks it. Relative Markdown links to files that are not packaged (e.g. `CONTRIBUTING.md`) are rewritten
to the GitHub URL of `origin` at the chosen ref.

## What the script verifies
1. Working tree has no uncommitted changes to tracked files (the package = a commit).
2. `plugin.json` and `marketplace.json` versions match.
3. No forbidden files, no test code, mock fixtures present, no broken relative links.
4. `claude plugin validate --strict` on the packaged marketplace and plugin.
5. MCP smoke test: starts the **packaged** server (on a throwaway copy) in mock mode over stdio, lists tools and runs a
   fixture-backed `catalog_sync_subscriptions(dry_run=true)`.
6. The staged folder matches `SHA256SUMS` exactly right before zipping. The zip is reproducible (sorted entries, fixed
   timestamps): the same commit gives a byte-identical zip.

Exit code 0 + `OK` = ready. Exit 1 lists `PROBLEMS:`; exit 2 = refused to run (not the repo root / dirty tree).

## After packaging
- Try it as a user would: `/plugin marketplace add <repo>\dist\yt-media-feed-helper-<version>` →
  `/plugin install yt-media-feed-helper@yt-media-feed-helper-marketplace` (remove any other copy of the marketplace first).
- Report to the maintainer: zip path, size, file count, smoke-test line, and the ref used for rewritten links.
- Publishing the zip (e.g. attaching it to a GitHub release) is the maintainer's decision — ask first. For releases,
  run this as part of the `dev-release` skill after the version bump.

## Keeping the script correct
When a new top-level file or folder becomes part of what users need (or the plugin starts reading a new path at
runtime), update `INCLUDE` / `KEEP_ANYWAY` in `scripts/package.py` and re-run — the smoke test only proves mock mode works.
