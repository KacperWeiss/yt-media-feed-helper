# yt-media-feed-helper

A Claude Code plugin for following news from YouTube creators (core) and Steam games (supplement), filtered through a
multi-dimensional channel catalog that you approve. **Read-only by design**: the OAuth scope is `youtube.readonly`, the MCP
server has no tools that write to YouTube or Steam, and hooks block mutating requests and access to secrets.

- MCP server `ytfeed` (Python, `mcp` SDK 2.x): 23 tools, 6 resources, 3 prompts. In Claude Code the tools are named
  `mcp__plugin_yt-media-feed-helper_ytfeed__<tool>`.
- Subagents: `channel-categorizer` (haiku), `scout` (sonnet), `steam-checker` (haiku), `summarizer` (opus).
- Commands: `/yt-media-feed-helper:catalog-sync`, `:categorize`, `:review-categories`, `:report`, `:diag`, `:quota`, `:doctor`.
- Skills: `feed-guide`, `catalog-workflow`, `report-workflow` (use MCP); `taxonomy-edit`, `diag-stats`, `reports-browse`,
  `catalog-export` (no MCP, stdlib scripts).
- Reports: a short summary in chat plus `reports/YYYY-MM-DD-<topic>.md`, in which **every claim links to its source**
  (enforced in code by `report_save`). Official, semi-official and community information are kept in separate sections.
  The report is written in the language of your question.

Architecture: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md). Security model: [`docs/SECURITY.md`](../docs/SECURITY.md).

## 1. Requirements
- Python >= 3.10 on `PATH` (Windows: from python.org or the `py` launcher; **not** the Microsoft Store alias —
  `python --version` must work in a fresh terminal).
- Claude Code with plugin support.
- A Google account with a YouTube channel (your subscriptions are the source of the catalog). To just try the plugin,
  mock mode (§8) is enough — no Google Cloud needed.

