"""Static plugin checks: agents (models per tier, minimal tools), commands, skills, hooks.json, .mcp.json."""
import json
import re
from pathlib import Path

import pytest

from test_mcp_server import EXPECTED_TOOLS

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "mcp__plugin_yt-media-feed-helper_ytfeed__"
FORBIDDEN = {"Bash", "PowerShell", "WebFetch", "WebSearch", "Write", "Edit", "MultiEdit", "NotebookEdit", "Agent", "Task"}
MODELS = {"channel-categorizer": "haiku", "steam-checker": "haiku", "scout": "sonnet", "summarizer": "opus"}


def frontmatter(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"missing frontmatter: {p}"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"')
    return out


@pytest.mark.parametrize("name,model", MODELS.items())
def test_agents(name, model):
    fm = frontmatter(ROOT / "agents" / f"{name}.md")
    assert fm["name"] == name and fm["model"] == model and fm["description"]
    tools = [t.strip() for t in fm["tools"].split(",")]
    assert not (set(tools) & FORBIDDEN)
    for t in tools:
        assert t.startswith(PREFIX) and t[len(PREFIX):] in EXPECTED_TOOLS, t
    main_only = {"catalog_commit", "catalog_set_manual", "yt_search", "catalog_sync_subscriptions", "catalog_add_channel"}
    assert not ({t[len(PREFIX):] for t in tools} & main_only)
    body = (ROOT / "agents" / f"{name}.md").read_text(encoding="utf-8")
    assert "## Untrusted data" in body and "untrusted" in body


def test_only_categorizer_can_propose():
    for name in MODELS:
        tools = frontmatter(ROOT / "agents" / f"{name}.md")["tools"]
        assert ("catalog_propose" in tools) == (name == "channel-categorizer")


def test_commands():
    names = {p.stem for p in (ROOT / "commands").glob("*.md")}
    assert names == {"catalog-sync", "categorize", "review-categories", "report", "diag", "quota", "doctor"}
    for p in (ROOT / "commands").glob("*.md"):
        assert frontmatter(p)["description"]


def test_skills():
    names = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert names == {"feed-guide", "catalog-workflow", "report-workflow", "taxonomy-edit", "diag-stats", "reports-browse", "catalog-export"}
    for p in (ROOT / "skills").glob("*/SKILL.md"):
        fm = frontmatter(p)
        assert fm["name"] == p.parent.name and 50 < len(fm["description"]) <= 1024
    for name in ("taxonomy-edit", "diag-stats", "reports-browse", "catalog-export"):
        assert PREFIX not in (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")  # type (b): no MCP


def test_hooks_json():
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop", "Stop", "SessionEnd"} <= set(cfg)
    guard = cfg["PreToolUse"][0]
    for t in ("Bash", "PowerShell", "WebFetch", "Write", "Edit", "Agent", "Read", "Grep", "Glob"):
        assert re.fullmatch(guard["matcher"], t) or t in guard["matcher"].split("|")
    assert guard["hooks"][0]["onFailure"] == "block"
    for groups in cfg.values():
        for g in groups:
            for h in g["hooks"]:
                assert h["command"] == "python" and isinstance(h["args"], list)  # exec form
                assert h["args"][0] == "-I"  # S-04: interpreter isolated from cwd/PYTHONPATH
                script = h["args"][1].replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))
                assert Path(script).is_file(), script
                if "diag_log" in script:
                    assert h.get("async") is True


def test_hooks_stdlib_only():
    allowed = {"__future__", "contextlib", "hashlib", "json", "os", "re", "sys", "tempfile", "time", "unicodedata", "datetime", "pathlib",
               "typing", "_common", "msvcrt", "fcntl", "ctypes", "base64", "binascii"}
    for p in (ROOT / "hooks" / "scripts").glob("*.py"):
        mods = set(re.findall(r"^\s*(?:import|from)\s+([\w_]+)", p.read_text(encoding="utf-8"), re.M))
        assert mods <= allowed, (p.name, mods - allowed)


def test_mcp_json_and_manifest():
    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert list(mcp) == ["ytfeed"]
    s = mcp["ytfeed"]
    assert s["args"][:2] == ["-I", "${CLAUDE_PLUGIN_ROOT}/server/launch.py"] and "YTFEED_STEAM_API_KEY" not in s["env"] and s["env"]["YTFEED_DATA_DIR"] == "${CLAUDE_PLUGIN_DATA}"
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "yt-media-feed-helper"
    used = set(re.findall(r"\$\{user_config\.(\w+)\}", json.dumps(s)))
    assert used <= set(manifest["userConfig"])
    assert manifest["userConfig"]["steam_api_key"]["sensitive"] is True
    market = json.loads((ROOT.parent / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["plugins"][0]["name"] == "yt-media-feed-helper" and market["plugins"][0]["source"] == "./yt-media-feed-helper"


def test_gitignore_excludes_secrets():
    gi = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    for pat in ("token.json", "client_secret*.json", "logs/", ".venv/"):
        assert pat in gi
    assert "catalog/" not in [line.strip() for line in gi.splitlines()] and "reports/" not in [line.strip() for line in gi.splitlines()]
