"""Catalog and taxonomy tools (§3.3). All local (cost 0) except catalog_sync_subscriptions."""
import math
from typing import Any, Literal

from pydantic import StrictBool

from .. import shaping
from ..catalog import proposals, query, store, taxonomy
from ..clock import iso, now_utc
from ..context import get_ctx
from ..errors import ToolError
from ..sources.common import Meter
from .registry import tool

def subscription_value(idx: taxonomy.TaxonomyIndex, preferred: str, position: int) -> str:
    """Subscription value written by the agent, read from the taxonomy: `preferred` if it exists, otherwise the
    agent_settable entry at `position` (0 = subscribed, -1 = not subscribed) — works with customized/legacy taxonomies."""
    if preferred in idx.keys.get("subscription", []):
        return preferred
    options = idx.agent_settable("subscription") or idx.keys.get("subscription") or [preferred]
    return options[position]


QUERY_FIELDS = {"channel_id", "title", "handle", "categories_compact", "status", "uploads_playlist", "categories", "notes", "subscription_lost_at"}


@tool
def taxonomy_get(dimensions: list[str] | None = None, include_descriptions: bool = False) -> str:
    """Category taxonomy: dimensions (cardinality, open), values, hierarchy, query aliases and term_aliases (term synonyms). Cost 0."""
    ctx = get_ctx()
    idx = taxonomy.load(ctx.ws)
    if dimensions:
        unknown = [d for d in dimensions if d not in idx.dims]
        if unknown:
            raise ToolError("INVALID_ARG", f"Unknown dimensions: {unknown}", f"Available: {list(idx.dims)}")
    return shaping.ok(taxonomy.summary(idx, dimensions, include_descriptions))


@tool
def catalog_query(filters: dict[str, list[str]] | None = None, exclude: dict[str, list[str]] | None = None, min_confidence: float = 0.0,
                  status: Literal["approved", "pending", "uncategorized", "any"] = "approved", text: str | None = None,
                  fields: list[str] | None = None, limit: int = 25, cursor: str | None = None) -> str:
    """Filters the local channel catalog: AND across dimensions, OR within a dimension, exclude=NOT; specialization hierarchy, synonyms and aliases. Unknown value → INVALID_ARG with a hint. Cost 0."""
    ctx = get_ctx()
    idx = taxonomy.load(ctx.ws)
    rows = query.run(store.load(ctx.ws), idx, filters or {}, exclude or {}, float(min_confidence), status, text)
    lim = shaping.clamp(limit, 1, 100, 25)
    params = {"f": filters, "e": exclude, "m": min_confidence, "s": status, "t": text}
    chunk, page = shaping.paginate(rows, cursor, params, lim)
    flds = fields or ["channel_id", "title", "handle", "categories_compact"]
    out = [shaping.project(r, flds, QUERY_FIELDS, always=("channel_id",)) for r in chunk]
    warn = store.integrity_warning(ctx.ws)
    return shaping.ok({"channels": out, "matched": len(rows)}, page=page, warnings=[warn] if warn else None)


def _strip_rationale(cats: dict) -> dict:
    return {d: [{k: v for k, v in x.items() if k not in ("rationale", "evidence_refs")} for x in vals] for d, vals in cats.items()}


@tool
def catalog_get_channel(channel_id: str, include_rationale: bool = True, include_history: bool = False) -> str:
    """Full channel entry from channels.yaml (categories with source/confidence/locked), optionally the last 5 history events. Cost 0."""
    ctx = get_ctx()
    entry = store.load(ctx.ws)["channels"].get(channel_id)
    if entry is None:
        raise ToolError("NOT_FOUND", f"Channel {channel_id} is not in the catalog", "Use catalog_query(text=...) to find the channel_id")
    data: dict[str, Any] = {"channel_id": channel_id, **entry}
    if not include_rationale:
        data["categories"] = _strip_rationale(entry.get("categories") or {})
    data["locked_dimensions"] = [d for d, v in (entry.get("categories") or {}).items() if store.is_locked(v)]
    if include_history:
        data["history"] = store.read_history(ctx.ws, channel_id)
    return shaping.ok(data)


