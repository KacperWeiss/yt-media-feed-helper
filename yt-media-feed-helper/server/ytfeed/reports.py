"""Reports (ADR-03/04, §3.6): `[[ref]]` citation validation, ref rendering → canonical links, the sources section, saving.

Sections are identified by an invisible marker on the line after the H2 heading: `<!-- section:KEY -->`
(KEY ∈ SECTION_KEYS). Reports without markers fall back to heading keyword matching (English + Polish).

Strict validation:
 1. every paragraph / list item in claim sections (tldr, findings, official, semi_official, creator_voices,
    community_reception, steam) contains ≥1 `[[ref]]`;
 2. every ref exists in the evidence registry;
 3. `> …` quotes carry a comment/review ref (yt:c / st:r) or a Steam news ref (st:n);
 4. no raw URLs outside the registry;
 5. a `methodology` section exists.
Exceptions to (1): table rows, headings, section markers, lines entirely in italics (`_…_`) — no claims.
 6. (S-06) every Markdown link/image target (`](…)`, `[x]: …` definitions, `<…>` autolinks, bare `www.`, HTML `<a>/<img>`),
    case-insensitively, must be a registry URL; images, `//…`, `javascript:`, `data:`, `file:` are rejected;
    `title` and strings in `query_spec` must not contain links; validation is repeated on the rendered document.
`>` quotes are checked as a whole block (Q-25): the ref may be on any line of the block.
 7. (Q-24, round 2) lines with numbers in the `discrepancies` section and italic notes in claim sections
    require [[ref]] (a number is a claim); lines without numbers (e.g. "No contradictions") do not.
Frontmatter: `quality` (quality metadata: sample_sizes, lang_unknown, pages_scanned, budget_used, coverage, …) — §5b-9.
"""
from __future__ import annotations

import re
from typing import Any

import yaml

from .catalog.rules import fold
from .clock import now_utc
from .evidence import REF_IN_TEXT, EvidenceRegistry
from .paths import Workspace, atomic_write_text

SLUG_RE = re.compile(r"^[a-z0-9-]{3,60}$")
LANG_RE = re.compile(r"^[a-z]{2}$")
SECTION_KEYS = ("tldr", "findings", "official", "semi_official", "creator_voices", "community_reception", "steam",
                "discrepancies", "methodology", "sources")
CITED_KEYS = frozenset({"tldr", "findings", "official", "semi_official", "creator_voices", "community_reception", "steam"})
SECTION_MARKER = re.compile(r"^\s*<!--\s*section:\s*([a-z_]+)\s*-->\s*$")
# Fallback for reports without markers: (key, folded keywords EN + PL). Order matters
# ("Creator voices (unofficial)" → creator_voices, "Semi-official …" → semi_official before official).
HEADING_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
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
SUBSECTION_NO_CITE = ("rozklad", "distribution", "breakdown")  # H3 with a stance table (no claims)
SOURCES_HEADING = {"en": "Sources", "pl": "Źródła"}
SOURCES_EMPTY = {"en": "_(none)_", "pl": "_(brak)_"}
URL_RE = re.compile(r"(?i)\b(?:https?|ftp|javascript|data|file|vbscript):[^\s)\]>]*|(?<![\w/])//[\w.-]+\.[a-z]{2,}[^\s)\]>]*|(?<![\w/.@])www\.[^\s)\]>]+")
LINK_TARGET = re.compile(r"\]\(\s*<?([^)\s>]*)")
REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)", re.M)
AUTOLINK = re.compile(r"<\s*((?:[a-z][a-z0-9+.-]*:|//|www\.)[^>\s]*)\s*>", re.I)
HTML_LINK = re.compile(r"(?i)<\s*(?:a|img|iframe|script|link|object|embed)\b|\b(?:href|src)\s*=")
MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+!|~])")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def md_escape(text: str) -> str:
    """Untrusted text as plain Markdown text (S-06): escapes link/image/HTML syntax characters and "defangs"
    addresses (GFM autolinks bare https://… and www.…): `https://x` → `https[:]//x`, `www.x` → `www[.]x`."""
    def defang(m: re.Match) -> str:
        return (m.group(1) + "[:]" if m.group(1) else "") + "//" + m.group(2).replace(".", "[.]")

    t = re.sub(r"(?i)(?:\b([a-z][a-z0-9+.-]*):)?(?<![\w/])//([^\s/)\]>]+)", defang, text)
    t = re.sub(r"(?i)\bwww\.", "www[.]", t)
    return MD_SPECIAL.sub(r"\\\1", t)


