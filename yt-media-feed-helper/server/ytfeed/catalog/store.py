"""`catalog/channels.yaml`: atomic writes, merge protecting `source: user` values (ADR-07), audit trail in history.jsonl."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from typing import Any, Iterator

import yaml

from ..clock import iso, now_utc
from ..errors import ToolError
from ..paths import Workspace, atomic_write_text, data_dir
from ..quota import file_lock
from .rules import TaxonomyIndex

HEADER = "# yt-media-feed-helper — channel catalog (editable; mark manual changes with source: user, locked: true)\n"


def load(ws: Workspace) -> dict:
    if not ws.channels_file.exists():
        return {"schema_version": 1, "channels": {}}
    try:
        doc = yaml.safe_load(ws.channels_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ToolError("INVALID_ARG", f"channels.yaml syntax error: {str(e).splitlines()[0]}", "Fix the file (taxonomy-edit skill)") from e
    doc.setdefault("schema_version", 1)
    if not isinstance(doc.get("channels"), dict):
        doc["channels"] = {}
    return doc


def _norm_key(path) -> str:
    return os.path.normcase(str(path.resolve())).replace("\\", "/").rstrip("/")


def save(ws: Workspace, doc: dict) -> None:
    text = HEADER + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120)
    atomic_write_text(ws.channels_file, text, ws)
    record_integrity(ws)


def record_integrity(ws: Workspace) -> None:
    """S-01: sha256 of channels.yaml after every MCP write (stored in the plugin data dir, outside the workspace)."""
    f = data_dir() / "catalog_integrity.json"
    try:
        st = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        st = {}
    st[_norm_key(ws.channels_file)] = hashlib.sha256(ws.channels_file.read_bytes()).hexdigest()
    atomic_write_text(f, json.dumps(st, indent=1))


def integrity_warning(ws: Workspace) -> str | None:
    f = data_dir() / "catalog_integrity.json"
    try:
        ref = json.loads(f.read_text(encoding="utf-8")).get(_norm_key(ws.channels_file))
    except (OSError, ValueError):
        return None
    if ref and ws.channels_file.exists() and hashlib.sha256(ws.channels_file.read_bytes()).hexdigest() != ref:
        return ("catalog/channels.yaml was changed outside the MCP tools (editor/shell/model) — review the diff before trusting the categories; "
                "this warning disappears after the next MCP write.")
    return None


@contextlib.contextmanager
def transaction(ws: Workspace) -> Iterator[dict]:
    with file_lock(ws.catalog / ".channels.lock"):
        doc = load(ws)
        yield doc
        save(ws, doc)


def append_history(ws: Workspace, event: dict) -> None:
    event = {"ts": iso(now_utc()), **event}
    ws.assert_writable(ws.history_file)
    with open(ws.history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_history(ws: Workspace, channel_id: str, n: int = 5) -> list[dict]:
    if not ws.history_file.exists():
        return []
    out = []
    for line in ws.history_file.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("channel_id") == channel_id:
            out.append(ev)
    return out[-n:]


def is_locked(values: list[dict] | None) -> bool:
    """A dimension is locked when it has a source=user value with locked (default true)."""
    return any(v.get("source") == "user" and v.get("locked", True) for v in values or [])


def new_entry(channel_id: str, title: str, handle: str | None, added_via: str, uploads_playlist: str | None = None) -> dict:
    return {
        "title": title,
        "handle": handle,
        "sources": {"youtube": {"channel_id": channel_id, "uploads_playlist": uploads_playlist or ("UU" + channel_id[2:] if channel_id.startswith("UC") else None)},
                    "steam": {"appids": []}},
        "status": "uncategorized",
        "added_at": iso(now_utc()),
        "added_via": added_via,
        "subscription_lost_at": None,
        "categories": {},
        "notes": None,
    }


def compact(entry: dict, order: tuple[str, ...] = ("channel_type", "content", "communication", "specialization", "language_region", "subscription")) -> str:
    cats = entry.get("categories") or {}
    parts = []
    for dim in order:
        vals = [str(v.get("value")) for v in cats.get(dim) or [] if isinstance(v, dict)]
        if vals:
            parts.append(", ".join(vals))
    return " | ".join(parts)


def user_values(idx: TaxonomyIndex, dim: str, values: list[str], allow_new: set[tuple[str, str]] | None = None) -> list[dict]:
    """Validates and builds manual values (source: user, locked: true)."""
    out = []
    now = iso(now_utc())
    if idx.cardinality(dim) == "single" and len(values) != 1:
        raise ToolError("INVALID_ARG", f"Dimension '{dim}' (single) requires exactly 1 value, got {len(values)}")
    for v in values:
        key, _ = idx.normalize(dim, v)
        if key is None and allow_new and (dim, v) in allow_new:
            key = v
        if key is None:
            raise ToolError("INVALID_ARG", f"'{v}' does not exist in dimension '{dim}'", f"Closest: {idx.suggest(dim, v)}")
        out.append({"value": key, "source": "user", "locked": True, "set_at": now})
    return out


def set_manual(ws: Workspace, idx: TaxonomyIndex, channel_id: str, set_: dict[str, list[str]], unlock: list[str]) -> dict[str, Any]:
    with transaction(ws) as doc:
        entry = doc["channels"].get(channel_id)
        if entry is None:
            raise ToolError("NOT_FOUND", f"Channel {channel_id} does not exist in the catalog")
        cats = entry.setdefault("categories", {})
        changed: dict[str, Any] = {}
        for dim, vals in (set_ or {}).items():
            if dim not in idx.dims:
                raise ToolError("INVALID_ARG", f"Unknown dimension '{dim}'", f"Available: {list(idx.dims)}")
            cats[dim] = user_values(idx, dim, vals)
            changed[dim] = [v["value"] for v in cats[dim]]
        unlocked = []
        for dim in unlock or []:
            for v in cats.get(dim) or []:
                if v.get("source") == "user":
                    v["locked"] = False
                    unlocked.append(dim)
        append_history(ws, {"event": "set_manual", "channel_id": channel_id, "set": changed, "unlock": sorted(set(unlocked)), "by": "user"})
    return {"channel_id": channel_id, "set": changed, "unlocked": sorted(set(unlocked))}
