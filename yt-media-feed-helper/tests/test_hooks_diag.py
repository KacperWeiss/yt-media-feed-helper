"""diag_log.py: JSONL format, redaction, limits, no result content, duration_ms, UTF-8; tracker and session_start."""
import json

import pytest

from conftest import hook_json, run_hook

P = "mcp__plugin_yt-media-feed-helper_ytfeed__"


def records(wsdir):
    files = list((wsdir / "logs").glob("diag-*.jsonl"))
    assert files, "no diag file"
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def base(wsdir, event, **kw):
    return {"session_id": "s1", "hook_event_name": event, "cwd": str(wsdir), "permission_mode": "default", **kw}


def test_pre_post_subagent_record(ws):
    w = ws.root
    pre = base(w, "PreToolUse", tool_name=P + "yt_comments", tool_use_id="toolu_A", agent_id="a-7f3c", agent_type="yt-media-feed-helper:scout",
               tool_input={"video_id": "dQw4w9WgXcQ", "order": "relevance", "search_terms": "Rzeczpospolita", "max_threads": 40})
    assert run_hook("diag_log.py", pre).returncode == 0
    env = {"ok": True, "data": {"comments": [{"untrusted": {"text": "SECRET COMMENT CONTENT"}}] * 3}, "quota": {"cost": 1}}
    post = base(w, "PostToolUse", tool_name=P + "yt_comments", tool_use_id="toolu_A", agent_id="a-7f3c", agent_type="yt-media-feed-helper:scout",
                tool_input={}, tool_response=[{"type": "text", "text": json.dumps(env, ensure_ascii=False)}])
    assert run_hook("diag_log.py", post).returncode == 0
    r_pre, r_post = records(w)
    for key in ("ts", "event", "session_id", "agent_id", "agent_type", "actor", "tool", "tool_short", "tool_use_id", "permission_mode", "cwd_hash", "hook_version"):
        assert key in r_post, key
    assert r_pre["args"]["search_terms"] == "Rzeczpospolita" and r_pre["args_truncated"] is False
    assert r_post["actor"] == "subagent" and r_post["agent_type"] == "yt-media-feed-helper:scout"
    assert r_post["tool_short"] == "ytfeed.yt_comments" and r_post["quota_cost"] == 1 and r_post["result_items"] == 3
    assert r_post["result_bytes"] > 50 and r_post["duration_ms"] is not None and r_post["duration_source"] == "pre_post_diff"
    assert "SECRET COMMENT" not in json.dumps(r_post, ensure_ascii=False)  # tool results are not logged


def test_duration_from_payload(ws):
    run_hook("diag_log.py", base(ws.root, "PostToolUse", tool_name="Bash", tool_use_id="t2", tool_response={"stdout": "x"}, duration_ms=842))
    r = records(ws.root)[-1]
    assert r["duration_ms"] == 842 and r["duration_source"] == "payload" and r["quota_cost"] is None


# Fake credentials in real formats, assembled at runtime so the source never contains a literal that secret scanners
# (e.g. GitHub secret scanning) flag. They are NOT real keys — only shaped like them to exercise the redaction rules.
FAKE_ACCESS_TOKEN = "ya29" + ".a0AfH6SMB-abc_def"
FAKE_REFRESH_TOKEN = "1//" + "0gABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
FAKE_CLIENT_SECRET = "GOCSPX" + "-AbCdEfGhIjKlMnOp_qrs"
FAKE_API_KEY = "AIza" + "SyA1234567890abcdefghijklmnopqrstuv"


@pytest.mark.parametrize("secret,leak", [
    (f"Authorization: Bearer {FAKE_ACCESS_TOKEN}", FAKE_ACCESS_TOKEN),
    (f"refresh={FAKE_REFRESH_TOKEN}", FAKE_REFRESH_TOKEN),
    (f"secret {FAKE_CLIENT_SECRET}", FAKE_CLIENT_SECRET),
    (f"k={FAKE_API_KEY}", FAKE_API_KEY),
    ("https://api.steampowered.com/x?key=0123456789ABCDEF0123456789ABCDEF&appid=1", "0123456789ABCDEF0123456789ABCDEF"),
    ('{"access_token": "abc.def.ghi"}', "abc.def.ghi"),
    ("password=hunter2", "hunter2"),
    ("C:\\Users\\someuser\\AppData\\Roaming\\yt-media-feed-helper\\token.json", "someuser"),
])
def test_redaction(ws, secret, leak):
    run_hook("diag_log.py", base(ws.root, "PreToolUse", tool_name="Bash", tool_use_id="t3", tool_input={"command": secret}))
    rec = records(ws.root)[-1]
    assert leak not in json.dumps(rec, ensure_ascii=False)
    assert "REDACTED" in rec["args"]["command"] or "<secrets>" in rec["args"]["command"]


