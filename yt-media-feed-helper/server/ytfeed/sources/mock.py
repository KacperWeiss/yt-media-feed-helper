"""MockTransport: (api, endpoint, params) → fixture file. No match → MockFixtureMissing
(an explicit error, never a silent empty result). Emulates searchTerms / order / maxResults filtering."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..catalog.rules import fold
from ..errors import MockFixtureMissing, UpstreamHTTPError

DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s))


class MockTransport:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get("YTFEED_FIXTURES") or DEFAULT_FIXTURES)
        self.calls: list[tuple[str, str, dict]] = []

    def _load(self, api: str, name: str) -> Any:
        p = self.root / api / f"{name}.json"
        if not p.exists():
            raise MockFixtureMissing(f"No fixture: {api}/{name}.json")
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "error" in data and isinstance(data["error"], dict):
            err = data["error"]
            reason = (err.get("errors") or [{}])[0].get("reason", "")
            raise UpstreamHTTPError(int(err.get("code", 400)), reason, err.get("message", ""))
        return data

    def _merge_ids(self, api: str, prefix: str, ids: str) -> dict:
        items = []
        for i in [x for x in ids.split(",") if x]:
            # like the real API: non-existent IDs are omitted from *.list?id=... responses
            if (self.root / api / f"{prefix}_{_safe(i)}.json").exists():
                items.extend(self._load(api, f"{prefix}_{_safe(i)}").get("items", []))
        return {"items": items}

    def get(self, api: str, endpoint: str, params: dict[str, Any]) -> Any:
        self.calls.append((api, endpoint, dict(params)))
        pt = params.get("pageToken")
        page = f"_{_safe(pt)}" if pt else ""
        if api == "youtube":
            if endpoint == "subscriptions":
                return self._load(api, f"subscriptions_mine{page}")
            if endpoint == "channels":
                if params.get("forHandle"):
                    h = fold(str(params["forHandle"]).lstrip("@"))
                    for f in sorted((self.root / api).glob("channels_*.json")):
                        for it in json.loads(f.read_text(encoding="utf-8")).get("items", []):
                            if fold(it.get("snippet", {}).get("customUrl", "")).lstrip("@") == h:
                                return {"items": [it]}
                    return {"items": []}
                return self._merge_ids(api, "channels", params.get("id", ""))
            if endpoint == "playlistItems":
                data = self._load(api, f"playlistItems_{_safe(params['playlistId'])}{page}")
                return _limit(data, params)
            if endpoint == "videos":
                return self._merge_ids(api, "videos", params.get("id", ""))
            if endpoint == "commentThreads":
                data = self._load(api, f"commentThreads_{_safe(params['videoId'])}{page}")
                items = list(data.get("items", []))
                st = params.get("searchTerms")
                if st:
                    terms = [fold(t) for t in str(st).split() if t]
                    items = [i for i in items if all(t in fold(i["snippet"]["topLevelComment"]["snippet"]["textOriginal"]) for t in terms)]
                if params.get("order") == "time":
                    items.sort(key=lambda i: i["snippet"]["topLevelComment"]["snippet"]["publishedAt"], reverse=True)
                return _limit({**data, "items": items}, params)
            if endpoint == "search":
                return _limit(self._load(api, f"search_{slug(params.get('q', ''))}"), params)
        if api == "steam":
            if endpoint == "storesearch":
                return self._load(api, f"storesearch_{slug(params.get('term', ''))}")
            if endpoint == "appdetails":
                return self._load(api, f"appdetails_{_safe(params['appids'])}")
            if endpoint == "news":
                data = self._load(api, f"news_{_safe(params['appid'])}")
                n = int(params.get("count", 20))
                data = json.loads(json.dumps(data))
                feeds = [f for f in str(params.get("feeds", "")).split(",") if f]
                items = [i for i in data["appnews"]["newsitems"] if not feeds or i.get("feedname") in feeds]
                data["appnews"]["newsitems"] = items[:n]
                return data
            if endpoint == "appreviews":
                cur = params.get("cursor", "*")
                suffix = "" if cur in ("*", None, "") else f"_{_safe(cur)}"
                return self._load(api, f"appreviews_{_safe(params['appid'])}_{_safe(params.get('language', 'all'))}{suffix}")
        raise MockFixtureMissing(f"No mock mapping for {api}/{endpoint}")


def _limit(data: dict, params: dict) -> dict:
    n = params.get("maxResults")
    if n is None:
        return data
    return {**data, "items": list(data.get("items", []))[: int(n)]}
