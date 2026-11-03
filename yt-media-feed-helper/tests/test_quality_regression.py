"""Quality regression tests (quality review findings Q-xx; Q-30: coverage gaps)."""
import json
import time

import pytest

from conftest import call, hook_json, run_hook
from ytfeed.catalog import proposals, taxonomy
from ytfeed.clock import parse_since
from ytfeed.errors import ToolError, UpstreamHTTPError
from ytfeed.sources.common import detect_lang, match_terms

PDX = "UCparadoxinteractive0001"
PLS = "UCpolskistrateg000000002"
INJ = "UClegitgaming00000000005"
EU5 = 3450310


# ---- Q-01: comment field projection ------------------------------------------------------------------------------
def test_q01_comment_fields_projection(app):
    r = call(app, "yt_comments", video_id="pdxEU5dd001", max_threads=5, fields=["ref", "likes", "untrusted.text"])
    c = r["data"]["comments"][0]
    assert set(c) <= {"ref", "likes", "untrusted", "injection_suspect"} and set(c["untrusted"]) == {"text"}
    assert call(app, "yt_comments", video_id="pdxEU5dd001", fields=["bogus"])["error"]["code"] == "INVALID_ARG"


# ---- Q-02: report budget lifecycle -------------------------------------------------------------------------------
def test_q02_catalog_tools_do_not_consume_report_budget(app):
    call(app, "quota_estimate", plan=[], begin_report=True, budget_units=3)
    for _ in range(3):
        assert call(app, "yt_channel_profile", channel=PLS)["ok"]  # like the categorizer — outside the report budget
    assert app.ledger.status()["report_budget"]["used"] == 0


def test_q02_budget_ttl_and_end_report(app, monkeypatch):
    call(app, "quota_estimate", plan=[], begin_report=True, budget_units=3)
    monkeypatch.setenv("YTFEED_NOW", "2026-11-02T15:00:00Z")  # +3 h > TTL 2 h
    assert app.ledger.status()["report_budget"]["active"] is False
    assert app.ledger.status()["report_budget"]["end_reason"].startswith("ttl_")
    call(app, "quota_estimate", plan=[], begin_report=True, budget_units=3)
    r = call(app, "quota_estimate", plan=[], end_report=True)
    assert r["data"]["report_closed"]["active"] is False and not app.ledger.status()["report_budget"]["active"]


def test_q02_budget_not_carried_over_day_reset(app, monkeypatch):
    call(app, "quota_estimate", plan=[], begin_report=True)
    monkeypatch.setenv("YTFEED_NOW", "2026-11-03T12:00:00Z")
    assert app.ledger.status().get("report_budget", {}).get("active") is not True


# ---- Q-03: a channel stays visible after an incomplete commit ---------------------------------------------------
def test_q03_incomplete_commit_stays_reviewable(app, ws):
    app.evidence.register(f"yt:ch:{INJ}", title="Legit")
    proposals.propose(ws, taxonomy.load(ws), app.evidence, INJ, {"content": [{"value": "Gaming", "confidence": 0.9, "rationale": "x"}]}, [], None)
    pid = proposals.read(ws, INJ)["proposal_id"]
    r = call(app, "catalog_commit", decisions=[{"proposal_id": pid, "action": "accept"}])
    assert r["ok"] and any("needs_input" in w for w in r["warnings"])
    pend = call(app, "catalog_list_proposals", status="pending", format="json")["data"]["proposals"]
    assert [p["channel_id"] for p in pend] == [INJ] and pend[0]["needs_input"]
    assert "⚠ missing:" in call(app, "catalog_list_proposals")["data"]["table"]
    over = {"channel_type": ["YouTube creator"], "communication": ["Unofficial"], "language_region": ["EN"], "subscription": ["None"]}
    r2 = call(app, "catalog_commit", decisions=[{"proposal_id": pid, "action": "accept_with_changes", "overrides": over}])
    assert r2["ok"], r2
    assert call(app, "catalog_get_channel", channel_id=INJ)["data"]["status"] == "approved"
    assert proposals.read(ws, INJ)["status"] == "accepted"


