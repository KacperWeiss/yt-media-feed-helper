# Contributing

Working with Claude Code? `CLAUDE.md` holds the project rules, and the project skills in `.claude/skills/`
(`dev-add-mcp-tool`, `dev-change-contract`, `dev-release`, `dev-package`) walk through the changes that touch many files
at once.

Thanks for your interest in `yt-media-feed-helper`. Bug reports and pull requests are welcome; for security issues follow
[docs/SECURITY.md §4](docs/SECURITY.md#4-reporting-a-vulnerability) instead of opening a public issue.

## Development setup

Requirements: Python >= 3.10 and Claude Code (for plugin validation and manual testing).

```bash
git clone https://github.com/kacperweiss/yt-media-feed-helper.git
cd yt-media-feed-helper
python -m venv .venv
# Windows: .venv\Scripts\python   |   Linux / macOS: .venv/bin/python
.venv/bin/python -m pip install -r yt-media-feed-helper/server/requirements.txt \
    -c yt-media-feed-helper/server/requirements.lock pytest pytest-cov ruff
```

Run the checks before submitting:

```bash
.venv/bin/python -m pytest yt-media-feed-helper/tests -q             # runs in mock mode, no network
.venv/bin/python -m ruff check yt-media-feed-helper --select E,F,W --ignore E501
claude plugin validate ./yt-media-feed-helper --strict                # plugin manifest, agents, skills, hooks
claude plugin validate . --strict                                     # marketplace
```

To try your changes in Claude Code, add the clone as a local marketplace (`/plugin marketplace add <path-to-your-clone>`), install the plugin and set `YTFEED_MOCK=1` to work against fixtures without Google Cloud.

## Repository layout

```text
.claude-plugin/marketplace.json   marketplace manifest
docs/                             ARCHITECTURE.md, SECURITY.md
yt-media-feed-helper/             the plugin
  .claude-plugin/plugin.json      plugin manifest and userConfig options
  .mcp.json                       MCP server definition (ytfeed)
  agents/                         subagents: channel-categorizer, scout, steam-checker, summarizer
  commands/                       slash commands (thin wrappers around skills)
  skills/                         skills; skills/*/scripts are stdlib-only helpers
  hooks/                          hooks.json + scripts (guard, diagnostics, info mode, subagent tracker)
  server/                         launch.py, requirements*, ytfeed/ package (tools, sources, catalog, reports, quota)
  templates/                      default taxonomy.yaml and config.yaml for new workspaces
  tests/                          pytest suite + fixtures (YouTube / Steam responses, hook payloads, catalog)
```

## Conventions
- **English** for code, comments, docstrings, messages, prompts and docs. Non-English text is fine where it is data or
  functionality: language-detection keyword lists, the info-mode keyword classifier, test fixture content, and taxonomy
  aliases / `term_aliases` (e.g. RON, Rzplita).
- **No absolute machine paths** or personal user names anywhere. Use `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`,
  `%APPDATA%`, `~`, `<plugin_root>` or relative paths; test data uses neutral examples such as `C:/Users/u/...`.
- **Tests are required** for every behaviour change. New fixtures go to `tests/fixtures/` (see `make_fixtures.py`); guard
  changes need hook-payload tests; security fixes get a regression test. Assert on error codes and keys rather than
  message text where possible.
- **Keep it read-only.** Never add tools or code paths that write to YouTube, Google or Steam, widen the OAuth scope, or make
  the guard return `allow`. Hooks and `skills/*/scripts` must stay standard-library only and run under `python -I`.
- Dependency changes must update `requirements.lock` (and `requirements.hashes.txt` where applicable).
- Never commit secrets, tokens, logs or a real `catalog/channels.yaml`.
