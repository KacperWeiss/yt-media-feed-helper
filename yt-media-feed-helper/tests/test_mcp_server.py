"""MCP server in-process (SDK client) and over stdio (launch.py serve)."""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

LAUNCH = Path(__file__).resolve().parent.parent / "server" / "launch.py"
EXPECTED_TOOLS = {
    "taxonomy_get", "catalog_query", "catalog_get_channel", "catalog_sync_subscriptions", "catalog_propose", "catalog_list_proposals",
    "catalog_commit", "catalog_set_manual", "yt_channel_profile", "yt_recent_uploads", "yt_video_details", "yt_comments", "yt_search",
    "steam_find_app", "steam_news", "steam_reviews", "quota_status", "quota_estimate", "auth_status", "evidence_resolve", "report_save",
    "catalog_add_channel", "stance_stats",
}
WRITE_VERBS = {"insert", "update", "delete", "subscribe", "unsubscribe", "rate", "upload", "post", "publish", "like", "reply", "moderate"}


def test_inprocess_listing(ws):
    from ytfeed.server import mcp

    async def run():
        async with Client(mcp) as c:
            tools = (await c.list_tools()).tools
            res = (await c.list_resources()).resources
            tmpl = (await c.list_resource_templates()).resource_templates
            prompts = (await c.list_prompts()).prompts
            names = {t.name for t in tools}
            assert names == EXPECTED_TOOLS and len(tools) == 23
            for t in tools:
                assert t.description and len(t.description) <= 400, (t.name, len(t.description or ""))
                assert not (set(t.name.split("_")) & WRITE_VERBS), t.name  # ADR-02: no external write tools
            uris = {str(r.uri) for r in res} | {t.uri_template for t in tmpl}
            assert uris == {"catalog://categories", "catalog://channels", "catalog://channels/{channel_id}", "catalog://proposals/pending",
                            "quota://today", "reports://index"}
            assert {p.name for p in prompts} == {"report_request", "categorize_channels", "review_proposals"}
            r = await c.call_tool("catalog_query", {"filters": {"specialization": ["EU5"]}})
            env = json.loads(r.content[0].text)
            assert env["ok"] and env["data"]["matched"] == 3
            cat = await c.read_resource("catalog://categories")
            assert "Europa Universalis" in cat.contents[0].text
            ch = await c.read_resource("catalog://channels/UCparadoxinteractive0001")
            assert json.loads(ch.contents[0].text)["data"]["title"] == "Paradox Interactive"

    asyncio.run(run())


def test_stdio_via_launcher(env):
    """`launch.py serve` (YTFEED_SKIP_VENV=1 → current interpreter with dependencies) answers initialize and tools/list."""
    e = {k: v for k, v in os.environ.items()}
    e.update(YTFEED_SKIP_VENV="1", PYTHONUTF8="1")
    params = StdioServerParameters(command=sys.executable, args=[str(LAUNCH), "serve"], env=e, cwd=str(env["ws"]))

    async def run():
        async with Client(params, read_timeout_seconds=60) as c:
            tools = (await c.list_tools()).tools
            assert len(tools) == 23
            r = await c.call_tool("auth_status", {})
            data = json.loads(r.content[0].text)["data"]
            assert data["mock"] is True and data["scope_ok"] is True
            assert "token" not in json.dumps(data).replace("token_expires_in_s", "").replace("refresh_token_present", "")

    asyncio.run(run())
    assert (env["ws"] / "catalog" / "taxonomy.yaml").exists()  # workspace initialized at startup
