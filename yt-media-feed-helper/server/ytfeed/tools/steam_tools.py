"""Steam tools (§3.5): public endpoints, YouTube quota cost 0."""
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from .. import shaping
from ..catalog import taxonomy
from ..catalog.rules import fold
from ..clock import iso, now_utc, parse_since
from ..context import get_ctx
from ..errors import ToolError
from ..sanitize import untrusted_block
from ..sources.common import Meter
from ..sources.common import match_terms as find_terms
from ..sources.steam import bbcode_to_text
from .registry import tool


def _app_obj(ctx, appid: int, d: dict) -> dict:
    rd = d.get("release_date") or {}
    ref = ctx.evidence.register(f"st:app:{appid}", title=d.get("name"), published_at=rd.get("date"))
    parent = (d.get("fullgame") or {}).get("appid")
    return {"ref": ref, "appid": appid, "type": d.get("type"), "name": untrusted_block({"n": d.get("name")}, 150)[0].get("n"),
            "release_date": rd.get("date"), "coming_soon": bool(rd.get("coming_soon")), "parent_appid": int(parent) if parent else None,
            "dlc_count": len(d.get("dlc") or [])}


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def _norm_name(s: str) -> str:
    """Q-07: game name comparison — fold, punctuation, Roman ↔ Arabic numerals (V ↔ 5)."""
    words = re.findall(r"\w+", fold(s or ""))
    return " ".join(_ROMAN.get(w, w) for w in words)


def _release_key(c: dict) -> tuple:
    if c.get("coming_soon"):
        return (1, "")
    try:
        return (0, datetime.strptime(str(c.get("release_date") or ""), "%d %b, %Y").strftime("%Y%m%d"))
    except ValueError:
        return (0, "")


@tool
def steam_find_app(name: str, include_dlc: bool = True, max_results: int = 5) -> str:
    """Name → Steam appid: candidates (game/DLC, release date, coming_soon) + DLC list of the base game (≤15). When ambiguous, needs_user_choice=true — do not guess. Cached 7 days."""
    ctx = get_ctx()
    meter = Meter()
    n = shaping.clamp(max_results, 1, 10, 5)
    items = ctx.steam.search("steam_find_app", name, meter)
    cands = []
    for it in items[:n]:
        d = ctx.steam.details("steam_find_app", int(it["id"]), meter)
        if d:
            cands.append(_app_obj(ctx, int(it["id"]), d))
    games = [c for c in cands if c["type"] == "game"]
    names = {_norm_name(name)}
    try:  # Q-07: term_aliases groups from the taxonomy (e.g. "EU5" → "Europa Universalis V"); no broad specialization synonyms
        for canon, al in (taxonomy.load(ctx.ws).term_aliases or {}).items():
            group = {_norm_name(x) for x in [canon, *(al or [])]}
            if _norm_name(name) in group:
                names |= group
    except ToolError:
        pass
    exact = [g for g in games if _norm_name(g["name"] or "") in names]
    needs_choice = len(exact) != 1 and len(games) > 1
    out: dict[str, Any] = {"candidates": cands, "needs_user_choice": needs_choice}
    if needs_choice and games:
        out["suggested"] = sorted(games, key=_release_key)[-1]["ref"]  # newest/coming_soon — only a suggestion, the user decides
    base = exact[0] if len(exact) == 1 else (games[0] if len(games) == 1 else None)
    if include_dlc and base:
        d = ctx.steam.details("steam_find_app", base["appid"], meter) or {}
        dlcs = []
        for dlc_id in (d.get("dlc") or [])[:15]:
            dd = ctx.steam.details("steam_find_app", int(dlc_id), meter)
            if dd:
                o = _app_obj(ctx, int(dlc_id), dd)
                dlcs.append({k: o[k] for k in ("ref", "appid", "name", "release_date", "coming_soon")})
        out["base_game"] = base["ref"]
        out["dlc"] = dlcs
        out["upcoming_dlc"] = [x for x in dlcs if x["coming_soon"]]
    warnings = ["Several matching games — ask the user to choose."] if needs_choice else []
    return shaping.ok(out, warnings=warnings, untrusted=True)


@tool
def steam_news(appid: int, since: str = "30d", count: int = 10, feeds: list[str] | None = None, body_max_chars: int = 1200,
               match_terms: list[str] | None = None, expand_aliases: bool = True) -> str:
    """Game news/patch notes (GetNewsForApp): by default only official steam_community_announcements (feeds=[] = all, including press → is_official=false). BBCode→text, match_terms filter. Cached 1 h."""
    ctx = get_ctx()
    meter = Meter()
    n = shaping.clamp(count, 1, 30, 10)
    feeds_ = ["steam_community_announcements"] if feeds is None else feeds
    since_dt = parse_since(since, 30)
    terms = list(match_terms or [])
    if terms and expand_aliases:
        terms = taxonomy.load(ctx.ws).expand_terms(terms)
    raw = ctx.steam.news("steam_news", int(appid), max(n * 2, 20), feeds_, meter)
    items = []
    skipped_old = 0
    for it in raw:
        if feeds_ and it.get("feedname") not in feeds_:
            continue  # defense in depth: filter feeds locally too
        pub = datetime.fromtimestamp(int(it.get("date", 0)), timezone.utc)
        if since_dt and pub < since_dt:
            skipped_old += 1
            continue
        body = bbcode_to_text(it.get("contents"))
        m = find_terms(f"{it.get('title', '')}\n{body}", terms) if terms else []
        if terms and not m:
            continue
        official = it.get("feedname") == "steam_community_announcements"
        url = it.get("url") if not it.get("is_external_url") else None
        ref = ctx.evidence.register(f"st:n:{it['gid']}", url=url, title=it.get("title"), published_at=iso(pub), author=it.get("author"),
                                    snippet=body, appid=int(appid)) if url else ctx.evidence.register(
            f"st:n:{it['gid']}", title=it.get("title"), published_at=iso(pub), snippet=body, appid=int(appid))
        u, suspect, markers = untrusted_block({"title": it.get("title"), "body": body}, {"title": 200, "body": shaping.clamp(body_max_chars, 50, 10000, 1200)})
        obj = {"ref": ref, "published_at": iso(pub), "feed": it.get("feedname"), "feed_label": it.get("feedlabel"), "is_official": official,
               "url": ctx.evidence.get(ref)["url"], "untrusted": u, "injection_suspect": suspect}
        if not official and it.get("is_external_url"):
            obj["external_source_domain"] = (it.get("url") or "").split("/")[2] if "//" in (it.get("url") or "") else None
        if terms:
            obj["match"] = m
        if suspect:
            obj["injection_markers"] = markers
        items.append(obj)
        if len(items) >= n:
            break
    return shaping.ok({"appid": int(appid), "items": items, "skipped_older_than_window": skipped_old, "match_terms_used": terms},
                      untrusted=True, warnings=[] if feeds_ else ["Press news included (is_official=false)."])


