"""Shared test setup: YTFEED_MOCK=1, temporary workspace/data/secrets, frozen time."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = PLUGIN_ROOT / "server"
HOOKS_DIR = PLUGIN_ROOT / "hooks" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FROZEN_NOW = "2026-11-02T12:00:00Z"

if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    data = tmp_path / "data"
    secrets = tmp_path / "secrets"
    for d in (ws, data, secrets):
        d.mkdir()
    for k in list(os.environ):
        if k.startswith(("YTFEED_", "CLAUDE_PLUGIN_OPTION_")) or k in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("YTFEED_MOCK", "1")
    monkeypatch.setenv("YTFEED_NOW", FROZEN_NOW)
    monkeypatch.setenv("YTFEED_WORKSPACE", str(ws))
    monkeypatch.setenv("YTFEED_DATA_DIR", str(data))
    monkeypatch.setenv("YTFEED_SECRETS_DIR", str(secrets))
    monkeypatch.setenv("YTFEED_FIXTURES", str(FIXTURES))
    yield {"ws": ws, "data": data, "secrets": secrets, "tmp": tmp_path}
    from ytfeed import context

    if context._CURRENT is not None:  # context created lazily by get_ctx()
        context._CURRENT.close()


@pytest.fixture
def ws(env):
    """Initialized workspace with the fixture catalog (6 channels)."""
    import shutil

    from ytfeed.paths import Workspace

    w = Workspace(env["ws"]).ensure()
    shutil.copyfile(FIXTURES / "catalog" / "channels.yaml", w.channels_file)
    return w


@pytest.fixture
def app(ws):
    from ytfeed.context import AppContext

    ctx = AppContext.create()
    yield ctx
    ctx.close()


def call(app, tool: str, **kwargs) -> dict:
    """Calls a tool function directly (no MCP transport) and parses the JSON envelope."""
    from ytfeed.server import TOOL_FUNCS

    return json.loads(TOOL_FUNCS[tool](**kwargs))


def run_hook(script: str, payload: dict | str, env_extra: dict | None = None, args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()}
    env.update(env_extra or {})
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / script), *(args or [])],
        input=data.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=30,
    )


def hook_json(cp: subprocess.CompletedProcess) -> dict | None:
    out = cp.stdout.decode("utf-8").strip()
    return json.loads(out) if out else None
