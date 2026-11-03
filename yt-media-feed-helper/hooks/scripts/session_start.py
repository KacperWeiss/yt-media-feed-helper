#!/usr/bin/env python3
"""SessionStart (§6.6, optional — session_start.enabled in config.yaml): a single status line
(catalog, proposals, quota) as additionalContext — reads local files only, no API calls."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def status_line(ws: Path) -> str | None:
    if not C.is_workspace(ws):
        return None
    cfg = C.load_config(ws)
    if not cfg.get("session_start", {}).get("enabled", True):
        return None
    ch = ws / "catalog" / "channels.yaml"
    text = ch.read_text(encoding="utf-8") if ch.is_file() else ""
    total = len(re.findall(r"^  [A-Za-z0-9_-]{10,}:\s*$", text, re.M))
    uncategorized = len(re.findall(r"^    status: uncategorized\s*$", text, re.M))
    pending = 0
    for p in (ws / "catalog" / "proposals").glob("*.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("status") == "pending":
                pending += 1
        except (OSError, ValueError):
            continue
    quota = ""
    q = C.data_dir() / "quota.json"
    try:
        limit = int(((cfg.get("quota") or {}).get("buckets") or {}).get("default", {}).get("daily_limit") or 10000)  # Q-19
    except (TypeError, ValueError, AttributeError):
        limit = 10000
    if q.is_file():
        try:
            used = json.loads(q.read_text(encoding="utf-8")).get("buckets", {}).get("default", {}).get("used", 0)
            quota = f", quota today: {used}/{limit} units (per ledger)"
        except (OSError, ValueError):
            pass
    tamper = ""
    if C.catalog_tampered(ws):  # S-01: channels.yaml changed outside the MCP tools
        tamper = " WARNING: catalog/channels.yaml was changed outside the MCP tools — review the diff (git diff) before approving."
    return (f"yt-media-feed-helper: workspace {ws} — catalog of {total} channels ({uncategorized} uncategorized, "
            f"{pending} proposals to review){quota}.{tamper}")


def main() -> int:
    p = C.read_payload()
    line = status_line(C.workspace(p))
    if line:
        C.emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": line}})
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"yt-media-feed-helper session_start: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
