"""Configuration via environment variables: precedence YTFEED_* > plugin options > file; secrets never leak."""
import json
import os
import subprocess
import sys

import pytest
from conftest import PLUGIN_ROOT, hook_json, run_hook

LAUNCH = PLUGIN_ROOT / "server" / "launch.py"
CID = "123-abc.apps.googleusercontent.com"
SECRET = "GOCSPX-envSecretValue_0123456789"
OPT_SECRET = "GOCSPX-optionSecretValue_987654"


def test_client_config_from_env(env, monkeypatch):
    from ytfeed.paths import client_config_from_env

    assert client_config_from_env() == ({}, "")
    monkeypatch.setenv("YTFEED_GOOGLE_CLIENT_ID", CID)
    assert client_config_from_env() == ({}, "")  # client_id alone is not enough
    monkeypatch.setenv("YTFEED_GOOGLE_CLIENT_SECRET", SECRET)
    cfg, src = client_config_from_env()
    assert cfg["installed"]["client_id"] == CID and cfg["installed"]["client_secret"] == SECRET
    assert cfg["installed"]["token_uri"].startswith("https://oauth2.googleapis.com/")
    assert SECRET not in src and "YTFEED_GOOGLE_CLIENT_SECRET" in src


def test_env_overrides_plugin_option(env, monkeypatch):
    from ytfeed.paths import client_config_from_env, setting, workspace_dir

    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_ID", "opt-id")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET", OPT_SECRET)
    cfg, src = client_config_from_env()
    assert cfg["installed"]["client_secret"] == OPT_SECRET and src == "plugin_option"
    monkeypatch.setenv("YTFEED_GOOGLE_CLIENT_SECRET", SECRET)
    assert client_config_from_env()[0]["installed"]["client_secret"] == SECRET
    # an unexpanded ${user_config.x} (no option value) = unset
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MOCK_MODE", "${user_config.mock_mode}")
    monkeypatch.delenv("YTFEED_MOCK")
    assert setting("mock") == ""
    # workspace: the environment variable wins over the plugin option
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR", str(env["tmp"] / "opt-ws"))
    assert workspace_dir() == env["ws"].resolve()


def test_legacy_client_secret_file_var(env, monkeypatch):
    from ytfeed.paths import client_secret_path

    monkeypatch.setenv("YTFEED_CLIENT_SECRET", str(env["tmp"] / "legacy.json"))
    assert client_secret_path() == env["tmp"] / "legacy.json"
    monkeypatch.setenv("YTFEED_GOOGLE_CLIENT_SECRET_FILE", str(env["tmp"] / "new.json"))
    assert client_secret_path() == env["tmp"] / "new.json"


def test_auth_status_reports_source_not_value(env, monkeypatch):
    from ytfeed.auth import AuthManager

    monkeypatch.setenv("YTFEED_GOOGLE_CLIENT_ID", CID)
    monkeypatch.setenv("YTFEED_GOOGLE_CLIENT_SECRET", SECRET)
    st = AuthManager(token_file=env["secrets"] / "token.json", client_secret=env["secrets"] / "missing.json", mock=False).status()
    assert st["client_secret_present"] is True and "YTFEED_GOOGLE_CLIENT_SECRET" in st["client_config_source"]
    assert SECRET not in json.dumps(st)


def test_token_does_not_store_env_client_secret(env, monkeypatch):
    from ytfeed.auth import read_token, write_token

    monkeypatch.setenv("YTFEED_GOOGLE_CLIENT_ID", CID)
    monkeypatch.setenv("YTFEED_GOOGLE_CLIENT_SECRET", SECRET)
    t = env["secrets"] / "token.json"
    write_token(t, {"refresh_token": "1//x", "client_id": CID, "client_secret": SECRET, "scopes": ["s"]}, None)
    data, _ = read_token(t)
    assert "client_secret" not in data and SECRET not in t.read_text(encoding="utf-8")


def test_quota_budget_env_precedence(env, monkeypatch):
    from ytfeed.quota import budget_baseline

    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_REPORT_BUDGET_UNITS", "3000")
    assert budget_baseline() == 3000
    monkeypatch.setenv("YTFEED_REPORT_BUDGET_UNITS", "4000")
    assert budget_baseline() == 4000


def test_mcp_json_does_not_override_user_env():
    """.mcp.json must not set YTFEED_* from plugin options — that would override the user's environment variables."""
    env_block = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["ytfeed"]["env"]
    assert {k for k in env_block if k.startswith("YTFEED_")} == {"YTFEED_DATA_DIR"}
    assert all(k.startswith("CLAUDE_PLUGIN_OPTION_") for k, v in env_block.items() if "${user_config." in v)
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["userConfig"]["google_client_secret"]["sensitive"] is True


def _doctor(env, extra):
    e = {k: v for k, v in os.environ.items() if k != "YTFEED_MOCK"}
    e.update(extra)
    cp = subprocess.run([sys.executable, str(LAUNCH), "doctor", "--data-dir", str(env["tmp"] / "d")], capture_output=True, env=e, check=False)
    return cp.stdout.decode("utf-8")


