"""YouTube tools (§3.4). Texts always go in `untrusted`, every item has a `ref` (evidence registry)."""
import json
import math
import re
from collections import Counter
from typing import Any, Literal

from .. import shaping
from ..catalog import store, taxonomy
from ..catalog.rules import fold
from ..clock import iso, parse_duration_iso8601, parse_iso, parse_since
from ..context import get_ctx
from ..errors import ToolError, UpstreamHTTPError
from ..sanitize import untrusted_block
from ..sources.common import LANG_FROM_CODE, Meter, detect_lang, extract_links, parse_chapters
from ..sources.common import match_terms as find_terms
from ..sources.youtube import published
from .registry import tool

VIDEO_FIELDS = {"ref", "channel_id", "published_at", "duration_s", "views", "likes", "comments", "chapters", "tags", "links", "is_short", "match",
                "untrusted", "untrusted.title", "untrusted.description"}
COMMENT_FIELDS = {"ref", "published_at", "likes", "weight", "duplicates", "sampled_by", "reply_count", "author_is_channel_owner", "lang",
                  "untrusted", "untrusted.text", "untrusted.author", "replies"}


def _int(x: Any) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _is_short(v: dict, max_s: int) -> bool:
    d = parse_duration_iso8601(v.get("contentDetails", {}).get("duration"))
    sn = v.get("snippet", {})
    text = fold(sn.get("title", "") + " " + sn.get("description", "") + " " + " ".join("#" + t for t in sn.get("tags") or []))
    return (d is not None and d <= max_s) or bool(re.search(r"#shorts?\b", text))  # Q-12: #short/#shorts in tags too


def _video_obj(ctx, v: dict, desc_max: int, include: set[str], terms: list[str], shorts_max: int) -> dict:
    s = v.get("snippet", {})
    st = v.get("statistics", {})
    ref = ctx.youtube.register_video(v["id"], s.get("title"), s.get("publishedAt"), s.get("channelId"), s.get("description"))
    u, suspect, markers = untrusted_block({"title": s.get("title"), "description": s.get("description")}, {"title": 300, "description": desc_max})
    out: dict[str, Any] = {"ref": ref, "channel_id": s.get("channelId"), "published_at": s.get("publishedAt"),
                           "duration_s": parse_duration_iso8601(v.get("contentDetails", {}).get("duration")), "is_short": _is_short(v, shorts_max)}
    if "stats" in include:
        out.update(views=_int(st.get("viewCount")), likes=_int(st.get("likeCount")), comments=_int(st.get("commentCount")))
    if "chapters" in include:
        ch = parse_chapters(s.get("description"))
        out["chapters"] = [{"t": c["t"], "ref": f"{ref}@{c['t']}", "title": untrusted_block({"t": c["title"]}, 100)[0]["t"]} for c in ch]
    if "tags" in include and s.get("tags"):
        out["tags"] = untrusted_block({"t": ", ".join(s["tags"][:20])}, 300)[0]["t"]
    if "links" in include:
        out["links"] = extract_links(s.get("description"))
    out["untrusted"] = u
    if terms:
        out["match"] = find_terms(f"{s.get('title', '')}\n{s.get('description', '')}", terms)
    out["injection_suspect"] = suspect
    if suspect:
        out["injection_markers"] = markers
    return out


