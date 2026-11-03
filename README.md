# yt-media-feed-helper

A Claude Code plugin marketplace with one plugin, [`yt-media-feed-helper`](yt-media-feed-helper/README.md): **read-only**
tracking of YouTube creators and Steam games, filtered through a channel catalog you approve, with source-cited reports.

## Features
- **Channel catalog** built from your YouTube subscriptions, categorized along several dimensions (channel type, content,
  communication, specialization, subscription, language / region) by parallel `channel-categorizer` subagents. Every change
  is a *proposal* that you review and approve.
- **Reports** on the latest news or on community reception (YouTube comments + Steam news and reviews). Every claim links to
  its source, official / semi-official / community information is kept apart, opinion distributions come with real counts
  and confidence intervals, and the report is written in the language of your question.
- **Quota-aware**: a per-report YouTube API budget (2000 units by default) with estimates before any call.
- **Read-only by design**: OAuth scope `youtube.readonly`, no write tools in the MCP server, guard hooks that block
  mutating requests, secret access and unapproved catalog changes, plus JSONL diagnostics of what every agent did.
- **Mock mode** with fixtures, so you can try everything without a Google Cloud project.

## Quick install
1. Create a Google Cloud OAuth client (**Desktop app**, YouTube Data API v3, consent screen in Testing mode, scope
   `youtube.readonly`) and provide the client ID and secret as `YTFEED_GOOGLE_CLIENT_ID` / `YTFEED_GOOGLE_CLIENT_SECRET`
   environment variables or as plugin options — see the [plugin README §2–3](yt-media-feed-helper/README.md#2-google-cloud--oauth-client-one-time-10-min).
2. In Claude Code:
   ```text
   /plugin marketplace add kacperweiss/yt-media-feed-helper
   /plugin install yt-media-feed-helper@yt-media-feed-helper-marketplace
   ```
3. In your own terminal (`<plugin_root>` is printed by `/yt-media-feed-helper:doctor`):
   ```text
   python -I "<plugin_root>/server/launch.py" install
   python -I "<plugin_root>/server/launch.py" auth
   python -I "<plugin_root>/server/launch.py" doctor
   ```
4. Start with `/yt-media-feed-helper:catalog-sync`, `/yt-media-feed-helper:categorize`,
   `/yt-media-feed-helper:review-categories`, then ask e.g.
   `/yt-media-feed-helper:report Check the community reception of the upcoming Europa Universalis 5 DLC`.

No Google account at hand? Set `YTFEED_MOCK=1` (or the `mock_mode` option) to run on fixtures.

## Documentation
- [Plugin user guide](yt-media-feed-helper/README.md) — installation, configuration, secrets, defaults, troubleshooting.
- [Architecture](docs/ARCHITECTURE.md) — components, data locations, request flows.
- [Security](docs/SECURITY.md) — threat model, defenses, residual risks, reporting vulnerabilities.
- [Contributing](CONTRIBUTING.md) — development setup, tests, conventions.

## License
MIT — see [`LICENSE`](LICENSE).
