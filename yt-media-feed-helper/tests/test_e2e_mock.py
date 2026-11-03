"""E2E mock: the 4 reference queries go through the MCP tools in the order of the report-workflow skill.

A deterministic script plays the LLM roles (scout/steam-checker/summarizer): it builds each report ONLY from refs
returned by the tools. The test checks: 4 reports in reports/, report_save without violations, every bullet with a link
(reports-browse lint), separation of official/semi-official/community sections, section markers, report language
(three English reports, one Polish report for the Polish-fans question), quota budget, diag.
"""
import json
import subprocess
import sys
from pathlib import Path

from conftest import call, run_hook

LINT = Path(__file__).resolve().parent.parent / "skills/reports-browse/scripts/reports_index.py"
EU5 = 3450310


def begin(app, plan):
    r = call(app, "quota_estimate", plan=plan, begin_report=True)
    assert r["ok"] and r["data"]["fits_report_budget"]


def official_ids(app, ids):
    out = {}
    for cid in ids:
        cats = call(app, "catalog_get_channel", channel_id=cid)["data"]["categories"]
        out[cid] = cats["communication"][0]["value"]
    return out


def save(app, slug, title, md, spec, language=None):
    r = call(app, "report_save", topic_slug=slug, title=title, markdown=md, query_spec=spec, chat_summary="ok", language=language)
    assert r["ok"], json.dumps(r, ensure_ascii=False)[:2000]
    return r["data"]["path"]


def h2(title, key):
    return ["", f"## {title}", f"<!-- section:{key} -->"]


def q1(app):
    """Latest official announcements from creators in the strategy category."""
    ch = call(app, "catalog_query", filters={"specialization": ["strategy"], "communication": ["Official", "Community Manager"]})["data"]["channels"]
    ids = [c["channel_id"] for c in ch]
    assert ids == ["UCparadoxinteractive0001"]
    begin(app, [{"tool": "yt_recent_uploads", "count": 1, "args_hint": {"channels": len(ids), "videos": 5}}])
    vids = call(app, "yt_recent_uploads", channel_ids=ids, since="14d")["data"]["videos"]
    news = call(app, "steam_news", appid=EU5, since="14d")["data"]["items"]
    lines = ["# Strategy — official announcements (14 days)", *h2("TL;DR", "tldr")]
    lines += [f"- New publisher material from {v['published_at'][:10]} [[{v['ref']}]]." for v in vids[:3]]
    lines += h2("Official information", "official")
    for v in vids:
        chap = v.get("chapters") or []
        extra = f" (chapter {chap[1]['title']} [[{chap[1]['ref']}]])" if len(chap) > 1 else ""
        lines.append(f"- Video published {v['published_at'][:10]} [[{v['ref']}]]{extra}.")
    lines += [f"- Steam announcement from {n['published_at'][:10]} [[{n['ref']}]]." for n in news]
    lines += [*h2("Methodology and limitations", "methodology"), "_News mode; Shorts skipped; no transcripts._"]
    return save(app, "strategy-official", "Strategy — official announcements", "\n".join(lines),
                {"mode": "news", "window": "14d", "report_language": "en"})


def q2(app):
    """Official news about Brandon Sanderson's books (30-day window for books)."""
    ch = call(app, "catalog_query", filters={"specialization": ["Sanderson"], "content": ["Books"], "communication": ["Official", "Community Manager"]})["data"]["channels"]
    ids = [c["channel_id"] for c in ch]
    assert ids == ["UCbrandonsanderson000003"]
    begin(app, [{"tool": "yt_recent_uploads", "count": 1, "args_hint": {"channels": 1}}, {"tool": "yt_comments", "count": 1, "args_hint": {"max_threads": 20}}])
    vids = call(app, "yt_recent_uploads", channel_ids=ids, since="30d")["data"]["videos"]
    assert {v["ref"] for v in vids} == {"yt:v:bsnState001", "yt:v:bsnUpdate01"}
    owner = [c for c in call(app, "yt_comments", video_id="bsnState001", max_threads=20)["data"]["comments"] if c["author_is_channel_owner"]]
    lines = ["# Brandon Sanderson — official news", *h2("TL;DR", "tldr"), f"- Progress update [[{vids[0]['ref']}]].",
             *h2("Official information", "official")]
    lines += [f"- Material from {v['published_at'][:10]} [[{v['ref']}]]." for v in vids]
    lines += [f"- Clarification (channel owner's comment) [[{c['ref']}]]." for c in owner]
    lines += [*h2("Methodology and limitations", "methodology"), "_30-day window (books); unofficial reviewers skipped._"]
    return save(app, "sanderson-official", "Sanderson — official", "\n".join(lines), {"mode": "news", "window": "30d"}, language="en")