@tool
def yt_channel_profile(channel: str, recent_uploads: int = 12, desc_max_chars: int = 800) -> str:
    """Compact channel profile for categorization: stats, country/language, topicCategories, description (untrusted), recent uploads, heuristic language detection. channel = UC… or @handle. Cost 2 units, cached 7 days."""
    ctx = get_ctx()
    meter = Meter()
    n = shaping.clamp(recent_uploads, 1, 25, 12)
    if channel.startswith("@"):
        item = ctx.youtube.channel_by_handle("yt_channel_profile", channel, meter)
    else:
        items = ctx.youtube.channels("yt_channel_profile", [channel], meter)
        item = items[0] if items else None
    if not item:
        raise ToolError("NOT_FOUND", f"Channel {channel} does not exist", "Check the ID (UC…) or @handle")
    cid = item["id"]
    ref = ctx.youtube.register_channel(item)
    sn, stats = item.get("snippet", {}), item.get("statistics", {})
    uploads_pl = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads") or "UU" + cid[2:]
    page = ctx.youtube.playlist_page("yt_channel_profile", uploads_pl, n, None, meter, ttl_s=7 * 86400)
    uploads = []
    titles = []
    for pi in page.get("items", [])[:n]:
        vid = pi.get("contentDetails", {}).get("videoId") or pi["snippet"]["resourceId"]["videoId"]
        t = pi["snippet"].get("title", "")
        titles.append(t)
        vref = ctx.youtube.register_video(vid, t, published(pi), cid)
        uploads.append({"ref": vref, "published_at": published(pi), "untrusted": untrusted_block({"title": t}, 200)[0]})
    u, suspect, markers = untrusted_block({"title": sn.get("title"), "description": sn.get("description"),
                                           "keywords": item.get("brandingSettings", {}).get("channel", {}).get("keywords")},
                                          {"title": 200, "description": shaping.clamp(desc_max_chars, 50, 5000, 800), "keywords": 300})
    for t in titles:
        _, s2, m2 = untrusted_block({"t": t})
        if s2:
            suspect = True
            markers = sorted(set(markers) | set(m2))
    lang_votes = Counter(detect_lang(t) for t in titles if detect_lang(t))
    basis = []
    dl = (sn.get("defaultLanguage") or "").split("-")[0].lower()
    if dl:
        basis.append(f"defaultLanguage={dl}")
    if lang_votes:
        top, cnt = lang_votes.most_common(1)[0]
        basis.append(f"titles: {cnt}/{len(titles)} {top.lower()}")
    else:
        top, cnt = None, 0
    value = LANG_FROM_CODE.get(dl) or top
    second = [k for k, c in lang_votes.items() if k != value and titles and c / len(titles) >= 0.3]
    conf = 0.5
    if value and titles:
        conf = round(min(0.95, 0.5 + 0.45 * (lang_votes.get(value, 0) / len(titles)) + (0.1 if dl else 0)), 2)
    data = {
        "ref": ref, "channel_id": cid, "handle": sn.get("customUrl"),
        "stats": {"subscribers": _int(stats.get("subscriberCount")), "videos": _int(stats.get("videoCount")),
                  "hidden_subscribers": bool(stats.get("hiddenSubscriberCount"))},
        "meta": {"country": sn.get("country"), "default_language": sn.get("defaultLanguage"), "published_at": sn.get("publishedAt"),
                 "topic_categories": [t.rsplit("/", 1)[-1] for t in item.get("topicDetails", {}).get("topicCategories", [])]},
        "untrusted": u,
        "recent_uploads": uploads,
        "detected_language": {"value": "multi" if second else value, "basis": basis, "confidence": conf, "secondary": second},
        "in_catalog": cid in store.load(ctx.ws)["channels"],
        "injection_suspect": suspect,
    }
    if suspect:
        data["injection_markers"] = markers
    return shaping.ok(data, quota=meter.envelope(ctx.ledger), untrusted=True)


