from conftest import call
from ytfeed.sources.steam import bbcode_to_text

EU5 = 3450310


def test_find_app_with_dlc(app):
    r = call(app, "steam_find_app", name="Europa Universalis V")
    d = r["data"]
    assert r["ok"] and not d["needs_user_choice"]
    assert d["base_game"] == f"st:app:{EU5}"
    assert {x["appid"] for x in d["upcoming_dlc"]} == {3999990}
    dlc = next(c for c in d["candidates"] if c["type"] == "dlc")
    assert dlc["parent_appid"] == EU5 and dlc["coming_soon"] is True
    assert app.ledger.status()["buckets"]["default"]["used"] == 0  # Steam does not consume YouTube quota


def test_find_app_ambiguous(app):
    d = call(app, "steam_find_app", name="Europa Universalis")["data"]
    assert d["needs_user_choice"] is True and "dlc" not in d


def test_news_official_and_bbcode(app):
    r = call(app, "steam_news", appid=EU5, since="30d")
    items = r["data"]["items"]
    assert [i["ref"] for i in items] == ["st:n:5100000000000000001", "st:n:5100000000000000002"]  # official only, no old items
    assert all(i["is_official"] for i in items)
    body = items[0]["untrusted"]["body"]
    assert "[list]" not in body and "• Reworked the Union of Lublin event chain" in body
    assert r["data"]["skipped_older_than_window"] == 1
    allf = call(app, "steam_news", appid=EU5, since="30d", feeds=[])["data"]["items"]
    press = next(i for i in allf if i["feed"] == "pcgamer")
    assert press["is_official"] is False and press["external_source_domain"] == "www.pcgamer.example"
    m = call(app, "steam_news", appid=EU5, since="60d", match_terms=["Commonwealth"])["data"]["items"]
    assert [i["ref"] for i in m] == ["st:n:5100000000000000001"]


def test_bbcode():
    assert bbcode_to_text("[h1]T[/h1][b]x[/b] [url=http://a]link[/url][list][*]a[*]b[/list]") == "T\nx link\n• a\n• b"


def test_reviews_summary_cursor_language_terms(app):
    r = call(app, "steam_reviews", appid=EU5, language="polish", max_reviews=3)
    d = r["data"]
    assert d["summary"]["total_reviews"] == 452 and d["summary"]["review_score_desc"] == "Mostly Positive"
    assert len(d["reviews"]) == 3 and r["page"]["has_more"]
    zw = next(x for x in d["reviews"] if x["ref"] == "st:r:170000003")
    assert zw["injection_suspect"] and "​" not in zw["untrusted"]["text"] and zw["edited"] is True
    assert d["reviews"][0]["playtime_at_review_h"] == 212.4
    r2 = call(app, "steam_reviews", appid=EU5, language="polish", max_reviews=3, cursor=r["page"]["next_cursor"])
    assert [x["ref"] for x in r2["data"]["reviews"]] == ["st:r:170000004"] and r2["data"]["summary"] is None
    full = call(app, "steam_reviews", appid=EU5, language="polish", match_terms=["RON"])
    assert {x["ref"] for x in full["data"]["reviews"]} == {"st:r:170000001", "st:r:170000002", "st:r:170000004"}
    en = call(app, "steam_reviews", appid=EU5, language="english")["data"]["reviews"]
    assert next(x for x in en if x["ref"] == "st:r:170000102")["injection_suspect"]


def test_reviews_window(app, monkeypatch):
    d = call(app, "steam_reviews", appid=EU5, language="polish", since_days=4)["data"]
    assert {x["ref"] for x in d["reviews"]} == {"st:r:170000003", "st:r:170000004"}