def test_args_limits(ws):
    run_hook("diag_log.py", base(ws.root, "PreToolUse", tool_name="Write", tool_use_id="t4", tool_input={"content": "x" * 5000, "file_path": "a"}))
    rec = records(ws.root)[-1]
    assert rec["args"] == {"file_path": "a"}  # S-09: Write/Edit — path only, no content
    run_hook("diag_log.py", base(ws.root, "PreToolUse", tool_name="Bash", tool_use_id="t4b", tool_input={"command": "x" * 5000}))
    rec = records(ws.root)[-1]
    assert rec["args_truncated"] is True and len(json.dumps(rec["args"])) <= 1100


def test_failure_prompt_and_utf8(ws):
    run_hook("diag_log.py", base(ws.root, "PostToolUseFailure", tool_name="Bash", tool_use_id="t5", error="Error: token=abc123\nline 2", is_interrupt=False))
    run_hook("diag_log.py", base(ws.root, "UserPromptSubmit", prompt="Podsumuj odbiór EU5 przez polskich fanów — Rzeczpospolita Obojga Narodów"))
    run_hook("diag_log.py", base(ws.root, "SubagentStop", agent_id="a1", agent_type="yt-media-feed-helper:summarizer", last_assistant_message="abc",
                                 agent_transcript_path="C:/t.jsonl"))
    run_hook("diag_log.py", base(ws.root, "SessionStart", source="startup"))
    fail, prompt, stop, start = records(ws.root)[-4:]
    assert fail["error"] == "Error: token=[REDACTED]" and fail["is_interrupt"] is False
    assert prompt["prompt_chars"] > 50 and "Rzeczpospolita" in prompt["prompt_preview"] and prompt["info_mode"] is True
    assert stop["last_message_chars"] == 3 and stop["transcript_path_hash"] and "abc" not in json.dumps(stop)
    assert start["source"] == "startup"
    raw = next((ws.root / "logs").glob("diag-*.jsonl")).read_bytes().decode("utf-8")
    assert "odbiór" in raw  # UTF-8, no \u-escapes (Polish prompt as non-ASCII test data)


def test_diag_never_blocks(ws):
    cp = run_hook("diag_log.py", "garbage{")
    assert cp.returncode == 0


def test_outside_workspace_logs_to_data_dir(env):
    other = env["tmp"] / "other"
    other.mkdir()
    run_hook("diag_log.py", base(other, "Stop"), {"YTFEED_WORKSPACE": ""})
    assert list((env["data"] / "logs").glob("diag-*.jsonl"))
    assert not (other / "logs").exists()


def test_subagent_tracker(ws, env):
    for i in range(3):
        run_hook("subagent_tracker.py", base(ws.root, "SubagentStart", agent_id=f"a{i}", agent_type="yt-media-feed-helper:scout"), args=["start"])
    run_hook("subagent_tracker.py", base(ws.root, "SubagentStart", agent_id="g1", agent_type="general-purpose"), args=["start"])
    run_hook("subagent_tracker.py", base(ws.root, "SubagentStop", agent_id="a1", agent_type="yt-media-feed-helper:scout"), args=["stop"])
    st = json.loads((env["data"] / "subagents.json").read_text(encoding="utf-8"))
    assert set(st["active"]) == {"a0", "a2"}


def test_session_start(ws, env):
    (ws.proposals / "UCx.json").write_text(json.dumps({"status": "pending"}), encoding="utf-8")
    out = hook_json(run_hook("session_start.py", base(ws.root, "SessionStart", source="startup")))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "catalog of 6 channels (1 uncategorized, 1 proposals" in ctx
    cfg = ws.config_file.read_text(encoding="utf-8").replace("session_start:\n  enabled: true", "session_start:\n  enabled: false")
    ws.config_file.write_text(cfg, encoding="utf-8")
    assert run_hook("session_start.py", base(ws.root, "SessionStart")).stdout == b""