@tool
def yt_recent_uploads(channel_ids: list[str], since: str | None = None, until: str | None = None, max_per_channel: int | None = None,
                      match_terms: list[str] | None = None, expand_aliases: bool = True, min_duration_s: int | None = None,
                      include_shorts: bool | None = None, desc_max_chars: int = 400, fields: list[str] | None = None,
                      limit: int = 50, cursor: str | None = None) -> str:
    """New videos from channels (≤25): since/until window ('7d', ISO; default from config), max_per_channel (default 5), local match_terms filter (OR, with aliases), Shorts skipped by default. Cost ≈ channels×pages + ceil(videos/50) units."""
    ctx = get_ctx()
    meter = Meter()
    if not channel_ids:
        raise ToolError("INVALID_ARG", "channel_ids is empty")
    if len(channel_ids) > 25:
        raise ToolError("INVALID_ARG", "At most 25 channels per call", "Split into groups")
    cfg = ctx.cfg
    chans = store.load(ctx.ws)["channels"]
    window_source = "since"
    if not since:  # Q-28: window by channel content (max of window_days_by_content), otherwise default_window_days
        by_content = cfg.get("reports.window_days_by_content", {}) or {}
        days = [int(by_content[v.get("value")]) for cid in channel_ids for v in ((chans.get(cid) or {}).get("categories") or {}).get("content") or []
                if isinstance(v, dict) and v.get("value") in by_content]
        window_days = max(days) if days else int(cfg.get("reports.default_window_days", 14))
        window_source = "content" if days else "default"
    else:
        window_days = int(cfg.get("reports.default_window_days", 14))
    since_dt = parse_since(since, window_days)
    until_dt = parse_since(until) if until else None
    per = shaping.clamp(max_per_channel, 1, 20, int(cfg.get("reports.videos_per_channel", 5)))
    shorts = bool(cfg.get("reports.include_shorts", False)) if include_shorts is None else include_shorts
    shorts_max = int(cfg.get("reports.shorts_max_duration_s", 60))
    terms = list(match_terms or [])
    if terms and expand_aliases:
        terms = taxonomy.load(ctx.ws).expand_terms(terms)
    candidates: dict[str, list[str]] = {}
    per_channel: dict[str, dict] = {}
    for cid in channel_ids:
        pl = ((chans.get(cid) or {}).get("sources", {}).get("youtube", {}) or {}).get("uploads_playlist") or ("UU" + cid[2:] if cid.startswith("UC") else None)
        if not pl:
            raise ToolError("INVALID_ARG", f"Invalid channel_id: {cid}")
        token, ids, cutoff = None, [], False
        for _ in range(3):  # max 3 pages of 50 per channel
            page = ctx.youtube.playlist_page("yt_recent_uploads", pl, 50, token, meter)
            for pi in page.get("items", []):
                pub = parse_iso(published(pi))
                if pub < since_dt:
                    cutoff = True
                    continue
                if until_dt and pub > until_dt:
                    continue
                ids.append(pi.get("contentDetails", {}).get("videoId") or pi["snippet"]["resourceId"]["videoId"])
            token = page.get("nextPageToken")
            if cutoff or not token:
                break
        candidates[cid] = ids
        per_channel[cid] = {"returned": 0, "older_cutoff_reached": cutoff, "skipped_shorts": 0, "skipped_no_match": 0}
    all_ids = [v for ids in candidates.values() for v in ids]
    details = {v["id"]: v for v in ctx.youtube.videos("yt_recent_uploads", all_ids, meter)} if all_ids else {}
    include = {"chapters", "stats"}
    videos = []
    for cid, ids in candidates.items():
        rows = []
        for vid in ids:
            v = details.get(vid)
            if not v:
                continue
            dur = parse_duration_iso8601(v.get("contentDetails", {}).get("duration"))
            if not shorts and _is_short(v, shorts_max):
                per_channel[cid]["skipped_shorts"] += 1
                continue
            if min_duration_s and dur is not None and dur < min_duration_s:
                continue
            obj = _video_obj(ctx, v, shaping.clamp(desc_max_chars, 0, 5000, 400), include, terms, shorts_max)
            if terms and not obj.get("match"):
                per_channel[cid]["skipped_no_match"] += 1
                continue
            rows.append(obj)
        rows.sort(key=lambda r: r["published_at"] or "", reverse=True)
        rows = rows[:per]
        per_channel[cid]["returned"] = len(rows)
        videos.extend(rows)
    videos.sort(key=lambda r: r["published_at"] or "", reverse=True)
    params = {"c": channel_ids, "s": iso(since_dt), "u": until, "p": per, "m": terms, "sh": shorts, "md": min_duration_s, "f": fields}
    chunk, page = shaping.paginate(videos, cursor, params, shaping.clamp(limit, 1, 200, 50))
    chunk = [shaping.project(v, fields, VIDEO_FIELDS) for v in chunk]
    warnings = []
    if not shorts:
        warnings.append("Shorts skipped (default; set include_shorts=true to include them).")
    return shaping.ok({"videos": chunk, "per_channel": per_channel, "window": {"since": iso(since_dt), "until": iso(until_dt), "source": window_source},
                       "match_terms_used": terms}, page=page, quota=meter.envelope(ctx.ledger), warnings=warnings, untrusted=True)


@tool
def yt_video_details(video_ids: list[str], desc_max_chars: int = 2000,
                     include: list[Literal["chapters", "tags", "stats", "links"]] | None = None) -> str:
    """Video details (≤50): chapters with refs yt:v:ID@sec, stats, tags, description links (domain+path only). Texts in untrusted. Cost 1 unit/50, cached 6 h."""
    ctx = get_ctx()
    meter = Meter()
    if not video_ids or len(video_ids) > 50:
        raise ToolError("INVALID_ARG", "video_ids: 1–50 items")
    inc = set(include or ["chapters", "stats"])
    items = ctx.youtube.videos("yt_video_details", list(dict.fromkeys(video_ids)), meter)
    shorts_max = int(ctx.cfg.get("reports.shorts_max_duration_s", 60))
    out = [_video_obj(ctx, v, shaping.clamp(desc_max_chars, 0, 10000, 2000), inc, [], shorts_max) for v in items]
    found = {v["id"] for v in items}
    missing = [v for v in video_ids if v not in found]
    return shaping.ok({"videos": out, "not_found": missing}, quota=meter.envelope(ctx.ledger), untrusted=True)