def q3(app):
    """Community reception of the upcoming EU5 DLC."""
    ident = call(app, "steam_find_app", name="Europa Universalis V")["data"]
    dlc = ident["upcoming_dlc"][0]
    ch = call(app, "catalog_query", filters={"specialization": ["Europa Universalis"]})["data"]["channels"]
    ids = [c["channel_id"] for c in ch]
    comm = official_ids(app, ids)
    begin(app, [{"tool": "yt_recent_uploads", "count": 1, "args_hint": {"channels": len(ids)}}, {"tool": "yt_comments", "count": 4}])
    vids = call(app, "yt_recent_uploads", channel_ids=ids, since="30d", match_terms=["Example Expansion", "DLC", "dodatek", "expansion"])["data"]["videos"]
    comments = []
    for v in vids:
        r = call(app, "yt_comments", video_id=v["ref"][5:], max_threads=100)["data"]
        comments += r["comments"]
    news = call(app, "steam_news", appid=EU5, since="30d", match_terms=["Example Expansion"])["data"]["items"]
    reviews = call(app, "steam_reviews", appid=EU5, language="all", since_days=30)
    lines = ["# EU5 — reception of the upcoming DLC", *h2("TL;DR", "tldr"), f"- The DLC was announced [[{dlc['ref']}]] [[{news[0]['ref']}]].",
             f"- Comments are divided [[{comments[0]['ref']}]] [[{comments[1]['ref']}]].", *h2("Official information", "official")]
    lines += [f"- Publisher video [[{v['ref']}]]." for v in vids if comm[v["channel_id"]] == "Official"]
    lines += h2("Semi-official information", "semi_official")
    lines += [f"- Media partner preview [[{v['ref']}]]." for v in vids if comm[v["channel_id"]] == "Media partner"]
    lines += h2("Creator voices (unofficial)", "creator_voices")
    lines += [f"- Creator opinion [[{v['ref']}]]." for v in vids if comm[v["channel_id"]] == "Unofficial"]
    lines += [*h2("Community reception", "community_reception"), "### Opinion distribution", "| source | sample |", "|---|---|",
              f"| YouTube | {len(comments)} |", "",
              "### Main themes", f"Price and the state of the base game [[{comments[0]['ref']}]].",
              f"> “{comments[0]['untrusted']['text'][:80]}” [[{comments[0]['ref']}]]",
              *h2("Steam", "steam"), f"Base game reviews: {reviews['data']['summary']['review_score_desc']} [[st:app:{EU5}]].",
              *h2("Methodology and limitations", "methodology"), "_DLC unreleased — no DLC reviews; relevance sample._"]
    return save(app, "eu5-dlc-reception", "EU5 DLC — reception", "\n".join(lines), {"mode": "reception", "games": ["EU5"]})


