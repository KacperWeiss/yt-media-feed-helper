"""Category proposals (ADR-05): `catalog/proposals/<channel_id>.json`, atomic writes, validation,
approval (`commit`) that protects user-set values."""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ..clock import iso, now_utc
from ..errors import ToolError
from ..evidence import EvidenceRegistry
from ..models import Decision, DimValue, NewValue, ProposedValue, SteamAppids
from ..paths import Workspace, atomic_write_text
from . import store, taxonomy
from .rules import TaxonomyIndex

MAX_NEW_VALUES = 3


def path_for(ws: Workspace, channel_id: str):
    safe = "".join(c for c in channel_id if c.isalnum() or c in "-_")
    if not safe:
        raise ToolError("INVALID_ARG", "Invalid channel_id")
    return ws.proposals / f"{safe}.json"


def read(ws: Workspace, channel_id: str) -> dict | None:
    p = path_for(ws, channel_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write(ws: Workspace, prop: dict) -> None:
    atomic_write_text(path_for(ws, prop["channel_id"]), json.dumps(prop, ensure_ascii=False, indent=2), ws)


def list_all(ws: Workspace, status: str = "pending") -> list[dict]:
    out = []
    for p in sorted(ws.proposals.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if status == "any" or d.get("status") == status:
            out.append(d)
    out.sort(key=lambda d: d.get("created_at", ""))
    return out


def propose(ws: Workspace, idx: TaxonomyIndex, ev: EvidenceRegistry, channel_id: str, proposal: dict, new_values: list[dict],
            notes: str | None, agent: str = "channel-categorizer") -> dict:
    doc = store.load(ws)
    entry = doc["channels"].get(channel_id)
    if entry is None:
        raise ToolError("NOT_FOUND", f"Channel {channel_id} is not in the catalog", "Run catalog_sync_subscriptions or add the channel first")
    if notes and len(notes) > 500:
        notes = notes[:500]
    accepted: dict[str, list[dict]] = {}
    invalid: list[dict] = []
    skipped_locked: list[str] = []
    warnings: list[str] = []

    try:
        nvs = [NewValue(**n) for n in new_values or []]
    except ValidationError as e:
        raise ToolError("INVALID_ARG", f"new_values: {e.errors()[0]['msg']}") from e
    if len(nvs) > MAX_NEW_VALUES:
        raise ToolError("INVALID_ARG", f"At most {MAX_NEW_VALUES} new values per channel")
    new_ok: set[tuple[str, str]] = set()
    nv_out = []
    for n in nvs:
        if n.dimension not in idx.dims:
            invalid.append({"dimension": n.dimension, "value": n.value, "reason": "unknown dimension"})
        elif not idx.is_open(n.dimension):
            invalid.append({"dimension": n.dimension, "value": n.value, "reason": "closed dimension (open: false) — new values only by the user"})
        elif idx.normalize(n.dimension, n.value)[0]:
            warnings.append(f"'{n.value}' already exists in '{n.dimension}' — use a regular proposal")
        elif n.parent and idx.normalize(n.dimension, n.parent)[0] is None:
            invalid.append({"dimension": n.dimension, "value": n.value, "reason": f"parent '{n.parent}' does not exist", "suggest": idx.suggest(n.dimension, n.parent)})
        else:
            new_ok.add((n.dimension, n.value))
            nv_out.append(n.model_dump(exclude_none=True))

    cats = entry.get("categories") or {}
    for dim, vals in (proposal or {}).items():
        if dim not in idx.dims:
            invalid.append({"dimension": dim, "reason": "unknown dimension", "hint": list(idx.dims)})
            continue
        if store.is_locked(cats.get(dim)):
            skipped_locked.append(dim)
            continue
        if not isinstance(vals, list):
            vals = [vals]
        good: list[dict] = []
        for raw in vals:
            try:
                pv = ProposedValue(**raw) if isinstance(raw, dict) else ProposedValue(value=str(raw), confidence=0.0, rationale="")
            except ValidationError as e:
                invalid.append({"dimension": dim, "value": raw.get("value") if isinstance(raw, dict) else raw, "reason": e.errors()[0]["msg"]})
                continue
            if not pv.rationale:
                invalid.append({"dimension": dim, "value": pv.value, "reason": "missing rationale"})
                continue
            key, via_syn = idx.normalize(dim, pv.value)
            if key is None and (dim, pv.value) in new_ok:
                key = pv.value
            if key is None:
                invalid.append({"dimension": dim, "value": pv.value, "reason": "value not in the taxonomy", "suggest": idx.suggest(dim, pv.value)})
                continue
            settable = idx.agent_settable(dim)
            if settable is not None and key not in settable:
                invalid.append({"dimension": dim, "value": key, "reason": "value can only be set manually by the user"})
                continue
            unknown_refs = [r for r in pv.evidence_refs if not ev.exists(r)]
            if unknown_refs:
                invalid.append({"dimension": dim, "value": key, "reason": f"unknown evidence_refs: {unknown_refs[:5]}"})
                continue
            if via_syn:
                warnings.append(f"{dim}: '{pv.value}' normalized to '{key}'")
            good.append({"value": key, "confidence": round(pv.confidence, 3), "rationale": pv.rationale, "evidence_refs": pv.evidence_refs})
        if good:
            if idx.cardinality(dim) == "single" and len(good) > 1:
                invalid.append({"dimension": dim, "reason": f"single dimension — {len(good)} values given"})
                continue
            accepted[dim] = good

    if not accepted and not nv_out:
        # Q-16: nothing valid — do not save an empty proposal and do not change the channel status
        raise ToolError("INVALID_ARG", "The proposal contains no valid value — nothing was saved.",
                        "Fix the values listed in 'invalid' and call catalog_propose again.", {"invalid": invalid[:20], "skipped_locked": skipped_locked})
    now = now_utc()
    prop = {
        "proposal_id": f"{channel_id}@{now.strftime('%Y-%m-%dT%H:%MZ')}",
        "channel_id": channel_id,
        "status": "pending",
        "agent": agent,
        "taxonomy_version": idx.version,
        "created_at": iso(now),
        "proposal": accepted,
        "new_values": nv_out,
        "skipped_locked": skipped_locked,
        "invalid": invalid,
        "notes": notes,
    }
    write(ws, prop)
    with store.transaction(ws) as d2:
        e2 = d2["channels"].get(channel_id)
        if e2 is not None and e2.get("status") == "uncategorized":
            e2["status"] = "pending"
        if e2 is not None:
            e2["last_categorized_at"] = iso(now)
    return {"proposal_id": prop["proposal_id"], "accepted_dims": list(accepted), "skipped_locked": skipped_locked, "invalid": invalid,
            "warnings": warnings}


def table(props: list[dict], low_conf: float = 0.6) -> str:
    cols = ["channel_type", "content", "communication", "specialization", "language_region", "subscription"]
    lines = ["| # | channel | channel_type | content | communication | specialization | lang | subscription | min_conf | new tags | proposal_id |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(props, 1):
        cells = []
        confs = []
        for c in cols:
            vals = p.get("proposal", {}).get(c) or []
            confs += [v.get("confidence", 0) for v in vals]
            txt = ", ".join(f"{v['value']}{' ⚠' if v.get('confidence', 1) < low_conf else ''}" for v in vals)
            if c in p.get("skipped_locked", []):
                txt = "🔒"
            cells.append(txt or "—")
        mc = min(confs) if confs else None
        nv = ", ".join(f"★{n['dimension']}: {n['value']}" + (f" ← {n['parent']}" if n.get("parent") else "") for n in p.get("new_values") or []) or "—"
        title = p.get("channel_title") or p["channel_id"]
        if p.get("needs_input"):  # Q-03: partially accepted — required dimensions are missing
            title = f"{title} ⚠ missing: {', '.join(p['needs_input'])}"
        lines.append(f"| {i} | {_esc(title)} | " + " | ".join(_esc(c) for c in cells) + f" | {mc if mc is not None else '—'}{' ⚠' if mc is not None and mc < low_conf else ''} | {_esc(nv)} | `{p['proposal_id']}` |")
    return "\n".join(lines)


def _esc(s: str) -> str:
    """S-18: channel titles (untrusted data) without active Markdown syntax (links, images, HTML) in the review table."""
    out = str(s).replace("\n", " ").replace("\r", " ")
    for ch in "\\`*_[]()<>!|#":
        out = out.replace(ch, "\\" + ch)
    return out


def commit(ws: Workspace, decisions: list[dict], approve_new_values: list[dict], user_note: str | None,
           set_steam_appids: list[dict] | None = None) -> dict[str, Any]:
    try:
        decs = [Decision(**d) for d in decisions or []]
        anv = [DimValue(**d) for d in approve_new_values or []]
        sap = [SteamAppids(**d) for d in set_steam_appids or []]
    except ValidationError as e:
        raise ToolError("INVALID_ARG", f"decisions/approve_new_values/set_steam_appids: {e.errors()[0]['msg']}") from e
    if not decs and not anv and not sap:
        raise ToolError("INVALID_ARG", "No decisions")

    # S-07: manual-only values (outside agent_settable) cannot enter via commit overrides — only via catalog_set_manual
    idx0 = taxonomy.load(ws)
    for d in decs:
        for dim, vals in (d.overrides or {}).items():
            settable = idx0.agent_settable(dim) if dim in idx0.dims else None
            for v in vals:
                key = idx0.normalize(dim, v)[0] if dim in idx0.dims else None
                if settable is not None and key is not None and key not in settable:
                    raise ToolError("INVALID_ARG", f"overrides: '{key}' ({dim}) can only be set via catalog_set_manual",
                                    f"Use catalog_set_manual(channel_id, set={{'{dim}': ['{key}']}}) — the approval prompt shows this change separately.")

    # 1) new taxonomy values (approved by the user) and Steam mappings
    taxonomy_added: list[str] = []
    steam_set: list[str] = []
    if anv or sap:
        with taxonomy.edit(ws) as tdoc:
            for nv in anv:
                if taxonomy.add_value(tdoc, nv.dimension, nv.value, nv.parent):
                    taxonomy_added.append(f"{nv.dimension}: {nv.value}" + (f" ← {nv.parent}" if nv.parent else ""))
            for sa in sap:
                if taxonomy.set_steam_appids(tdoc, sa.value, sa.appids):
                    steam_set.append(f"{sa.value}: {sa.appids}")
    idx = taxonomy.load(ws)
    approved_new = {(nv.dimension, nv.value) for nv in anv}

    committed = rejected = 0
    warnings: list[str] = []
    diff: list[str] = []
    now = iso(now_utc())
    with store.transaction(ws) as doc:
        for d in decs:
            cid = d.proposal_id.split("@", 1)[0]
            prop = read(ws, cid)
            if not prop or prop.get("proposal_id") != d.proposal_id:
                warnings.append(f"{d.proposal_id}: proposal does not exist or is outdated — skipped")
                continue
            if prop.get("status") not in ("pending",):
                warnings.append(f"{d.proposal_id}: status {prop.get('status')} — skipped")
                continue
            entry = doc["channels"].get(cid)
            if entry is None:
                warnings.append(f"{cid}: not in the catalog — skipped")
                continue
            if d.action == "reject":
                prop["status"] = "rejected"
                prop["decided_at"] = now
                write(ws, prop)
                if entry.get("status") == "pending":
                    entry["status"] = "approved" if entry.get("approved_at") else "uncategorized"
                store.append_history(ws, {"event": "reject", "channel_id": cid, "proposal_id": d.proposal_id, "by": "user", "note": user_note})
                rejected += 1
                diff.append(f"{entry.get('title', cid)}: rejected")
                continue
            cats = entry.setdefault("categories", {})
            changes: dict[str, Any] = {}
            # agent values (never overwrite locked user values)
            for dim, vals in (prop.get("proposal") or {}).items():
                if store.is_locked(cats.get(dim)):
                    warnings.append(f"{cid}.{dim}: locked by the user — agent value skipped")
                    continue
                kept = []
                for v in vals:
                    key, _ = idx.normalize(dim, v["value"])
                    if key is None and (dim, v["value"]) in approved_new:
                        key = v["value"]
                    if key is None:
                        warnings.append(f"{cid}.{dim}: '{v['value']}' was not approved as a new value — skipped")
                        continue
                    kept.append({"value": key, "source": "agent", "confidence": v.get("confidence", 0.0), "rationale": v.get("rationale", ""),
                                 "evidence_refs": v.get("evidence_refs", []), "proposed_at": prop.get("created_at"), "approved_by_user_at": now})
                if kept:
                    user_kept = [x for x in cats.get(dim) or [] if x.get("source") == "user"]
                    cats[dim] = user_kept + [k for k in kept if k["value"] not in {u["value"] for u in user_kept}]
                    if idx.cardinality(dim) == "single" and len(cats[dim]) > 1:
                        cats[dim] = kept[:1]
                    changes[dim] = [k["value"] for k in kept]
            # approved new values from the proposal's new_values → agent values
            for nv in prop.get("new_values") or []:
                if (nv["dimension"], nv["value"]) in approved_new and not store.is_locked(cats.get(nv["dimension"])):
                    lst = cats.setdefault(nv["dimension"], [])
                    if nv["value"] not in {x.get("value") for x in lst}:
                        lst.append({"value": nv["value"], "source": "agent", "confidence": 0.7, "rationale": nv.get("rationale") or "new value approved by the user",
                                    "evidence_refs": [], "proposed_at": prop.get("created_at"), "approved_by_user_at": now})
                        changes.setdefault(nv["dimension"], []).append(nv["value"])
            # user corrections → source: user, locked: true
            if d.action == "accept_with_changes" and d.overrides:
                for dim, vals in d.overrides.items():
                    if dim not in idx.dims:
                        raise ToolError("INVALID_ARG", f"overrides: unknown dimension '{dim}'")
                    cats[dim] = store.user_values(idx, dim, vals, approved_new)
                    changes[dim] = [f"{v['value']} (user)" for v in cats[dim]]
            missing = [dim for dim in idx.dims if idx.required(dim) and not cats.get(dim)]
            if missing:
                # Q-03: the proposal stays 'pending' with needs_input — it reappears in /review-categories, the channel is not lost
                entry["status"] = "pending"
                prop["needs_input"] = missing
                warnings.append(f"{cid}: missing required dimensions {missing} — channel and proposal stay 'pending' (needs_input); "
                                "ask the user and approve again with overrides (accept_with_changes)")
            else:
                entry["status"] = "approved"
                entry["approved_at"] = now
                prop["status"] = "accepted"
                prop.pop("needs_input", None)
                entry["taxonomy_version_at_categorization"] = prop.get("taxonomy_version", idx.version)
            prop["decided_at"] = now
            write(ws, prop)
            store.append_history(ws, {"event": d.action, "channel_id": cid, "proposal_id": d.proposal_id, "changes": changes, "by": "user", "note": user_note})
            committed += 1
            diff.append(f"{entry.get('title', cid)}: " + "; ".join(f"{k}={', '.join(map(str, v))}" for k, v in changes.items()))
    if taxonomy_added:
        store.append_history(ws, {"event": "taxonomy_add", "values": taxonomy_added, "by": "user"})
    if steam_set:
        store.append_history(ws, {"event": "taxonomy_steam_appids", "values": steam_set, "by": "user"})
    return {"committed": committed, "rejected": rejected, "taxonomy_added": taxonomy_added, "steam_appids_set": steam_set,
            "diff_summary": "\n".join(diff), "warnings": warnings}