@tool
def yt_comments(video_id: str, order: Literal["relevance", "time", "mixed"] = "relevance", search_terms: str | None = None, max_threads: int | None = None,
                include_replies: int = 0, min_likes: int = 0, text_max_chars: int = 400, language_hint: Literal["PL", "EN", "any"] = "any",
                max_pages: int = 3, fields: list[str] | None = None, cursor: str | None = None) -> str:
    """Video comments (pages of 100; max_pages default 3/max 10): order relevance|time|mixed (70% relevance + 30% time), searchTerms, min_likes, language filter (lang=null kept), fields, weight=log(1+likes), duplicates merged. Cost 1 unit/page."""
    if order == "mixed":
        return _comments_mixed(video_id, search_terms, max_threads, include_replies, min_likes, text_max_chars, language_hint, max_pages, fields, cursor)
    ctx = get_ctx()
    meter = Meter()
    n = shaping.clamp(max_threads, 1, 200, int(ctx.cfg.get("reports.comments_per_video", 100)))
    page_limit = shaping.clamp(max_pages, 1, 10, 3)  # Q-04: page limit with filters (each page = 1 unit)
    if fields:
        shaping.project({}, fields, COMMENT_FIELDS)  # validate field names before calling the API
    replies_n = shaping.clamp(include_replies, 0, 5, 0)
    params_q = {"v": video_id, "o": order, "s": search_terms, "n": n, "r": replies_n, "ml": min_likes, "l": language_hint}
    c = shaping.decode_cursor(cursor, params_q)
    token, skip = c.get("pt"), int(c.get("o") or 0)
    collected: list[dict] = []
    seen_text: dict[str, dict] = {}
    duplicates = 0
    filtered_lang = 0
    lang_unknown = 0
    scanned = 0
    pages = 0
    stopped_reason = "exhausted"
    video_ref = f"yt:v:{video_id}"
    next_cursor = None
    owner_channel = None
    try:
        while len(collected) < n:
            params = {"part": "snippet,replies" if replies_n else "snippet", "videoId": video_id, "order": order, "maxResults": 100, "textFormat": "plainText"}
            if search_terms:
                params["searchTerms"] = search_terms
            if token:
                params["pageToken"] = token
            data = ctx.youtube.call("yt_comments", "commentThreads", params, 6 * 3600, meter)
            pages += 1
            items = data.get("items", [])
            stopped_at = None
            for i, t in enumerate(items):
                if i < skip:
                    continue
                if len(collected) >= n:
                    stopped_at = i
                    break
                scanned += 1
                sn = t.get("snippet", {})
                owner_channel = sn.get("channelId") or owner_channel
                top = sn.get("topLevelComment", {}).get("snippet", {})
                text = top.get("textOriginal") or top.get("textDisplay") or ""
                likes = int(top.get("likeCount", 0))
                if likes < min_likes:
                    continue
                lang = detect_lang(text)
                if language_hint != "any" and lang is not None and lang != language_hint:
                    filtered_lang += 1  # Q-05: drop only comments recognized as ANOTHER language
                    continue
                key = re.sub(r"\W+", " ", fold(text)).strip()
                if len(key) >= 15 and key in seen_text:  # §5b-6: copypasta/spam counted once (duplicates on the first one)
                    seen_text[key]["duplicates"] = seen_text[key].get("duplicates", 0) + 1
                    duplicates += 1
                    continue
                if lang is None:
                    lang_unknown += 1
                ref = ctx.evidence.register(f"yt:c:{t['id']}", video_id=video_id, author=top.get("authorDisplayName"), published_at=top.get("publishedAt"),
                                            snippet=text, channel=sn.get("channelId"))
                u, suspect, markers = untrusted_block({"author": top.get("authorDisplayName"), "text": text}, {"author": 80, "text": shaping.clamp(text_max_chars, 20, 5000, 400)})
                is_owner = bool(sn.get("channelId")) and (top.get("authorChannelId") or {}).get("value") == sn.get("channelId")
                obj: dict[str, Any] = {"ref": ref, "published_at": top.get("publishedAt"), "likes": likes, "weight": round(math.log1p(max(likes, 0)), 3),
                                       "reply_count": sn.get("totalReplyCount", 0),
                                       "author_is_channel_owner": is_owner, "lang": lang, "untrusted": u, "injection_suspect": suspect}
                if suspect:
                    obj["injection_markers"] = markers
                if replies_n:
                    reps = []
                    for r in (t.get("replies") or {}).get("comments", [])[:replies_n]:
                        rs = r.get("snippet", {})
                        rref = ctx.evidence.register(f"yt:c:{r['id']}", video_id=video_id, author=rs.get("authorDisplayName"), published_at=rs.get("publishedAt"),
                                                     snippet=rs.get("textOriginal"))
                        ru, rsus, _ = untrusted_block({"author": rs.get("authorDisplayName"), "text": rs.get("textOriginal")}, {"author": 80, "text": 300})
                        reps.append({"ref": rref, "likes": int(rs.get("likeCount", 0)), "published_at": rs.get("publishedAt"),
                                     "author_is_channel_owner": (rs.get("authorChannelId") or {}).get("value") == sn.get("channelId"),
                                     "untrusted": ru, "injection_suspect": rsus})
                    obj["replies"] = reps
                if len(key) >= 15:
                    seen_text[key] = obj
                collected.append(obj)
            skip = 0
            if stopped_at is not None:
                next_cursor = shaping.encode_cursor(params_q, stopped_at, token)
                stopped_reason = "limit_reached"
                break
            token = data.get("nextPageToken")
            if not token:
                break
            if len(collected) >= n:
                next_cursor = shaping.encode_cursor(params_q, 0, token)
                stopped_reason = "limit_reached"
                break
            if pages >= page_limit:
                next_cursor = shaping.encode_cursor(params_q, 0, token)
                stopped_reason = "max_pages"
                break
    except UpstreamHTTPError as e:
        if e.reason == "commentsDisabled":
            ctx.evidence.register(video_ref)
            return shaping.ok({"video_ref": video_ref, "comments_disabled": True, "comments": [], "sample": {"returned": 0, "order": order}},
                              quota=meter.envelope(ctx.ledger), warnings=["Comments are disabled on this video."])
        if e.status == 404:
            raise ToolError("NOT_FOUND", f"Video {video_id} does not exist") from e
        raise
    total = _int((ctx.store.get(f"youtube:videostat:{video_id}") or {}).get("commentCount"))  # Q-20
    page = {"returned": len(collected), "has_more": bool(next_cursor), "next_cursor": next_cursor}
    warnings = []
    if order == "relevance":
        warnings.append("Comments sorted by relevance — a non-random sample; report the sample size.")
    if language_hint != "any":
        warnings.append(f"The language filter is heuristic: scanned {scanned} comments, {len(collected) - lang_unknown} recognized as {language_hint}, "
                        f"{lang_unknown} of undetermined language (lang=null, kept), {filtered_lang} dropped as another language.")
    if stopped_reason == "max_pages":
        warnings.append(f"Stopped after max_pages={page_limit} pages — incomplete sample (continue with next_cursor if the budget allows).")
    out_comments = [shaping.project(cm, fields, COMMENT_FIELDS) for cm in collected] if fields else collected
    return shaping.ok({"video_ref": video_ref, "comments_disabled": False, "total_reported": total, "owner_channel_id": owner_channel,
                       "sample": {"returned": len(collected), "order": order, "filtered_out_lang": filtered_lang, "lang_unknown": lang_unknown,
                                  "duplicates_merged": duplicates,
                                  "scanned": scanned, "pages_scanned": pages, "stopped_reason": stopped_reason, "search_terms": search_terms},
                       "comments": out_comments}, page=page, quota=meter.envelope(ctx.ledger), warnings=warnings, untrusted=True)