## 2. Google Cloud — OAuth client (one-time, ~10 min)
1. [Google Cloud Console](https://console.cloud.google.com/) → create a project (e.g. `yt-media-feed-helper`).
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **OAuth consent screen** (Google Auth Platform):
   - User type **External**, publishing status **Testing**;
   - add your Gmail address as a **Test user**;
   - **Data access / Scopes**: only `https://www.googleapis.com/auth/youtube.readonly`.
4. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app**.
5. Copy the **Client ID** and **Client secret** from the confirmation dialog. You do **not** need to download the JSON
   file — the plugin reads both values from environment variables (§3). Never paste them into a chat with Claude or
   into a repository.

> **Testing mode**: Google revokes the refresh token after about 7 days. The plugin detects this
> (`invalid_grant` → `AUTH_REQUIRED`) and prints a single command to re-authorize (§4.3), which you run yourself in a terminal.

## 3. Secrets and environment variables

### 3.1 What is configured and where
Every setting can be provided as an **environment variable** (`YTFEED_*`) or as a **plugin option** (§3.4).
Precedence: **environment variable → plugin option → file / default**. Neither the MCP tools, nor `doctor`, nor the logs
ever return secret values — only *where* a value came from.

| Environment variable | Plugin option | Secret | Description |
|---|---|---|---|
| `YTFEED_GOOGLE_CLIENT_ID` | `google_client_id` | no | Client ID of the Desktop app (§2.5) — **required** |
| `YTFEED_GOOGLE_CLIENT_SECRET` | `google_client_secret` (keychain) | **yes** | Client secret (§2.5) — **required** |
| `YTFEED_GOOGLE_CLIENT_SECRET_FILE` | `google_client_secret_path` | file | *Alternative* to the two above: path to a downloaded `client_secret_*.json` (the legacy name `YTFEED_CLIENT_SECRET` still works) |
| `YTFEED_STEAM_API_KEY` | `steam_api_key` (keychain) | **yes** | Reserved — not needed in v1 (public Steam endpoints are used) |
| `YTFEED_WORKSPACE` | `workspace_dir` | no | Directory for `catalog/`, `reports/`, `logs/` (empty = project directory; a dedicated directory is recommended) |
| `YTFEED_MOCK` | `mock_mode` | no | `1` = fixtures instead of real APIs (demo / tests) |
| `YTFEED_REPORT_BUDGET_UNITS` | `report_budget_units` | no | Permanently higher report quota budget (default 2000) |
| `YTFEED_STRICT_DEPS` | `strict_deps` | no | `1` = install dependencies with hash checking only (Windows x64 + Python 3.15) |
| `YTFEED_SECRETS_DIR` | — | directory | Where the OAuth token is stored (default `%APPDATA%\yt-media-feed-helper` on Windows, `~/.config/yt-media-feed-helper` on POSIX) |

**What is deliberately NOT an environment variable:** the OAuth token (access + refresh token). The plugin writes and
refreshes it itself, so it cannot live in a static variable. Instead it is **encrypted with DPAPI** on Windows (only your
account can decrypt it) and gets an ACL for your user only. A variable in the registry would be stored in plain text —
that would be a step backwards.

### 3.2 Setting variables — Windows (PowerShell, user scope)
The secret is typed into a hidden prompt (`-AsSecureString`) — it does not end up in PowerShell history or on screen:
```powershell
[Environment]::SetEnvironmentVariable("YTFEED_GOOGLE_CLIENT_ID", (Read-Host "Google Client ID"), "User")
$s = Read-Host "Google Client secret" -AsSecureString
[Environment]::SetEnvironmentVariable("YTFEED_GOOGLE_CLIENT_SECRET", [System.Net.NetworkCredential]::new("", $s).Password, "User")
Remove-Variable s
# optional: a dedicated workspace (recommended when the project contains code)
[Environment]::SetEnvironmentVariable("YTFEED_WORKSPACE", "$HOME\Documents\yt-feed", "User")
```
- Use the **`User`** scope, never `Machine` (visible to every account; `doctor` warns about it).
- **Close and reopen** your terminal and Claude Code — processes read the environment only at startup.
- Check without revealing the value: `[bool][Environment]::GetEnvironmentVariable("YTFEED_GOOGLE_CLIENT_SECRET","User")` → `True`.
- Remove: `[Environment]::SetEnvironmentVariable("YTFEED_GOOGLE_CLIENT_SECRET", $null, "User")`.

### 3.3 Setting variables — Linux / macOS
Do not type the secret directly into `export …` (it ends up in shell history). Ideally, read it from a secret manager in
your shell startup file:
```bash
# macOS (Keychain): one-time — security add-generic-password -a "$USER" -s ytfeed-google-client-secret -w
export YTFEED_GOOGLE_CLIENT_ID="123-abc.apps.googleusercontent.com"
export YTFEED_GOOGLE_CLIENT_SECRET="$(security find-generic-password -a "$USER" -s ytfeed-google-client-secret -w)"
# Linux (libsecret): secret-tool store --label=ytfeed service ytfeed key google-client-secret
export YTFEED_GOOGLE_CLIENT_SECRET="$(secret-tool lookup service ytfeed key google-client-secret)"
```
If you keep the values in a separate file (e.g. `~/.config/yt-media-feed-helper/env`), `chmod 600` it and never put it in
any repository.

### 3.4 Alternative: plugin options (OS secure store)
Instead of variables you can provide the values as plugin options. Sensitive fields (`google_client_secret`,
`steam_api_key`) are stored by Claude Code in the **operating system's secure credential store** (not in `settings.json`)
and are passed only to the plugin's MCP server — according to the Claude Code documentation they are not exposed to the
agent's Bash tool environment.
- In a session: `/plugin` → `yt-media-feed-helper` → **Configure** (masked field).
- From a terminal (Claude Code >= 2.1.285), without writing the secret to a file:
  ```powershell
  $s = Read-Host "Google Client secret" -AsSecureString
  @{ google_client_id = (Read-Host "Google Client ID"); google_client_secret = [System.Net.NetworkCredential]::new("", $s).Password } |
    ConvertTo-Json -Compress | claude plugin configure yt-media-feed-helper@yt-media-feed-helper-marketplace --values-stdin
  Remove-Variable s
  claude plugin configure yt-media-feed-helper@yt-media-feed-helper-marketplace   # lists set / not set, never values
  ```
  Restart Claude Code afterwards.

**Which method should I use?** Environment variables are the standard (12-factor, CI, scripts), but they are visible to
*every* process of your account — including the agent's shell. The guard hook blocks reading them and dumping the
environment, but that protection is best-effort. A plugin option does not enter the regular environment, so on a
workstation it is the **safer choice for the client secret**. Set each secret in one place only — if both are set, the
environment variable wins.

### 3.5 Secret hygiene
- After switching to variables, **delete** the downloaded `client_secret_*.json` (fewer copies of the secret; `doctor` reminds you).
- Never paste secrets into a conversation with Claude — type them only in your own terminal.
- Client secret leaked → Google Cloud Console → Credentials → **Reset secret**, update the variable, then run §4.3.
- Revoke the plugin's access to your account: <https://myaccount.google.com/permissions> → remove the app; then delete the
  token (`%APPDATA%\yt-media-feed-helper\token.json`, or `token.json` in your `YTFEED_SECRETS_DIR`).

## 4. Installing the plugin

### 4.1 Add the marketplace and install
From GitHub (in Claude Code):
```text
/plugin marketplace add kacperweiss/yt-media-feed-helper
/plugin install yt-media-feed-helper@yt-media-feed-helper-marketplace
```
From a local clone (pass the path to the cloned repository):
```text
/plugin marketplace add ./yt-media-feed-helper
/plugin install yt-media-feed-helper@yt-media-feed-helper-marketplace
```
From a terminal the equivalents are `claude plugin marketplace add …` and
`claude plugin install yt-media-feed-helper@yt-media-feed-helper-marketplace`. You can skip the option prompts shown when
the plugin is enabled if you use the environment variables from §3 — all options are optional. Non-sensitive options can be
changed later in `/config`, sensitive ones as in §3.4.

### 4.2 Python dependencies (in your own terminal)
The plugin path is printed by `/yt-media-feed-helper:doctor` (field `plugin_root`); typically
`~/.claude/plugins/cache/…/yt-media-feed-helper`.
```powershell
python -I "<plugin_root>/server/launch.py" install   # venv in ${CLAUDE_PLUGIN_DATA}/venv, versions from requirements.lock
```
The first MCP server start creates the venv too if you skip `install`, but it may exceed the MCP startup timeout.
With `YTFEED_STRICT_DEPS=1` the install uses `pip --require-hashes` (`requirements.hashes.txt`, Windows x64 + CPython 3.15
only; PyYAML is pinned there by its sdist hash and built locally). Leave it disabled on other platforms.

### 4.3 YouTube authorization (in your own terminal — not through the agent)
```powershell
python -I "<plugin_root>/server/launch.py" auth
```
A browser opens → sign in with the account you added as a Test user → grant **read** access to YouTube. The
"Google hasn't verified this app" screen is expected for apps in Testing mode (Continue). The token is written to
`%APPDATA%\yt-media-feed-helper\token.json` (POSIX: `~/.config/yt-media-feed-helper/token.json`) — DPAPI-encrypted on
Windows with an ACL for your user only, mode `0600` on POSIX. If Google grants any scope other than `youtube.readonly`,
the token is **not** saved. Re-run the same command about every 7 days (Testing mode).

### 4.4 Verification
```powershell
python -I "<plugin_root>/server/launch.py" doctor
```
`doctor` (JSON output) shows: Python version, venv, **where** each setting comes from (`settings` — secrets as
"set (VARIABLE_NAME)", never the value), whether the client configuration and token are present, the token's actual scope
according to Google (`tokeninfo`), the workspace and catalog validity. `warnings` lists redundant copies of secrets and
machine-level variables. In Claude Code: `/yt-media-feed-helper:doctor`, then `/mcp` — the `ytfeed` server should be connected.

Trying it without Google Cloud: `YTFEED_MOCK=1` (or the `mock_mode` option in `/config`) → everything runs on fixtures.

### 4.5 Update and uninstall
- Update: `claude plugin marketplace update yt-media-feed-helper-marketplace` and
  `claude plugin update yt-media-feed-helper@yt-media-feed-helper-marketplace`, then repeat §4.2 (a changed
  `requirements.lock` triggers a fresh venv automatically).
- Uninstall: `/plugin uninstall yt-media-feed-helper@yt-media-feed-helper-marketplace`; remove the `YTFEED_*` variables
  (§3.2), the `%APPDATA%\yt-media-feed-helper` directory (POSIX: `~/.config/yt-media-feed-helper`) and revoke the app's
  access in your Google account (§3.5).

## 5. First steps
Use the **default** permission mode (not bypass/auto) — catalog approvals are blocked in those modes.
```text
/yt-media-feed-helper:doctor
/yt-media-feed-helper:catalog-sync                 # dry run: new channels and cost
/yt-media-feed-helper:catalog-sync --apply         # permission prompt → channels.yaml is written
/yt-media-feed-helper:categorize uncategorized     # waves of 5 subagents → proposals
/yt-media-feed-helper:review-categories            # table → your decisions → catalog_commit (prompt shows the full content)
/yt-media-feed-helper:report Give me the latest official announcements from the Strategy category
/yt-media-feed-helper:quota                        # compare usage with the estimate
```
Example questions:
- "Give me official news about Brandon Sanderson's books."
- "Check the overall community reception of the upcoming Europa Universalis 5 DLC."
- "Summarize how Polish fans of EU5 received the latest change to the events around forming the Polish-Lithuanian Commonwealth."
- Questions may be asked in any language, and the report follows that language, e.g.
  "Podsumuj odbiór najnowszego patcha do EU5 wśród polskich graczy." produces a Polish report.

Channels marked "⚠ missing: …" in the review table need the missing dimension filled in during a later review.
The `Supporter` subscription value (channel memberships) is invisible to the API — set it yourself via `catalog_set_manual`.

## 6. Default behaviour (editable in `catalog/config.yaml`)
- Catalog dimensions: `channel_type` (e.g. YouTube creator, Book author, Game publisher, Game developer, Media outlet),
  `content` (Gaming, Books, Movies, TV series, Anime, Politics, History, Science), `communication` (Official,
  Community Manager, Media partner, Unofficial, Not applicable), `specialization` (hierarchical, e.g. Strategy → Grand
  strategy → Europa Universalis), `subscription` (Supporter, Subscriber, None), `language_region` (PL, EN, DE, multi).
  Edit `catalog/taxonomy.yaml` (skill `taxonomy-edit`) to add values, synonyms and aliases.
- "Latest" window: Gaming 14 days; Books / Movies / Anime / Politics 30 days (`--window` overrides).
- 5 videos per channel, 100 most relevant comments per video, Shorts skipped.
- **Quota budget: 2000 units per report (hard limit).** A one-off increase requires your approval (permission prompt);
  a permanently higher budget can ONLY be set via `userConfig.report_budget_units` (or `YTFEED_REPORT_BUDGET_UNITS`).
  `reports.budget_units` in `config.yaml` can only lower the budget (values > 2000 are ignored). An abandoned budget expires
  after `reports.budget_ttl_minutes` (120) or at midnight Pacific Time; manually: `quota_estimate(plan=[], end_report=true)`.
  Syncing and categorizing do not count against the report budget.
- Comments: at most `max_pages` (default 3) pages per video; comments whose language cannot be determined (`lang: null`)
  are not dropped by the `language_hint` filter, only counted in `sample.lang_unknown`.
- Shorts: <= 60 s, or `#short` / `#shorts` in the title, description or tags (a 180 s threshold would drop 1–3 minute trailers).
- **This file cannot disable the guards**: `guard.strict_modes` is ignored (bypass/auto are always denied), and security
  settings can only be tightened (`guard.scope: everywhere`, `search.require_confirmation: true`).
- Report language = the language of your question. Quotes stay in their original language, with a translation when it
  differs from the report language.
- Community reception: mixed sample of ~70% most relevant + ~30% newest comments (`order="mixed"`), copypasta counted once,
  `log(1+likes)` weighting; counts and Wilson 95% intervals come from the `stance_stats` tool; with a sample < 30 the report
  gives counts only; for questions about a change, the distribution before/after the change date T0 is shown. The report's
  front matter has a `quality` block (sample sizes, unknown language, pages scanned, budget used, coverage). In chat:
  3–5 bullets with links to report sections.
- Report validator: every line containing a number in the "Discrepancies" section and in italic notes needs a `[[ref]]`.
- `yt_search` (outside the catalog) only on explicit request or with the `--search` flag (counted against the budget).
- Synonyms / aliases (e.g. EU5, RON, Rzplita, PLC) live in `catalog/taxonomy.yaml` (`synonyms`, `aliases`, `term_aliases`) — extend them.

## 7. Security
Full threat model: [`docs/SECURITY.md`](../docs/SECURITY.md).
- OAuth uses only `youtube.readonly`; the server refuses to work with a token carrying any other scope (`SCOPE_MISMATCH`).
- The MCP server has no external write tools; local writes go only to `catalog/`, `reports/`, `logs/` and the data directory.
- Hooks (active wherever the plugin is enabled):
  - mutating HTTP requests to YouTube / Google / Steam (curl / wget / Invoke-WebRequest / `python -c …`) → **deny**;
  - reading secrets (token, client secret, Steam key) via Read / Grep / Glob / Bash / PowerShell → **deny**; paths are
    normalized (`%APPDATA%` / `$env:` / `~` variables, `..`, `\.\`, 8.3 names, case), and searching a directory that is an
    **ancestor** of the secrets directory (e.g. `Grep` over `AppData`, `grep -r ~`) is blocked;
  - local catalog changes (`catalog_commit`, `catalog_set_manual`, `catalog_add_channel`, writing sync results) → **ask**,
    with the full content of the decision in the prompt; subagent → deny; bypass / auto / dontAsk (and unknown) modes →
    deny "approve in normal mode";
  - **direct edits of `catalog/*.yaml`, proposals and history** (Write / Edit / MultiEdit / NotebookEdit, and `sed -i`, `>`,
    `Set-Content`, `python …` in a shell) → **ask** with a preview of the change; subagents and strict modes → deny; a
    warning appears when `channels.yaml` was changed outside MCP (`catalog_query`, status line);
  - **browser / computer-use / third-party MCP** (Claude in Chrome, Playwright, computer-use…): an action on youtube.com /
    youtu.be / googleapis.com / steampowered.com / steamcommunity.com (including a click in a session that opened YouTube)
    → **ask** (you may knowingly approve), subagents and strict modes → deny; action URLs (e.g. `sub_confirmation`) → deny;
  - in info mode, WebFetch with parameters to a domain other than YouTube / Steam → ask (strict: deny) — exfiltration protection;
  - writes (Write / Edit) outside the workspace are gated by path rules (a workspace is a directory containing `catalog/taxonomy.yaml`).
- Configuration secrets come from environment variables or the OS secure store (§3), never from files in the repository.
  When a plugin secret is set in the environment, the guard denies reading those variables
  (`$env:YTFEED_GOOGLE_CLIENT_SECRET`, `printenv …`) and dumping the whole environment (`env`, `set`,
  `Get-ChildItem env:`, `os.environ`, `process.env`, `/proc/*/environ`); their values are redacted in logs (`[REDACTED-ENV]`).
- Hooks and the MCP server run with an isolated interpreter (`python -I`, server with `cwd` = server directory), so
  `yaml.py` / `ytfeed/` files in an opened repository are never imported.
- Internet content is returned in an `untrusted` field (sanitized, with an `injection_suspect` flag). Titles in the report's
  "Sources" section are escaped, and `report_save` accepts only links from the evidence registry (no images, `//…`, `javascript:`).
- Pattern matching in Bash / PowerShell is best-effort. **Recommended**: add extra `permissions.deny` rules to
  `~/.claude/settings.json`:
  ```json
  {"permissions": {"deny": ["Read(~/AppData/Roaming/yt-media-feed-helper/**)", "Bash(curl * -X POST *)",
                            "Bash(*googleapis.com/youtube*)"]}}
  ```
  (on POSIX use `Read(~/.config/yt-media-feed-helper/**)`; `Read` deny rules also cover Grep and Glob). Run reports in a
  session without browser / computer-use MCP servers.
- The guard log (source of truth) is written to `${CLAUDE_PLUGIN_DATA}/logs/guard-*.jsonl` (outside the workspace);
  `logs/guard-*.jsonl` in the workspace is only an informational copy (agent edits trigger a prompt).
- Diagnostic logs (`logs/diag-*.jsonl`, `logs/guard-*.jsonl`) do not contain tool results; secrets are redacted (also by key
  name: `password`, `token`, `api_key`, `Authorization`…); outside the workspace only tool names are logged, without
  arguments. `.gitignore` excludes logs, tokens and client secrets — the catalog and reports may be committed.
- **Privacy**: `catalog/channels.yaml` is the list of your subscriptions (personal data). Do not publish your workspace in a public repository.

## 8. Mock mode and tests
`YTFEED_MOCK=1` or `mock_mode=true` in `/config` → everything runs on fixtures from `tests/fixtures`, without Google Cloud.
From the plugin directory:
```powershell
python -m venv .venv; .venv\Scripts\python -m pip install -r server\requirements.txt -c server\requirements.lock pytest pytest-cov
.venv\Scripts\python -m pytest tests -q
```
On Linux / macOS use `.venv/bin/python` instead. See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the development workflow.

## 9. Troubleshooting
- **`python` does not work in hooks / Store alias (Windows)**: disable the alias (Settings → Apps → App execution aliases)
  or change `"command": "python"` to `"command": "py"` with `"args": ["-3", ...]` in `hooks/hooks.json` and `.mcp.json`.
- **MCP server does not start**: run `launch.py install`, then `/mcp` → reconnect. Server logs go to stderr.
- **`AUTH_REQUIRED`**: run the command from the `hint` field (re-authorization).
- **`YTFEED_*` variables "do not work"**: processes read the environment at startup — close all terminal windows and
  Claude Code, then reopen them; `launch.py doctor` → the `settings` field shows where each setting comes from.
- **`client_config_present: false` with "incomplete"**: only one of `YTFEED_GOOGLE_CLIENT_ID` /
  `YTFEED_GOOGLE_CLIENT_SECRET` is set — both are required (or a file via `YTFEED_GOOGLE_CLIENT_SECRET_FILE`).
- **`REPORT_BUDGET_EXCEEDED`**: narrow the question or approve a higher budget.
- **Non-ASCII characters in logs look broken**: hooks write UTF-8; in PowerShell use `Get-Content -Encoding utf8`.

## 10. Privacy
No data leaves your machine except calls to the Google / Steam APIs. The response cache and evidence registry
(`${CLAUDE_PLUGIN_DATA}/cache.sqlite`) have a TTL of <= 30 days (YouTube API policy).
