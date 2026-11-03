#!/usr/bin/env python3
"""Statistics from the yt-media-feed-helper hook logs (diag-*.jsonl + guard-*.jsonl). Stdlib only, no network.

Examples:
  python diag_stats.py --logs ./logs --since 1d --by tool
  python diag_stats.py --since 7d --by agent --format json
  python diag_stats.py --session abc123 --top 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_since(s: str) -> datetime:
    m = re.match(r"^(\d+)([dh])$", s.strip())
    if not m:
        raise SystemExit(f"--since: oczekiwano np. 1d, 7d, 24h (podano {s!r})")
    n, u = int(m.group(1)), m.group(2)
    return datetime.now(timezone.utc) - (timedelta(days=n) if u == "d" else timedelta(hours=n))


def ts(r: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(r.get("ts", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def load(logs: Path, prefix: str, since: datetime, session: str | None) -> list[dict]:
    out = []
    for f in sorted(logs.glob(f"{prefix}-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ts(r)
            if t is None or t < since or (session and r.get("session_id") != session):
                continue
            out.append(r)
    return out


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = max(0, min(len(v) - 1, round(p / 100 * (len(v) - 1))))
    return v[k]


def key_for(r: dict, by: str) -> str:
    if by == "agent":
        return r.get("agent_type") or ("main" if r.get("actor") == "main" else "subagent")
    if by == "event":
        return r.get("event") or "?"
    return r.get("tool_short") or r.get("tool") or r.get("event") or "?"


def stats(diag: list[dict], guard: list[dict], by: str, top: int) -> dict:
    groups: dict[str, dict] = defaultdict(lambda: {"calls": 0, "durations": [], "bytes": 0, "errors": 0, "quota": 0})
    for r in diag:
        if by != "event" and r.get("event") not in ("PostToolUse", "PostToolUseFailure"):
            continue
        g = groups[key_for(r, by)]
        g["calls"] += 1
        if isinstance(r.get("duration_ms"), (int, float)):
            g["durations"].append(float(r["duration_ms"]))
        g["bytes"] += int(r.get("result_bytes") or 0)
        g["quota"] += int(r.get("quota_cost") or 0)
        if r.get("event") == "PostToolUseFailure" or r.get("error"):
            g["errors"] += 1
    rows = []
    for k, g in groups.items():
        rows.append({"key": k, "calls": g["calls"], "p50_ms": pct(g["durations"], 50), "p95_ms": pct(g["durations"], 95),
                     "result_bytes": g["bytes"], "approx_tokens": g["bytes"] // 4, "errors": g["errors"], "quota_units": g["quota"]})
    rows.sort(key=lambda x: (-x["calls"], x["key"]))
    # subagent parallelism
    events = sorted((ts(r), 1 if r["event"] == "SubagentStart" else -1, r.get("agent_type")) for r in diag
                    if r.get("event") in ("SubagentStart", "SubagentStop") and ts(r) and str(r.get("agent_type") or "").startswith("yt-media-feed-helper:"))
    cur = peak = 0
    for _, d, _ in events:
        cur = max(0, cur + d)
        peak = max(peak, cur)
    decisions: dict[str, int] = defaultdict(int)
    for g in guard:
        decisions[f"{g.get('decision')} {g.get('rule')} {g.get('tool')}"] += 1
    costly = sorted((r for r in rows if r["quota_units"]), key=lambda x: -x["quota_units"])[:top]
    return {"rows": rows[:top], "total_events": len(diag), "guard_decisions": dict(sorted(decisions.items(), key=lambda x: -x[1])),
            "costliest": costly, "max_parallel_subagents": peak,
            "sessions": sorted({r.get("session_id") for r in diag if r.get("session_id")})}


def fmt(v) -> str:
    return "—" if v is None else (f"{v:.0f}" if isinstance(v, float) else str(v))


def to_md(s: dict, by: str) -> str:
    out = [f"### Calls by `{by}` (events: {s['total_events']}, sessions: {len(s['sessions'])})", "",
           f"| {by} | calls | p50 ms | p95 ms | result bytes | ≈ tokens | errors | quota units |", "|---|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        out.append(f"| {r['key']} | {r['calls']} | {fmt(r['p50_ms'])} | {fmt(r['p95_ms'])} | {r['result_bytes']} | {r['approx_tokens']} | {r['errors']} | {r['quota_units']} |")
    out += ["", f"**Max parallel active yt-media-feed-helper subagents:** {s['max_parallel_subagents']}", ""]
    if s["guard_decisions"]:
        out += ["### Guard decisions", "", "| decision rule tool | count |", "|---|---|"]
        out += [f"| {k} | {v} |" for k, v in s["guard_decisions"].items()]
    if s["costliest"]:
        out += ["", "### Costliest (quota)", ""] + [f"- {r['key']}: {r['quota_units']} units" for r in s["costliest"]]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", default=None, help="log directory (default: ./logs)")
    ap.add_argument("--since", default="1d")
    ap.add_argument("--session")
    ap.add_argument("--by", choices=["tool", "agent", "event"], default="tool")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--archive-older-than", type=int, help="list files older than N days (archive candidates; deletes nothing)")
    a = ap.parse_args()
    logs = Path(a.logs or os.path.join(os.getcwd(), "logs"))
    if not logs.is_dir():
        print(f"Log directory does not exist: {logs}", file=sys.stderr)
        return 2
    if a.archive_older_than:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=a.archive_older_than)
        old = [f.name for f in logs.glob("*-*.jsonl") if re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
               and datetime.strptime(re.search(r"(\d{4}-\d{2}-\d{2})", f.name).group(1), "%Y-%m-%d").date() < cutoff]
        print(json.dumps({"archive_candidates": sorted(old)}, ensure_ascii=False, indent=2))
        return 0
    since = parse_since(a.since)
    s = stats(load(logs, "diag", since, a.session), load(logs, "guard", since, a.session), a.by, a.top)
    print(json.dumps(s, ensure_ascii=False, indent=2) if a.format == "json" else to_md(s, a.by))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
