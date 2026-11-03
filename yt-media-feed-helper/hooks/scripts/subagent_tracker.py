#!/usr/bin/env python3
"""SubagentStart/SubagentStop (§6.6): counter of active yt-media-feed-helper:* subagents in
${CLAUDE_PLUGIN_DATA}/subagents.json (used by guard rule R15), keyed by session (Q-09). Never blocks (exit 0)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def update(action: str, payload: dict) -> dict:
    agent_type = str(payload.get("agent_type") or "")
    agent_id = str(payload.get("agent_id") or "")
    path = C.data_dir() / "subagents.json"
    with C.file_lock(path.with_suffix(".lock")):
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {"active": {}}
        active = st.setdefault("active", {})
        now = time.time()
        sid = payload.get("session_id")
        for k in [k for k, v in active.items() if now - float(v.get("started_at_ts", 0)) > 15 * 60]:
            del active[k]  # stale entries (> 15 min; Q-09)
        if action == "session_reset" and sid:
            for k in [k for k, v in active.items() if v.get("session_id") == sid]:
                del active[k]  # SessionStart: orphaned entries of this session (Esc, crash)
        elif agent_id and agent_type.startswith(C.AGENT_PREFIX):
            if action == "start":
                active[agent_id] = {"agent_type": agent_type, "session_id": sid, "started_at": C.now_iso(), "started_at_ts": now}
            else:
                active.pop(agent_id, None)
        path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        return st


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    update(action, C.read_payload())
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"yt-media-feed-helper subagent_tracker: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
