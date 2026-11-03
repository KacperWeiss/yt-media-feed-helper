"""SteamSource — public Steam endpoints (GET, no key): storesearch, appdetails, GetNewsForApp, appreviews."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..cache import Store
from ..evidence import EvidenceRegistry
from ..models import Entity, Item, Reaction
from .common import Meter, api_call


def bbcode_to_text(s: str | None) -> str:
    if not s:
        return ""
    t = re.sub(r"\[\*\]", "\n• ", s)
    t = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", t, flags=re.S | re.I)
    t = re.sub(r"\[img\].*?\[/img\]", "", t, flags=re.S | re.I)
    t = re.sub(r"\[/?(?:b|i|u|s|strike|spoiler|noparse)\]", "", t, flags=re.I)  # inline tags
    t = re.sub(r"\[/?(?:h\d|list|olist|quote|code|hr|table|tr|td|th|p|previewyoutube)[^\]]*\]", "\n", t, flags=re.I)  # block tags
    t = re.sub(r"<[^>]+>", " ", t)  # some news items contain HTML
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


class SteamSource:
    name = "steam"
    capabilities = {"entities", "items", "reactions", "news", "reviews"}

    def __init__(self, transport: Any, store: Store, evidence: EvidenceRegistry):
        self.transport = transport
        self.store = store
        self.evidence = evidence

    def call(self, tool: str, endpoint: str, params: dict, ttl_s: int, meter: Meter) -> Any:
        return api_call(transport=self.transport, store=self.store, ledger=None, api="steam", endpoint=endpoint, params=params, tool=tool,
                        ttl_s=ttl_s, meter=meter)

    def search(self, tool: str, term: str, meter: Meter) -> list[dict]:
        return self.call(tool, "storesearch", {"term": term, "l": "english", "cc": "US"}, 7 * 86400, meter).get("items", [])

    def details(self, tool: str, appid: int, meter: Meter) -> dict | None:
        data = self.call(tool, "appdetails", {"appids": str(appid), "l": "english", "filters": "basic,release_date,dlc,genres,categories"}, 7 * 86400, meter)
        entry = (data or {}).get(str(appid)) or {}
        return entry.get("data") if entry.get("success") else None

    def news(self, tool: str, appid: int, count: int, feeds: list[str], meter: Meter) -> list[dict]:
        params: dict[str, Any] = {"appid": appid, "count": count, "maxlength": 0}
        if feeds:
            params["feeds"] = ",".join(feeds)
        return self.call(tool, "news", params, 3600, meter).get("appnews", {}).get("newsitems", [])

    def reviews_page(self, tool: str, appid: int, language: str, filter_: str, day_range: int, review_type: str, cursor: str, meter: Meter) -> dict:
        params = {"appid": appid, "json": 1, "filter": filter_, "language": language, "review_type": review_type, "purchase_type": "all",
                  "num_per_page": 100, "cursor": cursor}
        if filter_ == "all":
            params["day_range"] = day_range
        return self.call(tool, "appreviews", params, 3 * 3600, meter)

    # ---- Source protocol --------------------------------------------------------------------------
    def resolve_entity(self, query: str) -> list[Entity]:
        out = []
        for it in self.search("resolve_entity", query, Meter()):
            ref = self.evidence.register(f"st:app:{it['id']}", title=it.get("name"))
            out.append(Entity(ref=ref, source=self.name, kind="app", title=it.get("name", ""), url=f"https://store.steampowered.com/app/{it['id']}/"))
        return out

    def list_items(self, entity_refs: list[str], since: datetime, until: datetime | None, limit: int, match_terms: list[str]) -> list[Item]:
        out = []
        for ref in entity_refs:
            appid = int(ref.split(":")[-1])
            for n in self.news("list_items", appid, limit, ["steam_community_announcements"], Meter()):
                pub = datetime.fromtimestamp(int(n["date"]), timezone.utc)
                if pub < since or (until and pub > until):
                    continue
                r = self.evidence.register(f"st:n:{n['gid']}", url=None, title=n.get("title"), appid=appid)
                out.append(Item(ref=r, source=self.name, kind="news", entity_ref=ref, url=self.evidence.get(r)["url"], published_at=pub,
                                is_official_hint=n.get("feedname") == "steam_community_announcements", untrusted={"title": n.get("title", "")}))
        return out[:limit]

    def list_reactions(self, target_ref: str, limit: int, order: str, match_terms: list[str], lang: str | None) -> list[Reaction]:
        appid = int(target_ref.split(":")[-1])
        data = self.reviews_page("list_reactions", appid, lang or "all", "recent", 30, "all", "*", Meter())
        out = []
        for r in data.get("reviews", [])[:limit]:
            sid = r.get("author", {}).get("steamid")
            ref = self.evidence.register(f"st:r:{r['recommendationid']}", steamid=sid, appid=appid, snippet=r.get("review"))
            out.append(Reaction(ref=ref, source=self.name, item_ref=None, entity_ref=target_ref, url=self.evidence.get(ref)["url"],
                                published_at=datetime.fromtimestamp(int(r["timestamp_created"]), timezone.utc),
                                stance_hint="positive" if r.get("voted_up") else "negative", weight=float(r.get("votes_up", 0)),
                                lang=r.get("language"), untrusted={"text": r.get("review", "")}))
        return out

    def estimate_cost(self, op: str, **kw: Any) -> tuple[str, int]:
        return "steam", 0
