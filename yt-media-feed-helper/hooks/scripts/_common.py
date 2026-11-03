"""Shared helpers for the yt-media-feed-helper hooks — standard library ONLY (Windows/Linux/macOS).

Reading the payload from stdin (UTF-8), paths (workspace, plugin data dir), secret redaction,
JSONL writes under a lock, a minimal YAML config parser (key: value blocks), the prompt classifier.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

HOOK_VERSION = "1.1.0"
PLUGIN = "yt-media-feed-helper"
AGENT_PREFIX = f"{PLUGIN}:"
# Only the exact plugin server prefix (S-03): `mcp__ytfeed__` could belong to a foreign server from the project's .mcp.json.
OUR_SERVER_PREFIXES = (f"mcp__plugin_{PLUGIN}_ytfeed__",)
# Default report budget 2000 units; raising it (also via config.yaml) requires approval (S-01).
BUDGET_BASELINE = 2000


# ---- I/O -------------------------------------------------------------------------------------------
def setup_io() -> None:
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def read_payload() -> dict:
    setup_io()
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("hook payload is not a JSON object")
    return data


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    return "" if v.startswith("${") else v


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def short_hash(s: str | None) -> str | None:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12] if s else None


# ---- paths -----------------------------------------------------------------------------------------
def data_dir() -> Path:
    v = env("CLAUDE_PLUGIN_DATA") or env("YTFEED_DATA_DIR")
    p = Path(v) if v else Path(tempfile.gettempdir()) / f"{PLUGIN}-hooks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def workspace(payload: dict) -> Path:
    # same order as the server (ytfeed.paths.SETTINGS): environment variable > plugin option
    v = env("YTFEED_WORKSPACE") or env("CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR") or env("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    return Path(v)


def is_workspace(ws: Path) -> bool:
    return (ws / "catalog" / "taxonomy.yaml").is_file()


# ---- path normalization and the secrets dir (S-02) ---------------------------------------------------
_ENV_PATTERNS = (
    re.compile(r"\$env:([A-Za-z_][A-Za-z0-9_]*)", re.I),   # PowerShell
    re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%"),             # cmd
    re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}"),         # sh ${VAR}
    re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)"),             # sh $VAR
)


def _env_lookup(name: str) -> str | None:
    if name.upper() == "HOME" and "HOME" not in os.environ:
        return str(Path.home())
    for k, v in os.environ.items():
        if k.upper() == name.upper():
            return v
    return None


def expand(s: str) -> str:
    """Expands ~, $VAR, ${VAR}, %VAR%, $env:VAR (unknown variables are left unchanged)."""
    s = s.strip().strip("\"'")
    for rx in _ENV_PATTERNS:
        s = rx.sub(lambda m: _env_lookup(m.group(1)) if _env_lookup(m.group(1)) is not None else m.group(0), s)
    if s == "~" or s.startswith(("~/", "~\\")):
        s = str(Path.home()) + s[1:]
    if os.name == "nt":
        m = re.match(r"^/([a-zA-Z])(/.*)?$", s)  # Git Bash: /c/Users/... → C:/Users/...
        if m:
            s = f"{m.group(1).upper()}:{m.group(2) or '/'}"
        if s.startswith(("\\\\?\\", "//?/")):
            s = s[4:]
    return s


def _long_path(p: str) -> str:
    """Windows: 8.3 names (TOKEN~1.JSO) → long names for the existing path prefix."""
    if os.name != "nt" or "~" not in p:
        return p
    try:
        import ctypes

        parts = Path(p)
        probe = parts
        tail: list[str] = []
        while not probe.exists() and probe != probe.parent:
            tail.insert(0, probe.name)
            probe = probe.parent
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(str(probe), buf, 32768)  # type: ignore[attr-defined]
        if 0 < n < 32768:
            return str(Path(buf.value, *tail))
    except Exception:  # noqa: BLE001 — best effort
        pass
    return p


def norm_path(p: str, base: Path | None = None) -> str:
    """Canonical path for comparisons: variables expanded, absolute, `..`/`\\.\\` resolved, no ADS,
    8.3 names expanded, case normalized on Windows, `/` separator, no trailing `/`."""
    s = expand(p)
    if not s:
        return ""
    if os.name == "nt":
        s = re.sub(r"^([A-Za-z]:[^:]*):.*$", r"\1", s)  # NTFS alternate data streams (file:$DATA)
    path = Path(s)
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    try:
        resolved = str(path.resolve(strict=False))
    except (OSError, ValueError, RuntimeError):
        resolved = os.path.abspath(str(path))
    resolved = _long_path(resolved)
    out = os.path.normcase(resolved) if os.name == "nt" else resolved
    out = out.replace("\\", "/")
    return out.rstrip("/") or "/"


def within(p: str, root: str) -> bool:
    return bool(p and root) and (p == root or p.startswith(root.rstrip("/") + "/"))


def secrets_dirs() -> list[str]:
    """Secrets dirs as in `ytfeed.paths.secrets_dir()` (YTFEED_SECRETS_DIR, %APPDATA%, $XDG_CONFIG_HOME or ~/.config)."""
    out = []
    v = env("YTFEED_SECRETS_DIR")
    if v:
        out.append(v)
    if os.name == "nt":
        out.append(str(Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / PLUGIN))
    out.append(str(Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / PLUGIN))
    return list(dict.fromkeys(norm_path(x) for x in out if x))


def secret_files() -> list[str]:
    """Secret files outside the secrets dir: the client secret pointed to by a plugin option / variable."""
    out = []
    for name in ("YTFEED_GOOGLE_CLIENT_SECRET_FILE", "YTFEED_CLIENT_SECRET", "CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET_PATH"):
        v = env(name)
        if v:
            out.append(norm_path(v))
    return list(dict.fromkeys(out))


def protected_roots() -> list[str]:
    """Directories whose search (or a search of any ancestor) would expose secrets."""
    roots = secrets_dirs()
    for f in secret_files():
        roots.append(f.rsplit("/", 1)[0] if "/" in f else f)
    return list(dict.fromkeys(roots))


def is_secret_path(p: str) -> bool:
    return any(within(p, d) for d in secrets_dirs()) or p in secret_files()


def is_ancestor_of_secrets(p: str) -> bool:
    return any(within(r, p) for r in protected_roots())


def catalog_tampered(ws: Path) -> bool:
    """S-01: the MCP server stores the sha256 of `channels.yaml` after each of its writes (`catalog_integrity.json` in the
    data dir). A mismatch = the file was changed outside MCP (editor, shell, model)."""
    ch = ws / "catalog" / "channels.yaml"
    try:
        ref = json.loads((data_dir() / "catalog_integrity.json").read_text(encoding="utf-8")).get(norm_path(str(ch)))
    except (OSError, ValueError):
        return False
    if not ref or not ch.is_file():
        return False
    return hashlib.sha256(ch.read_bytes()).hexdigest() != ref


# ---- session state (info mode, browser) ----------------------------------------------------------------
def _session_file(session_id: str | None, kind: str) -> Path:
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id or "nosession"))[:80]
    d = data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sid}-{kind}.json"


def session_get(session_id: str | None, kind: str, max_age_s: float = 6 * 3600) -> dict:
    try:
        st = json.loads(_session_file(session_id, kind).read_text(encoding="utf-8"))
        if time.time() - float(st.get("ts", 0)) <= max_age_s:
            return st
    except (OSError, ValueError):
        pass
    return {}


def session_set(session_id: str | None, kind: str, data: dict) -> None:
    with contextlib.suppress(OSError):
        _session_file(session_id, kind).write_text(json.dumps({**data, "ts": time.time()}), encoding="utf-8")


def log_dir(payload: dict) -> Path:
    ws = workspace(payload)
    d = ws / "logs" if is_workspace(ws) else data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 5.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+b")
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.02)
            try:
                yield
            finally:
                f.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def append_jsonl(path: Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with file_lock(path.with_suffix(path.suffix + ".lock")):
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)


# ---- secret redaction (§6.5) -------------------------------------------------------------------------
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ya29\.[0-9A-Za-z_\-]+"), "ya29.[REDACTED]"),
    (re.compile(r"1//[0-9A-Za-z_\-]{20,}"), "1//[REDACTED]"),
    (re.compile(r"GOCSPX-[0-9A-Za-z_\-]+"), "GOCSPX-[REDACTED]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "AIza[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[0-9A-Za-z._\-]+"), "Bearer [REDACTED]"),
    # the whole Authorization header (Basic/Digest/…), not only the scheme word (S-09)
    (re.compile(r"(?i)(authorization\"?'?\s*[:=]\s*\"?'?)(?!Bearer \[REDACTED\])(?:basic|digest|token|negotiate|ntlm)\s+[^\s\"',}]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(?<![A-Za-z0-9])(api_key|key|access_token|refresh_token|client_secret|token|password|authorization)(\"?'?\s*[=:]\s*\"?'?)(?!\[REDACTED\])(?!Bearer \[)([^\s\"'&,}]+)"),
     r"\1\2[REDACTED]"),
    (re.compile(r"(?i)(steam[_-]?(?:web[_-]?)?api[_-]?key\W{0,3})[0-9A-F]{32}\b"), r"\1[REDACTED]"),  # Steam key (also outside key=)
    (re.compile(r"(?<![0-9A-Fa-f])[0-9A-F]{32}(?![0-9A-Fa-f])"), "[REDACTED-HEX32]"),  # bare Steam Web API key (S-09)
    (re.compile(r"(?i)[^\s\"'=]*[\\/]+((?:token|credentials)\.json|client_secret[^\s\"'\\/]*\.json)"), r"<secrets>/\1"),
]


# Environment variables holding plugin secrets — their VALUES are redacted in logs regardless of format.
SECRET_ENV_VARS = ("YTFEED_GOOGLE_CLIENT_SECRET", "CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET",
                   "YTFEED_STEAM_API_KEY", "CLAUDE_PLUGIN_OPTION_STEAM_API_KEY")


def secret_env_values() -> list[str]:
    return [v for v in (env(n) for n in SECRET_ENV_VARS) if len(v) >= 8]


def redact(s: str) -> str:
    if not isinstance(s, str) or not s:
        return s
    for v in secret_env_values():
        if v in s:
            s = s.replace(v, "[REDACTED-ENV]")
    for rx, rep in _REDACTIONS:
        s = rx.sub(rep, s)
    return s


# Keys whose values are always fully redacted (S-09) — regardless of the value format.
SECRET_KEY = re.compile(r"(?i)(pass(word|wd)?|secret|token|api[_-]?key|apikey|authorization|cookie|session[_-]?(id|key|token)|credential|private[_-]?key)$"
                        r"|^(?i:x-api-key|auth)$")


def redact_obj(obj: Any, max_str: int = 200, max_total: int = 1024) -> tuple[Any, bool]:
    """Redaction + truncation: each string ≤ max_str, the whole object ≤ max_total JSON bytes."""
    truncated = False

    def walk(o: Any, depth: int = 0) -> Any:
        nonlocal truncated
        if depth > 6:
            truncated = True
            return "…"
        if isinstance(o, str):
            r = redact(o)
            if len(r) > max_str:
                truncated = True
                return r[:max_str] + "…"
            return r
        if isinstance(o, dict):
            return {str(k): ("[REDACTED]" if SECRET_KEY.search(str(k)) and v not in (None, "", False) else walk(v, depth + 1))
                    for k, v in list(o.items())[:50]}
        if isinstance(o, (list, tuple)):
            if len(o) > 20:
                truncated = True
            return [walk(v, depth + 1) for v in list(o)[:20]]
        return o

    out = walk(obj)
    blob = json.dumps(out, ensure_ascii=False)
    if len(blob.encode("utf-8")) > max_total:
        truncated = True
        out = {"_truncated_json": redact(blob)[: max_total // 2]}
    return out, truncated


# ---- minimal YAML parser (block mappings with scalars only) -------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "guard": {"scope": "workspace_only", "strict_modes": True},
    # max_active_subagents 6 = 5 scouts + steam-checker (Q-09)
    "parallelism": {"categorizer_wave_size": 5, "scout_max_parallel": 5, "max_active_subagents": 6, "enforce_with_hook": True},
    "search": {"require_confirmation": False},
    "session_start": {"enabled": True},
    "reports": {"budget_units": 2000},
}


def _scalar(v: str) -> Any:
    v = v.strip()
    if not v:
        return None
    if v[0] in "\"'" and v[-1:] == v[0]:
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def parse_simple_yaml(text: str) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        line = re.sub(r"\s+#.*$", "", raw) if "#" in raw else raw
        if not line.strip() or line.lstrip().startswith(("#", "-")):
            continue
        m = re.match(r"^(\s*)([^:]+?):\s*(.*)$", line)
        if not m:
            continue
        indent, key, val = len(m.group(1)), m.group(2).strip().strip("\"'"), m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if val.strip() == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        elif not val.strip().startswith(("{", "[")):
            parent[key] = _scalar(val)
    return root


def load_config(ws: Path) -> dict:
    """Config from `catalog/config.yaml` — the model can edit this workspace file, so security settings
    are "tighten-only" (S-01): the file can make them stricter, never looser.
    * guard.strict_modes — always True (bypass/auto modes → deny), the file value is ignored;
    * guard.scope — `everywhere` (stricter) or the default `workspace_only`;
    * search.require_confirmation — True is stricter; False = default;
    * reports.budget_units — hook approval threshold = min(file, BUDGET_BASELINE); a higher budget always needs approval."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = ws / "catalog" / "config.yaml"
    if p.is_file():
        try:
            data = parse_simple_yaml(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            data = {}
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update({kk: vv for kk, vv in v.items() if not isinstance(vv, dict)})
            elif k not in cfg:
                cfg[k] = v
    guard = cfg.setdefault("guard", {})
    guard["strict_modes"] = True
    guard["scope"] = "everywhere" if str(guard.get("scope")) == "everywhere" else "workspace_only"
    cfg.setdefault("search", {})["require_confirmation"] = cfg["search"].get("require_confirmation") is True
    try:
        configured = int(float(str(cfg.get("reports", {}).get("budget_units", BUDGET_BASELINE))))
    except (TypeError, ValueError):
        configured = BUDGET_BASELINE
    # Round 2 (§5a-2): a permanently raised budget only from userConfig.report_budget_units (out of the model's reach);
    # config.yaml can only lower it (value < 2000). Same rule on the server side (quota.effective_budget).
    cfg.setdefault("reports", {})["budget_units_configured"] = configured
    base = budget_baseline()
    cfg["reports"]["budget_threshold"] = configured if 0 < configured < BUDGET_BASELINE else base
    return cfg


def budget_baseline() -> int:
    try:
        n = int(float(env("YTFEED_REPORT_BUDGET_UNITS") or env("CLAUDE_PLUGIN_OPTION_REPORT_BUDGET_UNITS") or 0))
    except ValueError:
        n = 0
    return n if n > 0 else BUDGET_BASELINE


# ---- prompt classification (§6.4) ----------------------------------------------------------------------
# Trigger keywords are matched on folded text (lowercase, no diacritics). Polish and English keywords are both
# functional data: users may ask in either language.
def fold(s: str) -> str:
    s = s.replace("ł", "l").replace("Ł", "L")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


REPORT_TRIGGER = re.compile(
    r"\b(raport\w*|podsumuj\w*|podsumowani\w*|przeglad\w*|przejrzyj|sprawdz\w*|odbior\w*|opini\w*|najnowsze|ogloszeni\w*|"
    r"informacj\w* (o|na temat)|co nowego|newsy|news|patch\w*|"
    r"report\w*|summar\w*|review\w*|check\w*|latest|what'?s new|reception|announcement\w*|opinion\w*|community|overview|recap)\b"
    r"|/yt-media-feed-helper:(report|review-categories|catalog-sync|categorize)"
)
MUTATION_INTENT = re.compile(
    r"\b(zasubskrybuj|subskrybuj|odsubskrybuj|skomentuj|napisz komentarz|polub|wesprzyj|dolacz|kup|dodaj do listy zyczen|"
    r"subscribe|unsubscribe|comment on|post a comment|like (the|this)|join (the|this)|buy|wishlist)\b"
)
DOMAIN_HINT = re.compile(r"\b(youtube|yt|steam|kanal\w*|tworc\w*|creator\w*|channel\w*|film\w*|video\w*|gr[aey]\b|gier|game\w*|dlc|"
                         r"katalog\w*|catalog|subskrypc\w*|subscription\w*)")


def taxonomy_terms(ws: Path) -> list[str]:
    """Value names and synonyms from taxonomy.yaml (regex, no PyYAML); cached 60 s in the data dir."""
    tax = ws / "catalog" / "taxonomy.yaml"
    if not tax.is_file():
        return []
    cache = data_dir() / "taxonomy_terms.json"
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("path") == str(tax) and time.time() - c.get("at", 0) < 60 and c.get("mtime") == tax.stat().st_mtime:
            return c["terms"]
    except (OSError, ValueError, KeyError):
        pass
    text = tax.read_text(encoding="utf-8")
    terms = set()
    for m in re.finditer(r"key:\s*([^\n,}]+)", text):
        terms.add(m.group(1).strip().strip("\"'"))
    for m in re.finditer(r"synonyms:\s*\[([^\]]*)\]", text):
        terms.update(x.strip().strip("\"'") for x in m.group(1).split(","))
    for m in re.finditer(r"^\s{2}([^\s:#][^:]*):\s*\{\s*dimension", text, re.M):
        terms.add(m.group(1).strip())
    out = sorted(t for t in terms if len(t) >= 3)
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps({"path": str(tax), "at": time.time(), "mtime": tax.stat().st_mtime, "terms": out}), encoding="utf-8")
    return out


def classify_prompt(prompt: str, ws: Path) -> dict:
    f = fold(prompt or "")
    in_ws = is_workspace(ws)
    explicit = "yt-media-feed-helper" in f
    domain = bool(DOMAIN_HINT.search(f)) or explicit
    report = bool(REPORT_TRIGGER.search(f))
    if not report and in_ws:
        for t in taxonomy_terms(ws):
            ft = fold(t)
            if len(ft) >= 3 and re.search(rf"(?<!\w){re.escape(ft)}", f):
                report = True
                break
    if report and not in_ws and not domain:
        report = False  # outside the workspace react only to questions about YouTube/Steam/channels
    mutation = bool(MUTATION_INTENT.search(f)) and (domain or in_ws)
    return {"report": report, "mutation": mutation}
