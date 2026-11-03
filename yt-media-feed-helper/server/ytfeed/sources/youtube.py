"""YouTubeSource — YouTube Data API v3 (*.list only, youtube.readonly scope). Transport: HTTP or mock."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..cache import Store
from ..clock import parse_iso
from ..config import Config
from ..evidence import EvidenceRegistry
from ..models import Entity, Item, Reaction
from ..quota import QuotaLedger
from .common import Meter, api_call


class YouTubeSource:
    name = "youtube"
    capabilities = {"entities", "items", "reactions", "search"}

    def __init__(self, transport: Any, store: Store, ledger: QuotaLedger, evidence: EvidenceRegistry, cfg: Config, auth: Any):
        self.transport = transport
        self.store = store
        self.ledger = ledger
        self.evidence = evidence
        self.cfg = cfg
        self.auth = auth

    def call(self, tool: str, endpoint: str, params: dict, ttl_s: int, meter: Meter) -> Any:
        return api_call(transport=self.transport, store=self.store, ledger=self.ledger, api="youtube", endpoint=endpoint, params=params,
                        tool=tool, ttl_s=ttl_s, meter=meter, pre_check=self.auth.check)

    # ---- low-level fetches --------------------------------------------------------------------
    def channels(self, tool: str, ids: list[str], meter: Meter, ttl_s: int = 7 * 86400) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            data = self.call(tool, "channels", {"part": "snippet,brandingSettings,topicDetails,statistics,contentDetails", "id": ",".join(chunk),
                                                "maxResults": 50}, ttl_s, meter)
            out.extend(data.get("items", []))
        return out

    def channel_by_handle(self, tool: str, handle: str, meter: Meter) -> dict | None:
        data = self.call(tool, "channels", {"part": "snippet,brandingSettings,topicDetails,statistics,contentDetails", "forHandle": handle}, 7 * 86400, meter)
        items = data.get("items", [])
        return items[0] if items else None

    def playlist_page(self, tool: str, playlist_id: str, max_results: int, page_token: str | None, meter: Meter, ttl_s: int = 3600) -> dict:
        params = {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        return self.call(tool, "playlistItems", params, ttl_s, meter)

    def videos(self, tool: str, ids: list[str], meter: Meter, ttl_s: int = 6 * 3600) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            data = self.call(tool, "videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(ids[i:i + 50]), "maxResults": 50}, ttl_s, meter)
            for it in data.get("items", []):
                if it.get("id") and it.get("statistics"):  # Q-20: lightweight per-video statistics index (total_reported in yt_comments)
                    self.store.set(f"youtube:videostat:{it['id']}", it["statistics"], ttl_s)
            out.extend(data.get("items", []))
        return out

    # ---- evidence registry ----------------------------------------------------------------------
    def register_channel(self, item: dict) -> str:
        s = item.get("snippet", {})
        return self.evidence.register(f"yt:ch:{item['id']}", title=s.get("title"), published_at=s.get("publishedAt"), channel=item["id"],
                                      snippet=s.get("description"))

    def register_video(self, vid: str, title: str | None, published_at: str | None, channel_id: str | None, description: str | None = None) -> str:
        return self.evidence.register(f"yt:v:{vid}", title=title, published_at=published_at, channel=channel_id, snippet=description)

    # ---- Source protocol (ADR-08) ------------------------------------------------------------------
    def resolve_entity(self, query: str) -> list[Entity]:
        m = Meter()
        item = self.channel_by_handle("resolve_entity", query, m) if query.startswith("@") else (self.channels("resolve_entity", [query], m) or [None])[0]
        if not item:
            return []
        ref = self.register_channel(item)
        return [Entity(ref=ref, source=self.name, kind="channel", title=item["snippet"]["title"], url=f"https://www.youtube.com/channel/{item['id']}")]

    def list_items(self, entity_refs: list[str], since: datetime, until: datetime | None, limit: int, match_terms: list[str]) -> list[Item]:
        m = Meter()
        out: list[Item] = []
        for ref in entity_refs:
            cid = ref.split(":")[-1]
            page = self.playlist_page("list_items", "UU" + cid[2:], min(50, limit), None, m)
            vids = [pi["contentDetails"]["videoId"] for pi in page.get("items", [])
                    if parse_iso(pi["contentDetails"].get("videoPublishedAt") or pi["snippet"]["publishedAt"]) >= since]
            for v in self.videos("list_items", vids, m):
                pub = parse_iso(v["snippet"]["publishedAt"])
                if until and pub > until:
                    continue
                out.append(Item(ref=self.register_video(v["id"], v["snippet"]["title"], v["snippet"]["publishedAt"], cid), source=self.name, kind="video",
                                entity_ref=ref, url=f"https://www.youtube.com/watch?v={v['id']}", published_at=pub,
                                metrics={"views": int(v.get("statistics", {}).get("viewCount", 0))}, untrusted={"title": v["snippet"]["title"]}))
        return out[:limit]

    def list_reactions(self, target_ref: str, limit: int, order: str, match_terms: list[str], lang: str | None) -> list[Reaction]:
        m = Meter()
        vid = target_ref.split(":")[-1].split("@")[0]
        data = self.call("list_reactions", "commentThreads", {"part": "snippet", "videoId": vid, "order": order, "maxResults": min(100, limit),
                                                               "textFormat": "plainText"}, 6 * 3600, m)
        out = []
        for t in data.get("items", []):
            s = t["snippet"]["topLevelComment"]["snippet"]
            ref = self.evidence.register(f"yt:c:{t['id']}", video_id=vid, author=s.get("authorDisplayName"), published_at=s.get("publishedAt"),
                                         snippet=s.get("textOriginal"))
            out.append(Reaction(ref=ref, source=self.name, item_ref=f"yt:v:{vid}", entity_ref=f"yt:ch:{t['snippet'].get('channelId')}",
                                url=f"https://www.youtube.com/watch?v={vid}&lc={t['id']}", published_at=parse_iso(s["publishedAt"]),
                                weight=float(s.get("likeCount", 0)), untrusted={"text": s.get("textOriginal", "")}))
        return out

    def estimate_cost(self, op: str, **kw: Any) -> tuple[str, int]:
        bucket, units, _ = self.ledger.resolve_bucket(f"{op}.list")
        return bucket, units * int(kw.get("count", 1))


def published(pi: dict) -> str:
    """Video publish date from a playlist item (videoPublishedAt > publishedAt of adding to the playlist)."""
    return pi.get("contentDetails", {}).get("videoPublishedAt") or pi.get("snippet", {}).get("publishedAt") or ""
