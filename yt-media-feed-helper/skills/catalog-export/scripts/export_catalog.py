#!/usr/bin/env python3
"""Export catalog/channels.yaml to Markdown or CSV with dimension filters — no MCP, no network (stdlib + PyYAML).

Examples:
  python export_catalog.py --format md --filter content=Gaming --columns title,handle,communication
  python export_catalog.py --format csv --filter "communication=Official,Community Manager" --out reports/catalog-export.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "server"))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "taxonomy-edit" / "scripts"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default=os.environ.get("YTFEED_WORKSPACE") or os.getcwd())
    ap.add_argument("--format", choices=["md", "csv"], default="md")
    ap.add_argument("--filter", action="append", default=[], help="dimension=value[,value] (repeatable)")
    ap.add_argument("--status", choices=["approved", "pending", "uncategorized", "any"], default="any")
    ap.add_argument("--columns", default="title,handle,status,channel_type,content,communication,specialization,language_region,subscription")
    ap.add_argument("--out", help="output file (default: stdout)")
    a = ap.parse_args()

    from validate_catalog import ensure_yaml

    ensure_yaml()
    from ytfeed.catalog.rules import TaxonomyIndex, load_yaml

    ws = Path(a.workspace)
    idx = TaxonomyIndex(load_yaml(ws / "catalog/taxonomy.yaml") or {})
    chans = (load_yaml(ws / "catalog/channels.yaml") or {}).get("channels") or {}
    filters: dict[str, set[str]] = {}
    for f in a.filter:
        if "=" not in f:
            ap.error(f"--filter: expected dimension=value, got {f!r}")
        dim, vals = f.split("=", 1)
        if dim not in idx.dims:
            ap.error(f"unknown dimension {dim!r}; available: {', '.join(idx.dims)}")
        keys = set()
        for v in vals.split(","):
            k, _ = idx.normalize(dim, v.strip())
            if not k:
                ap.error(f"value {v!r} does not exist in dimension {dim!r}; suggestion: {idx.suggest(dim, v)}")
            keys.add(k)
        filters[dim] = keys
    cols = [c.strip() for c in a.columns.split(",") if c.strip()]
    rows = []
    for cid, e in chans.items():
        e = e or {}
        if a.status != "any" and e.get("status") != a.status:
            continue
        cats = e.get("categories") or {}
        vals_of = {d: [str(v.get("value")) + ("*" if v.get("source") == "user" else "") for v in cats.get(d) or []] for d in idx.dims}
        if any(not ({x.rstrip("*") for x in vals_of.get(d, [])} & keys) for d, keys in filters.items()):
            continue
        row = {"channel_id": cid, "title": e.get("title") or "", "handle": e.get("handle") or "", "status": e.get("status") or "",
               "notes": e.get("notes") or "", "url": f"https://www.youtube.com/channel/{cid}"}
        for d in idx.dims:
            row[d] = ", ".join(vals_of[d])
        rows.append({c: row.get(c, "") for c in cols})
    rows.sort(key=lambda r: str(r.get("title", "")).lower())
    if a.format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
        text = buf.getvalue()
    else:
        text = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
        text += "".join("| " + " | ".join(str(r[c]).replace("|", "\\|") for c in cols) + " |\n" for r in rows)
        text += f"\n_{len(rows)} channels; * = value set manually (source: user)._\n"
    if a.out:
        out = Path(a.out)
        if not out.is_absolute():
            out = ws / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="")
        print(f"Saved {len(rows)} rows → {out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