def link_violations(text: str, known_urls: set[str], line: int | None = None) -> list[dict]:
    """All link targets and addresses in the text must come from the evidence registry; images and HTML are forbidden."""
    out: list[dict] = []
    if "![" in text:
        out.append({"line": line, "rule": "image_not_allowed", "text": text[:120]})
    if HTML_LINK.search(text):
        out.append({"line": line, "rule": "html_link_not_allowed", "text": text[:120]})
    targets = LINK_TARGET.findall(text) + REF_DEF.findall(text) + AUTOLINK.findall(text)
    for t in targets:
        if t.startswith("#") and len(t) > 1:
            continue  # anchor within the report
        if t not in known_urls:
            out.append({"line": line, "rule": "link_target_not_in_registry", "text": t[:120]})
    for u in URL_RE.findall(LINK_TARGET.sub("](", text)):
        if u.rstrip(".,;") not in known_urls:
            out.append({"line": line, "rule": "raw_url_not_in_registry", "text": u[:120]})
    return out


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _strings(v)


QUOTE_REFS = ("yt:c:", "st:r:", "st:n:")


def section_key_from_heading(heading: str) -> str | None:
    """Fallback (no marker): section KEY from heading keywords (English or Polish, case/diacritics-insensitive)."""
    h = fold(REF_IN_TEXT.sub("", heading))
    for key, words in HEADING_KEYWORDS:
        if any(w in h for w in words):
            return key
    return None


def section_keys(lines: list[str]) -> dict[int, str | None]:
    """Line index (0-based) of each H1/H2 heading → section KEY: the marker on the next non-blank line wins,
    otherwise heading keyword fallback. Unknown marker KEYs map to None (reported by `validate`)."""
    out: dict[int, str | None] = {}
    for i, raw in enumerate(lines):
        m = HEADING_RE.match(raw.rstrip())
        if not m or len(m.group(1)) > 2:
            continue
        key = section_key_from_heading(m.group(2))
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                continue
            mk = SECTION_MARKER.match(nxt)
            if mk:
                key = mk.group(1) if mk.group(1) in SECTION_KEYS else None
            break
        out[i] = key
    return out


def strip_frontmatter(md: str) -> str:
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            return md[end + 4:].lstrip("\n")
    return md


def validate(md: str, ev: EvidenceRegistry, strict: bool = True) -> tuple[list[dict], list[str]]:
    """Returns (violations, refs in order of appearance)."""
    violations: list[dict] = []
    refs_in_order: list[str] = []
    lines = md.splitlines()
    keys = section_keys(lines)
    known_urls = ev.all_urls()
    h2_requires = False
    current_requires = False
    section_numeric = False  # Q-24: discrepancies section
    has_method = False
    para: list[tuple[int, str]] = []

    quote: list[tuple[int, str]] = []

    def flush_quote() -> None:
        if not quote:
            return
        refs = REF_IN_TEXT.findall(" ".join(t for _, t in quote))
        if strict and not any(r.startswith(QUOTE_REFS) for r in refs):
            violations.append({"line": quote[0][0], "rule": "quote_without_comment_or_review_ref", "text": quote[0][1][:120]})
        quote.clear()

    def flush() -> None:
        flush_quote()
        flush_para()

    def flush_para() -> None:
        if not para:
            return
        text = " ".join(t for _, t in para)
        if current_requires and strict and not REF_IN_TEXT.search(text):
            violations.append({"line": para[0][0], "rule": "paragraph_without_ref", "text": para[0][1][:120]})
        elif section_numeric and strict and _numeric_without_ref(text):
            violations.append({"line": para[0][0], "rule": "numeric_claim_without_ref", "text": para[0][1][:120]})
        para.clear()

    in_code = False
    for no, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if line.strip().startswith("ʼʼʼ") or line.strip().startswith("```"):
            in_code = not in_code
            continue
        mk = SECTION_MARKER.match(line)
        if mk and not in_code:
            flush()
            if strict and mk.group(1) not in SECTION_KEYS:
                violations.append({"line": no, "rule": "unknown_section_marker", "text": line.strip()[:120]})
            continue
        for r in REF_IN_TEXT.findall(line):
            if r not in refs_in_order:
                refs_in_order.append(r)
        violations.extend(link_violations(REF_IN_TEXT.sub("", line), known_urls, no))
        m = HEADING_RE.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2)
            if level <= 2:
                key = keys.get(no - 1)
                h2_requires = key in CITED_KEYS
                current_requires = h2_requires
                section_numeric = key == "discrepancies"
                if key == "methodology":
                    has_method = True
            else:
                sub = section_key_from_heading(title)
                if sub == "methodology":
                    has_method = True
                current_requires = (h2_requires and not any(w in fold(title) for w in SUBSECTION_NO_CITE)) or sub in CITED_KEYS
            continue
        if in_code:
            continue
        s = line.strip()
        if not s:
            flush()
            continue
        if s.startswith("|"):
            flush()
            continue
        if s.startswith(">"):
            flush_para()
            quote.append((no, s))
            continue
        flush_quote()
        if re.match(r"^_.*_$", s) or re.match(r"^\*[^*].*\*$", s):
            flush()
            if (current_requires or section_numeric) and strict and _numeric_without_ref(s):
                violations.append({"line": no, "rule": "numeric_claim_without_ref", "text": s[:120]})
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", s):
            flush()
            if current_requires and strict and not REF_IN_TEXT.search(s):
                violations.append({"line": no, "rule": "list_item_without_ref", "text": s[:120]})
            elif section_numeric and strict and _numeric_without_ref(re.sub(r"^\d+[.)]\s+", "", s)):
                violations.append({"line": no, "rule": "numeric_claim_without_ref", "text": s[:120]})
            continue
        para.append((no, s))
    flush()
    unknown = [r for r in refs_in_order if not ev.exists(r)]
    for r in unknown:
        violations.append({"line": None, "rule": "unknown_ref", "text": r})
    if strict and not has_method:
        violations.append({"line": None, "rule": "missing_methodology_section",
                           "text": "Missing methodology section (H2 heading followed by <!-- section:methodology -->)"})
    return violations, [r for r in refs_in_order if r not in unknown]


