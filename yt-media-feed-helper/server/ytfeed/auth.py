"""OAuth (InstalledAppFlow, loopback) — ONLY the youtube.readonly scope (layer L1 §9.1).

* `python launch.py auth` (→ `python -m ytfeed.auth`) is run by the user THEMSELVES in a terminal.
* The server verifies the token scope and refuses YouTube calls with any other scope (SCOPE_MISMATCH).
* Testing mode: the refresh token expires after ~7 days → `invalid_grant` → AUTH_REQUIRED with a single fix command.
* No function returns tokens.
* Windows: the token at rest is DPAPI-encrypted (CryptProtectData, user context; ctypes, no new dependencies) — S-10.
  A legacy plaintext `token.json` is read and rewritten in encrypted form on first read.
  `client_secret` is not copied into the token if it can be loaded from the client file.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .errors import ToolError
from .paths import SERVER_DIR, client_config_from_env, client_secret_path, is_mock, token_path

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def tokeninfo_scopes(access_token: str, timeout: float = 10.0) -> set[str]:
    """Actual scopes of the access token according to Google (S-11 / §5a-9). GET only, no writes."""
    import urllib.parse
    import urllib.request

    url = TOKENINFO_URL + "?" + urllib.parse.urlencode({"access_token": access_token})
    with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as r:  # noqa: S310 — fixed https host
        data = json.loads(r.read().decode("utf-8"))
    return set(str(data.get("scope") or "").split())
DPAPI_FORMAT = "ytfeed-dpapi-v1"
_ENTROPY = b"yt-media-feed-helper/token"


# ---- DPAPI (Windows) ------------------------------------------------------------------------------------
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    src, _keep1 = blob(data)
    ent, _keep2 = blob(_ENTROPY)
    out = Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))  # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed (code {ctypes.GetLastError()})")  # type: ignore[attr-defined]
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def encrypt_token(data: dict) -> str:
    raw = json.dumps(data).encode("utf-8")
    if os.name != "nt":
        return raw.decode("utf-8")  # POSIX: 0600 (keyring = future option)
    return json.dumps({"format": DPAPI_FORMAT, "data": base64.b64encode(_dpapi(raw, True)).decode("ascii")})


def read_token(path: Path) -> tuple[dict, bool]:
    """(token data, whether the file was encrypted)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and doc.get("format") == DPAPI_FORMAT:
        return json.loads(_dpapi(base64.b64decode(doc["data"]), False).decode("utf-8")), True
    return doc, False


