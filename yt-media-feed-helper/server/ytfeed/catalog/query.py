"""Catalog filtering: AND across dimensions, OR within a dimension, NOT (`exclude`),
specialization hierarchy (a parent includes its descendants), synonyms and aliases, `min_confidence`."""
from __future__ import annotations

from ..errors import ToolError
from . import store
from .rules import TaxonomyIndex, fold


def _expand(idx: TaxonomyIndex, filters: dict[str, list[str]], label: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for dim, values in (filters or {}).items():
        if isinstance(values, str):
            values = [values]
        if dim not in idx.dims:
            # the dimension may be an alias (e.g. {"strategy": []}) — no guessing: error with a hint
            raise ToolError("INVALID_ARG", f"{label}: unknown dimension '{dim}'", f"Available dimensions: {list(idx.dims)}")
        keys: set[str] = set()
        for v in values:
            key, _ = idx.normalize(dim, v)
            if key is None:
                alias = idx.resolve_alias(v)
                if alias and alias[0] == dim:
                    key = alias[1]
            if key is None:
                raise ToolError("INVALID_ARG", f"{label}: value '{v}' does not exist in dimension '{dim}'",
                                f"Closest values: {idx.suggest(dim, v)}")
            keys |= idx.descendants(dim, key)
        out[dim] = keys
    return out


def _values(entry: dict, dim: str, min_conf: float) -> set[str]:
    out = set()
    for v in (entry.get("categories") or {}).get(dim) or []:
        if not isinstance(v, dict):
            continue
        if v.get("source") == "user" or float(v.get("confidence", 0) or 0) >= min_conf:
            out.add(str(v.get("value")))
    return out


def run(doc: dict, idx: TaxonomyIndex, filters: dict | None = None, exclude: dict | None = None, min_confidence: float = 0.0,
        status: str = "approved", text: str | None = None) -> list[dict]:
    inc = _expand(idx, filters or {}, "filters")
    exc = _expand(idx, exclude or {}, "exclude")
    t = fold(text) if text else None
    out = []
    for cid, e in (doc.get("channels") or {}).items():
        if not isinstance(e, dict):
            continue
        if status != "any" and e.get("status", "uncategorized") != status:
            continue
        if any(not (_values(e, d, min_confidence) & keys) for d, keys in inc.items()):
            continue
        if any(_values(e, d, 0.0) & keys for d, keys in exc.items()):
            continue
        if t and t not in fold(" ".join(str(x or "") for x in (e.get("title"), e.get("handle"), e.get("notes"), cid))):
            continue
        out.append({"channel_id": cid, "title": e.get("title"), "handle": e.get("handle"), "status": e.get("status"),
                    "categories_compact": store.compact(e), "uploads_playlist": ((e.get("sources") or {}).get("youtube") or {}).get("uploads_playlist"),
                    "categories": e.get("categories") or {}, "notes": e.get("notes"), "subscription_lost_at": e.get("subscription_lost_at")})
    out.sort(key=lambda r: fold(r.get("title") or r["channel_id"]))
    return out