def _numeric_without_ref(text: str) -> bool:
    return not REF_IN_TEXT.search(text) and bool(re.search(r"\d", text))


QUALITY_KEYS = {"sample_sizes", "lang_unknown", "pages_scanned", "budget_used", "coverage", "duplicates_merged", "small_samples", "notes"}


def clean_quality(q: dict | None, quota_units: int | None) -> dict:
    """§5b-9: quality metadata for the frontmatter — known keys only; numbers, short strings, nested dicts and lists."""
    if q is None:
        q = {}
    if not isinstance(q, dict):
        raise ValueError("quality must be an object")
    unknown = sorted(set(q) - QUALITY_KEYS)
    if unknown:
        raise ValueError(f"quality: unknown keys {unknown}; allowed: {sorted(QUALITY_KEYS)}")

    def ok(v, depth=0):
        if depth > 3:
            return False
        if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
            return True
        if isinstance(v, str):
            return len(v) <= 200
        if isinstance(v, list):
            return len(v) <= 50 and all(ok(x, depth + 1) for x in v)
        if isinstance(v, dict):
            return len(v) <= 50 and all(isinstance(k, str) and len(k) <= 60 and ok(x, depth + 1) for k, x in v.items())
        return False

    if not all(ok(v) for v in q.values()):
        raise ValueError("quality: allowed are numbers, strings ≤ 200 chars, lists/objects (≤ 50 items, depth ≤ 3)")
    out = dict(q)
    if "budget_used" not in out and quota_units is not None:
        out["budget_used"] = quota_units
    return out


def heading_anchors(md: str) -> dict[str, str]:
    """§5b-8: GitHub/VS Code style heading anchors (for the chat TL;DR linking to report sections)."""
    out: dict[str, str] = {}
    used: dict[str, int] = {}
    for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", md, re.M):
        title = REF_IN_TEXT.sub("", m.group(1)).strip()
        slug = re.sub(r"[^\w\- ]", "", title.lower()).strip().replace(" ", "-")
        n = used.get(slug, 0)
        used[slug] = n + 1
        out[title] = "#" + (slug if n == 0 else f"{slug}-{n}")
    return out


def strip_sources_section(md: str) -> str:
    """Removes an author-written sources section (from its H2 to the end) — report_save generates its own."""
    lines = md.split("\n")
    for i, key in section_keys(lines).items():
        if key == "sources" and lines[i].startswith("## "):
            return "\n".join(lines[:i])
    return md


