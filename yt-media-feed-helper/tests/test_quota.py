import json
import subprocess
import sys
from pathlib import Path

import pytest

from ytfeed.config import Config
from ytfeed.errors import ToolError
from ytfeed.quota import QuotaLedger

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"


def ledger(env, **cfg):
    return QuotaLedger(env["data"], Config(cfg))


def test_reserve_and_adjust(env):
    q = ledger(env)
    snap = q.reserve("yt_comments", "commentThreads.list", 3)
    assert snap["cost"] == 3 and snap["used_today"] == 3 and snap["remaining"] == 9997
    q.adjust("yt_comments", "commentThreads.list", -2)
    st = q.status()
    assert st["buckets"]["default"]["used"] == 1
    assert st["top_consumers"][0] == {"tool": "yt_comments", "units": 1}


def test_reset_at_pacific_midnight(env, monkeypatch):
    q = ledger(env)
    # 2026-11-02 12:00Z = 04:00 PST (after the DST change on 11/1) → PT day 2026-11-02
    assert q.day_pt() == "2026-11-02"
    assert q.resets_at() == "2026-11-03T08:00:00Z"  # PST = UTC-8
    q.reserve("t", "videos.list", 5)
    monkeypatch.setenv("YTFEED_NOW", "2026-11-03T07:59:00Z")
    assert q.status()["buckets"]["default"]["used"] == 5
    monkeypatch.setenv("YTFEED_NOW", "2026-11-03T08:00:01Z")
    assert q.status()["buckets"]["default"]["used"] == 0


def test_dst_boundary(env, monkeypatch):
    q = ledger(env)
    # before the DST change (PDT = UTC-7): reset on November 1 at 07:00Z
    monkeypatch.setenv("YTFEED_NOW", "2026-10-31T12:00:00Z")
    assert q.resets_at() == "2026-11-01T07:00:00Z"
    monkeypatch.setenv("YTFEED_NOW", "2026-11-01T06:59:00Z")
    assert q.day_pt() == "2026-10-31"
    monkeypatch.setenv("YTFEED_NOW", "2026-11-01T07:00:00Z")
    assert q.day_pt() == "2026-11-01"


def test_search_bucket_toggle(env):
    q = ledger(env)
    snap = q.reserve("yt_search", "search.list")
    assert snap["bucket"] == "default" and snap["cost"] == 100
    q2 = ledger(env, quota={"buckets": {"search": {"enabled": True}}})
    snap2 = q2.reserve("yt_search", "search.list")
    assert snap2["bucket"] == "search" and snap2["cost"] == 1
    assert q2.status()["buckets"]["search"]["calls"] == 1


def test_reserve_reached(env):
    q = ledger(env, quota={"buckets": {"default": {"daily_limit": 100, "reserve": 10}}})
    q.reserve("t", "videos.list", 90)
    with pytest.raises(ToolError) as e:
        q.reserve("t", "videos.list", 1)
    assert e.value.code == "QUOTA_RESERVE_REACHED"


def test_quota_exceeded_from_api(env):
    q = ledger(env)
    q.mark_exhausted()
    assert q.status()["buckets"]["default"]["used"] == 10000
    with pytest.raises(ToolError):
        q.reserve("t", "videos.list", 1)


def test_report_budget_hard_limit(env):
    q = ledger(env)
    rep = q.begin_report()
    assert rep["budget"] == 2000  # default report budget
    q.reserve("t", "videos.list", 1990)
    with pytest.raises(ToolError) as e:
        q.reserve("t", "videos.list", 20)
    assert e.value.code == "REPORT_BUDGET_EXCEEDED"
    q.begin_report(5000)
    q.reserve("t", "videos.list", 20)
    q.end_report()
    q.reserve("t", "videos.list", 3000)  # outside a report only the daily limit applies


def test_estimate(env):
    q = ledger(env)
    est = q.estimate([
        {"tool": "yt_recent_uploads", "count": 1, "args_hint": {"channels": 9, "videos": 45}},
        {"tool": "yt_comments", "count": 4, "args_hint": {"max_threads": 100}},
        {"tool": "steam_news", "count": 2},
        {"tool": "yt_search", "count": 1},
    ])
    assert est["estimated_units"] == 9 + 1 + 4 + 100
    assert est["fits"] and est["fits_report_budget"]


def test_concurrent_processes(env):
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "from pathlib import Path; from ytfeed.config import Config; from ytfeed.quota import QuotaLedger;"
        "q=QuotaLedger(Path(r'%s'), Config());"
        "[q.reserve('t','videos.list',1) for _ in range(40)]"
    ) % (SERVER_DIR, env["data"])
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(2)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    state = json.loads((env["data"] / "quota.json").read_text(encoding="utf-8"))
    assert state["buckets"]["default"]["used"] == 80