@tool
def steam_reviews(appid: int, language: str = "all", since_days: int = 30, filter: Literal["recent", "updated", "all"] = "recent",
                  review_type: Literal["all", "positive", "negative"] = "all", max_reviews: int = 40, match_terms: list[str] | None = None,
                  text_max_chars: int = 500, cursor: str | None = None) -> str:
    """Steam reviews: summary (all-time/window) + sample (language e.g. 'polish', window in days, type filter, match_terms). Reviews exist only for released products. Cached 3 h."""
    ctx = get_ctx()
    meter = Meter()
    days = shaping.clamp(since_days, 1, 365, 30)
    n = shaping.clamp(max_reviews, 1, 200, 40)
    terms = taxonomy.load(ctx.ws).expand_terms(list(match_terms or [])) if match_terms else []
    params_q = {"a": appid, "l": language, "d": days, "f": filter, "t": review_type, "n": n, "m": terms}
    c = shaping.decode_cursor(cursor, params_q)
    steam_cursor = c.get("pt") or "*"
    cutoff = now_utc() - timedelta(days=days)
    summary = None
    reviews: list[dict] = []
    matched = 0
    pages = 0
    next_cursor = None
    while len(reviews) < n and pages < 5:
        data = ctx.steam.reviews_page("steam_reviews", int(appid), language, filter, days, review_type, steam_cursor, meter)
        pages += 1
        if not data.get("success"):
            raise ToolError("UPSTREAM_ERROR", "Steam appreviews: success=0")
        if summary is None and steam_cursor == "*":
            qs = data.get("query_summary") or {}
            # Q-11: with filter=recent/updated Steam gets no day_range — query_summary covers ALL time (for the language)
            scope = "day_range" if filter == "all" else "all_time"
            summary = {"total_reviews": qs.get("total_reviews"), "total_positive": qs.get("total_positive"), "total_negative": qs.get("total_negative"),
                       "review_score_desc": qs.get("review_score_desc"), "language": language, "scope": scope,
                       "window_days": days if scope == "day_range" else None, "filter": filter}
        batch = data.get("reviews") or []
        for r in batch:
            created = datetime.fromtimestamp(int(r.get("timestamp_created", 0)), timezone.utc)
            if filter != "all" and created < cutoff:
                continue
            text = r.get("review") or ""
            m = find_terms(text, terms) if terms else []
            if terms and not m:
                continue
            matched += 1 if m else 0
            sid = (r.get("author") or {}).get("steamid")
            ref = ctx.evidence.register(f"st:r:{r['recommendationid']}", steamid=sid, appid=int(appid), published_at=iso(created), snippet=text,
                                        title=f"Steam review ({'👍' if r.get('voted_up') else '👎'})")
            u, suspect, markers = untrusted_block({"text": text}, shaping.clamp(text_max_chars, 50, 8000, 500))
            obj = {"ref": ref, "voted_up": bool(r.get("voted_up")), "created_at": iso(created),
                   "edited": int(r.get("timestamp_updated", 0)) > int(r.get("timestamp_created", 0)),
                   "playtime_at_review_h": round(int((r.get("author") or {}).get("playtime_at_review", 0)) / 60, 1),
                   "votes_up": int(r.get("votes_up", 0)), "lang": r.get("language"), "untrusted": u, "injection_suspect": suspect}
            if terms:
                obj["match"] = m
            if suspect:
                obj["injection_markers"] = markers
            reviews.append(obj)
            if len(reviews) >= n:
                break
        new_cursor = data.get("cursor")
        newest_on_page = max((int(r.get("timestamp_created", 0)) for r in batch), default=0)
        if filter == "recent" and batch and datetime.fromtimestamp(newest_on_page, timezone.utc) < cutoff:
            next_cursor = None  # Q-11: the whole page is older than the window — later pages (newest first) are too
            break
        if not batch or not new_cursor or new_cursor == steam_cursor:
            next_cursor = None  # Steam has no more pages
            break
        steam_cursor = new_cursor
        next_cursor = shaping.encode_cursor(params_q, 0, steam_cursor)
    page = {"returned": len(reviews), "has_more": bool(next_cursor), "next_cursor": next_cursor}
    warnings = ["Steam: the full query_summary is only on the first page."] if steam_cursor != "*" and summary is None else []
    return shaping.ok({"appid": int(appid), "summary": summary, "sample": {"returned": len(reviews), "matched_terms": matched}, "reviews": reviews},
                      page=page, untrusted=True, warnings=warnings)