def _comments_mixed(video_id, search_terms, max_threads, include_replies, min_likes, text_max_chars, language_hint, max_pages, fields, cursor) -> str:
    """§5b-2: mixed sample — ~70% most relevant (relevance) + ~30% newest (time), without duplicate refs."""
    if cursor:
        raise ToolError("INVALID_ARG", "order=mixed does not support a cursor", "For further pages use order=relevance or time")
    if fields:
        shaping.project({}, fields, COMMENT_FIELDS)
    ctx = get_ctx()
    n = shaping.clamp(max_threads, 2, 200, int(ctx.cfg.get("reports.comments_per_video", 100)))
    n_rel = max(1, round(n * 0.7))
    parts = []
    for order_, k in (("relevance", n_rel), ("time", n - n_rel)):
        if k <= 0:
            continue
        env = json.loads(yt_comments(video_id, order_, search_terms, k, include_replies, min_likes, text_max_chars, language_hint, max_pages, None, None))
        if not env.get("ok"):
            return json.dumps(env, ensure_ascii=False, separators=(",", ":"))
        parts.append((order_, env))
    comments: list[dict] = []
    refs: set[str] = set()
    for order_, env in parts:
        for cm in env["data"].get("comments", []):
            if cm["ref"] in refs:
                continue
            refs.add(cm["ref"])
            comments.append({**cm, "sampled_by": order_})
    first = parts[0][1]
    sample = {"returned": len(comments), "order": "mixed", "mix": {o: sum(1 for c in comments if c["sampled_by"] == o) for o, _ in parts}}
    for key in ("filtered_out_lang", "lang_unknown", "duplicates_merged", "scanned", "pages_scanned"):
        sample[key] = sum(int(e["data"]["sample"].get(key) or 0) for _, e in parts)
    sample["search_terms"] = search_terms
    quota = {**(parts[-1][1].get("quota") or {}), "cost": sum(int((e.get("quota") or {}).get("cost", 0)) for _, e in parts)}
    warnings = ["Mixed sample: ~70% relevance + ~30% newest (order=time); sampled_by field on every comment."]
    warnings += [w for _, e in parts for w in e.get("warnings", []) if "relevance" not in w]
    out = [shaping.project(c, fields, COMMENT_FIELDS) for c in comments] if fields else comments
    data = {k: first["data"][k] for k in ("video_ref", "comments_disabled", "total_reported", "owner_channel_id") if k in first["data"]}
    return shaping.ok({**data, "sample": sample, "comments": out}, page={"returned": len(out), "has_more": False, "next_cursor": None},
                      quota=quota, warnings=warnings, untrusted=True)