def _client_file_info(cs: Path | None) -> dict:
    if not cs:
        return {}
    try:
        doc = json.loads(cs.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (doc.get("installed") or doc.get("web") or {}) if isinstance(doc, dict) else {}


def _client_info(cs: Path | None) -> dict:
    """OAuth client data: environment variables / plugin options first (YTFEED_GOOGLE_CLIENT_ID + _SECRET), then the JSON file."""
    cfg, _ = client_config_from_env()
    return cfg["installed"] if cfg else _client_file_info(cs)


def client_config_source(cs: Path | None) -> str:
    """Where the client config comes from (without values): 'env:…' / 'plugin_option' / 'file' / ''."""
    _, src = client_config_from_env()
    if src:
        return src
    return "file" if _client_file_info(cs).get("client_id") else ""


def reauth_command() -> str:
    return f'python "{SERVER_DIR / "launch.py"}" auth'


class AuthManager:
    def __init__(self, token_file: Path | None = None, client_secret: Path | None = None, mock: bool | None = None):
        self.token_file = token_file or token_path()
        self.client_secret = client_secret or client_secret_path()
        self.mock = is_mock() if mock is None else mock
        self._creds = None
        self._remote: dict | None = None  # tokeninfo result — once per server process (= session)

    def _load(self):
        from google.oauth2.credentials import Credentials

        if not self.token_file.exists():
            raise ToolError("AUTH_REQUIRED", "YouTube is not authorized (no token).", f"Run it yourself in a terminal: {reauth_command()}")
        try:
            data, encrypted = read_token(self.token_file)
        except (OSError, ValueError) as e:
            raise ToolError("AUTH_REQUIRED", f"Cannot read the token ({type(e).__name__}) — e.g. a different Windows user or a corrupted file.",
                            f"Delete the token and authorize again: {reauth_command()}") from e
        if not encrypted and os.name == "nt":
            try:
                write_token(self.token_file, data, self.client_secret)  # migrate a plaintext token to DPAPI (S-10)
            except OSError:
                pass
        if not data.get("client_secret"):
            info = _client_info(self.client_secret)
            if info.get("client_secret") and (not data.get("client_id") or info.get("client_id") == data.get("client_id")):
                data = {**data, "client_secret": info["client_secret"]}
        scopes = data.get("scopes") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        return Credentials.from_authorized_user_info(data, scopes=scopes or None), set(scopes)

    def scopes(self) -> set[str]:
        return self._load()[1]

    def check(self) -> None:
        if self.mock:
            return
        _, scopes = self._load()
        if scopes != {SCOPE}:
            raise ToolError("SCOPE_MISMATCH", f"The token has scopes other than youtube.readonly: {sorted(scopes)}",
                            f"Delete token.json and authorize again: {reauth_command()}")

    def access_token(self) -> str:
        if self.mock:
            return "mock-token"
        self.check()
        if self._creds is None:
            self._creds = self._load()[0]
        creds = self._creds
        if not creds.valid:
            from google.auth.exceptions import RefreshError
            from google.auth.transport.requests import Request

            try:
                creds.refresh(Request())
            except RefreshError as e:
                self._creds = None
                if "invalid_grant" in str(e):
                    raise ToolError("AUTH_REQUIRED", "The refresh token expired or was revoked (invalid_grant; Testing mode expires it after ~7 days).",
                                    f"Run it yourself in a terminal: {reauth_command()}") from e
                raise ToolError("AUTH_REQUIRED", f"Token refresh failed: {str(e)[:200]}", f"Run: {reauth_command()}") from e
            self._save(creds)
        return creds.token

    def _save(self, creds) -> None:
        data = json.loads(creds.to_json())
        data["scopes"] = [SCOPE]
        write_token(self.token_file, data, self.client_secret)

    def remote_check(self) -> dict:
        """tokeninfo once per session; no network in mock mode."""
        if self.mock:
            return {"scope_verified_remote": None, "remote_check": "mock — skipped"}
        if self._remote is None:
            try:
                remote = tokeninfo_scopes(self.access_token())
                self._remote = {"scope_verified_remote": remote == {SCOPE}, "scope_remote": sorted(remote)}
            except ToolError as e:
                return {"scope_verified_remote": None, "remote_check": f"{e.code}: {e.message[:120]}"}
            except Exception as e:  # noqa: BLE001 — no network etc.: not cached, retried later
                return {"scope_verified_remote": None, "remote_check": f"{type(e).__name__}: {str(e)[:120]}"}
        return dict(self._remote)

    def status(self, remote: bool = False) -> dict:
        if self.mock:
            out = {"authorized": True, "scopes": [SCOPE], "scope_ok": True, "token_expires_in_s": None, "refresh_token_present": True, "mock": True}
            if remote:
                out.update(self.remote_check())
            return out
        src = client_config_source(self.client_secret)
        out = {"authorized": False, "scopes": [], "scope_ok": False, "token_expires_in_s": None, "refresh_token_present": False, "mock": False,
               "client_secret_present": bool(src), "client_config_source": src or None}
        try:
            creds, scopes = self._load()
        except ToolError as e:
            out["hint"] = e.hint
            return out
        out["scopes"] = sorted(scopes)
        out["scope_ok"] = scopes == {SCOPE}
        out["refresh_token_present"] = bool(creds.refresh_token)
        out["authorized"] = out["scope_ok"] and bool(creds.refresh_token or creds.valid)
        if creds.expiry:
            exp = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry.tzinfo is None else creds.expiry
            out["token_expires_in_s"] = int((exp - datetime.now(timezone.utc)).total_seconds())
        if remote and out["authorized"]:
            out.update(self.remote_check())
            if out.get("scope_verified_remote") is False:
                out["scope_ok"] = out["authorized"] = False
        if not out["scope_ok"]:
            out["hint"] = f"Wrong scope — authorize again: {reauth_command()}"
        return out


def write_token(path: Path, data: dict, client_secret: Path | None = None) -> tuple[bool, str]:
    """Token write: ACL restricted BEFORE writing content (empty temp file → icacls/0600 → content → os.replace),
    content DPAPI-encrypted (Windows), without `client_secret` if it is available in the client file. Returns the ACL result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    info = _client_info(client_secret)
    if info.get("client_secret") and info.get("client_secret") == data.get("client_secret"):
        data.pop("client_secret", None)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("", encoding="utf-8")
    acl = restrict_acl(tmp)
    tmp.write_text(encrypt_token(data), encoding="utf-8")
    os.replace(tmp, path)
    return acl


def restrict_acl(path: Path) -> tuple[bool, str]:
    """Permissions for the current user only (Windows: icacls DOMAIN\\user; POSIX: 0600). The result is checked."""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME", "")
            dom = os.environ.get("USERDOMAIN", "")
            if not user:
                return False, "USERNAME not set"
            principal = f"{dom}\\{user}" if dom else user
            r = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:F"], capture_output=True, check=False)
            if r.returncode != 0:
                err = (r.stderr or r.stdout).decode("utf-8", "replace")[:200]
                return False, f"icacls code {r.returncode}: {err}"
            return True, f"icacls: only {principal}"
        os.chmod(path, 0o600)
        return True, "0600"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def run_flow() -> int:
    """Interactive browser authorization (loopback). Run by the user."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    cs = client_secret_path()
    cfg, src = client_config_from_env()
    if cfg:
        print(f"OAuth client from environment variables / plugin options ({src}).", file=sys.stderr)
        flow = InstalledAppFlow.from_client_config(cfg, scopes=[SCOPE])
    elif cs.exists():
        flow = InstalledAppFlow.from_client_secrets_file(str(cs), scopes=[SCOPE])
    else:
        print("No OAuth client configuration. Set the environment variables YTFEED_GOOGLE_CLIENT_ID and YTFEED_GOOGLE_CLIENT_SECRET\n"
              f"(recommended) or point YTFEED_GOOGLE_CLIENT_SECRET_FILE at a JSON file (looked for: {cs}).", file=sys.stderr)
        return 2
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent", access_type="offline")
    granted = set(getattr(creds, "granted_scopes", None) or creds.scopes or [SCOPE])  # S-11: GRANTED scopes, not requested ones
    if granted != {SCOPE}:
        print(f"Refused: granted scopes {sorted(granted)} != {{{SCOPE}}}. The token was NOT saved.", file=sys.stderr)
        return 3
    data = json.loads(creds.to_json())
    data["scopes"] = [SCOPE]
    acl_ok, acl_detail = write_token(token_path(), data, cs)
    enc = "DPAPI-encrypted" if os.name == "nt" else "chmod 0600"
    print(f"OK — token saved in {token_path().parent} (scope: youtube.readonly, {enc}).", file=sys.stderr)
    if not acl_ok:
        print(f"WARNING: could not restrict the token file permissions: {acl_detail}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if "--status" in sys.argv[1:]:  # launch.py doctor: status + tokeninfo (no secrets)
        print(json.dumps(AuthManager().status(remote=True), ensure_ascii=False))
        sys.exit(0)
    sys.exit(run_flow())
