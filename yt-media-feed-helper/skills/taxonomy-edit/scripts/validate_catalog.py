#!/usr/bin/env python3
"""Validate catalog/taxonomy.yaml and catalog/channels.yaml (rules §4.2) — no MCP, no network.

Uses the same rules as the MCP server (server/ytfeed/catalog/rules.py), so the results are identical.
Requires PyYAML; if it is missing, the script re-runs itself with the plugin venv interpreter.

Examples:
  python validate_catalog.py --workspace ~/yt-feed
  python validate_catalog.py --json
  python validate_catalog.py --fix-synonyms      # replaces synonyms with canonical keys in channels.yaml

Exit code: 0 = no errors, 1 = errors found, 2 = startup error.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "server"))


def ensure_yaml() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        if os.environ.get("YTFEED_REEXEC"):
            sys.exit("PyYAML is missing. Run: python server/launch.py install (plugin venv) or pip install pyyaml")
        cands = glob.glob(str(Path.home() / ".claude/plugins/data/yt-media-feed-helper*/venv/Scripts/python.exe")) + \
            glob.glob(str(Path.home() / ".claude/plugins/data/yt-media-feed-helper*/venv/bin/python"))
        if not cands:
            sys.exit("PyYAML and the plugin venv are missing. Run: python server/launch.py install")
        env = dict(os.environ, YTFEED_REEXEC="1")
        sys.exit(subprocess.call([cands[0], *sys.argv], env=env))


def fix_synonyms(ws: Path) -> int:
    import yaml

    from ytfeed.catalog.rules import TaxonomyIndex

    idx = TaxonomyIndex(yaml.safe_load((ws / "catalog/taxonomy.yaml").read_text(encoding="utf-8")))
    p = ws / "catalog/channels.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    n = 0
    for entry in (doc.get("channels") or {}).values():
        for dim, vals in ((entry or {}).get("categories") or {}).items():
            for v in vals or []:
                key, via = idx.normalize(dim, str(v.get("value")))
                if key and via:
                    v["value"] = key
                    n += 1
    if n:
        tmp = p.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        os.replace(tmp, p)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default=os.environ.get("YTFEED_WORKSPACE") or os.getcwd(), help="directory containing catalog/ (default: current)")
    ap.add_argument("--json", action="store_true", help="output as JSON")
    ap.add_argument("--fix-synonyms", action="store_true", help="normalize synonyms to canonical keys")
    ap.add_argument("--stale-after", type=int, default=3, help="how many taxonomy versions back = needs recategorization")
    a = ap.parse_args()
    ensure_yaml()
    from ytfeed.catalog.rules import validate_workspace

    ws = Path(a.workspace)
    fixed = fix_synonyms(ws) if a.fix_synonyms else 0
    issues = validate_workspace(ws, a.stale_after)
    errors = [i for i in issues if i["level"] == "error"]
    if a.json:
        print(json.dumps({"ok": not errors, "errors": len(errors), "warnings": len(issues) - len(errors), "fixed_synonyms": fixed,
                          "issues": issues}, ensure_ascii=False, indent=2))
    else:
        if fixed:
            print(f"Synonyms normalized: {fixed}")
        if not issues:
            print("OK — no errors or warnings.")
        for i in issues:
            print(f"[{i['level'].upper()}] {i['code']} @ {i['path']}: {i['message']}")
        print(f"\nSummary: {len(errors)} errors, {len(issues) - len(errors)} warnings.")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
