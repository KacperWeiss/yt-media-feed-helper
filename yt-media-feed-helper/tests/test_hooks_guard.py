"""guard_pretool.py run as a subprocess with JSON payloads on stdin (R1–R15 × main/subagent × modes)."""
import json
import time

import pytest

from conftest import FIXTURES, hook_json, run_hook

P = "mcp__plugin_yt-media-feed-helper_ytfeed__"
SUB = {"agent_id": "a-1", "agent_type": "yt-media-feed-helper:scout"}
CAT = {"agent_id": "a-2", "agent_type": "yt-media-feed-helper:channel-categorizer"}


@pytest.fixture
def wsdir(ws):
    return ws.root


def pay(wsdir, tool, inp, mode="default", **extra):
    return {"session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(wsdir), "permission_mode": mode, "tool_name": tool,
            "tool_input": inp, "tool_use_id": "toolu_x", **extra}


def decision(cp):
    assert cp.returncode == 0, cp.stderr.decode("utf-8", "replace")
    out = hook_json(cp)
    return None if out is None else out["hookSpecificOutput"]["permissionDecision"]


def guard(payload, **env):
    return run_hook("guard_pretool.py", payload, env)


def load_fixture(name, wsdir):
    return json.loads((FIXTURES / "hooks" / name).read_text(encoding="utf-8").replace("WORKSPACE", str(wsdir).replace("\\", "\\\\")))


# ---- fixtures from §2 ----------------------------------------------------------------------------
def test_fixture_payloads(wsdir):
    assert decision(guard(load_fixture("pretool_bash_curl_post.json", wsdir))) == "deny"
    assert decision(guard(load_fixture("pretool_write_outside.json", wsdir))) == "ask"
    assert decision(guard(load_fixture("pretool_mcp_unknown_insert.json", wsdir))) == "ask"
    assert decision(guard(load_fixture("pretool_catalog_commit_subagent.json", wsdir))) == "deny"


# ---- R1 / R1b / R2 / R3 ------------------------------------------------------------------------------
@pytest.mark.parametrize("tool,inp", [
    ("catalog_commit", {"decisions": [{"proposal_id": "x", "action": "accept"}], "approve_new_values": [{"dimension": "specialization", "value": "Crusader Kings"}]}),
    ("catalog_set_manual", {"channel_id": "UC1", "set": {"subscription": ["Supporter"]}}),
    ("catalog_sync_subscriptions", {"dry_run": False}),
])
def test_r1_local_catalog_changes(wsdir, tool, inp):
    cp = guard(pay(wsdir, P + tool, inp))
    out = hook_json(cp)["hookSpecificOutput"]
    assert out["permissionDecision"] == "ask" and "LOCAL" in out["permissionDecisionReason"]
    assert decision(guard(pay(wsdir, P + tool, inp, **SUB))) == "deny"
    for mode in ("bypassPermissions", "dontAsk", "auto"):
        o = hook_json(guard(pay(wsdir, P + tool, inp, mode=mode)))["hookSpecificOutput"]
        assert o["permissionDecision"] == "deny" and "approve in normal mode" in o["permissionDecisionReason"]
    assert decision(guard(pay(wsdir, P + tool, inp, mode="acceptEdits"))) == "ask"


def test_r1_dry_run_and_reads_pass(wsdir):
    assert decision(guard(pay(wsdir, P + "catalog_sync_subscriptions", {"dry_run": True}))) is None
    assert decision(guard(pay(wsdir, P + "catalog_query", {}, **SUB))) is None
    assert decision(guard(pay(wsdir, P + "yt_comments", {"video_id": "x"}, **SUB))) is None


def test_r1b_budget_raise(wsdir):
    assert decision(guard(pay(wsdir, P + "quota_estimate", {"plan": [], "budget_units": 5000}))) == "ask"
    assert decision(guard(pay(wsdir, P + "quota_estimate", {"plan": [], "budget_units": 1500}))) is None
    assert decision(guard(pay(wsdir, P + "quota_estimate", {"plan": [], "budget_units": 5000}, **SUB))) == "deny"


def test_r2_search(wsdir, ws):
    assert decision(guard(pay(wsdir, P + "yt_search", {"query": "x"}))) is None  # no confirmation by default
    assert decision(guard(pay(wsdir, P + "yt_search", {"query": "x"}, **SUB))) == "deny"
    cfg = ws.config_file.read_text(encoding="utf-8").replace("require_confirmation: false", "require_confirmation: true")
    ws.config_file.write_text(cfg, encoding="utf-8")
    assert decision(guard(pay(wsdir, P + "yt_search", {"query": "x"}))) == "ask"


def test_r3_propose_only_categorizer(wsdir):
    assert decision(guard(pay(wsdir, P + "catalog_propose", {"channel_id": "UC1", "proposal": {}}, **CAT))) is None
    assert decision(guard(pay(wsdir, P + "catalog_propose", {"channel_id": "UC1", "proposal": {}}, **SUB))) == "deny"
    assert decision(guard(pay(wsdir, P + "catalog_propose", {"channel_id": "UC1", "proposal": {}}))) is None


# ---- R4 / R5 -------------------------------------------------------------------------------------------
def test_r4_r5_other_mcp(wsdir):
    assert decision(guard(pay(wsdir, "mcp__github__create_issue", {}))) == "ask"
    assert decision(guard(pay(wsdir, "mcp__github__create_issue", {}, **SUB))) == "deny"
    assert decision(guard(pay(wsdir, "mcp__yt__list_videos", {}))) is None
    assert decision(guard(pay(wsdir, "mcp__google-drive__list_files", {}))) == "ask"
    assert decision(guard(pay(wsdir, "mcp__docs__search", {}))) is None


# ---- R6 / R7 (obfuscation) ------------------------------------------------------------------------------
@pytest.mark.parametrize("tool,cmd", [
    ("Bash", "curl -X POST https://www.googleapis.com/youtube/v3/subscriptions -d '{}'"),
    ("Bash", "CURL -X POST https://www.googleapis.com/youtube/v3/subscriptions"),
    ("Bash", "curl.exe --request DELETE https://www.googleapis.com/youtube/v3/subscriptions?id=1"),
    ("Bash", "c^url -X PATCH https://www.googleapis.com/youtube/v3/channels"),
    ("PowerShell", "iwr -Method POST https://www.googleapis.com/youtube/v3/commentThreads -Body $b"),
    ("PowerShell", "Invoke-RestMethod -Method Delete https://www.googleapis.com/youtube/v3/subscriptions?id=x"),
    ("PowerShell", "In`voke-WebRequest -Uri https://store.steampowered.com/api/addtowishlist -Method Post"),
    ("Bash", "python -c \"import requests;requests.post('https://www.googleapis.com/youtube/v3/subscriptions', json={})\""),
    ("Bash", "wget --post-data='x' https://steamcommunity.com/comment/post"),
])
def test_r6_mutating_http_denied(wsdir, tool, cmd):
    assert decision(guard(pay(wsdir, tool, {"command": cmd}))) == "deny"


@pytest.mark.parametrize("cmd", [
    "curl https://www.googleapis.com/youtube/v3/videos?id=x&part=snippet",
    "irm https://store.steampowered.com/api/appdetails?appids=1",
])
def test_r7_get_bypassing_mcp_asks(wsdir, cmd):
    assert decision(guard(pay(wsdir, "Bash", {"command": cmd}))) == "ask"
    assert decision(guard(pay(wsdir, "Bash", {"command": cmd}, **SUB))) == "deny"
    assert decision(guard(pay(wsdir, "Bash", {"command": cmd}, mode="bypassPermissions"))) == "deny"


@pytest.mark.parametrize("cmd", ["git status", "curl https://example.com -d x", "python -m pytest", "echo youtube.com"])
def test_shell_benign_passes(wsdir, cmd):
    assert decision(guard(pay(wsdir, "Bash", {"command": cmd}))) is None


# ---- R8 secrets ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tool,inp", [
    ("Bash", {"command": "type %APPDATA%\\yt-media-feed-helper\\token.json"}),
    ("PowerShell", {"command": "Get-Content $env:APPDATA\\yt-media-feed-helper\\client_secret.json"}),
    ("Bash", {"command": "echo $YTFEED_STEAM_API_KEY"}),
    ("Read", {"file_path": "C:\\Users\\u\\AppData\\Roaming\\yt-media-feed-helper\\token.json"}),
    ("Read", {"file_path": "D:/Downloads/client_secret_123-abc.apps.googleusercontent.com.json"}),
    ("Grep", {"pattern": "refresh", "path": "C:/Users/u/AppData/Roaming/yt-media-feed-helper"}),
    ("Glob", {"pattern": "**/client_secret*"}),
])
def test_r8_secrets(wsdir, tool, inp):
    assert decision(guard(pay(wsdir, tool, inp))) == "deny"
    assert decision(guard(pay(wsdir, tool, inp, **SUB))) == "deny"


def test_r8_normal_reads_pass(wsdir):
    assert decision(guard(pay(wsdir, "Read", {"file_path": "D:/proj/yt-media-feed-helper/server/ytfeed/auth.py"}))) is None
    assert decision(guard(pay(wsdir, "Grep", {"pattern": "client_secret", "path": "src"}))) is None


# ---- R9 / R10 -------------------------------------------------------------------------------------------
def test_r9_auth_and_r10(wsdir):
    assert decision(guard(pay(wsdir, "Bash", {"command": 'python "C:/x/server/launch.py" auth'}))) == "ask"
    assert decision(guard(pay(wsdir, "Bash", {"command": "python launch.py auth"}, **SUB))) == "deny"
    assert decision(guard(pay(wsdir, "Bash", {"command": "python launch.py doctor"}))) is None
    assert decision(guard(pay(wsdir, "Bash", {"command": "git push origin main"}))) == "ask"
    assert decision(guard(pay(wsdir, "Bash", {"command": "yt-dlp --upload x"}))) == "ask"
    assert decision(guard(pay(wsdir, "Bash", {"command": "gcloud projects list"}))) is None


# ---- R11 / R12 ------------------------------------------------------------------------------------------
def test_webfetch(wsdir):
    assert decision(guard(pay(wsdir, "WebFetch", {"url": "https://www.youtube.com/subscription_center?add_user=x"}))) == "deny"
    assert decision(guard(pay(wsdir, "WebFetch", {"url": "https://store.steampowered.com/api/addtowishlist"}))) == "deny"
    assert decision(guard(pay(wsdir, "WebFetch", {"url": "https://www.googleapis.com/youtube/v3/videos?id=x"}))) == "ask"
    assert decision(guard(pay(wsdir, "WebFetch", {"url": "https://www.youtube.com/watch?v=x"}))) is None


# ---- R13 / R14 paths --------------------------------------------------------------------------------------
def test_write_paths(wsdir):
    w = str(wsdir)
    assert decision(guard(pay(wsdir, "Write", {"file_path": f"{w}\\reports\\2026-11-02-x.md"}))) is None
    assert decision(guard(pay(wsdir, "Write", {"file_path": f"{w}/REPORTS/a.md".replace("\\", "/")}))) is None  # case-insensitive (Windows)
    assert decision(guard(pay(wsdir, "Edit", {"file_path": f"{w}\\catalog\\channels.yaml"}))) == "ask"  # S-01: catalog edit outside MCP
    assert decision(guard(pay(wsdir, "Edit", {"file_path": f"{w}\\catalog\\channels.yaml"}, mode="acceptEdits"))) == "ask"
    assert decision(guard(pay(wsdir, "Edit", {"file_path": f"{w}\\catalog\\config.yaml"}, mode="auto"))) == "deny"
    assert decision(guard(pay(wsdir, "Edit", {"file_path": f"{w}\\catalog\\channels.yaml"}, **SUB))) == "deny"
    assert decision(guard(pay(wsdir, "Write", {"file_path": f"{w}\\reports\\..\\..\\evil.txt"}))) == "ask"  # ..
    assert decision(guard(pay(wsdir, "Write", {"file_path": f"{w}\\catalog\\proposals\\x.json"}))) == "ask"
    assert decision(guard(pay(wsdir, "Write", {"file_path": "reports/rel.md"}))) is None  # relative to cwd
    assert decision(guard(pay(wsdir, "Write", {"file_path": "C:\\temp\\x.txt"}, **SUB))) == "deny"


def test_write_rules_only_in_workspace(env):
    other = env["tmp"] / "other-project"
    other.mkdir()
    assert decision(guard(pay(other, "Write", {"file_path": str(other / "src.py")}), CLAUDE_PROJECT_DIR=str(other), YTFEED_WORKSPACE="")) is None
    # but YouTube blocks apply everywhere
    assert decision(guard(pay(other, "Bash", {"command": "curl -X POST https://www.googleapis.com/youtube/v3/x"}), YTFEED_WORKSPACE="")) == "deny"


# ---- R15 subagent waves -------------------------------------------------------------------------------------
def test_r15_wave_limit(wsdir, env):
    st = {"active": {f"a{i}": {"agent_type": "yt-media-feed-helper:scout", "started_at_ts": time.time()} for i in range(6)}}
    (env["data"] / "subagents.json").write_text(json.dumps(st), encoding="utf-8")
    assert decision(guard(pay(wsdir, "Agent", {"subagent_type": "yt-media-feed-helper:scout", "prompt": "x"}))) == "deny"
    assert decision(guard(pay(wsdir, "Agent", {"subagent_type": "general-purpose", "prompt": "x"}))) is None
    st["active"] = {k: {**v, "started_at_ts": time.time() - 3600} for k, v in st["active"].items()}  # stale
    (env["data"] / "subagents.json").write_text(json.dumps(st), encoding="utf-8")
    assert decision(guard(pay(wsdir, "Agent", {"subagent_type": "yt-media-feed-helper:scout", "prompt": "x"}))) is None


# ---- audit and fail-closed -------------------------------------------------------------------------------------
def test_guard_log_and_redaction(wsdir):
    guard(load_fixture("pretool_bash_curl_post.json", wsdir))
    logs = list((wsdir / "logs").glob("guard-*.jsonl"))
    rec = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[-1])
    assert rec["rule"] == "R6" and rec["decision"] == "deny"
    assert "ya29.a0AfH6SMBxyz" not in json.dumps(rec) and "[REDACTED]" in rec["args"]["command"]


def test_fail_closed(wsdir):
    cp = run_hook("guard_pretool.py", "{not json")
    assert cp.returncode == 2 and "fail-closed" in cp.stderr.decode("utf-8")


def test_unmatched_tools_silent(wsdir):
    for t in ("TodoWrite", "WebSearch"):
        cp = guard(pay(wsdir, t, {}))
        assert cp.returncode == 0 and cp.stdout == b""
