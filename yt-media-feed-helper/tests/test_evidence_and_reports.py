import pytest

from conftest import call
from ytfeed.evidence import canonical_url

GOOD = """---
title: ignored
---
# EU5 — reception of the RON changes

## TL;DR
<!-- section:tldr -->
- Paradox reworked the Union of Lublin event chain [[yt:v:pdxEU5dd001@125]] [[st:n:5100000000000000001]].
- Polish players are divided [[yt:c:UgxPL001]] [[yt:c:UgxPL002]].

## Official information
<!-- section:official -->
Patch 1.2.1 is scheduled for November 12 (channel owner's comment) [[yt:c:UgxOWN01]].

## Community reception
<!-- section:community_reception -->
### Opinion distribution
| stance | count |
|---|---|
| positive | 1 |

### Main themes
Theme: RON is too easy to form [[yt:c:UgxPL002]].
> „Szkoda, że RON da się teraz utworzyć za łatwo” [[yt:c:UgxPL002]]
> _(transl.)_ “Too easy to form” [[yt:c:UgxPL002]]

## Discrepancies and uncertainties
<!-- section:discrepancies -->
No contradictions between official sources.

## Methodology and limitations
<!-- section:methodology -->
_Comment sample by relevance (non-random)._
Analyzed 9 comments.
"""

# Polish report without section markers — validated via the EN+PL heading keyword fallback.
GOOD_PL_NO_MARKERS = """# EU5 — odbiór zmian RON

## TL;DR
- Paradox przerobił łańcuch eventów unii lubelskiej [[yt:v:pdxEU5dd001@125]].

## Informacje oficjalne
Patch 1.2.1 zaplanowano na 12 listopada [[yt:c:UgxOWN01]].

## Odbiór community
### Rozkład opinii
| stanowisko | liczba |
|---|---|
| positive | 1 |

## Rozbieżności i niepewności
Brak sprzeczności między źródłami oficjalnymi.

## Metodologia i ograniczenia
Przeanalizowano 9 komentarzy.
"""


@pytest.fixture
def seeded(app):
    call(app, "yt_comments", video_id="pdxEU5dd001")
    call(app, "yt_video_details", video_ids=["pdxEU5dd001"])
    call(app, "steam_news", appid=3450310)
    return app


@pytest.mark.parametrize("ref,url", [
    ("yt:ch:UC1", "https://www.youtube.com/channel/UC1"),
    ("yt:v:abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"),
    ("yt:v:abcdefghijk@125", "https://www.youtube.com/watch?v=abcdefghijk&t=125s"),
    ("st:app:3450310", "https://store.steampowered.com/app/3450310/"),
])
def test_canonical_urls(ref, url):
    assert canonical_url(ref) == url


def test_ref_urls_from_registry(seeded):
    r = call(seeded, "evidence_resolve", refs=["yt:c:UgxPL001", "st:r:999", "yt:v:pdxEU5dd001@125", "st:n:5100000000000000001"])
    res = r["data"]["resolved"]
    assert res["yt:c:UgxPL001"]["url"] == "https://www.youtube.com/watch?v=pdxEU5dd001&lc=UgxPL001"
    assert res["yt:v:pdxEU5dd001@125"]["url"].endswith("&t=125s")
    assert res["st:n:5100000000000000001"]["url"].startswith("https://store.steampowered.com/news/app/3450310/")
    assert r["data"]["unknown"] == ["st:r:999"]
    call(seeded, "steam_reviews", appid=3450310, language="polish")
    rv = call(seeded, "evidence_resolve", refs=["st:r:170000001"])["data"]["resolved"]["st:r:170000001"]
    assert rv["url"] == "https://steamcommunity.com/profiles/76561198000000001/recommended/3450310/"


def test_report_save_ok_and_render(seeded, ws):
    r = call(seeded, "report_save", topic_slug="eu5-ron-odbior", title="EU5 RON", markdown=GOOD, query_spec={"mode": "reception", "window": "30d"},
             chat_summary="Short.")
    assert r["ok"], r
    d = r["data"]
    assert d["path"] == "reports/2026-11-02-eu5-ron-odbior.md" and d["unique_sources"] == 5 and d["citations"] == 8
    text = (ws.root / d["path"]).read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: EU5 RON\ndate: '2026-11-02'") or text.startswith("---\ntitle: EU5 RON\ndate: 2026-11-02")
    assert "[[" not in text and "](https://www.youtube.com/watch?v=pdxEU5dd001&lc=UgxPL002)" in text
    assert "## Sources\n<!-- section:sources -->" in text and "`yt:c:UgxOWN01`" in text
    assert "report_language: en" in text
    r2 = call(seeded, "report_save", topic_slug="eu5-ron-odbior", title="EU5 RON", markdown=GOOD)
    assert r2["data"]["path"] == "reports/2026-11-02-eu5-ron-odbior-2.md"  # name collision


