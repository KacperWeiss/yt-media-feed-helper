"""Load/save `catalog/taxonomy.yaml` (validation shared with rules.py)."""
from __future__ import annotations

import contextlib
from typing import Iterator

import yaml

from ..clock import iso, now_utc
from ..errors import ToolError
from ..paths import Workspace, atomic_write_text
from ..quota import file_lock
from .rules import TaxonomyIndex, validate_taxonomy

HEADER = (
    "# yt-media-feed-helper — channel category taxonomy (editable by hand; bump taxonomy_version after a change).\n"
    "# File written by the MCP server (catalog_commit) — comments inside may have been lost.\n"
)


def load(ws: Workspace) -> TaxonomyIndex:
    try:
        doc = yaml.safe_load(ws.taxonomy_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ToolError("INVALID_ARG", f"taxonomy.yaml syntax error: {str(e).splitlines()[0]}", "Fix the file (taxonomy-edit skill)") from e
    errs = [i for i in validate_taxonomy(doc) if i["level"] == "error"]
    if errs:
        raise ToolError("INVALID_ARG", f"taxonomy.yaml is invalid: {errs[0]['message']} ({errs[0]['path']})",
                        "Run validate_catalog.py (taxonomy-edit skill)", {"issues": errs[:10]})
    return TaxonomyIndex(doc)


@contextlib.contextmanager
def edit(ws: Workspace) -> Iterator[dict]:
    """Transaction on taxonomy.yaml: lock → load → (modify) → version bump → atomic write."""
    with file_lock(ws.catalog / ".taxonomy.lock"):
        doc = yaml.safe_load(ws.taxonomy_file.read_text(encoding="utf-8")) or {}
        before = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        yield doc
        after = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        if after != before:
            doc["taxonomy_version"] = int(doc.get("taxonomy_version", 0)) + 1
            doc["updated_at"] = iso(now_utc())
            atomic_write_text(ws.taxonomy_file, HEADER + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120), ws)


def add_value(doc: dict, dimension: str, value: str, parent: str | None = None) -> bool:
    dims = doc.get("dimensions") or {}
    if dimension not in dims:
        raise ToolError("INVALID_ARG", f"Unknown dimension '{dimension}'")
    vals = dims[dimension].setdefault("values", [])
    idx = TaxonomyIndex(doc)
    if idx.normalize(dimension, value)[0]:
        return False
    item: dict = {"key": value}
    if parent:  # Q-17: the parent must exist (a filter on the parent will include the new value)
        key, _ = idx.normalize(dimension, parent)
        if key is None:
            raise ToolError("INVALID_ARG", f"Parent '{parent}' does not exist in dimension '{dimension}'", f"Closest: {idx.suggest(dimension, parent)}")
        item["parent"] = key
    vals.append(item)
    return True


def set_steam_appids(doc: dict, value: str, appids: list[int]) -> bool:
    """Q-08: `steam_appids` of a specialization value (used by report-workflow to launch the steam-checker)."""
    idx = TaxonomyIndex(doc)
    key, _ = idx.normalize("specialization", value)
    if key is None:
        raise ToolError("INVALID_ARG", f"'{value}' does not exist in dimension 'specialization'", f"Closest: {idx.suggest('specialization', value)}")
    for v in (doc.get("dimensions") or {}).get("specialization", {}).get("values") or []:
        if isinstance(v, dict) and v.get("key") == key:
            new = sorted({int(a) for a in appids if int(a) > 0})
            if v.get("steam_appids") == new:
                return False
            v["steam_appids"] = new
            return True
    return False


def summary(idx: TaxonomyIndex, dimensions: list[str] | None = None, include_descriptions: bool = False) -> dict:
    out = []
    for name, d in idx.dims.items():
        if dimensions and name not in dimensions:
            continue
        item: dict = {"key": name, "label": d.get("label"), "cardinality": idx.cardinality(name), "open": idx.is_open(name),
                      "required": idx.required(name)}
        if include_descriptions:
            vals = []
            for v in d.get("values") or []:
                v = v if isinstance(v, dict) else {"key": v}
                vals.append({k: v[k] for k in ("key", "description", "parent", "synonyms", "official", "semi_official", "steam_appids") if k in v})
            item["values"] = vals
        else:
            item["values"] = idx.keys[name]
            parents = {k: p for k, p in idx.parent[name].items() if p}
            if parents:
                item["parents"] = parents
        if idx.agent_settable(name):
            item["agent_settable"] = idx.agent_settable(name)
        out.append(item)
    res = {"dimensions": out, "version": idx.version}
    if not dimensions:
        res["aliases"] = {a: f"{t.get('dimension')}={t.get('value')}" for a, t in (idx.doc.get("aliases") or {}).items()}
        res["term_aliases"] = idx.term_aliases
    return res
