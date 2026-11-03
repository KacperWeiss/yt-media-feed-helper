---
description: Diagnose the yt-media-feed-helper installation (Python, venv, authorization, workspace, catalog, logs)
---
1. Run `python "${CLAUDE_PLUGIN_ROOT}/server/launch.py" doctor` and read the JSON (`checks`, `fixes`).
2. Call `auth_status` (ytfeed server). If the server does not respond, the venv is most likely missing: tell the user to run
   `python "${CLAUDE_PLUGIN_ROOT}/server/launch.py" install` in their own terminal.
3. List the problems with concrete fixes. The user runs authorization (`launch.py auth`) THEMSELVES in a terminal —
   do not run it. Do not read token files or the client secret.