def _videos_key(video_id: str) -> str:
    import json

    return json.dumps({"id": video_id, "maxResults": 50, "part": "snippet,contentDetails,statistics"}, sort_keys=True, ensure_ascii=False)


@tool
def yt_search(query: str, type: Literal["video", "channel"] = "video", published_after: str | None = "30d", relevance_language: str | None = None,
              region_code: str | None = None, order: Literal["relevance", "date", "viewCount"] = "relevance", max_results: int = 10,
              exclude_cataloged: bool = False) -> str:
    """Search OUTSIDE the catalog (search.list, 100 units). Only on the user's explicit request or with the --search flag; counts toward the report budget. Compact result with in_catalog."""
    ctx = get_ctx()
    meter = Meter()
    n = shaping.clamp(max_results, 1, 25, 10)
    params: dict[str, Any] = {"part": "snippet", "q": query, "type": type, "order": order, "maxResults": n}
    if published_after and type == "video":
        params["publishedAfter"] = iso(parse_since(published_after))
    if relevance_language:
        params["relevanceLanguage"] = relevance_language
    if region_code:
        params["regionCode"] = region_code
    data = ctx.youtube.call("yt_search", "search", params, 3600, meter)
    chans = store.load(ctx.ws)["channels"]
    out = []
    for it in data.get("items", []):
        sn = it.get("snippet", {})
        cid = sn.get("channelId")
        in_cat = cid in chans
        if exclude_cataloged and in_cat:
            continue
        if it["id"].get("videoId"):
            ref = ctx.youtube.register_video(it["id"]["videoId"], sn.get("title"), sn.get("publishedAt"), cid)
        else:
            ref = ctx.evidence.register(f"yt:ch:{it['id'].get('channelId')}", title=sn.get("title"), channel=cid)
        u, suspect, _ = untrusted_block({"title": sn.get("title"), "channel_title": sn.get("channelTitle")}, 200)
        out.append({"ref": ref, "channel_id": cid, "published_at": sn.get("publishedAt"), "in_catalog": in_cat, "untrusted": u, "injection_suspect": suspect})
    return shaping.ok({"results": out, "query": query, "type": type}, quota=meter.envelope(ctx.ledger), untrusted=True,
                      warnings=["Results from outside the catalog — adding a channel to the catalog requires the user's decision."])
