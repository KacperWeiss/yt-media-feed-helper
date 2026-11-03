"""Configuration: built-in defaults ← `catalog/config.yaml` ← environment variables."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "quota": {
        "timezone": "America/Los_Angeles",
        "buckets": {
            "default": {"unit": "units", "daily_limit": 10000, "reserve": 500, "warn_at": 0.8},
            "search": {"unit": "calls", "daily_limit": 100, "enabled": False},
        },
        "costs": {
            "channels.list": 1,
            "playlistItems.list": 1,
            "videos.list": 1,
            "commentThreads.list": 1,
            "subscriptions.list": 1,
            "search.list": 100,
        },
    },
    "reports": {
        "language": "auto",  # auto = the language of the user's question
        "translate_quotes_to": "auto",
        "budget_units": 2000,
        "budget_ttl_minutes": 120,
        "default_window_days": 14,
        "window_days_by_content": {"Gaming": 14, "Books": 30, "Movies": 30, "TV series": 30, "Anime": 30, "Politics": 30,
                                    "History": 30, "Science": 30},
        "max_channels_per_report": 40,
        "videos_per_channel": 5,
        "comments_per_video": 100,
        "include_shorts": False,
        "shorts_max_duration_s": 60,
    },
    "parallelism": {"categorizer_wave_size": 5, "scout_max_parallel": 5, "max_active_subagents": 6, "enforce_with_hook": True},
    "cache": {"ttl_days_max": 30},
    "search": {"require_confirmation": False},
    "guard": {"scope": "workspace_only", "strict_modes": True},
    "session_start": {"enabled": True},
    "steam": {"min_interval_s": 1.0},
    "catalog": {"stale_taxonomy_versions": 3},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict | None = None):
        self.data = deep_merge(DEFAULTS, data or {})

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        if path and path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return cls(raw)
        return cls()

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur
