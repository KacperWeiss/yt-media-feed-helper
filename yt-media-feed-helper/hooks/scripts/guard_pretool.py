#!/usr/bin/env python3
"""PreToolUse — the yt-media-feed-helper guard (§6.3). Stdlib only.

Returns only `deny`/`ask` or nothing (never `allow` — it never widens permissions).
* subagent (payload has `agent_id`): every `ask` → `deny` (a subagent cannot obtain human approval);
* `ask` is possible only in default/plan/acceptEdits modes (missing field = default); bypassPermissions/dontAsk/auto
  and any unknown mode → `deny` "approve in normal mode" (S-16). This cannot be disabled by a workspace
  file (S-01) — `guard.strict_modes` from config.yaml is ignored;
* within one tool family `deny` rules are checked before `ask` (deny > ask);
* an ambiguous argument of a gated tool = `ask` (fail-closed, S-05).
Fail-closed: exception or empty stdin → stderr + exit code 2 (block), plus `onFailure: block` in hooks.json.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

ASK_MODES = {"default", "plan", "acceptEdits"}
CATEGORIZER = f"{C.AGENT_PREFIX}channel-categorizer"
OURS = C.OUR_SERVER_PREFIXES[0]

HTTP_CLIENT = re.compile(
    r"(?<![\w-])(curl|wget|httpie|xh|invoke-webrequest|invoke-restmethod|start-bitstransfer|aria2c)(?![\w-])"
    r"|(?:^|[\s;|&(])https?\s+(?:get|post|put|patch|delete|:|-)"
    r"|\b(?:python3?|py)(?:\.exe)?\s+(?:-\d\s+)?-c\b.*\b(requests|httpx|urllib|http\.client|aiohttp)"
    r"|\b(?:python3?|py)(?:\.exe)?\s+-\s*(?:<<|$)"          # heredoc / stdin: python3 - <<EOF (S-12)
    r"|\b(requests|httpx|aiohttp)\.(?:get|post|put|patch|delete|request)\s*\("
    r"|\burllib\.request\b|\burlopen\s*\("
    r"|\bnode(?:\.exe)?\s+-e\b.*\b(fetch|axios|https?\.request)"
    r"|\bgh(?:\.exe)?\s+api\b"                                # gh api -X POST … (S-12)
)
# YouTube/Google/Steam hosts; also concatenated fragments ("googleapis", "steampowered" without .com) — S-12
HOSTS = re.compile(r"(googleapis|youtube\.com|youtu\.be|google\.com/o?auth|accounts\.google\.com|steampowered|steamcommunity)", re.I)
BROWSER_HOSTS = re.compile(r"(youtube\.com|youtu\.be|googleapis\.com|steampowered\.com|steamcommunity\.com|accounts\.google\.com)", re.I)
MUTATION = re.compile(
    r"(?:-X|--request)\s*=?\s*['\"]?(?i:post|put|patch|delete)\b"
    r"|(?:^|\s)(?:-d|--data(?:-raw|-binary|-urlencode)?|--form|-F|--json|--upload-file|-T)(?:\s|=|$)"
    r"|(?i:(?:^|\s)-me(?:t(?:h(?:od?)?)?)?[:\s]+['\"]?(?:post|put|patch|delete))|(?i:(?:^|\s)-body\b)"
    r"|(?i:--post-data|--post-file|--method[=\s]+['\"]?(?:post|put|patch|delete))"
    r"|(?i:\.(?:post|put|patch|delete)\s*\()|(?i:method\s*[=:]\s*['\"]?(?:post|put|patch|delete))"
    r"|(?i:x-http-method-override)|(?i:\bdata\s*=\s*(?:b?['\"]|\{|json))"
)
SECRETS = re.compile(
    r"token\.json|client_secret[\w.\-]*\.json|credentials\.json"
    r"|(?:appdata[\\/]+roaming|%appdata%|\$env:appdata|\$appdata|\.config)[\\/]+yt-media-feed-helper"
    r"|ytfeed_steam_api_key|claude_plugin_option_steam_api_key|ytfeed_client_secret|claude_plugin_option_google_client_secret_path"
    r"|ytfeed_google_client_secret\w*|claude_plugin_option_google_client_secret\w*",
    re.I,
)
# Dumping the whole environment would expose secrets passed via variables (YTFEED_GOOGLE_CLIENT_SECRET, Steam key).
ENV_DUMP = re.compile(
    r"(?:^|[;&|(`]\s*)(?:env|printenv|set|export\s+-p|declare\s+-x|declare\s+-p|compgen\s+-[ev]|typeset\s+-x)\s*(?:$|[;&|)>`])"
    r"|(?:get-childitem|gci|ls|dir|get-item|gi|get-content|gc|type|cat)\s+['\"]?env:(?:[\\/]?\*|[\\/]?['\"]?(?:\s|$|[;&|)]))"
    r"|\bget-childitem\s+-path\s+['\"]?env:|\[(?:system\.)?environment\]::getenvironmentvariables\b"
    r"|\bos\.environ\b(?!\s*(?:\.get\s*\(|\[))|\bprocess\.env\b(?!\s*(?:\.|\[))"
    r"|/proc/(?:self|\d+)/environ|\bwmic\s+environment\b|\bgetenv\s*\(\s*\)",
    re.IGNORECASE,
)
SECRET_VALUES = re.compile(r"refresh_token|ya29\.|GOCSPX-|(?<![\w/])1//0[\w-]", re.I)
SECRETS_PATHLIKE = re.compile(r"(^|[\\/])(token\.json|token\.tmp|client_secret[^\\/]*|credentials\.json)$", re.I)
PLUGIN_DIR_TOKEN = re.compile(r"(?:^|[\\/'\"\s(=])yt-media-feed-helper(?:[\\/'\")]|\s|$)", re.I)
APPDATA_HINT = re.compile(r"appdata|roaming|\.config\b|xdg_config_home", re.I)
RECURSIVE = re.compile(
    r"\b(?:e|f)?grep\b[^|;&]*\s(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|--dereference-recursive)(?:\s|$)"
    r"|(?<![\w-])(?:rg|ag|ack|fd|find|tree|findstr|robocopy|xcopy|du|tar|zip|7z|compress-archive)(?![\w-])"
    r"|(?:get-childitem|(?<![\w-])gci|(?<![\w-])ls|(?<![\w-])dir)\b[^|;&]*\s-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?\b"
    r"|(?<![\w-])dir\b[^|;&]*\s/s\b|\bcp\b[^|;&]*\s-[a-zA-Z]*[rR]|copy-item[^|;&]*-recurse",
    re.I,
)
B64_EXEC = re.compile(r"(base64\s+(-d|--decode|-D)|frombase64string|certutil\s+-decode)[^\n]*\|\s*(sh|bash|zsh|pwsh|powershell|iex|invoke-expression|python3?|node)\b", re.I)
OTHER_MCP_MUTATION = re.compile(
    r"(?i)(insert|update|delete|remove|create|subscribe|unsubscribe|comment|reply|post|publish|rate|like|dislike|upload|set_|write|send|"
    r"purchase|buy|wishlist|follow|join|sponsor|member|donate|moderat|ban|commit|approve|accept|grant|confirm|authori[sz]e|checkout|pay)"
)
BROWSER_SERVER = re.compile(r"(?i)chrome|browser|playwright|puppeteer|computer|desktop|selenium|webdriver|automation|remote-devices")
BROWSER_ACTION = re.compile(r"(?i)click|type|fill|form|press|key|select|submit|javascript|evaluate|execute|script|drag|upload|hover|input|act$|^act_")
EXEC_TOOL = re.compile(r"(?i)shell|exec|command|terminal|run_|bash|powershell|cmd|process|fetch|http|request|curl")
# Mutation keywords in English and Polish (detection data — users and pages may use either language).
MUTATION_WORDS = re.compile(r"subscri|subskryb|comment|komentarz|\blike\b|polub|\bjoin\b|dołącz|dolacz|wesprzyj|sponsor|members?hip|"
                            r"\bbuy\b|\bkup\b|purchase|wishlist|add to cart|koszyk|publish|opublikuj|follow|obserwuj")
TOOLING = re.compile(r"(?<![\w-])(gcloud|yt-dlp|youtube-dl|steamcmd)(?![\w-])")
TOOLING_MUT = re.compile(r"\b(upload|delete|create|update|set|add|remove|post|publish|subscribe|comment|rate|like|login|auth)\b")
WEBFETCH_MUT = re.compile(r"(subscription_center|add_user|sub_confirmation|/comment|action_|/subscribe|/like|/join|/sponsor|addtocart|wishlist|/follow|/rate)", re.I)
WEBFETCH_API = re.compile(r"(googleapis\.com/youtube/v3|api\.steampowered\.com)", re.I)
WEBFETCH_ALLOW = re.compile(r"^https?://([\w-]+\.)*(youtube\.com|youtu\.be|steampowered\.com|steamcommunity\.com|claude\.com|anthropic\.com)(/|$)", re.I)
# S-01: catalog files are changed only via the controlled MCP tools (Write/Edit/shell → ask/deny)
CATALOG_FILE = re.compile(r"/catalog/(?:(?:channels|taxonomy|config)\.ya?ml|history\.jsonl|proposals(?:/.*)?)$")
CATALOG_SHELL = re.compile(r"catalog[\\/]+(?:(?:channels|taxonomy|config)\.ya?ml|history\.jsonl|proposals)|validate_catalog\.py[^|;&]*--fix", re.I)
SHELL_WRITE = re.compile(
    r"(?:^|[^<>\d&=-])>{1,2}(?![&=])|\bsed\b[^|;&]*\s-i|\btee\b|set-content|add-content|out-file|clear-content|copy-item|move-item|remove-item|"
    r"rename-item|new-item|(?<![\w.-])(?:mv|cp|rm|del|erase|copy|move|ren|rename|truncate|dd|perl|ruby|node|awk|py)(?![\w.-])|"
    r"\bpython\d?(?:\.exe)?\b|writealltext|appendalltext|\[io\.file\]|--fix|\bgit\s+(?:checkout|restore|apply|stash|reset)\b",
    re.I,
)
AUDIT_LOG = re.compile(r"/logs/(?:guard|diag)-[^/]*\.jsonl(?:\.lock)?$")


class Ctx:
    def __init__(self, payload: dict):
        self.p = payload
        self.tool = str(payload.get("tool_name", ""))
        inp = payload.get("tool_input")
        if inp is None:
            inp = {}
        if not isinstance(inp, dict):
            raise ValueError("tool_input is not a JSON object")
        self.inp = inp
        self.is_sub = bool(payload.get("agent_id"))
        self.agent_type = str(payload.get("agent_type") or "")
        self.mode = str(payload.get("permission_mode") or "default")
        self.session = payload.get("session_id")
        self.ws = C.workspace(payload)
        self.cwd = Path(str(payload.get("cwd") or self.ws))
        self.cfg = C.load_config(self.ws)


def _decode_b64(cmd: str) -> str:
    """S-12: base64 literals in the command are decoded and added to the analysis (echo …== | base64 -d | sh)."""
    import base64
    import binascii

    out = []
    for tok in re.findall(r"[A-Za-z0-9+/]{20,}={0,2}", cmd)[:10]:
        try:
            dec = base64.b64decode(tok + "=" * (-len(tok) % 4), validate=False).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if dec.isprintable():
            out.append(dec)
    return (cmd + " ; " + " ; ".join(out)) if out else cmd


def normalize_cmd(cmd: str) -> tuple[str, str]:
    """(raw without escapes and string concatenations + decoded base64, lowercase with PowerShell aliases expanded)."""
    raw = _decode_b64(cmd).replace("^", "").replace("`", "")
    raw = re.sub(r"(['\"])\s*\+\s*\1", "", raw)  # 'https://www.google'+'apis.com' → joined (S-12)
    raw = re.sub(r"\s+", " ", raw)
    low = raw.lower()
    low = re.sub(r"(?<![\w-])iwr(?![\w-])", "invoke-webrequest", low)
    low = re.sub(r"(?<![\w-])irm(?![\w-])", "invoke-restmethod", low)
    low = re.sub(r"(?<![\w-])curl\.exe(?![\w-])", "curl", low)
    low = re.sub(r"(?<![\w-])wget\.exe(?![\w-])", "wget", low)
    return raw, low


def split_mcp(name: str) -> tuple[str, str, bool]:
    rest = name[len("mcp__"):]
    server, _, tool = rest.partition("__")
    return server, tool, name.startswith(OURS)


def _clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- R1: approval text for catalog changes (S-07) ------------------------------------------------------------
def _channel_title(c: Ctx, cid: str) -> str:
    try:
        text = (c.ws / "catalog" / "channels.yaml").read_text(encoding="utf-8")
        m = re.search(rf"^  ['\"]?{re.escape(cid)}['\"]?:\s*\n\s+title:\s*(.+)$", text, re.M)
        return _clip(m.group(1).strip("'\""), 40) if m else ""
    except (OSError, re.error):
        return ""


def _proposal_values(c: Ctx, cid: str) -> str:
    safe = "".join(ch for ch in cid if ch.isalnum() or ch in "-_")
    try:
        prop = json.loads((c.ws / "catalog" / "proposals" / f"{safe}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "(proposal not found)"
    vals = []
    for dim, items in (prop.get("proposal") or {}).items():
        if isinstance(items, list):
            vals.append(f"{dim}=" + "/".join(str(i.get("value")) for i in items if isinstance(i, dict)))
    for nv in prop.get("new_values") or []:
        if isinstance(nv, dict):
            vals.append(f"★{nv.get('dimension')}={nv.get('value')}")
    return ", ".join(vals) or "(empty)"


def describe_commit(c: Ctx) -> str:
    decs = c.inp.get("decisions")
    parts: list[str] = []
    if decs is not None and not isinstance(decs, list):
        parts.append(f"decisions: invalid type ({type(decs).__name__})")
        decs = []
    for d in decs or []:
        if not isinstance(d, dict):
            parts.append(f"? {_clip(json.dumps(d, ensure_ascii=False), 60)}")
            continue
        pid = str(d.get("proposal_id") or "?")
        cid = pid.split("@", 1)[0]
        who = f"{cid}" + (f" ({_channel_title(c, cid)})" if _channel_title(c, cid) else "")
        action = str(d.get("action"))
        if action == "reject":
            parts.append(f"REJECT {who}")
            continue
        s = f"{'ACCEPT' if action == 'accept' else action.upper()} {who}: {_proposal_values(c, cid)}"
        ov = d.get("overrides")
        if ov:
            s += " + CORRECTIONS (user, locked): " + _clip(json.dumps(ov, ensure_ascii=False), 200)
        parts.append(s)
    for nv in c.inp.get("approve_new_values") or []:
        if isinstance(nv, dict):
            parts.append(f"NEW VALUE {nv.get('dimension')}={nv.get('value')}" + (f" (parent: {nv.get('parent')})" if nv.get("parent") else ""))
    for sa in c.inp.get("set_steam_appids") or []:
        if isinstance(sa, dict):
            parts.append(f"STEAM APPID {sa.get('value')} = {sa.get('appids')}")
    out = ""
    for i, p in enumerate(parts):
        if len(out) + len(p) > 1500:
            out += f" … +{len(parts) - i} more"
            break
        out += ("; " if out else "") + p
    return out or "(no decisions)"


def _budget(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def mcp_rules(c: Ctx):
    server, tool, ours = split_mcp(c.tool)
    if ours:
        if tool == "catalog_commit":
            return "ask", ("yt-media-feed-helper: catalog_commit will change the LOCAL catalog (nothing is sent to YouTube/Steam). "
                           f"You are approving: {describe_commit(c)}"), "R1"
        if tool == "catalog_set_manual":
            return "ask", (f"yt-media-feed-helper: manual change of the LOCAL catalog for {_clip(str(c.inp.get('channel_id')), 40)}: "
                           f"set={_clip(json.dumps(c.inp.get('set') or {}, ensure_ascii=False), 300)}, unlock={_clip(json.dumps(c.inp.get('unlock') or []), 100)}."), "R1"
        if tool == "catalog_add_channel":
            return "ask", (f"yt-media-feed-helper: add channel {_clip(str(c.inp.get('channel')), 60)} to the LOCAL catalog "
                           "(channels.list read, 1 unit; nothing is subscribed on YouTube)."), "R1"
        if tool == "catalog_sync_subscriptions" and "dry_run" in c.inp and c.inp.get("dry_run") is not True:
            v = c.inp.get("dry_run")
            extra = "" if v is False else f" [ambiguous dry_run={_clip(json.dumps(v), 30)} treated as a write]"
            return "ask", "yt-media-feed-helper: write subscription sync results to the LOCAL catalog (channels.yaml). Read-only on YouTube." + extra, "R1"
        if tool == "quota_estimate":
            thr = int(c.cfg["reports"]["budget_threshold"])
            if "budget_units" in c.inp and c.inp.get("budget_units") is not None:
                raw = c.inp.get("budget_units")
                b = _budget(raw)
                if b is None or not isinstance(raw, int):
                    return "ask", f"yt-media-feed-helper: ambiguous budget_units value={_clip(json.dumps(raw), 40)} — requires your approval.", "R1b"
                if b > thr:
                    return "ask", f"yt-media-feed-helper: raising the report quota budget to {int(b)} units (threshold {thr} units) — requires your approval.", "R1b"
            elif c.inp.get("begin_report") not in (None, False, True):
                return "ask", f"yt-media-feed-helper: ambiguous begin_report={_clip(json.dumps(c.inp.get('begin_report')), 30)}.", "R1b"
        if tool == "report_save" and "strict" in c.inp and c.inp.get("strict") is not True:
            return "ask", "yt-media-feed-helper: report_save(strict=false) disables the [[ref]] source requirement for claims and quotes.", "R16"
        if tool == "yt_search":
            if c.is_sub:
                return "deny", "yt-media-feed-helper: yt_search is available only to the main agent.", "R2"
            if c.cfg.get("search", {}).get("require_confirmation"):
                return "ask", f"yt-media-feed-helper: search.list costs 100 units; query: {_clip(str(c.inp.get('query')), 120)}", "R2"
            return None
        if tool == "catalog_propose" and c.is_sub and c.agent_type != CATEGORIZER:
            return "deny", f"yt-media-feed-helper: catalog_propose is allowed only for {CATEGORIZER}.", "R3"
        return None
    return foreign_mcp_rules(c, server, tool)


# ---- foreign MCP: browser, computer-use, shell, fetch (S-03) ---------------------------------------------
def _strings(o, depth: int = 0):
    if depth > 6:
        return
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v, depth + 1)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _strings(v, depth + 1)


def foreign_mcp_rules(c: Ctx, server: str, tool: str):
    blob = json.dumps(c.inp, ensure_ascii=False)
    raw, low = normalize_cmd(blob)
    # deny: secrets (paths/values) in the arguments of any foreign tool
    if SECRETS.search(low) or SECRET_VALUES.search(raw) or any(C.is_secret_path(C.norm_path(s, c.cwd)) for s in _strings(c.inp)
                                                               if re.search(r"[\\/]", s) and len(s) < 1024):
        return "deny", f"yt-media-feed-helper: arguments of '{c.tool}' point to OAuth secrets / API keys — blocked.", "R8"
    browser = bool(BROWSER_SERVER.search(server) or BROWSER_SERVER.search(tool))
    urls = [s for s in _strings(c.inp) if re.match(r"(?i)^\s*(https?://|www\.|[\w-]+\.(com|be|net|org|pl)\b)", s)]
    on_yt = bool(BROWSER_HOSTS.search(low))
    if browser and urls:
        C.session_set(c.session, "browser", {"yt": on_yt, "url": _clip(urls[0], 120)})  # state recorded even on deny
    # deny: mutating HTTP / action URLs in the arguments (e.g. an MCP with a shell command)
    http = bool(HTTP_CLIENT.search(low)) or bool(EXEC_TOOL.search(tool))
    if HOSTS.search(low) and ((http and MUTATION.search(raw)) or WEBFETCH_MUT.search(low)):
        return "deny", f"yt-media-feed-helper: '{c.tool}' — a mutating request / action (subscribe, comment, purchase) on YouTube/Google/Steam. The plugin is read-only.", "R6"
    # browser / computer-use: human approval for actions on YouTube/Steam (main agent ask; subagent and strict modes deny)
    if browser:
        if on_yt:
            return "ask", (f"yt-media-feed-helper: browser/computer action '{tool}' on a YouTube/Google/Steam page. The plugin is read-only — "
                           "approve only if you asked for it yourself (no subscriptions, comments, likes, purchases)."), "R4b"
        action = " ".join(str(c.inp.get(k) or "") for k in ("action", "type", "kind"))
        if MUTATION_WORDS.search(low):
            return "ask", (f"yt-media-feed-helper: browser/computer action '{tool}' looks like a subscribe/comment/like/purchase "
                           f"({_clip(blob, 120)}). The plugin is read-only — approve only your own action."), "R4b"
        if not urls and (BROWSER_ACTION.search(tool) or BROWSER_ACTION.search(action)) and C.session_get(c.session, "browser").get("yt"):
            return "ask", (f"yt-media-feed-helper: interaction '{tool}' in a browser that last opened YouTube/Steam "
                           f"({C.session_get(c.session, 'browser').get('url')}). Approve only if it is your action."), "R4b"
    if OTHER_MCP_MUTATION.search(tool):
        return "ask", f"MCP tool '{c.tool}' looks like a write/mutation operation. yt-media-feed-helper runs in read-only mode.", "R4"
    if re.search(r"(?i)youtube|google|steam", c.tool):
        return "ask", f"Unknown MCP server related to YouTube/Steam ('{server}'). Use the ytfeed tools.", "R5"
    if HOSTS.search(low) and http:
        return "ask", f"yt-media-feed-helper: '{c.tool}' calls YouTube/Google/Steam bypassing the ytfeed server (quota, sanitization).", "R7"
    return None


# ---- shell ------------------------------------------------------------------------------------------------
_HARMLESS_REDIR = re.compile(r"\d?>\s*&\s*\d|\d?>>?\s*(?:/dev/null|\$null|nul)\b", re.I)


def _path_tokens(raw: str) -> list[str]:
    toks = re.split(r"[\s;|&<>(),=`]+", raw.replace('"', " ").replace("'", " "))
    return [t for t in toks if t and not t.startswith("-") and (re.search(r"[\\/~$%]", t) or t in (".", ".."))]


def secret_shell_rules(c: Ctx, raw: str, low: str):
    if SECRETS.search(low) or SECRET_VALUES.search(raw):
        return "deny", "yt-media-feed-helper: access to OAuth secrets / API keys is blocked.", "R8"
    if C.secret_env_values() and ENV_DUMP.search(low):
        return "deny", ("yt-media-feed-helper: dumping environment variables is blocked — they contain plugin secrets "
                        "(YTFEED_GOOGLE_CLIENT_SECRET / Steam key). Read a specific non-secret variable instead."), "R8"
    if PLUGIN_DIR_TOKEN.search(low) and APPDATA_HINT.search(low):
        return "deny", "yt-media-feed-helper: access to the plugin secrets dir (%APPDATA%/yt-media-feed-helper or ~/.config/yt-media-feed-helper) is blocked.", "R8"
    recursive = bool(RECURSIVE.search(low))
    toks = _path_tokens(raw)
    for t in toks:
        p = C.norm_path(t, c.cwd)
        if C.is_secret_path(p):
            return "deny", "yt-media-feed-helper: reading secret files (token, client_secret) is blocked.", "R8"
        if recursive and C.is_ancestor_of_secrets(p):
            return "deny", f"yt-media-feed-helper: recursive search/copy of '{_clip(t, 60)}' covers the plugin secrets dir — blocked.", "R8"
    if recursive and not toks and C.is_ancestor_of_secrets(C.norm_path(str(c.cwd))):
        return "deny", "yt-media-feed-helper: recursive search of the current directory covers the plugin secrets dir — blocked.", "R8"
    return None


def shell_rules(c: Ctx):
    cmd = str(c.inp.get("command") or "")
    raw, low = normalize_cmd(cmd)
    http = bool(HTTP_CLIENT.search(low))
    host = bool(HOSTS.search(low))
    # deny first
    if http and host and MUTATION.search(raw):
        return "deny", "yt-media-feed-helper: blocked a mutating HTTP request to YouTube/Google/Steam (read-only mode).", "R6"
    sec = secret_shell_rules(c, raw, low)
    if sec:
        return sec
    if CATALOG_SHELL.search(low) and SHELL_WRITE.search(_HARMLESS_REDIR.sub(" ", low)):
        return "ask", (f"yt-media-feed-helper: the command may change the LOCAL catalog (catalog/*.yaml, proposals, history) bypassing "
                       f"the MCP tools: {_clip(C.redact(cmd), 300)}"), "R14"
    if http and host:
        return "ask", "yt-media-feed-helper: this bypasses the MCP server (quota ledger, sanitization). Use the ytfeed tools.", "R7"
    if re.search(r"launch\.py[\"']?\s+auth\b", low):
        return "ask", "yt-media-feed-helper: run the OAuth authorization YOURSELF in your terminal (it opens a browser).", "R9"
    if (TOOLING.search(low) and TOOLING_MUT.search(low)) or re.search(r"\bgit\s+push\b", low):
        return "ask", "yt-media-feed-helper: the command may change external resources — confirm.", "R10"
    if B64_EXEC.search(low):
        return "ask", "yt-media-feed-helper: executing a decoded (base64) command — content invisible to the guard, confirm.", "R10"
    return None


# ---- Read/Grep/Glob (S-02) ---------------------------------------------------------------------------------
def _glob_root(pattern: str) -> str:
    parts = re.split(r"[\\/]", pattern)
    static = []
    for part in parts:
        if re.search(r"[*?\[{]", part):
            break
        static.append(part)
    return "/".join(static)


def read_rules(c: Ctx):
    deny = ("deny", "yt-media-feed-helper: reading secret files (token, client_secret) is blocked.", "R8")
    keys = ("file_path", "notebook_path", "path", "glob", "pattern" if c.tool == "Glob" else "")
    vals = [str(c.inp.get(k) or "") for k in keys if k]
    for v in vals:
        if not v:
            continue
        n = v.replace("\\", "/")
        if SECRETS.search(n) or SECRETS_PATHLIKE.search(n) or SECRETS.search(C.expand(v)):
            return deny
    for k in ("file_path", "notebook_path"):
        if c.inp.get(k) and C.is_secret_path(C.norm_path(str(c.inp[k]), c.cwd)):
            return deny
    if c.tool in ("Grep", "Glob"):
        roots = [str(c.inp.get("path") or "") or str(c.cwd)]
        if c.tool == "Glob" and c.inp.get("pattern"):
            g = _glob_root(C.expand(str(c.inp["pattern"])))
            if g and (Path(g).is_absolute() or g.startswith(("~", "$", "%"))):
                roots.append(g)
        for r in roots:
            p = C.norm_path(r, c.cwd)
            if C.is_secret_path(p):
                return deny
            if C.is_ancestor_of_secrets(p):
                return "deny", (f"yt-media-feed-helper: searching '{_clip(r, 80)}' covers the plugin secrets dir "
                                "(OAuth token, client secret). Narrow the path."), "R8"
    return None


# ---- WebFetch (R11/R12 + S-13) -----------------------------------------------------------------------------
def webfetch_rules(c: Ctx):
    url = str(c.inp.get("url") or "")
    if HOSTS.search(url) and WEBFETCH_MUT.search(url):
        return "deny", "yt-media-feed-helper: the URL looks like an action (subscribe/comment/purchase) — blocked.", "R11"
    if WEBFETCH_API.search(url):
        return "ask", "yt-media-feed-helper: a direct API call bypasses MCP (quota, sanitization). Use the ytfeed tools.", "R12"
    if C.session_get(c.session, "info").get("info_mode") and not WEBFETCH_ALLOW.match(url) and ("?" in url or len(url) > 200):
        return "ask", (f"yt-media-feed-helper: info mode — fetching a URL with parameters outside YouTube/Steam ({_clip(url, 120)}) "
                       "could exfiltrate report/catalog data. Approve only a known address."), "R17"
    return None


# ---- Write/Edit (R13/R14/R18) ------------------------------------------------------------------------------
def _edit_preview(c: Ctx) -> str:
    if c.inp.get("old_string") is not None or c.inp.get("new_string") is not None:
        return f" Change: «{_clip(C.redact(str(c.inp.get('old_string') or '')), 200)}» → «{_clip(C.redact(str(c.inp.get('new_string') or '')), 300)}»"
    if isinstance(c.inp.get("edits"), list):
        return f" Edits: {len(c.inp['edits'])}; first → «{_clip(C.redact(str((c.inp['edits'][0] or {}).get('new_string', ''))), 300)}»" if c.inp["edits"] else ""
    if c.inp.get("content") is not None:
        return f" New content (excerpt): «{_clip(C.redact(str(c.inp.get('content'))), 400)}»"
    return ""


def write_rules(c: Ctx):
    target = str(c.inp.get("file_path") or c.inp.get("notebook_path") or "")
    if not target:
        return None
    t = C.norm_path(target, c.cwd)
    ws = C.norm_path(str(c.ws), c.cwd)
    if C.is_secret_path(t):
        return "deny", "yt-media-feed-helper: writing to the plugin secrets dir is blocked.", "R8"
    m = CATALOG_FILE.search(t)
    if m:
        cat_dir = t[: m.start()] + "/catalog"
        if within(t, ws + "/catalog") or (Path(cat_dir) / "taxonomy.yaml").is_file():
            if c.is_sub:
                return "deny", "yt-media-feed-helper: subagents do not edit the catalog/taxonomy.", "R14"
            return "ask", (f"yt-media-feed-helper: direct edit of the LOCAL catalog ({target}) bypassing MCP — "
                           "approve only a change you asked for." + _edit_preview(c)), "R14"
    if AUDIT_LOG.search(t):
        return "ask", f"yt-media-feed-helper: modifying the audit/diagnostic log ({target}).", "R18"
    scope = str(c.cfg.get("guard", {}).get("scope", "workspace_only"))
    if scope != "everywhere" and not C.is_workspace(c.ws):
        return None
    allowed_dirs = [f"{ws}/reports", f"{ws}/logs"]
    if any(within(t, d) for d in allowed_dirs):
        return None
    return "ask", f"yt-media-feed-helper: write outside the allowed workspace paths: {target}", "R13"


def within(p: str, root: str) -> bool:
    return C.within(p, root)


# ---- Agent (R15, Q-09) ---------------------------------------------------------------------------------------
def agent_rules(c: Ctx):
    sub = str(c.inp.get("subagent_type") or "")
    par = c.cfg.get("parallelism", {})
    if not sub.startswith(C.AGENT_PREFIX) or not par.get("enforce_with_hook", True):
        return None
    try:
        limit = int(par.get("max_active_subagents") or 0) or int(par.get("scout_max_parallel") or 5) + 1
    except (TypeError, ValueError):
        limit = 6
    state = C.data_dir() / "subagents.json"
    try:
        active = json.loads(state.read_text(encoding="utf-8")).get("active", {})
    except (OSError, ValueError):
        active = {}
    now = time.time()
    alive = [a for a in active.values() if now - float(a.get("started_at_ts", 0)) < 15 * 60
             and (not c.session or not a.get("session_id") or a.get("session_id") == c.session)]
    if len(alive) >= limit:
        return "deny", f"yt-media-feed-helper: wave limit of {limit} active subagents in this session; wait for the current ones to finish.", "R15"
    return None


def evaluate(c: Ctx):
    t = c.tool
    if t.startswith("mcp__"):
        return mcp_rules(c)
    if t in ("Bash", "PowerShell"):
        return shell_rules(c)
    if t in ("Read", "Grep", "Glob", "NotebookRead"):
        return read_rules(c)
    if t == "WebFetch":
        return webfetch_rules(c)
    if t in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return write_rules(c)
    if t in ("Agent", "Task"):
        return agent_rules(c)
    return None


def main() -> int:
    C.setup_io()
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("empty PreToolUse payload")  # S-16: fail-closed
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("hook payload is not a JSON object")
    c = Ctx(payload)
    res = evaluate(c)
    if not res:
        return 0
    level, reason, rule = res
    final = level
    if level == "ask" and c.is_sub:
        final = "deny"
        reason += " [subagent: human approval is not possible → deny]"
    elif level == "ask" and c.mode not in ASK_MODES:
        final = "deny"
        reason += f" [mode {c.mode}: approve in normal mode]"
    C.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": final, "permissionDecisionReason": reason}})
    try:
        rec_args, _ = C.redact_obj(c.inp)
        rec = {
            "ts": C.now_iso(), "rule": rule, "decision": final, "requested": level, "tool": t_short(c.tool), "tool_name": c.tool,
            "actor": "subagent" if c.is_sub else "main", "agent_type": c.agent_type or None, "permission_mode": c.mode,
            "session_id": payload.get("session_id"), "tool_use_id": payload.get("tool_use_id"), "args": rec_args, "hook_version": C.HOOK_VERSION}
        # §5a-8: the audit log (source of truth) in ${CLAUDE_PLUGIN_DATA}/logs — outside the model-editable workspace
        primary = C.data_dir() / "logs"
        primary.mkdir(parents=True, exist_ok=True)
        C.append_jsonl(primary / f"guard-{C.today()}.jsonl", rec)
        if C.is_workspace(c.ws):  # informational copy for /diag (the hook's write needs no approval; agent edits of the copy → R18)
            C.append_jsonl(C.log_dir(payload) / f"guard-{C.today()}.jsonl", {**rec, "copy_of": "${CLAUDE_PLUGIN_DATA}/logs"})
    except Exception:  # noqa: BLE001 — the audit log must not change the decision
        pass
    return 0


def t_short(name: str) -> str:
    if name.startswith("mcp__"):
        server, tool, ours = split_mcp(name)
        return f"ytfeed.{tool}" if ours else f"{server}.{tool}"
    return name


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — fail-closed
        print(f"yt-media-feed-helper guard: guard error ({type(e).__name__}: {e}) — blocking (fail-closed).", file=sys.stderr)
        sys.exit(2)
