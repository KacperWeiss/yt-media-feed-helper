import json

import pytest
import yaml

from conftest import call

PDX, PLS, BSN, CZY, INJ, PRT = ("UCparadoxinteractive0001", "UCpolskistrateg000000002", "UCbrandonsanderson000003",
                                "UCczytelniarecenzji00004", "UClegitgaming00000000005", "UCstratpartner0000000006")


def used(app):
    return app.ledger.status()["buckets"]["default"]["used"]


def test_channel_profile_language_and_cost(app):
    r = call(app, "yt_channel_profile", channel=PLS)
    assert r["ok"], r
    d = r["data"]
    assert d["ref"] == f"yt:ch:{PLS}" and d["detected_language"]["value"] == "PL"
    assert d["meta"]["topic_categories"] == ["Video_game_culture", "Strategy_video_game"]
    assert r["quota"]["cost"] == 2 and used(app) == 2  # table §3.4: 2 units
    assert all(u["ref"].startswith("yt:v:") for u in d["recent_uploads"])
    r2 = call(app, "yt_channel_profile", channel=PLS)  # cached 7 days
    assert r2["quota"]["cost"] == 0 and used(app) == 2
    h = call(app, "yt_channel_profile", channel="@brandonsanderson")
    assert h["data"]["channel_id"] == BSN and h["data"]["detected_language"]["value"] == "EN"


def test_channel_profile_injection(app):
    d = call(app, "yt_channel_profile", channel=INJ)["data"]
    assert d["injection_suspect"] is True
    assert "ʼʼʼ" in d["untrusted"]["description"] and "```" not in d["untrusted"]["description"]
    assert "ignore_instructions_en" in d["injection_markers"]


def test_recent_uploads_since_shorts_match(app):
    r = call(app, "yt_recent_uploads", channel_ids=[PDX, PLS], since="14d")
    assert r["ok"], r
    refs = [v["ref"] for v in r["data"]["videos"]]
    assert "yt:v:pdxShort001" not in refs  # Shorts skipped by default
    assert "yt:v:pdxOld00001" not in refs  # older than the window
    assert r["data"]["per_channel"][PDX]["older_cutoff_reached"] is True
    assert r["data"]["per_channel"][PDX]["skipped_shorts"] == 1
    assert r["quota"]["cost"] == 3  # 2 × playlistItems + 1 × videos.list
    with_shorts = call(app, "yt_recent_uploads", channel_ids=[PDX], since="14d", include_shorts=True, max_per_channel=10)
    assert "yt:v:pdxShort001" in [v["ref"] for v in with_shorts["data"]["videos"]]
    m = call(app, "yt_recent_uploads", channel_ids=[PDX, PLS], since="14d", match_terms=["RON"])
    got = {v["ref"]: v["match"] for v in m["data"]["videos"]}
    assert set(got) == {"yt:v:pdxEU5dd001", "yt:v:plsRON00001"}  # RON → aliases: Commonwealth, Rzeczpospolita…
    vid = next(v for v in m["data"]["videos"] if v["ref"] == "yt:v:pdxEU5dd001")
    assert [c["t"] for c in vid["chapters"]] == [0, 125, 410, 690] and vid["chapters"][1]["ref"] == "yt:v:pdxEU5dd001@125"


def test_recent_uploads_default_limits_and_fields(app):
    r = call(app, "yt_recent_uploads", channel_ids=[PDX], since="90d", max_per_channel=2, fields=["published_at", "untrusted.title"])
    vids = r["data"]["videos"]
    assert len(vids) == 2 and set(vids[0]) <= {"ref", "published_at", "untrusted", "injection_suspect"}
    bad = call(app, "yt_recent_uploads", channel_ids=[PDX], fields=["secret"])
    assert not bad["ok"] and bad["error"]["code"] == "INVALID_ARG"
    too_many = call(app, "yt_recent_uploads", channel_ids=[PDX] * 26)
    assert too_many["error"]["code"] == "INVALID_ARG"


def test_video_details_links_strip_tracking(app):
    r = call(app, "yt_video_details", video_ids=["pdxEU5dd001", "missing0001"], include=["chapters", "links", "tags", "stats"])
    v = r["data"]["videos"][0]
    assert v["links"] == ["forum.paradoxplaza.com/forum/threads/eu5-patch.123"]
    assert v["views"] == 182000 and "eu5" in v["tags"]
    assert r["quota"]["cost"] == 1


def test_comments_owner_flag_pagination_lang(app):
    r = call(app, "yt_comments", video_id="pdxEU5dd001", max_threads=4)
    assert r["ok"], r
    cs = r["data"]["comments"]
    assert len(cs) == 4 and r["page"]["has_more"]
    r2 = call(app, "yt_comments", video_id="pdxEU5dd001", max_threads=4, cursor=r["page"]["next_cursor"])
    refs2 = [c["ref"] for c in r2["data"]["comments"]]
    assert refs2[:2] == ["yt:c:UgxOWN01", "yt:c:UgxINJ01"]  # resume in the middle of a page
    allc = call(app, "yt_comments", video_id="pdxEU5dd001")["data"]["comments"]
    assert len(allc) == 9  # 2 pages (6 + 3)
    owner = next(c for c in allc if c["ref"] == "yt:c:UgxOWN01")
    assert owner["author_is_channel_owner"] is True
    inj = next(c for c in allc if c["ref"] == "yt:c:UgxINJ01")
    assert inj["injection_suspect"] and "‹/untrusted›" in inj["untrusted"]["text"]
    pl = call(app, "yt_comments", video_id="pdxEU5dd001", language_hint="PL")["data"]
    assert all(c["lang"] == "PL" for c in pl["comments"]) and pl["sample"]["filtered_out_lang"] > 0
    st = call(app, "yt_comments", video_id="plsRON00001", search_terms="unia", include_replies=1)["data"]
    assert {c["ref"] for c in st["comments"]} == {"yt:c:UgyPL104"}  # searchTerms literally (no inflection)
    rep = call(app, "yt_comments", video_id="pdxEU5dd001", include_replies=2, max_threads=1)["data"]["comments"][0]
    assert rep["replies"][0]["author_is_channel_owner"] is True
    liked = call(app, "yt_comments", video_id="pdxEU5dd001", min_likes=500)["data"]["comments"]
    assert all(c["likes"] >= 500 for c in liked)