def test_doctor_env_client_config_without_leaking(env):
    out = _doctor(env, {"YTFEED_GOOGLE_CLIENT_ID": CID, "YTFEED_GOOGLE_CLIENT_SECRET": SECRET})
    rep = json.loads(out)
    checks = {c["name"]: c for c in rep["checks"]}
    assert checks["client_config_present"]["ok"] is True
    assert SECRET not in out and "set (YTFEED_GOOGLE_CLIENT_SECRET)" in rep["settings"]["google_client_secret"]
    half = json.loads(_doctor(env, {"YTFEED_GOOGLE_CLIENT_ID": CID}))
    assert {c["name"]: c for c in half["checks"]}["client_config_present"]["ok"] is False


# ---- guard and redaction ----------------------------------------------------------------------------
def _pay(ws, cmd):
    return {"session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(ws.root), "permission_mode": "default",
            "tool_name": "Bash", "tool_input": {"command": cmd}, "tool_use_id": "t"}


def _decision(cp):
    out = hook_json(cp)
    return None if out is None else out["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize("cmd", [
    "echo $YTFEED_GOOGLE_CLIENT_SECRET",
    "Write-Output $env:YTFEED_GOOGLE_CLIENT_SECRET",
    "printenv CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET",
    "[Environment]::GetEnvironmentVariable('YTFEED_GOOGLE_CLIENT_SECRET','User')",
])
def test_guard_blocks_secret_env_names(ws, cmd):
    assert _decision(run_hook("guard_pretool.py", _pay(ws, cmd))) == "deny"


@pytest.mark.parametrize("cmd", [
    "env", "printenv", "set", "export -p", "env | sort", "Get-ChildItem env:", "gci Env:", "dir env:*",
    "python -c \"import os; print(os.environ)\"", "node -e \"console.log(process.env)\"", "cat /proc/self/environ",
    "[System.Environment]::GetEnvironmentVariables()",
])
def test_guard_blocks_env_dump_when_secret_present(ws, cmd):
    assert _decision(run_hook("guard_pretool.py", _pay(ws, cmd), {"YTFEED_GOOGLE_CLIENT_SECRET": SECRET})) == "deny"


@pytest.mark.parametrize("cmd", ["env FOO=1 pytest -q", "git status", "echo $env:YTFEED_WORKSPACE", "set -e; ls"])
def test_guard_allows_harmless_env_usage(ws, cmd):
    assert _decision(run_hook("guard_pretool.py", _pay(ws, cmd), {"YTFEED_GOOGLE_CLIENT_SECRET": SECRET})) is None


def test_env_dump_allowed_without_plugin_secrets(ws):
    assert _decision(run_hook("guard_pretool.py", _pay(ws, "env"))) is None


def test_redact_env_secret_value(monkeypatch):
    sys.path.insert(0, str(PLUGIN_ROOT / "hooks" / "scripts"))
    import _common as C

    monkeypatch.setenv("YTFEED_STEAM_API_KEY", "plainSteamKeyValue42")
    assert "plainSteamKeyValue42" not in C.redact("key: plainSteamKeyValue42 in text")


@pytest.mark.parametrize("os_name", ["nt", "posix"])
def test_secrets_dir_rule_matches_launcher_and_hooks(env, monkeypatch, tmp_path, os_name):
    """paths.secrets_dir(), launch._secrets_dir() and hooks _common.secrets_dirs() follow one documented rule:
    YTFEED_SECRETS_DIR overrides; Windows %APPDATA%/<plugin>; POSIX (incl. macOS) $XDG_CONFIG_HOME or ~/.config/<plugin>."""
    import importlib.util

    from ytfeed import paths

    spec = importlib.util.spec_from_file_location("ytfeed_launch_t", LAUNCH)
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    hooks_spec = importlib.util.spec_from_file_location("ytfeed_hooks_common_t", PLUGIN_ROOT / "hooks" / "scripts" / "_common.py")
    common = importlib.util.module_from_spec(hooks_spec)
    hooks_spec.loader.exec_module(common)

    override = tmp_path / "override"
    monkeypatch.setenv("YTFEED_SECRETS_DIR", str(override))
    assert paths.secrets_dir() == launch._secrets_dir() == override

    monkeypatch.delenv("YTFEED_SECRETS_DIR")
    appdata, xdg, home = tmp_path / "appdata", tmp_path / "xdg", tmp_path / "home"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    expected = (appdata if os_name == "nt" else xdg) / "yt-media-feed-helper"
    assert paths.secrets_dir(os_name) == launch._secrets_dir(os_name) == expected
    if os_name == os.name:  # hooks list the platform dirs of the running OS
        assert common.norm_path(str(expected)) in common.secrets_dirs()
    if os_name == "posix":  # no XDG_CONFIG_HOME → ~/.config (also on macOS, not ~/Library/Application Support)
        monkeypatch.delenv("XDG_CONFIG_HOME")
        monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: home))
        assert paths.secrets_dir(os_name) == launch._secrets_dir(os_name) == home / ".config" / "yt-media-feed-helper"
