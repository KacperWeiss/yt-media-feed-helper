"""Shared helpers: API calls with cache + quota (Meter), language heuristic, chapters, links, term matching."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Callable
from urllib.parse import urlsplit

from ..cache import Store
from ..catalog.rules import fold
from ..errors import MockFixtureMissing, ToolError, UpstreamHTTPError
from ..quota import QuotaLedger


class Meter:
    """Sums the quota cost within a single tool call (for the `quota` envelope)."""

    def __init__(self) -> None:
        self.cost = 0
        self.last: dict | None = None
        self.cache_hits = 0
        self.api_calls = 0

    def add(self, snapshot: dict) -> None:
        self.cost += int(snapshot.get("cost", 0))
        self.last = snapshot
        self.api_calls += 1

    def envelope(self, ledger: QuotaLedger) -> dict:
        if self.last is None:
            st = ledger.status()["buckets"]["default"]
            return {"cost": 0, "bucket": "default", "used_today": st["used"], "limit": st["limit"], "remaining": max(0, st["limit"] - st["used"]),
                    "cache_hits": self.cache_hits}
        return {**self.last, "cost": self.cost, "cache_hits": self.cache_hits}


def api_call(*, transport: Any, store: Store, ledger: QuotaLedger | None, api: str, endpoint: str, params: dict, tool: str,
             ttl_s: int, meter: Meter, pre_check: Callable[[], None] | None = None, method: str | None = None) -> Any:
    key = f"{api}:{endpoint}:" + json.dumps(params, sort_keys=True, ensure_ascii=False)
    hit = store.get(key)
    if hit is not None:
        meter.cache_hits += 1
        return hit
    if pre_check:
        pre_check()
    m = method or f"{endpoint}.list"
    if ledger is not None:
        meter.add(ledger.reserve(tool, m))
    try:
        data = transport.get(api, endpoint, dict(params))
    except ToolError as e:
        if ledger is not None and e.code == "AUTH_REQUIRED":
            ledger.adjust(tool, m, -1)  # Q-23: a failed token refresh consumes neither quota nor report budget
        raise
    except UpstreamHTTPError as e:
        if ledger is not None and e.reason in ("quotaExceeded", "dailyLimitExceeded") and e.status == 403:
            ledger.mark_exhausted()
            raise ToolError("QUOTA_RESERVE_REACHED", "The API returned quotaExceeded — the daily quota is exhausted.", "Try again after the reset (midnight PT).") from e
        if e.reason in ("rateLimitExceeded", "userRateLimitExceeded") or e.status == 429:
            # Q-10: a temporary rate limit ≠ the daily quota (the HTTP transport already backs off)
            raise ToolError("UPSTREAM_ERROR", f"Temporary API rate limit ({e.reason or e.status}).", "Try again shortly (this is not daily quota exhaustion).") from e
        if e.status in (401,):
            raise ToolError("AUTH_REQUIRED", "The API rejected the token (401).", "Run the authorization again (launch.py auth).") from e
        raise
    except MockFixtureMissing as e:
        raise ToolError("UPSTREAM_ERROR", f"MOCK: {e}", "Add a fixture in tests/fixtures or disable mock_mode") from e
    store.set(key, data, ttl_s)
    return data


# ---- language ---------------------------------------------------------------------------------
# Language-detection data: Polish/English/German stop lists, Polish diacritics and suffixes (kept as data).
_PL_CHARS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
_STOP = {
    "PL": set(("i w nie się na jest że to jak ale co z do tak czy już mi mnie bardzo tylko jestem dla po od za są ten ta być "
               "fajnie fajny fajna super spoko gra grze gry dodatek dodatku będzie bedzie jest jestem mam masz ma było bylo wreszcie "
               "dzięki dzieki dziękuję dziekuje świetny swietny świetnie swietnie słaby slaby lepiej gorzej trochę troche może moze "
               "teraz znowu czemu dlaczego kiedy wszystko nic coś cos ktoś ktos polska polski polsce polaków polakow nasz nasza "
               "eventy eventów eventow łatwo latwo trudno szkoda brawo serio xd").split()),
    "EN": set(("the and is to of it that this you for with not but are was have be on my they just great game update love "
               "awesome amazing good bad nice cool thanks thank please can't cant don't dont i'm im it's its finally really "
               "what why when how so very too much more best worst ever new patch fix fixed broken").split()),
    "DE": set("und der die das ist nicht ich mit sie es ein eine zu auf für den auch wir".split()),
}
_PL_SUFFIX = re.compile(r"\w{3,}(?:ość|osc|ego|emu|ych|ymi|ami|ach|ać|ać|ować|owac|ę|ą|ów|ców|szy|liśmy|łem|łam|cie)\b", re.I)


def detect_lang(text: str | None) -> str | None:
    """PL/EN/DE heuristic: stop lists (including colloquial comment words), Polish characters and inflectional suffixes.
    None = undetermined (e.g. "RON op") — language filters do NOT drop such comments (Q-05)."""
    if not text:
        return None
    words = re.findall(r"[\wąćęłńóśźż']+", text.lower())
    if not words:
        return None
    scores = {k: sum(1 for w in words if w in s) for k, s in _STOP.items()}
    if _PL_CHARS.search(text):
        scores["PL"] += 2
    scores["PL"] += min(2, len(_PL_SUFFIX.findall(text)))
    best = max(scores, key=lambda k: scores[k])
    if scores[best] <= 0 or list(scores.values()).count(scores[best]) > 1:
        return None
    return best


LANG_FROM_CODE = {"pl": "PL", "en": "EN", "de": "DE"}


# ---- chapters and links ------------------------------------------------------------------------
_CHAPTER = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*[-–—:]?\s+(.+?)\s*$")


def parse_chapters(description: str | None) -> list[dict]:
    out = []
    for line in (description or "").splitlines():
        m = _CHAPTER.match(line)
        if m:
            h, mi, s, title = m.groups()
            out.append({"t": int(h or 0) * 3600 + int(mi) * 60 + int(s), "title": title[:100]})
    # YouTube: chapters start at 0:00 and there are ≥ 3 of them
    if len(out) < 2 or out[0]["t"] != 0:
        return []
    return out


_URL = re.compile(r"https?://[^\s)<>\]\"']+")


def extract_links(description: str | None) -> list[str]:
    """Domain + path (no query/fragment — no tracking parameters)."""
    out = []
    for u in _URL.findall(description or ""):
        p = urlsplit(u)
        v = f"{p.netloc}{p.path}".rstrip("/")
        if v and v not in out:
            out.append(v)
    return out[:20]


def _word_match(tok: str, word: str) -> bool:
    """Word matching that tolerates Polish inflection (Q-06):
    ≤ 3 chars — whole word; 4 chars — whole word or len-1 prefix ("unia" ~ "unii", the acronym RON only literally);
    5–6 — len-1 prefix from a word boundary ("event" ~ "eventy", but not "prevent"); ≥ 7 — len-2 prefix or similarity ≥ 0.85
    with the same beginning ("Rzeczpospolita" ~ "Rzeczypospolitej")."""
    n = len(tok)
    if n <= 3 or tok.isupper() or not tok.isalpha():
        return word == tok
    if n == 4:
        return word == tok or (word.startswith(tok[:3]) and len(word) <= 6 and word[:3].isalpha() and not tok[:3].isupper() and word != tok[:3])
    if n <= 6:
        return word.startswith(tok[:-1])
    if word.startswith(tok[:-2]):
        return True
    return word[:3] == tok[:3] and SequenceMatcher(None, tok, word[: len(tok) + 3]).ratio() >= 0.85


def match_terms(text: str, terms: list[str]) -> list[str]:
    """Local term filter: words and phrases (token by token, in order, consecutive words) with light inflection."""
    if not terms:
        return []
    words = re.findall(r"\w+", fold(text))
    out = []
    for t in terms:
        raw_toks = re.findall(r"\w+", t)
        toks = [fold(x) if not (x.isupper() and len(x) <= 5) else fold(x).upper() for x in raw_toks]
        if not toks:
            continue
        # uppercase acronyms (RON, PLC, EU5) — whole word only
        toks_cmp = [(x.lower(), x.isupper()) for x in toks]
        k = len(toks_cmp)
        found = False
        for i in range(len(words) - k + 1):
            if all((words[i + j] == tk) if up or len(tk) <= 3 else _word_match(tk, words[i + j]) for j, (tk, up) in enumerate(toks_cmp)):
                found = True
                break
        if found:
            out.append(t)
    return out
