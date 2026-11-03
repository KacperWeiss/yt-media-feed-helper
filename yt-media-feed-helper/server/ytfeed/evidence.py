"""Evidence registry (ADR-03): every external item has a stable `ref` → canonical URL.

Stored: ref, URL, title, date, author, channel, a ≤300-char snippet; purged after 30 days.
Chapter refs `yt:v:<id>@<sec>` exist if the base video exists.
"""
from __future__ import annotations

import re
from typing import Any

from .cache import Store
from .sanitize import clean, truncate

REF_RE = re.compile(r"^(yt:ch:[A-Za-z0-9_-]+|yt:v:[A-Za-z0-9_-]{6,}(?:@\d+)?|yt:c:[A-Za-z0-9_.-]+|st:app:\d+|st:n:\d+|st:r:\d+)$")
SAFE_URL = re.compile(r"^https://(store\.steampowered\.com|steamcommunity\.com|steamstore-a\.akamaihd\.net|www\.youtube\.com)/[^\s<>()\[\]]*$")
REF_IN_TEXT = re.compile(r"\[\[([a-z]{2}:[a-z]+:[A-Za-z0-9_.@-]+)\]\]")


def canonical_url(ref: str, **kw: Any) -> str | None:
    """URL from the ref alone (no external content — links are always canonical, L7)."""
    if ref.startswith("yt:ch:"):
        return f"https://www.youtube.com/channel/{ref[6:]}"
    if ref.startswith("yt:v:"):
        vid, _, sec = ref[5:].partition("@")
        return f"https://www.youtube.com/watch?v={vid}" + (f"&t={sec}s" if sec else "")
    if ref.startswith("yt:c:"):
        vid = kw.get("video_id")
        return f"https://www.youtube.com/watch?v={vid}&lc={ref[5:]}" if vid else None
    if ref.startswith("st:app:"):
        return f"https://store.steampowered.com/app/{ref[7:]}/"
    if ref.startswith("st:r:"):
        sid, app = kw.get("steamid"), kw.get("appid")
        return f"https://steamcommunity.com/profiles/{sid}/recommended/{app}/" if sid and app else None
    if ref.startswith("st:n:"):
        url = kw.get("url")
        if url and re.match(r"^https://(store\.steampowered\.com|steamcommunity\.com|steamstore-a\.akamaihd\.net)/", url):
            return url
        return f"https://store.steampowered.com/news/app/{kw.get('appid')}/view/{ref[5:]}" if kw.get("appid") else None
    return None


class EvidenceRegistry:
    def __init__(self, store: Store):
        self.store = store

    def register(self, ref: str, *, title: str | None = None, published_at: str | None = None, author: str | None = None,
                 channel: str | None = None, snippet: str | None = None, url: str | None = None, **kw: Any) -> str:
        if not REF_RE.match(ref):
            raise ValueError(f"invalid ref: {ref}")
        # S-14: an explicit `url` (e.g. from the Steam API) only from the domain allowlist; otherwise the canonical URL from the ref
        u = url if url and SAFE_URL.match(url) and "/linkfilter/" not in url else None
        u = u or canonical_url(ref, **kw)
        if not u:
            raise ValueError(f"no URL for {ref}")
        kind = ref.split(":")[1]
        with self.store._lock:
            self.store.conn.execute(
                "INSERT OR REPLACE INTO evidence(ref,url,title,published_at,author,channel,snippet,kind,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (ref, u, truncate(clean(title), 200) if title else None, published_at, truncate(clean(author), 80) if author else None,
                 channel, truncate(clean(snippet), 300) if snippet else None, kind, self.store._now()),
            )
            self.store.conn.commit()
        return ref

    def get(self, ref: str) -> dict | None:
        base, _, sec = ref.partition("@") if ref.startswith("yt:v:") else (ref, "", "")
        with self.store._lock:
            row = self.store.conn.execute(
                "SELECT ref,url,title,published_at,author,channel,snippet,kind FROM evidence WHERE ref=?", (base,)
            ).fetchone()
        if not row:
            return None
        d = dict(zip(("ref", "url", "title", "published_at", "author", "channel", "snippet", "kind"), row))
        if sec:
            d["ref"] = ref
            d["url"] = canonical_url(ref)
            d["kind"] = "chapter"
        return d

    def exists(self, ref: str) -> bool:
        return self.get(ref) is not None

    def all_urls(self) -> set[str]:
        with self.store._lock:
            return {r[0] for r in self.store.conn.execute("SELECT url FROM evidence")}

    def resolve(self, refs: list[str]) -> dict:
        resolved: dict[str, dict] = {}
        unknown: list[str] = []
        for r in refs:
            d = self.get(r)
            if d:
                resolved[r] = {k: d[k] for k in ("url", "title", "published_at", "channel", "author") if d.get(k)}
            else:
                unknown.append(r)
        return {"resolved": resolved, "unknown": unknown}
