#!/usr/bin/env python3
"""Diagnostic hook (§6.5): one JSONL line per event → <workspace>/logs/diag-YYYY-MM-DD.jsonl
(outside the workspace: ${CLAUDE_PLUGIN_DATA}/logs/, without arguments of foreign tools). Tool results are NOT logged; secrets are redacted.
Never blocks: any error → exit 0.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

LIST_KEYS = ("videos", "comments", "channels", "items", "reviews", "results", "candidates", "proposals", "dimensions")


def tool_short(name: str) -> str:
    if name.startswith("mcp__"):
        rest = name[5:]
        server, _, tool = rest.partition("__")
        if any(name.startswith(p) for p in C.OUR_SERVER_PREFIXES):
            return f"ytfeed.{tool}"
        return f"{server}.{tool}"
    return name


def _envelope_text(resp) -> str | None:
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        for b in resp:
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text")
    if isinstance(resp, dict):
        if "content" in resp:
            return _envelope_text(resp["content"])
        if "ok" in resp:
            return json.dumps(resp)
    return None


def parse_envelope(resp) -> dict:
    txt = _envelope_text(resp)
    if not txt:
        return {}
    try:
        env = json.loads(txt)
    except (ValueError, TypeError):
        return {}
    if not isinstance(env, dict) or "ok" not in env:
        return {}
    out: dict = {"ok": env.get("ok")}
    q = env.get("quota") or {}
    if "cost" in q:
        out["quota_cost"] = q["cost"]
    data = env.get("data") or {}
    if isinstance(data, dict):
        for k in LIST_KEYS:
            if isinstance(data.get(k), list):
                out["result_items"] = len(data[k])
                break
    if not env.get("ok"):
        out["error_code"] = (env.get("error") or {}).get("code")
    return out


PRE_STATE = "diag_pre_times.json"


def _pre_state_update(tool_use_id: str | None, mode: str) -> float | None:
    if not tool_use_id:
        return None
    p = C.data_dir() / PRE_STATE
    with C.file_lock(p.with_suffix(".lock")):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        now = time.time()
        st = {k: v for k, v in st.items() if now - v < 3600}
        res = None
        if mode == "set":
            st[tool_use_id] = now
        else:
            t0 = st.pop(tool_use_id, None)
            if t0 is not None:
                res = (now - t0) * 1000
        p.write_text(json.dumps(st), encoding="utf-8")
        return res


FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _args_for_log(p: dict, tool: str) -> tuple[object, bool]:
    """S-09: arguments are logged only for plugin tools/Agent or inside the plugin workspace; Write/Edit — path only.
    Foreign tools outside the workspace: name only (no arguments) — the log does not collect data from other projects."""
    inp = p.get("tool_input") or {}
    if not isinstance(inp, dict):
        return None, True
    if tool in FILE_TOOLS:
        return {k: C.redact(str(inp[k])) for k in ("file_path", "notebook_path") if inp.get(k)}, False
    ours = tool.startswith(C.OUR_SERVER_PREFIXES) or tool in ("Agent", "Task")
    if ours or C.is_workspace(C.workspace(p)):
        return C.redact_obj(inp)
    return None, False


def build_record(p: dict) -> dict:
    ev = p.get("hook_event_name") or "unknown"
    rec: dict = {
        "ts": C.now_iso(), "event": ev, "session_id": p.get("session_id"),
        "agent_id": p.get("agent_id"), "agent_type": p.get("agent_type"),
        "actor": "subagent" if p.get("agent_id") else "main",
        "permission_mode": p.get("permission_mode"), "cwd_hash": C.short_hash(p.get("cwd")), "hook_version": C.HOOK_VERSION,
    }
    tool = p.get("tool_name")
    if tool:
        rec.update(tool=tool, tool_short=tool_short(tool), tool_use_id=p.get("tool_use_id"))
    if ev == "PreToolUse":
        rec["args"], rec["args_truncated"] = _args_for_log(p, tool or "")
        try:
            _pre_state_update(p.get("tool_use_id"), "set")
        except Exception:  # noqa: BLE001
            pass
    elif ev == "PostToolUse":
        resp = p.get("tool_response")
        rec["result_bytes"] = len(json.dumps(resp, ensure_ascii=False).encode("utf-8")) if resp is not None else 0
        env = parse_envelope(resp) if (tool or "").startswith(C.OUR_SERVER_PREFIXES) else {}
        rec["result_items"] = env.get("result_items")
        rec["quota_cost"] = env.get("quota_cost")
        if env.get("error_code"):
            rec["error"] = env["error_code"]
        dur = p.get("duration_ms")
        if dur is None:
            try:
                d = _pre_state_update(p.get("tool_use_id"), "pop")
                dur = round(d) if d is not None else None
                rec["duration_source"] = "pre_post_diff" if d is not None else None
            except Exception:  # noqa: BLE001
                dur = None
        else:
            rec["duration_source"] = "payload"
        rec["duration_ms"] = dur
    elif ev == "PostToolUseFailure":
        err = str(p.get("error") or "")
        rec["error"] = C.redact(err.splitlines()[0] if err else "")[:300]
        rec["is_interrupt"] = bool(p.get("is_interrupt"))
        try:
            d = _pre_state_update(p.get("tool_use_id"), "pop")
            rec["duration_ms"] = p.get("duration_ms") if p.get("duration_ms") is not None else (round(d) if d is not None else None)
        except Exception:  # noqa: BLE001
            pass
    elif ev == "UserPromptSubmit":
        prompt = str(p.get("prompt") or "")
        rec["prompt_chars"] = len(prompt)
        if C.is_workspace(C.workspace(p)):  # S-09: no prompt content outside the workspace
            rec["prompt_preview"] = C.redact(prompt[:120])
        try:
            rec["info_mode"] = C.classify_prompt(prompt, C.workspace(p))["report"]
        except Exception:  # noqa: BLE001
            rec["info_mode"] = None
    elif ev in ("SubagentStart", "SubagentStop"):
        if ev == "SubagentStop":
            rec["last_message_chars"] = len(str(p.get("last_assistant_message") or ""))
        rec["transcript_path_hash"] = C.short_hash(p.get("agent_transcript_path") or p.get("transcript_path"))
    elif ev in ("SessionStart", "SessionEnd", "Stop"):
        for k in ("source", "reason", "stop_hook_active"):
            if k in p:
                rec[k] = p[k]
    return rec


def main() -> int:
    p = C.read_payload()
    rec = build_record(p)
    C.append_jsonl(C.log_dir(p) / f"diag-{C.today()}.jsonl", rec)
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 — logging never blocks
        print(f"yt-media-feed-helper diag_log: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