def q4(app):
    """Polish fans' reception of the EU5 RON event change — asked in Polish, so the report is written in Polish
    (multilingual test data: Polish headings + section markers, report_language=pl → "Źródła")."""
    news = call(app, "steam_news", appid=EU5, since="60d", match_terms=["Commonwealth", "Lublin"])["data"]["items"]
    t0 = news[-1]
    ch = call(app, "catalog_query", filters={"specialization": ["EU5"], "language_region": ["PL", "multi"]})["data"]["channels"]
    pl_ids = [c["channel_id"] for c in ch]
    assert pl_ids == ["UCpolskistrateg000000002"]
    begin(app, [{"tool": "yt_recent_uploads", "count": 1}, {"tool": "yt_comments", "count": 2}])
    vids = call(app, "yt_recent_uploads", channel_ids=pl_ids + ["UCparadoxinteractive0001"], since=t0["published_at"], match_terms=["RON"])["data"]["videos"]
    pl_comments = []
    for v in vids:
        pl_comments += call(app, "yt_comments", video_id=v["ref"][5:], language_hint="PL")["data"]["comments"]
    rev = call(app, "steam_reviews", appid=EU5, language="polish", filter="all", since_days=7, match_terms=["RON"])["data"]
    assert all(c["lang"] == "PL" for c in pl_comments) and len(pl_comments) >= 5
    neg = [c for c in pl_comments if "łatwo" in c["untrusted"]["text"] or "szybko" in c["untrusted"]["text"]]
    lines = ["# EU5 — zmiana eventów RON: odbiór polskich fanów", *h2("TL;DR", "tldr"),
             f"- Zmiana: przebudowa łańcucha unii lubelskiej [[{t0['ref']}]].",
             f"- Część polskich graczy uważa formowanie RON za zbyt łatwe [[{neg[0]['ref']}]].",
             *h2("Informacje oficjalne", "official"), f"Patch notes [[{t0['ref']}]].",
             *h2("Odbiór community", "community_reception"), "### Rozkład opinii", "| źródło | próba |", "|---|---|",
             f"| YouTube PL | {len(pl_comments)} |", f"| Steam polish | {len(rev['reviews'])} |", "", "### Główne wątki"]
    for c in neg[:2]:
        lines += [f"Zbyt łatwe formowanie [[{c['ref']}]].", f"> „{c['untrusted']['text'][:100]}” [[{c['ref']}]]", ""]
    lines += [*h2("Steam", "steam"), f"Recenzje PL: {rev['summary']['review_score_desc']} [[{rev['reviews'][0]['ref']}]].",
              *h2("Metodologia i ograniczenia", "methodology"),
              f"_Mała próba PL ({len(pl_comments)} komentarzy) — wnioski orientacyjne; filtr języka heurystyczny._"]
    return save(app, "eu5-ron-events-reception-pl", "EU5 RON — odbiór PL", "\n".join(lines),
                {"mode": "reception", "language": "PL", "report_language": "pl"})


def test_four_reports_end_to_end(app, ws):
    for i, sid in enumerate(("scout-a", "scout-b")):  # parallel scouts visible in diag
        run_hook("diag_log.py", {"hook_event_name": "SubagentStart", "session_id": "e2e", "cwd": str(ws.root), "agent_id": sid,
                                 "agent_type": "yt-media-feed-helper:scout"})
    paths = [q1(app), q2(app), q3(app), q4(app)]
    for sid in ("scout-a", "scout-b"):
        run_hook("diag_log.py", {"hook_event_name": "SubagentStop", "session_id": "e2e", "cwd": str(ws.root), "agent_id": sid,
                                 "agent_type": "yt-media-feed-helper:scout"})
    assert paths == ["reports/2026-11-02-strategy-official.md", "reports/2026-11-02-sanderson-official.md",
                     "reports/2026-11-02-eu5-dlc-reception.md", "reports/2026-11-02-eu5-ron-events-reception-pl.md"]
    cp = subprocess.run([sys.executable, str(LINT), "--workspace", str(ws.root), "lint"], capture_output=True)
    assert cp.returncode == 0, cp.stdout.decode("utf-8")
    q3text = (ws.root / paths[2]).read_text(encoding="utf-8")
    off = q3text.split("<!-- section:official -->")[1].split("<!-- section:semi_official -->")[0]
    semi = q3text.split("<!-- section:semi_official -->")[1].split("<!-- section:creator_voices -->")[0]
    assert "pdxEU5dlc01" in off and "prtEU5prev1" not in off and "prtEU5prev1" in semi
    for p in paths:
        t = (ws.root / p).read_text(encoding="utf-8")
        assert "<!-- section:sources -->" in t and "<!-- section:methodology -->" in t and "quota_units:" in t and "[[" not in t
    for p in paths[:3]:
        t = (ws.root / p).read_text(encoding="utf-8")
        assert "report_language: en" in t and "## Sources" in t
    pl = (ws.root / paths[3]).read_text(encoding="utf-8")
    assert "report_language: pl" in pl and "language: PL" in pl and "## Źródła" in pl
    status = app.ledger.status()
    assert status["buckets"]["default"]["used"] < 100 and status["report_budget"]["active"] is False
    stats = subprocess.run([sys.executable, str(Path(LINT).parents[2] / "diag-stats/scripts/diag_stats.py"), "--logs", str(ws.logs),
                            "--format", "json"], capture_output=True)
    assert json.loads(stats.stdout)["max_parallel_subagents"] == 2