def test_comments_disabled_and_missing_fixture(app):
    r = call(app, "yt_comments", video_id="prtEU5prev1")
    assert r["ok"] and r["data"]["comments_disabled"] is True
    m = call(app, "yt_comments", video_id="nofixture01")
    assert not m["ok"] and "MOCK" in m["error"]["message"]  # missing fixture → explicit error


def test_sync_subscriptions_dry_run_and_write(app, ws):
    r = call(app, "catalog_sync_subscriptions")
    d = r["data"]
    assert d["dry_run"] and {a["channel_id"] for a in d["added"]} == {"UCnewchannelalpha0000007", "UCnewchannelbeta00000008"}
    assert [u["channel_id"] for u in d["unsubscribed_since_last"]] == [CZY]
    assert r["quota"]["cost"] == 2  # 2 pages of subscriptions.list; dry run without channels.list
    before = ws.channels_file.read_text(encoding="utf-8")
    assert before == ws.channels_file.read_text(encoding="utf-8")
    w = call(app, "catalog_sync_subscriptions", dry_run=False)
    assert w["data"]["written"] and w["quota"]["cost"] == 1  # pages from cache + 1 × channels.list
    chans = yaml.safe_load(ws.channels_file.read_text(encoding="utf-8"))["channels"]
    new = chans["UCnewchannelalpha0000007"]
    assert new["status"] == "uncategorized" and new["handle"] == "@alphahistory"
    assert new["categories"]["subscription"][0]["value"] == "Subscriber"
    assert chans[CZY]["subscription_lost_at"]
    assert chans[PLS]["categories"]["subscription"][0] == {"value": "Supporter", "source": "user", "locked": True, "set_at": "2026-10-02T10:00:00Z"}


def test_search_cost_and_in_catalog(app):
    r = call(app, "yt_search", query="Brandon Sanderson", type="channel")
    assert r["quota"]["cost"] == 100
    res = {x["channel_id"]: x["in_catalog"] for x in r["data"]["results"]}
    assert res == {BSN: True, "UCsandersonfanclub000009": False}
    ex = call(app, "yt_search", query="EU5 RON", exclude_cataloged=True)
    assert [x["channel_id"] for x in ex["data"]["results"]] == ["UCsearchonly000000000010"]


def test_report_budget_blocks_api(app):
    call(app, "quota_estimate", plan=[], begin_report=True, budget_units=150)
    assert call(app, "yt_search", query="Brandon Sanderson", type="channel")["ok"]
    r = call(app, "yt_search", query="EU5 RON")
    assert not r["ok"] and r["error"]["code"] == "REPORT_BUDGET_EXCEEDED"


def test_categorizer_flow_end_to_end(app, ws):
    """Profile → proposal with evidence_refs → table → commit (mock)."""
    prof = call(app, "yt_channel_profile", channel=INJ)["data"]
    ev = [prof["ref"], prof["recent_uploads"][0]["ref"]]
    p = call(app, "catalog_propose", channel_id=INJ, proposal={
        "channel_type": [{"value": "YouTube creator", "confidence": 0.5, "rationale": "Little data", "evidence_refs": ev}],
        "content": [{"value": "Gaming", "confidence": 0.7, "rationale": "topic Video_game_culture", "evidence_refs": ev}],
        "communication": [{"value": "Unofficial", "confidence": 0.6, "rationale": "no evidence of official status", "evidence_refs": ev}],
        "language_region": [{"value": "EN", "confidence": 0.5, "rationale": "EN titles", "evidence_refs": ev}]}, notes="injection_suspect")
    assert p["ok"] and not p["data"]["invalid"], p
    t = call(app, "catalog_list_proposals")["data"]["table"]
    assert "Totally Legit Gaming" in t and "⚠" in t
    c = call(app, "catalog_commit", decisions=[{"proposal_id": p["data"]["proposal_id"], "action": "accept"}])
    assert c["data"]["committed"] == 1
    assert call(app, "catalog_get_channel", channel_id=INJ)["data"]["status"] == "approved"
    assert json.loads(ws.history_file.read_text(encoding="utf-8").splitlines()[-1])["event"] == "accept"


@pytest.mark.parametrize("tool,kw,cost", [
    ("yt_channel_profile", {"channel": PDX}, 2),
    ("yt_video_details", {"video_ids": ["pdxEU5dd001"]}, 1),
    ("yt_comments", {"video_id": "plsRON00001"}, 1),
    ("yt_search", {"query": "Brandon Sanderson"}, 100),
    ("taxonomy_get", {}, 0),
    ("catalog_query", {}, 0),
])
def test_cost_table(app, tool, kw, cost):
    before = used(app)
    r = call(app, tool, **kw)
    assert r["ok"], r
    assert used(app) - before == cost