@tool
def catalog_sync_subscriptions(dry_run: StrictBool = True, max_pages: int = 20) -> str:
    """Syncs YouTube subscriptions into the catalog: new channels → uncategorized (subscription=Subscriber), lost ones → subscription_lost_at. Never touches manual values (e.g. Supporter). Cost ≈ ceil(N/50)+ceil(new/50) units. dry_run=false requires approval."""
    if not isinstance(dry_run, bool):
        raise ToolError("INVALID_ARG", "dry_run must be a boolean true/false")  # S-05: no coercion of "false" → False
    ctx = get_ctx()
    meter = Meter()
    pages = shaping.clamp(max_pages, 1, 100, 20)
    subs: dict[str, str] = {}
    token = None
    for _ in range(pages):
        params = {"part": "snippet", "mine": "true", "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = ctx.youtube.call("catalog_sync_subscriptions", "subscriptions", params, 3600, meter)
        for it in data.get("items", []):
            rid = it.get("snippet", {}).get("resourceId", {})
            if rid.get("channelId"):
                subs[rid["channelId"]] = it["snippet"].get("title", "")
        token = data.get("nextPageToken")
        if not token:
            break
    doc = store.load(ctx.ws)
    existing = doc["channels"]
    new_ids = [c for c in subs if c not in existing]
    lost = [c for c, e in existing.items() if c not in subs and (e or {}).get("added_via") == "subscriptions_sync" and not (e or {}).get("subscription_lost_at")]
    unchanged = len([c for c in subs if c in existing])
    result: dict[str, Any] = {
        "added": [{"channel_id": c, "title": subs[c]} for c in new_ids[:50]],
        "added_total": len(new_ids),
        "unsubscribed_since_last": [{"channel_id": c, "title": existing[c].get("title")} for c in lost[:50]],
        "unchanged": unchanged,
        "would_write": bool(new_ids or lost),
        "dry_run": dry_run,
    }
    warnings = []
    if token:
        warnings.append(f"Reached max_pages={pages}; some subscriptions were not fetched.")
    if not dry_run and (new_ids or lost):
        details = {c["id"]: c for c in ctx.youtube.channels("catalog_sync_subscriptions", new_ids, meter)} if new_ids else {}
        now = iso(now_utc())
        subscribed = subscription_value(taxonomy.load(ctx.ws), "Subscriber", 0)
        with store.transaction(ctx.ws) as d2:
            for c in new_ids:
                it = details.get(c, {})
                sn = it.get("snippet", {})
                uploads = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                entry = store.new_entry(c, sn.get("title") or subs[c], sn.get("customUrl"), "subscriptions_sync", uploads)
                entry["categories"]["subscription"] = [{"value": subscribed, "source": "agent", "confidence": 1.0,
                                                        "rationale": "subscriptions.list mine=true", "proposed_at": now, "approved_by_user_at": now}]
                d2["channels"][c] = entry
                if it:
                    ctx.youtube.register_channel(it)
            for c in lost:
                d2["channels"][c]["subscription_lost_at"] = now
            # re-subscribed → clear the marker
            for c in subs:
                e = d2["channels"].get(c)
                if e and e.get("subscription_lost_at"):
                    e["subscription_lost_at"] = None
        store.append_history(ctx.ws, {"event": "sync_subscriptions", "added": len(new_ids), "lost": len(lost), "by": "user"})
        result["written"] = True
    result["estimated_write_cost"] = math.ceil(len(new_ids) / 50) if new_ids else 0
    return shaping.ok(result, quota=meter.envelope(ctx.ledger), warnings=warnings)


@tool
def catalog_propose(channel_id: str, proposal: dict[str, list[dict[str, Any]]], new_values: list[dict[str, str]] | None = None,
                    notes: str | None = None) -> str:
    """Saves a category PROPOSAL for one channel (status pending; does not approve). proposal={dimension:[{value,confidence,rationale≤300,evidence_refs}]}; new values only via new_values (open dimensions). Dimensions locked by the user are skipped. Cost 0."""
    ctx = get_ctx()
    idx = taxonomy.load(ctx.ws)
    res = proposals.propose(ctx.ws, idx, ctx.evidence, channel_id, proposal, new_values or [], notes)
    warnings = res.pop("warnings", [])
    return shaping.ok(res, warnings=warnings)


@tool
def catalog_list_proposals(status: Literal["pending", "accepted", "rejected", "any"] = "pending", limit: int = 20, cursor: str | None = None,
                           format: Literal["table", "json"] = "table") -> str:
    """Lists category proposals. format=table: a ready Markdown table to show the user (⚠ low confidence, ★ new values, 🔒 locked). Cost 0."""
    ctx = get_ctx()
    props = proposals.list_all(ctx.ws, status)
    chans = store.load(ctx.ws)["channels"]
    for p in props:
        p["channel_title"] = (chans.get(p["channel_id"]) or {}).get("title")
    chunk, page = shaping.paginate(props, cursor, {"s": status, "f": format}, shaping.clamp(limit, 1, 100, 20))
    idx = taxonomy.load(ctx.ws)
    low = float((idx.doc.get("rules") or {}).get("low_confidence_flag", 0.6))
    if format == "table":
        return shaping.ok({"table": proposals.table(chunk, low) if chunk else "(no proposals)", "total": len(props)}, page=page)
    return shaping.ok({"proposals": chunk, "total": len(props)}, page=page)


@tool
def catalog_commit(decisions: list[dict[str, Any]], approve_new_values: list[dict[str, Any]] | None = None, user_note: str | None = None,
                   set_steam_appids: list[dict[str, Any]] | None = None) -> str:
    """Commits proposals AFTER the user's review: accept / reject / accept_with_changes (overrides → user, locked). New taxonomy values (with parent) via approve_new_values; set_steam_appids=[{value,appids}]. Always requires approval (hook ask). Cost 0."""
    ctx = get_ctx()
    res = proposals.commit(ctx.ws, decisions, approve_new_values or [], user_note, set_steam_appids or [])
    warnings = res.pop("warnings", [])
    return shaping.ok(res, warnings=warnings)


@tool
def catalog_add_channel(channel: str, note: str | None = None) -> str:
    """Adds a non-subscribed channel (e.g. from yt_search) to the LOCAL catalog: status uncategorized, subscription=None. channel = UC… or @handle. Requires approval (hook ask); subscribes to nothing. Cost 1 unit."""
    ctx = get_ctx()
    meter = Meter()
    if channel.startswith("@"):
        item = ctx.youtube.channel_by_handle("catalog_add_channel", channel, meter)
    else:
        items = ctx.youtube.channels("catalog_add_channel", [channel], meter)
        item = items[0] if items else None
    if not item:
        raise ToolError("NOT_FOUND", f"Channel {channel} does not exist", "Check the ID (UC…) or @handle")
    cid = item["id"]
    sn = item.get("snippet", {})
    if cid in store.load(ctx.ws)["channels"]:
        return shaping.ok({"channel_id": cid, "added": False, "reason": "already in the catalog"}, quota=meter.envelope(ctx.ledger))
    now = iso(now_utc())
    uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    not_subscribed = subscription_value(taxonomy.load(ctx.ws), "None", -1)
    with store.transaction(ctx.ws) as doc:
        entry = store.new_entry(cid, sn.get("title") or cid, sn.get("customUrl"), "search", uploads)
        entry["categories"]["subscription"] = [{"value": not_subscribed, "source": "agent", "confidence": 1.0,
                                                "rationale": "added from outside subscriptions (catalog_add_channel)", "proposed_at": now, "approved_by_user_at": now}]
        entry["notes"] = (note or None) and str(note)[:300]
        doc["channels"][cid] = entry
    ctx.youtube.register_channel(item)
    store.append_history(ctx.ws, {"event": "add_channel", "channel_id": cid, "added_via": "search", "by": "user"})
    return shaping.ok({"channel_id": cid, "added": True, "status": "uncategorized", "next": "/yt-media-feed-helper:categorize " + cid},
                      quota=meter.envelope(ctx.ledger))


@tool
def catalog_set_manual(channel_id: str, set: dict[str, list[str]] | None = None, unlock: list[str] | None = None) -> str:
    """Manual channel correction by the user (source=user, locked=true), e.g. subscription=Supporter; unlock removes a dimension lock. Requires approval (hook ask). Cost 0."""
    ctx = get_ctx()
    idx = taxonomy.load(ctx.ws)
    return shaping.ok(store.set_manual(ctx.ws, idx, channel_id, set or {}, unlock or []))
