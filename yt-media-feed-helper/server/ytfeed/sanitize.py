"""Sanitization of untrusted content (§3.2): control/zero-width/bidi characters, NFKC, neutralizing
code blocks and pseudo-tags, truncation with a suffix, heuristic prompt-injection detection."""
from __future__ import annotations

import re
import unicodedata

_INVISIBLE = re.compile("[​-‏‪-‮⁦-⁩﻿­]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PSEUDO_TAG = re.compile(r"<\s*(/?\s*(?:untrusted|system|tool|tool_use|tool_result|assistant|user|human|function_calls|instructions?)\b[^>]*)>", re.I)

# Injection heuristics in English and Polish (Polish patterns are detection data, not UI text).
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_instructions_en", re.compile(r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+instructions?\b", re.I)),
    ("disregard_en", re.compile(r"\bdisregard\s+(?:all\s+)?(?:previous|prior|your)\s+(?:instructions?|rules)\b", re.I)),
    ("you_are_now", re.compile(r"\byou\s+are\s+now\b", re.I)),
    ("system_prompt", re.compile(r"\bsystem\s+prompt\b", re.I)),
    ("ignore_instructions_pl", re.compile(r"\b(?:zignoruj|ignoruj)\s+(?:wszystkie\s+)?(?:poprzednie|wcześniejsze|wczesniejsze|powyższe|powyzsze)\s+(?:instrukcje|polecenia)\b", re.I)),
    ("imperative_mutation_pl", re.compile(r"\b(?:zasubskrybuj|subskrybuj|odsubskrybuj|napisz\s+komentarz|polub\s+(?:ten|to)|wesprzyj\s+kanał)\b", re.I)),
    ("imperative_mutation_en", re.compile(r"(?:^|[.!?]\s*|\band\s+)(?:subscribe|unsubscribe|post\s+a\s+comment|write\s+a\s+comment|like\s+this\s+video)\b", re.I)),
    ("tool_reference", re.compile(r"mcp__|tool_use|function_call|\bcall\s+(?:the\s+)?(?:tool|catalog_commit|catalog_set_manual)\b|catalog_commit", re.I)),
    ("fake_role_marker", re.compile(r"(?:^|\n)\s*(?:system|assistant|human)\s*:", re.I)),
    ("tag_escape", re.compile(r"</?\s*(?:untrusted|system|tool)\b", re.I)),
]


def clean(text: str | None) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = _INVISIBLE.sub("", t)
    t = _CONTROL.sub("", t)
    t = t.replace("```", "ʼʼʼ")
    t = _PSEUDO_TAG.sub(lambda m: "‹" + m.group(1) + "›", t)
    return t


def truncate(text: str, max_chars: int | None) -> str:
    if max_chars is None or max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"…[+{len(text) - max_chars} chars]"


def detect_injection(text: str | None) -> list[str]:
    """Markers on the text BEFORE sanitization (also detects hidden characters) and after it."""
    if not text:
        return []
    raw = str(text)
    markers: list[str] = []
    if _INVISIBLE.search(raw):
        markers.append("invisible_chars")
    normalized = _INVISIBLE.sub("", unicodedata.normalize("NFKC", raw))
    for name, rx in INJECTION_PATTERNS:
        if rx.search(normalized):
            markers.append(name)
    return markers


def untrusted_block(fields: dict[str, str | None], max_chars: dict[str, int] | int | None = None) -> tuple[dict[str, str], bool, list[str]]:
    """Builds the `untrusted` field + the `injection_suspect` flag + the marker list."""
    out: dict[str, str] = {}
    markers: list[str] = []
    for k, v in fields.items():
        if v is None:
            continue
        for m in detect_injection(v):
            if m not in markers:
                markers.append(m)
        limit = max_chars.get(k) if isinstance(max_chars, dict) else max_chars
        out[k] = truncate(clean(v), limit)
    # informational flag: data is not removed; hidden characters (zero-width/bidi) also set it (§9.1)
    return out, bool(markers), markers