def render(md: str, ev: EvidenceRegistry, language: str = "en") -> tuple[str, list[dict]]:
    sources: dict[str, int] = {}
    info: list[dict] = []

    def repl(m: re.Match) -> str:
        ref = m.group(1)
        d = ev.get(ref)
        if not d:
            return m.group(0)
        if ref not in sources:
            sources[ref] = len(sources) + 1
            info.append({"n": sources[ref], **d})
        return f"[↗{sources[ref]}]({d['url']})"

    body = REF_IN_TEXT.sub(repl, strip_sources_section(md))
    src_lines = ["", f"## {SOURCES_HEADING.get(language, SOURCES_HEADING['en'])}", "<!-- section:sources -->", ""]
    for s in info:
        # S-06: title/author (untrusted data) as escaped plain text; the link separately, only the canonical registry URL
        title = md_escape((s.get("title") or s.get("snippet") or s["ref"]).replace("\n", " ")[:140])
        date = (s.get("published_at") or "")[:10]
        who = f" — {md_escape(str(s['author'])[:80])}" if s.get("author") else ""
        src_lines.append(f"{s['n']}. {title}{who}{' (' + date + ')' if date else ''} — [link]({s['url']}) `{s['ref']}`")
    if not info:
        src_lines.append(SOURCES_EMPTY.get(language, SOURCES_EMPTY["en"]))
    return body.rstrip() + "\n" + "\n".join(src_lines) + "\n", info


def save(ws: Workspace, ev: EvidenceRegistry, topic_slug: str, title: str, markdown: str, query_spec: dict, quota_units: int | None,
         strict: bool = True, quality: dict | None = None, language: str | None = None) -> dict[str, Any]:
    if not SLUG_RE.match(topic_slug):
        return {"ok": False, "violations": [{"line": None, "rule": "bad_slug", "text": "topic_slug must match [a-z0-9-]{3,60}"}]}
    # report language: explicit param > query_spec.report_language > "en" (query_spec.language is the comment-language filter)
    qs_lang = (query_spec or {}).get("report_language")
    language = str(language or (qs_lang if isinstance(qs_lang, str) else "") or "en").strip().lower()
    if not LANG_RE.match(language):
        return {"ok": False, "violations": [{"line": None, "rule": "bad_language", "text": "language must be an ISO 639-1 code (e.g. en, pl)"}]}
    body = strip_frontmatter(markdown)
    violations, refs = validate(body, ev, strict)
    try:
        q = clean_quality(quality, quota_units)
    except ValueError as e:
        return {"ok": False, "violations": [{"line": None, "rule": "bad_quality_metadata", "text": str(e)[:200]}]}
    for field_text in [title, *_strings(query_spec or {}), *_strings(q)]:  # S-06: no links in metadata
        if URL_RE.search(field_text) or LINK_TARGET.search(field_text) or AUTOLINK.search(field_text) or HTML_LINK.search(field_text) or "![" in field_text:
            violations.append({"line": None, "rule": "link_in_metadata", "text": str(field_text)[:120]})
    if violations:
        return {"ok": False, "violations": violations[:50], "violations_total": len(violations)}
    rendered, info = render(body, ev, language)
    kinds: dict[str, int] = {}
    for s in info:
        kinds[s.get("kind") or "?"] = kinds.get(s.get("kind") or "?", 0) + 1
    date = now_utc().date().isoformat()
    fm = {"title": title, "date": date, "report_language": language,
          **{k: v for k, v in (query_spec or {}).items() if k not in ("title", "date", "report_language")},
          "sources": {"youtube_channels": kinds.get("ch", 0), "youtube_videos": kinds.get("v", 0) + kinds.get("chapter", 0),
                      "youtube_comments": kinds.get("c", 0), "steam_news": kinds.get("n", 0), "steam_reviews": kinds.get("r", 0),
                      "steam_apps": kinds.get("app", 0)},
          "quota_units": quota_units, **({"quality": q} if q else {}), "generator": "yt-media-feed-helper report_save"}
    known = ev.all_urls() | {s["url"] for s in info}  # + chapter URLs (yt:v:ID@sec) derived from the ref
    post = [v for v in link_violations(rendered, known) if v["rule"] != "raw_url_not_in_registry" or v["text"] not in known]
    if post:  # S-06: assertion on the rendered document (e.g. a source title trying to inject a link)
        return {"ok": False, "violations": [{**v, "rule": "rendered_" + v["rule"]} for v in post[:20]], "violations_total": len(post)}
    doc = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=200) + "---\n\n" + rendered
    ws.reports.mkdir(parents=True, exist_ok=True)
    path = ws.reports / f"{date}-{topic_slug}.md"
    i = 2
    while path.exists():
        path = ws.reports / f"{date}-{topic_slug}-{i}.md"
        i += 1
    atomic_write_text(path, doc, ws)
    citations = len(REF_IN_TEXT.findall(body))
    rel = path.relative_to(ws.root).as_posix()
    anchors = {h: f"{rel}{a}" for h, a in list(heading_anchors(body).items())[:30]}
    return {"ok": True, "path": rel, "citations": citations, "unique_sources": len(info), "anchors": anchors, "violations": []}