@pytest.mark.parametrize("mutate,rule", [
    (lambda s: s.replace("Patch 1.2.1 is scheduled for November 12 (channel owner's comment) [[yt:c:UgxOWN01]].", "Patch soon."), "paragraph_without_ref"),
    (lambda s: s.replace("- Polish players are divided [[yt:c:UgxPL001]] [[yt:c:UgxPL002]].", "- Polish players are divided."), "list_item_without_ref"),
    (lambda s: s.replace("[[yt:c:UgxPL001]]", "[[yt:c:UgxFAKE99]]"), "unknown_ref"),
    (lambda s: s.replace("No contradictions", "See https://evil.example/phish — no contradictions"), "raw_url_not_in_registry"),
    (lambda s: s.replace("> „Szkoda, że RON da się teraz utworzyć za łatwo” [[yt:c:UgxPL002]]\n> _(transl.)_ “Too easy to form” [[yt:c:UgxPL002]]",
                         "> „Szkoda” [[yt:v:pdxEU5dd001]]\n> _(transl.)_ “Pity” [[yt:v:pdxEU5dd001]]"), "quote_without_comment_or_review_ref"),  # Q-25: quote block as a whole
    (lambda s: s.replace("## Methodology and limitations\n<!-- section:methodology -->", "## Other"), "missing_methodology_section"),
    # the marker wins over the heading: a neutral heading marked as a claim section requires refs
    (lambda s: s.replace("## Discrepancies and uncertainties\n<!-- section:discrepancies -->\nNo contradictions between official sources.",
                         "## Notes\n<!-- section:findings -->\nAn unsourced claim."), "paragraph_without_ref"),
    (lambda s: s.replace("<!-- section:official -->", "<!-- section:bogus -->"), "unknown_section_marker"),
])
def test_report_save_rejects(seeded, mutate, rule):
    md = mutate(GOOD)
    assert md != GOOD
    r = call(seeded, "report_save", topic_slug="bad-report", title="x", markdown=md)
    assert not r["ok"] and r["error"]["code"] == "VALIDATION_FAILED"
    assert rule in {v["rule"] for v in r["error"]["violations"]}


def test_report_save_bad_slug(seeded):
    r = call(seeded, "report_save", topic_slug="Bad Slug", title="x", markdown=GOOD)
    assert r["error"]["violations"][0]["rule"] == "bad_slug"


def test_report_quota_units_in_frontmatter(seeded, ws):
    call(seeded, "quota_estimate", plan=[{"tool": "yt_comments", "count": 1}], begin_report=True)
    call(seeded, "yt_comments", video_id="plsRON00001")
    r = call(seeded, "report_save", topic_slug="with-quota", title="Q", markdown=GOOD)
    assert r["data"]["quota_units"] == 1
    assert "quota_units: 1" in (ws.root / r["data"]["path"]).read_text(encoding="utf-8")
    assert not seeded.ledger.status()["report_budget"]["active"]


def test_report_heading_fallback_pl_without_markers(seeded, ws):
    r = call(seeded, "report_save", topic_slug="pl-fallback", title="EU5 RON", markdown=GOOD_PL_NO_MARKERS, language="pl")
    assert r["ok"], r
    text = (ws.root / r["data"]["path"]).read_text(encoding="utf-8")
    assert "## Źródła\n<!-- section:sources -->" in text and "report_language: pl" in text
    bad = GOOD_PL_NO_MARKERS.replace(" [[yt:c:UgxOWN01]]", "")
    r2 = call(seeded, "report_save", topic_slug="pl-fallback-bad", title="x", markdown=bad, language="pl")
    assert not r2["ok"] and "paragraph_without_ref" in {v["rule"] for v in r2["error"]["violations"]}
    r3 = call(seeded, "report_save", topic_slug="pl-fallback-nometh", title="x",
              markdown=GOOD_PL_NO_MARKERS.replace("## Metodologia i ograniczenia", "## Inne"))
    assert "missing_methodology_section" in {v["rule"] for v in r3["error"]["violations"]}


def test_report_heading_fallback_en_without_markers(seeded):
    md = "\n".join(ln for ln in GOOD.splitlines() if not ln.startswith("<!-- section:"))
    assert "<!--" not in md
    assert call(seeded, "report_save", topic_slug="en-fallback", title="x", markdown=md)["ok"]
    bad = md.replace(" [[yt:c:UgxOWN01]]", "")
    r = call(seeded, "report_save", topic_slug="en-fallback-bad", title="x", markdown=bad)
    assert "paragraph_without_ref" in {v["rule"] for v in r["error"]["violations"]}


@pytest.mark.parametrize("language,heading", [("en", "## Sources"), ("pl", "## Źródła"), ("de", "## Sources"), ("PL", "## Źródła")])
def test_report_sources_heading_language(seeded, ws, language, heading):
    r = call(seeded, "report_save", topic_slug=f"lang-{language.lower()}", title="x", markdown=GOOD, language=language)
    assert r["ok"], r
    text = (ws.root / r["data"]["path"]).read_text(encoding="utf-8")
    assert f"{heading}\n<!-- section:sources -->" in text
    assert text.count("<!-- section:sources -->") == 1


def test_report_bad_language(seeded):
    r = call(seeded, "report_save", topic_slug="lang-bad", title="x", markdown=GOOD, language="english")
    assert not r["ok"] and r["error"]["violations"][0]["rule"] == "bad_language"


def test_report_author_sources_section_replaced(seeded, ws):
    md = GOOD + "\n## Sources\n<!-- section:sources -->\n1. something\n"
    r = call(seeded, "report_save", topic_slug="own-sources", title="x", markdown=md)
    assert r["ok"], r
    text = (ws.root / r["data"]["path"]).read_text(encoding="utf-8")
    assert "1. something" not in text and text.count("## Sources") == 1


def test_report_language_from_query_spec(seeded, ws):
    r = call(seeded, "report_save", topic_slug="qs-lang", title="x", markdown=GOOD,
             query_spec={"report_language": "pl", "language": "PL"})
    assert r["ok"], r
    text = (ws.root / r["data"]["path"]).read_text(encoding="utf-8")
    assert "report_language: pl" in text and "language: PL" in text and "## Źródła" in text