# ---- Q-04 / Q-05: page limit and language filter (Polish/English comment samples are detection test data) ---------
def test_q04_comment_page_limit(app):
    r = call(app, "yt_comments", video_id="pdxEU5dd001", language_hint="PL", max_pages=1)
    s = r["data"]["sample"]
    assert s["pages_scanned"] == 1 and s["stopped_reason"] == "max_pages" and r["page"]["has_more"]
    assert r["quota"]["cost"] == 1 and any("max_pages" in w for w in r["warnings"])


@pytest.mark.parametrize("text,lang", [
    ("super eventy RON", "PL"), ("fajnie że wreszcie dodatek", "PL"), ("great update", "EN"), ("love this game", "EN"),
    ("RON op", None), ("Łódź is cool city", None),  # mixed → undetermined (kept by the filter) ("Szkoda, że RON da się teraz utworzyć za łatwo", "PL"),
])
def test_q05_detect_lang_short_comments(text, lang):
    assert detect_lang(text) == lang


def test_q05_unknown_language_kept(app):
    r = call(app, "yt_comments", video_id="pdxEU5dd001", language_hint="PL", max_threads=200, max_pages=10)
    s = r["data"]["sample"]
    assert "lang_unknown" in s and s["scanned"] >= s["returned"]
    assert all(c["lang"] in ("PL", None) for c in r["data"]["comments"])


# ---- Q-06: phrase inflection (Polish) --------------------------------------------------------------------------------
@pytest.mark.parametrize("text,terms,hit", [
    ("Nowe eventy unii lubelskiej", ["Unia Lubelska"], True),
    ("Zmiany w Rzeczypospolitej Obojga Narodów", ["Rzeczpospolita Obojga Narodów"], True),
    ("Nowe eventów pack", ["event"], True),
    ("We must prevent this", ["event"], False),
    ("Jedziemy na rondo", ["RON"], False),
    ("Formowanie RON jest łatwe", ["RON"], True),
    ("Europa Universalis V dev diary", ["Europa Universalis V"], True),
])
def test_q06_match_terms_inflection(text, terms, hit):
    assert bool(match_terms(text, terms)) is hit


# ---- Q-07: steam_find_app ----------------------------------------------------------------------------------------------
def test_q07_find_app_numerals_and_suggestion(app):
    d = call(app, "steam_find_app", name="Europa Universalis 5")["data"]
    assert d["needs_user_choice"] is False and d["base_game"] == f"st:app:{EU5}"
    amb = call(app, "steam_find_app", name="Europa Universalis")["data"]
    assert amb["needs_user_choice"] is True and amb["suggested"] == f"st:app:{EU5}"


# ---- Q-08 / Q-17: steam_appids and the parent of a new value ----------------------------------------------------------
def test_q08_q17_commit_taxonomy_extensions(app, ws):
    r = call(app, "catalog_commit", decisions=[], set_steam_appids=[{"value": "EU5", "appids": [EU5]}],
             approve_new_values=[{"dimension": "specialization", "value": "Crusader Kings", "parent": "Grand strategy"}])
    assert r["ok"], r
    idx = taxonomy.load(ws)
    assert idx.value_attr("specialization", "Europa Universalis", "steam_appids") == [EU5]
    assert idx.parent["specialization"]["Crusader Kings"] == "Grand strategy"
    assert "Crusader Kings" in idx.descendants("specialization", "Strategy")
    bad = call(app, "catalog_commit", decisions=[], approve_new_values=[{"dimension": "specialization", "value": "X", "parent": "Nonexistent"}])
    assert bad["error"]["code"] == "INVALID_ARG"


