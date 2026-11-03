"""Taxonomy and catalog rules (§4.2) — stdlib ONLY (+ PyYAML loaded lazily).

Single source of truth for validation in the MCP server and in the skill script `validate_catalog.py`
(the skill imports this module directly), so both give identical results.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

SOURCES = {"agent", "user"}
STATUSES = {"uncategorized", "pending", "approved", "ignored"}


def fold(s: str) -> str:
    """Lowercase + strip diacritics (ł→l) — for case/diacritics-insensitive comparisons."""
    s = str(s).replace("ł", "l").replace("Ł", "L")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def load_yaml(path: Path) -> Any:
    import yaml  # lazily: rules.py must import without PyYAML

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def issue(level: str, code: str, path: str, message: str) -> dict:
    return {"level": level, "code": code, "path": path, "message": message}


class TaxonomyIndex:
    """Taxonomy index: keys, synonyms, hierarchy, aliases, cardinality."""

    def __init__(self, doc: dict):
        self.doc = doc or {}
        self.version = int(self.doc.get("taxonomy_version", 0) or 0)
        self.dims: dict[str, dict] = {}
        self.keys: dict[str, list[str]] = {}
        self.syn: dict[str, dict[str, str]] = {}
        self.parent: dict[str, dict[str, str | None]] = {}
        for dname, d in (self.doc.get("dimensions") or {}).items():
            d = d or {}
            self.dims[dname] = d
            keys: list[str] = []
            syn: dict[str, str] = {}
            par: dict[str, str | None] = {}
            for v in d.get("values") or []:
                if isinstance(v, str):
                    v = {"key": v}
                k = str(v.get("key", "")).strip()
                if not k:
                    continue
                keys.append(k)
                syn[fold(k)] = k
                for s in v.get("synonyms") or []:
                    syn.setdefault(fold(s), k)
                par[k] = v.get("parent")
            self.keys[dname] = keys
            self.syn[dname] = syn
            self.parent[dname] = par
        self.aliases: dict[str, dict] = {fold(a): t for a, t in (self.doc.get("aliases") or {}).items() if isinstance(t, dict)}
        self.term_aliases: dict[str, list[str]] = {k: list(v or []) for k, v in (self.doc.get("term_aliases") or {}).items()}

    def cardinality(self, dim: str) -> str:
        return str(self.dims.get(dim, {}).get("cardinality", "multi"))

    def is_open(self, dim: str) -> bool:
        return bool(self.dims.get(dim, {}).get("open", False))

    def required(self, dim: str) -> bool:
        return bool(self.dims.get(dim, {}).get("required", False))

    def agent_settable(self, dim: str) -> list[str] | None:
        v = self.dims.get(dim, {}).get("agent_settable")
        return list(v) if v else None

    def value_attr(self, dim: str, key: str, attr: str) -> Any:
        for v in self.dims.get(dim, {}).get("values") or []:
            if isinstance(v, dict) and v.get("key") == key:
                return v.get(attr)
        return None

    def normalize(self, dim: str, value: str) -> tuple[str | None, bool]:
        """(canonical key | None, whether matched via a synonym)."""
        if dim not in self.syn:
            return None, False
        k = self.syn[dim].get(fold(value))
        if k is None:
            return None, False
        return k, k != value

    def descendants(self, dim: str, key: str) -> set[str]:
        out = {key}
        changed = True
        par = self.parent.get(dim, {})
        while changed:
            changed = False
            for child, p in par.items():
                if p in out and child not in out:
                    out.add(child)
                    changed = True
        return out

    def suggest(self, dim: str, value: str, n: int = 3) -> list[str]:
        import difflib

        keys = self.keys.get(dim, [])
        folded = {fold(k): k for k in keys}
        hits = difflib.get_close_matches(fold(value), list(folded), n=n, cutoff=0.4)
        return [folded[h] for h in hits] or keys[:n]

    def resolve_alias(self, term: str) -> tuple[str, str] | None:
        t = self.aliases.get(fold(term))
        if t and t.get("dimension") in self.dims:
            return str(t["dimension"]), str(t["value"])
        return None

    def expand_terms(self, terms: list[str]) -> list[str]:
        """Expands terms with synonyms from term_aliases and the synonyms of open-dimension values."""
        out: list[str] = []
        seen: set[str] = set()

        def add(x: str) -> None:
            if fold(x) not in seen:
                seen.add(fold(x))
                out.append(x)

        for t in terms:
            add(t)
            ft = fold(t)
            for canon, syns in self.term_aliases.items():
                group = [canon, *syns]
                if ft in {fold(g) for g in group}:
                    for g in group:
                        add(g)
            for dim in self.dims:
                k = self.syn[dim].get(ft)
                if k and self.dims[dim].get("open"):
                    add(k)
                    for v in self.dims[dim].get("values") or []:
                        if isinstance(v, dict) and v.get("key") == k:
                            for s in v.get("synonyms") or []:
                                add(str(s))
        return out


def validate_taxonomy(doc: dict) -> list[dict]:
    out: list[dict] = []
    if not isinstance(doc, dict):
        return [issue("error", "TAX_NOT_MAPPING", "taxonomy", "The taxonomy file must be a YAML mapping")]
    if not isinstance(doc.get("taxonomy_version"), int):
        out.append(issue("error", "TAX_VERSION", "taxonomy_version", "taxonomy_version must be an integer"))
    dims = doc.get("dimensions")
    if not isinstance(dims, dict) or not dims:
        return out + [issue("error", "TAX_NO_DIMENSIONS", "dimensions", "No dimensions")]
    idx = TaxonomyIndex(doc)
    for dname, d in dims.items():
        p = f"dimensions.{dname}"
        if not isinstance(d, dict):
            out.append(issue("error", "DIM_NOT_MAPPING", p, "A dimension must be a mapping"))
            continue
        if d.get("cardinality", "multi") not in ("single", "multi"):
            out.append(issue("error", "DIM_CARDINALITY", p, "cardinality ∈ {single, multi}"))
        vals = d.get("values") or []
        if not vals:
            out.append(issue("error", "DIM_NO_VALUES", p, "Dimension without values"))
        seen: dict[str, str] = {}
        for i, v in enumerate(vals):
            vk = v.get("key") if isinstance(v, dict) else v
            if not vk:
                out.append(issue("error", "VALUE_NO_KEY", f"{p}.values[{i}]", "Value without 'key'"))
                continue
            if fold(vk) in seen:
                out.append(issue("error", "VALUE_DUPLICATE", f"{p}.values[{i}]", f"Duplicate value '{vk}'"))
            seen[fold(vk)] = vk
            if isinstance(v, dict):
                par = v.get("parent")
                if par and par not in idx.keys[dname]:
                    out.append(issue("error", "VALUE_BAD_PARENT", f"{p}.values[{i}]", f"Parent '{par}' does not exist"))
        for i, v in enumerate(vals):
            if isinstance(v, dict):
                for s in v.get("synonyms") or []:
                    owner = seen.get(fold(s))
                    if owner and owner != v.get("key"):
                        out.append(issue("warning", "SYNONYM_COLLISION", f"{p}.values[{i}]", f"Synonym '{s}' collides with value '{owner}'"))
        ag = d.get("agent_settable")
        if ag:
            for a in ag:
                if a not in idx.keys[dname]:
                    out.append(issue("error", "AGENT_SETTABLE_UNKNOWN", p, f"agent_settable: unknown value '{a}'"))
    for alias, t in (doc.get("aliases") or {}).items():
        if not isinstance(t, dict) or t.get("dimension") not in dims or t.get("value") not in idx.keys.get(t.get("dimension"), []):
            out.append(issue("error", "ALIAS_BAD_TARGET", f"aliases.{alias}", "Alias points to a non-existent dimension/value"))
    ta = doc.get("term_aliases")
    if ta is not None and (not isinstance(ta, dict) or any(not isinstance(v, list) for v in ta.values())):
        out.append(issue("error", "TERM_ALIASES_FORMAT", "term_aliases", "term_aliases: mapping term → list of synonyms"))
    return out


def validate_channel(idx: TaxonomyIndex, cid: str, entry: dict, stale_after: int = 3) -> list[dict]:
    out: list[dict] = []
    p = f"channels.{cid}"
    if not isinstance(entry, dict):
        return [issue("error", "CHANNEL_NOT_MAPPING", p, "A channel entry must be a mapping")]
    status = entry.get("status", "uncategorized")
    if status not in STATUSES:
        out.append(issue("error", "CHANNEL_STATUS", p, f"status ∈ {sorted(STATUSES)}"))
    cats = entry.get("categories") or {}
    if not isinstance(cats, dict):
        return out + [issue("error", "CATEGORIES_NOT_MAPPING", p, "categories must be a mapping")]
    for dname, values in cats.items():
        dp = f"{p}.categories.{dname}"
        if dname not in idx.dims:
            out.append(issue("error", "UNKNOWN_DIMENSION", dp, f"Unknown dimension '{dname}'"))
            continue
        if not isinstance(values, list):
            out.append(issue("error", "VALUES_NOT_LIST", dp, "Dimension values must be a list"))
            continue
        # rule 2: single → exactly 1
        if idx.cardinality(dname) == "single" and len(values) != 1 and values:
            out.append(issue("error", "CARDINALITY_SINGLE", dp, f"'single' dimension has {len(values)} values"))
        for i, v in enumerate(values):
            vp = f"{dp}[{i}]"
            if not isinstance(v, dict) or "value" not in v:
                out.append(issue("error", "VALUE_FORMAT", vp, "Expected {value, source, ...}"))
                continue
            # rule 3: the value exists (synonym → warning)
            key, via_syn = idx.normalize(dname, str(v["value"]))
            if key is None:
                out.append(issue("error", "UNKNOWN_VALUE", vp, f"'{v['value']}' does not exist in the taxonomy (suggestion: {idx.suggest(dname, str(v['value']))})"))
            elif via_syn:
                out.append(issue("warning", "SYNONYM_USED", vp, f"'{v['value']}' is a synonym — normalize to '{key}'"))
            # rule 4: source
            src = v.get("source")
            if src not in SOURCES:
                out.append(issue("error", "BAD_SOURCE", vp, "source ∈ {agent, user}"))
            elif src == "agent":
                c = v.get("confidence")
                if not isinstance(c, (int, float)) or not 0 <= float(c) <= 1:
                    out.append(issue("error", "AGENT_CONFIDENCE", vp, "source=agent requires confidence ∈ [0,1]"))
                if not v.get("rationale"):
                    out.append(issue("error", "AGENT_RATIONALE", vp, "source=agent requires rationale"))
            # rule 5: values outside agent_settable (e.g. Supporter) are set manually only
            settable = idx.agent_settable(dname)
            if src == "agent" and settable is not None and key is not None and key not in settable:
                out.append(issue("error", "AGENT_NOT_SETTABLE", vp, f"Value '{key}' can only be set by the user (source: user)"))
    # rule 1: required dimensions for approved entries
    if status == "approved":
        for dname in idx.dims:
            if idx.required(dname) and not cats.get(dname):
                out.append(issue("error", "REQUIRED_MISSING", f"{p}.categories.{dname}", f"Required dimension '{dname}' is empty for status=approved"))
    # rule 6: stale categorization
    tv = entry.get("taxonomy_version_at_categorization")
    if isinstance(tv, int) and idx.version - tv > stale_after:
        out.append(issue("warning", "STALE_CATEGORIZATION", p, f"Categorized at taxonomy_version={tv} (current {idx.version}) — needs recategorization"))
    return out


def validate_channels(tax_doc: dict, channels_doc: dict, stale_after: int = 3) -> list[dict]:
    idx = TaxonomyIndex(tax_doc)
    out: list[dict] = []
    if not isinstance(channels_doc, dict):
        return [issue("error", "CHANNELS_NOT_MAPPING", "channels", "The channels file must be a mapping")]
    chans = channels_doc.get("channels") or {}
    if not isinstance(chans, dict):
        return [issue("error", "CHANNELS_NOT_MAPPING", "channels", "'channels' must be a mapping channel_id → entry")]
    for cid, entry in chans.items():
        out.extend(validate_channel(idx, str(cid), entry, stale_after))
    return out


def validate_workspace(ws: Path, stale_after: int = 3) -> list[dict]:
    ws = Path(ws)
    tax_path = ws / "catalog" / "taxonomy.yaml"
    ch_path = ws / "catalog" / "channels.yaml"
    out: list[dict] = []
    try:
        tax = load_yaml(tax_path) if tax_path.exists() else None
    except Exception as e:  # noqa: BLE001 — YAML syntax error
        return [issue("error", "YAML_SYNTAX", str(tax_path), str(e).splitlines()[0])]
    if tax is None:
        return [issue("error", "TAX_MISSING", str(tax_path), "taxonomy.yaml is missing")]
    out.extend(validate_taxonomy(tax))
    if ch_path.exists():
        try:
            ch = load_yaml(ch_path) or {}
        except Exception as e:  # noqa: BLE001
            return out + [issue("error", "YAML_SYNTAX", str(ch_path), str(e).splitlines()[0])]
        out.extend(validate_channels(tax, ch, stale_after))
    return out
