#!/usr/bin/env python3
"""Browse yt-media-feed-helper reports in reports/ — no MCP, no network (stdlib only).

Subcommands:
  list                 — report table (date, title, file, source count)
  index                — (re)generates reports/index.md
  lint [file...]       — offline: bullets/paragraphs without a link in claim sections, links outside YouTube/Steam,
                         missing methodology section; exit code 1 on problems
  show --topic <text>  — prints paths and the TL;DR of matching reports

Sections are identified by the `<!-- section:KEY -->` marker on the line after each H2 heading; reports without
markers fall back to English/Polish heading keywords (e.g. "Methodology and limitations" / "Metodologia i ograniczenia").

Example: python reports_index.py --workspace ~/yt-feed lint
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

ALLOWED_LINK = re.compile(r"^https://(www\.youtube\.com|store\.steampowered\.com|steamcommunity\.com)/")
LINK = re.compile(r"\]\((https?://[^)\s]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
SECTION_MARKER = re.compile(r"^\s*<!--\s*section:\s*([a-z_]+)\s*-->\s*$")
CITED_KEYS = {"tldr", "findings", "official", "semi_official", "creator_voices", "community_reception", "steam"}
# Same fallback as the server (ytfeed/reports.py): (key, folded keywords EN + PL), order matters.
HEADING_KEYWORDS = (
    ("methodology", ("methodolog", "metodolog")),
    ("sources", ("sources", "zrodla")),
    ("discrepancies", ("discrepanc", "uncertaint", "rozbiez")),
    ("tldr", ("tl;dr", "tldr")),
    ("creator_voices", ("creator voices", "creators", "glosy tworcow")),
    ("semi_official", ("semi-official", "semi official", "semiofficial", "polofic")),
    ("official", ("official", "oficjal")),
    ("community_reception", ("community", "reception", "odbior")),
    ("steam", ("steam",)),
    ("findings", ("findings", "ustalenia", "themes", "watki")),
)


def fold(s: str) -> str:
    s = s.replace("ł", "l").replace("Ł", "L")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).casefold()


def key_from_heading(title: str) -> str | None:
    t = fold(title)
    for key, words in HEADING_KEYWORDS:
        if any(w in t for w in words):
            return key
    return None


def section_keys(lines: list[str]) -> dict[int, str | None]:
    """Line index of each H1/H2 → section KEY (marker on the next non-blank line wins, else heading keywords)."""
    out: dict[int, str | None] = {}
    for i, line in enumerate(lines):
        h = HEADING.match(line.strip())
        if not h or len(h.group(1)) > 2:
            continue
        key = key_from_heading(h.group(2))
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                continue
            m = SECTION_MARKER.match(nxt)
            if m:
                key = m.group(1)
            break
        out[i] = key
    return out


def frontmatter(text: str) -> dict:
    """Minimal frontmatter parser (key: value + nested sources)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    fm: dict = {}
    section = None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        m = re.match(r"^(\s*)([\w-]+):\s*(.*)$", line)
        if not m:
            continue
        ind, k, v = m.groups()
        v = v.strip().strip("'\"")
        if not ind:
            section = k if not v else None
            fm[k] = {} if not v else v
        elif section and isinstance(fm.get(section), dict):
            fm[section][k] = v
    return fm


def body_of(text: str) -> str:
    return text[text.find("\n---", 3) + 4:] if text.startswith("---") else text


def reports(ws: Path) -> list[Path]:
    return sorted((p for p in (ws / "reports").glob("*.md") if p.name != "index.md"), reverse=True)


def n_sources(fm: dict) -> int:
    s = fm.get("sources")
    if isinstance(s, dict):
        return sum(int(v) for v in s.values() if str(v).isdigit())
    return 0


def table(ws: Path) -> str:
    out = ["| date | title | file | sources |", "|---|---|---|---|"]
    for p in reports(ws):
        fm = frontmatter(p.read_text(encoding="utf-8"))
        out.append(f"| {fm.get('date', '')} | {fm.get('title', p.stem)} | [{p.name}]({p.name}) | {n_sources(fm)} |")
    return "\n".join(out)


def lint(path: Path) -> list[str]:
    lines = body_of(path.read_text(encoding="utf-8")).splitlines()
    keys = section_keys(lines)
    problems = []
    requires = False
    has_method = False
    for i, line in enumerate(lines):
        no = i + 1
        s = line.strip()
        h = HEADING.match(s)
        if h:
            if len(h.group(1)) <= 2:
                key = keys.get(i)
                if key == "sources":
                    break  # generated sources section — not linted
                requires = key in CITED_KEYS
                has_method = has_method or key == "methodology"
            elif key_from_heading(h.group(2)) == "methodology":
                has_method = True
            continue
        if SECTION_MARKER.match(s):
            continue
        for u in LINK.findall(s):
            if not ALLOWED_LINK.match(u):
                problems.append(f"{path.name}:{no}: link outside YouTube/Steam: {u}")
        if not s or s.startswith("|") or re.match(r"^_.*_$", s) or s.startswith("#"):
            continue
        if requires and not LINK.search(s):
            problems.append(f"{path.name}:{no}: no source link: {s[:80]}")
    if not has_method:
        problems.append(f"{path.name}: missing methodology section (<!-- section:methodology --> or a 'Methodology and limitations' heading)")
    return problems


def tldr(text: str) -> str | None:
    lines = body_of(text).splitlines()
    keys = section_keys(lines)
    starts = sorted(keys)
    for n, i in enumerate(starts):
        if keys[i] == "tldr":
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            chunk = [ln for ln in lines[i + 1:end] if not SECTION_MARKER.match(ln)]
            return "\n".join(chunk).strip()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default=os.environ.get("YTFEED_WORKSPACE") or os.getcwd())
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("index")
    lp = sub.add_parser("lint")
    lp.add_argument("files", nargs="*")
    sp = sub.add_parser("show")
    sp.add_argument("--topic", required=True)
    a = ap.parse_args()
    ws = Path(a.workspace)
    if not (ws / "reports").is_dir():
        print(f"Directory {ws / 'reports'} does not exist", file=sys.stderr)
        return 2
    if a.cmd == "list":
        print(table(ws))
    elif a.cmd == "index":
        idx = ws / "reports" / "index.md"
        idx.write_text("# yt-media-feed-helper reports\n\n" + table(ws) + "\n", encoding="utf-8")
        print(f"Saved {idx}")
    elif a.cmd == "lint":
        files = [Path(f) for f in a.files] or reports(ws)
        problems = [p for f in files for p in lint(f)]
        print("\n".join(problems) if problems else f"OK — {len(files)} reports without problems.")
        return 1 if problems else 0
    elif a.cmd == "show":
        t = fold(a.topic)
        for p in reports(ws):
            text = p.read_text(encoding="utf-8")
            if t in fold(p.name) or t in fold(frontmatter(text).get("title", "")):
                print(f"## reports/{p.name}\n{tldr(text) or '(no TL;DR)'}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
