"""auth.py without network (scope, missing token, invalid_grant → AUTH_REQUIRED with a single command) and `launch.py doctor`."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ytfeed.auth import SCOPE, AuthManager, reauth_command
from ytfeed.errors import ToolError

LAUNCH = Path(__file__).resolve().parent.parent / "server" / "launch.py"


def token(path: Path, scopes, expiry="2000-01-01T00:00:00Z"):
    path.write_text(json.dumps({"token": "ya29.fake", "refresh_token": "1//fake", "client_id": "x.apps.googleusercontent.com",
                                "client_secret": "GOCSPX-fake", "token_uri": "https://oauth2.googleapis.com/token", "scopes": scopes,
                                "expiry": expiry}), encoding="utf-8")


def test_missing_token(env):
    a = AuthManager(token_file=env["secrets"] / "token.json", mock=False)
    with pytest.raises(ToolError) as e:
        a.access_token()
    assert e.value.code == "AUTH_REQUIRED" and "launch.py" in e.value.hint and "auth" in e.value.hint
    st = a.status()
    assert st["authorized"] is False and "token" not in json.dumps({k: v for k, v in st.items() if k not in ("token_expires_in_s", "refresh_token_present", "hint")})


def test_scope_mismatch(env):
    t = env["secrets"] / "token.json"
    token(t, [SCOPE, "https://www.googleapis.com/auth/youtube.force-ssl"])
    a = AuthManager(token_file=t, mock=False)
    with pytest.raises(ToolError) as e:
        a.check()
    assert e.value.code == "SCOPE_MISMATCH"
    assert a.status()["scope_ok"] is False


def test_invalid_grant_one_command(env, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    t = env["secrets"] / "token.json"
    token(t, [SCOPE])
    monkeypatch.setattr(Credentials, "refresh", lambda self, req: (_ for _ in ()).throw(RefreshError("invalid_grant: Token has been expired or revoked.")))
    a = AuthManager(token_file=t, mock=False)
    st = a.status()
    assert st["scope_ok"] and st["refresh_token_present"] and "ya29" not in json.dumps(st)
    with pytest.raises(ToolError) as e:
        a.access_token()
    assert e.value.code == "AUTH_REQUIRED" and "invalid_grant" in e.value.message
    assert e.value.hint.endswith(reauth_command())


def test_missing_pyyaml_is_not_reported_as_yaml_syntax_error(ws, monkeypatch):
    """Regression: without PyYAML, validate_workspace used to report a bogus YAML_SYNTAX error on a valid catalog."""
    from ytfeed.catalog import rules

    def no_yaml(path):
        raise ModuleNotFoundError("No module named 'yaml'")

    monkeypatch.setattr(rules, "load_yaml", no_yaml)
    with pytest.raises(ImportError):
        rules.validate_workspace(ws.root)


def test_doctor_validates_fresh_catalog(ws, env):
    """doctor reports a freshly initialized catalog as valid (validation runs where PyYAML is available)."""
    e = {k: v for k, v in os.environ.items() if k != "YTFEED_MOCK"}
    cp = subprocess.run([sys.executable, str(LAUNCH), "doctor", "--data-dir", str(env["tmp"] / "empty-data")], capture_output=True, env=e)
    checks = {c["name"]: c for c in json.loads(cp.stdout.decode("utf-8"))["checks"]}
    assert checks["catalog_valid"]["ok"] is True, checks["catalog_valid"]


def test_doctor_detects_missing_venv_and_token(env):
    e = {k: v for k, v in os.environ.items() if k != "YTFEED_MOCK"}
    cp = subprocess.run([sys.executable, str(LAUNCH), "doctor", "--data-dir", str(env["tmp"] / "empty-data")], capture_output=True, env=e)
    rep = json.loads(cp.stdout.decode("utf-8"))
    checks = {c["name"]: c["ok"] for c in rep["checks"]}
    assert cp.returncode == 1 and checks["venv"] is False and checks["token_present"] is False
    assert any("launch.py" in f and "install" in f for f in rep["fixes"]) and any(f.endswith("auth") for f in rep["fixes"])
