"""Real HTTP transport (httpx) — GET only, known hosts only (read-only by design)."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import httpx

from ..errors import UpstreamHTTPError

YT_BASE = "https://www.googleapis.com/youtube/v3/"
STEAM_URLS = {
    "storesearch": "https://store.steampowered.com/api/storesearch/",
    "appdetails": "https://store.steampowered.com/api/appdetails",
    "news": "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/",
    "appreviews": "https://store.steampowered.com/appreviews/{appid}",
}
YT_ENDPOINTS = {"subscriptions", "channels", "playlistItems", "videos", "commentThreads", "search"}


class HttpTransport:
    def __init__(self, token_provider: Callable[[], str], steam_min_interval_s: float = 1.0, timeout: float = 20.0):
        self.token_provider = token_provider
        self.steam_min_interval_s = steam_min_interval_s
        self._last_store_call = 0.0
        self._store_lock = threading.Lock()  # Q-22: MCP tools run in threads (anyio.to_thread)
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": "yt-media-feed-helper/0.1 (read-only)"}, follow_redirects=False)

    def get(self, api: str, endpoint: str, params: dict[str, Any]) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        if api == "youtube":
            if endpoint not in YT_ENDPOINTS:
                raise ValueError(f"niedozwolony endpoint YouTube: {endpoint}")
            return self._request(YT_BASE + endpoint, params, {"Authorization": f"Bearer {self.token_provider()}"}, retries=3)
        if api == "steam":
            url = STEAM_URLS[endpoint]
            if "{appid}" in url:
                url = url.format(appid=int(params.pop("appid")))
            if "store.steampowered.com" in url:
                with self._store_lock:
                    wait = self.steam_min_interval_s - (time.monotonic() - self._last_store_call)
                    if wait > 0:
                        time.sleep(wait)
                    self._last_store_call = time.monotonic()
            return self._request(url, params, {}, retries=3)
        raise ValueError(f"nieznane API: {api}")

    def _request(self, url: str, params: dict, headers: dict, retries: int = 1) -> Any:
        delay = 2.0
        for attempt in range(retries):
            r = self.client.get(url, params=params, headers=headers)
            if r.status_code >= 400:
                reason, message = "", r.text[:300]
                try:
                    err = r.json().get("error", {})
                    message = err.get("message", message)
                    reason = (err.get("errors") or [{}])[0].get("reason", "")
                except Exception:  # noqa: BLE001
                    pass
                transient = r.status_code in (429, 500, 502, 503, 504) or reason in ("rateLimitExceeded", "userRateLimitExceeded", "backendError")
                if transient and attempt + 1 < retries:  # Q-10/Q-22: backoff for rate limits and 5xx (GET is idempotent)
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise UpstreamHTTPError(r.status_code, reason, message)
            return r.json()
        raise UpstreamHTTPError(429, "rateLimited", "too many requests")

    def close(self) -> None:
        self.client.close()