# ---- Q-09: wave limit per session --------------------------------------------------------------------------------------
def test_q09_wave_limit_per_session(ws, env):
    st = {"active": {f"a{i}": {"agent_type": "yt-media-feed-helper:scout", "session_id": "other", "started_at_ts": time.time()} for i in range(6)}}
    (env["data"] / "subagents.json").write_text(json.dumps(st), encoding="utf-8")
    pay = {"session_id": "mine", "hook_event_name": "PreToolUse", "cwd": str(ws.root), "permission_mode": "default", "tool_name": "Agent",
           "tool_input": {"subagent_type": "yt-media-feed-helper:scout", "prompt": "x"}, "tool_use_id": "t"}
    assert hook_json(run_hook("guard_pretool.py", pay)) is None
    run_hook("subagent_tracker.py", {"session_id": "other", "hook_event_name": "SessionStart"}, args=["session_reset"])
    assert json.loads((env["data"] / "subagents.json").read_text(encoding="utf-8"))["active"] == {}


# ---- Q-10: rateLimitExceeded ≠ exhausted quota ------------------------------------------------------------------------
def test_q10_rate_limit_not_exhaustion(app, monkeypatch):
    def boom(api, endpoint, params):
        raise UpstreamHTTPError(403, "rateLimitExceeded", "slow down")

    monkeypatch.setattr(app.transport, "get", boom)
    r = call(app, "yt_channel_profile", channel="UCnewchannelalpha0000007")
    assert r["error"]["code"] == "UPSTREAM_ERROR" and "temporary" in r["error"]["message"].lower()
    assert not app.ledger.status()["buckets"]["default"].get("warning") and app.ledger.status()["buckets"]["default"]["used"] < 10000


# ---- Q-11: Steam summary semantics -------------------------------------------------------------------------------------
def test_q11_reviews_summary_scope(app):
    s = call(app, "steam_reviews", appid=EU5, language="polish")["data"]["summary"]
    assert s["scope"] == "all_time" and s["window_days"] is None


# ---- Q-14: adding a channel from outside the subscriptions --------------------------------------------------------------
def test_q14_add_channel(app):
    r = call(app, "catalog_add_channel", channel="UCnewchannelalpha0000007")
    assert r["ok"] and r["data"]["added"] is True
    e = call(app, "catalog_get_channel", channel_id="UCnewchannelalpha0000007")["data"]
    assert e["status"] == "uncategorized" and e["added_via"] == "search" and e["categories"]["subscription"][0]["value"] == "None"
    assert call(app, "catalog_add_channel", channel="UCnewchannelalpha0000007")["data"]["added"] is False


# ---- Q-19 / Q-13: status line --------------------------------------------------------------------------------------------
def test_q19_session_start_limit_from_config(ws, env):
    (env["data"] / "quota.json").write_text(json.dumps({"buckets": {"default": {"used": 5}}}), encoding="utf-8")
    ws.config_file.write_text(ws.config_file.read_text(encoding="utf-8").replace("daily_limit: 10000", "daily_limit: 20000"), encoding="utf-8")
    ctx = hook_json(run_hook("session_start.py", {"session_id": "s", "hook_event_name": "SessionStart", "cwd": str(ws.root)}))
    text = ctx["hookSpecificOutput"]["additionalContext"]
    assert "5/20000" in text and str(ws.root) in text


# ---- Q-20 / Q-21 / Q-28 ----------------------------------------------------------------------------------------------------
def test_q20_total_reported_after_bulk_details(app):
    call(app, "yt_recent_uploads", channel_ids=[PDX], since="30d")
    assert call(app, "yt_comments", video_id="pdxEU5dd001", max_threads=5)["data"]["total_reported"] is not None


def test_q21_minutes_unambiguous():
    with pytest.raises(ToolError):
        parse_since("30m")
    assert parse_since("30min") is not None


def test_q28_window_from_content(app):
    r = call(app, "yt_recent_uploads", channel_ids=["UCbrandonsanderson000003"])
    assert r["data"]["window"]["source"] == "content" and r["data"]["window"]["since"].startswith("2026-10-03")
