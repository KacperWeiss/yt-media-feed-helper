#!/usr/bin/env python3
"""yt-media-feed-helper — launcher of the `ytfeed` MCP server (standard library only).

Subcommands:
  install  — creates a venv in the plugin data dir (${CLAUDE_PLUGIN_DATA}) and installs requirements.txt
  serve    — (default) ensures the venv, then runs `python -m ytfeed.server` inheriting stdio
  auth     — one-time OAuth authorization (youtube.readonly) — RUN IT YOURSELF in a terminal
  doctor   — installation diagnostics (JSON on stdout)

All logs go to stderr — in `serve` mode stdout is the MCP protocol channel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

PLUGIN_NAME = "yt-media-feed-helper"
MARKETPLACE = "yt-media-feed-helper-marketplace"
SERVER_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SERVER_DIR.parent
REQUIREMENTS = SERVER_DIR / "requirements.txt"
LOCK = SERVER_DIR / "requirements.lock"  # S-08: exact versions of the whole dependency tree (constraints)
HASHES = SERVER_DIR / "requirements.hashes.txt"  # S-08 round 2: Windows x64 / CPython 3.15, pip --require-hashes (userConfig strict_deps)
# S-04: the server starts with an isolated interpreter (-I: no cwd/PYTHONPATH/PYTHONSTARTUP/user site) with cwd = SERVER_DIR;
# the `ytfeed` package path is added explicitly and the project dir is passed via YTFEED_PROJECT_DIR.
BOOT = "import sys, runpy; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module(sys.argv.pop(1), run_name='__main__', alter_sys=True)"


def log(msg: str) -> None:
    print(f"[ytfeed-launch] {msg}", file=sys.stderr, flush=True)


def _env(name: str) -> str:
    """Variable value; an unexpanded `${...}` (no userConfig value) is treated as empty."""
    v = os.environ.get(name, "").strip()
    return "" if v.startswith("${") else v


# Copy of ytfeed.paths.SETTINGS (launcher = stdlib only, no package import): environment variable > plugin option.
SETTINGS: dict[str, tuple[str, ...]] = {
    "workspace": ("YTFEED_WORKSPACE", "CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR"),
    "google_client_id": ("YTFEED_GOOGLE_CLIENT_ID", "CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_ID"),
    "google_client_secret": ("YTFEED_GOOGLE_CLIENT_SECRET", "CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET"),
    "google_client_secret_file": ("YTFEED_GOOGLE_CLIENT_SECRET_FILE", "YTFEED_CLIENT_SECRET", "CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET_PATH"),
    "mock": ("YTFEED_MOCK", "CLAUDE_PLUGIN_OPTION_MOCK_MODE"),
    "report_budget_units": ("YTFEED_REPORT_BUDGET_UNITS", "CLAUDE_PLUGIN_OPTION_REPORT_BUDGET_UNITS"),
    "strict_deps": ("YTFEED_STRICT_DEPS", "CLAUDE_PLUGIN_OPTION_STRICT_DEPS"),
    "steam_api_key": ("YTFEED_STEAM_API_KEY", "CLAUDE_PLUGIN_OPTION_STEAM_API_KEY"),
}
SECRET_SETTINGS = {"google_client_secret", "steam_api_key"}


def _setting_source(key: str) -> tuple[str, str]:
    for name in SETTINGS[key]:
        v = _env(name)
        if v:
            return v, name
    return "", ""


def _setting(key: str) -> str:
    return _setting_source(key)[0]


def settings_report() -> dict[str, str]:
    """Where each setting comes from — WITHOUT secret values (only the variable name / 'not set')."""
    out = {}
    for key in SETTINGS:
        v, name = _setting_source(key)
        if not name:
            out[key] = "not set"
        elif key in SECRET_SETTINGS:
            out[key] = f"set ({'plugin option / keychain' if name.startswith('CLAUDE_PLUGIN_OPTION_') else name})"
        else:
            out[key] = f"{v} ({'plugin option' if name.startswith('CLAUDE_PLUGIN_OPTION_') else name})"
    return out


def data_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    for name in ("YTFEED_DATA_DIR", "CLAUDE_PLUGIN_DATA"):
        v = _env(name)
        if v:
            return Path(v)
    base = Path.home() / ".claude" / "plugins" / "data"
    found = sorted(base.glob(f"{PLUGIN_NAME}*")) if base.exists() else []
    if found:
        return found[0]
    return base / f"{PLUGIN_NAME}-{MARKETPLACE}"


def venv_python(vdir: Path) -> Path:
    return vdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def strict_deps() -> bool:
    """userConfig.strict_deps (YTFEED_STRICT_DEPS) — install only packages with verified hashes."""
    return _setting("strict_deps").lower() in ("1", "true", "yes", "on")


def requirements_hash(strict: bool | None = None) -> str:
    strict = strict_deps() if strict is None else strict
    h = hashlib.sha256(REQUIREMENTS.read_bytes())
    if LOCK.exists():
        h.update(LOCK.read_bytes())
    if strict:
        h.update(b"strict:" + HASHES.read_bytes())
    return h.hexdigest()[:16]


def venv_ok(ddir: Path) -> bool:
    vdir = ddir / "venv"
    stamp = vdir / ".requirements.sha"
    return venv_python(vdir).exists() and stamp.exists() and stamp.read_text().strip() == requirements_hash()


def install(ddir: Path, force: bool = False) -> Path:
    vdir = ddir / "venv"
    if venv_ok(ddir) and not force:
        log(f"venv up to date: {vdir}")
        return venv_python(vdir)
    ddir.mkdir(parents=True, exist_ok=True)
    if force and vdir.exists():
        shutil.rmtree(vdir)
    if not venv_python(vdir).exists():
        log(f"creating venv: {vdir}")
        venv.EnvBuilder(with_pip=True, clear=False).create(vdir)
    py = venv_python(vdir)
    log("installing dependencies (pip install -r requirements.txt -c requirements.lock) …")
    # pip's stdout is redirected to stderr — stdout is reserved for MCP.
    # -c requirements.lock: every installed package (including transitive dependencies) at an exactly pinned version (S-08).
    cmd = [str(py), "-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-q", "--prefer-binary", "-r", str(REQUIREMENTS)]
    if LOCK.exists():
        cmd += ["-c", str(LOCK)]
    if strict_deps():
        if not HASHES.exists():
            raise SystemExit("strict_deps=true, but requirements.hashes.txt is missing")
        log("strict_deps: pip install --require-hashes --no-deps -r requirements.hashes.txt (Windows x64 / CPython 3.15)")
        cmd = [str(py), "-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-q", "--require-hashes", "--no-deps",
               "-r", str(HASHES)]
    rc = subprocess.call(cmd, stdout=sys.stderr, stderr=sys.stderr, cwd=str(SERVER_DIR))
    if rc != 0:
        raise SystemExit(f"pip install failed (code {rc})")
    (vdir / ".requirements.sha").write_text(requirements_hash())
    log("installation finished")
    return py


def _server_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONSTARTUP", None)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if not _env("YTFEED_PROJECT_DIR"):
        env["YTFEED_PROJECT_DIR"] = os.getcwd()  # project dir (workspace fallback) — the server runs with cwd = SERVER_DIR
    return env


def _module_cmd(py: Path, module: str) -> list[str]:
    """`python -I -X utf8 -c BOOT <server_dir> <module>` — isolated from cwd (S-04)."""
    return [str(py), "-I", "-X", "utf8", "-c", BOOT, str(SERVER_DIR), module]


def resolve_python(ddir: Path) -> Path:
    # YTFEED_SKIP_VENV=1 → the current interpreter (tests/dev, when dependencies are already available)
    if _env("YTFEED_SKIP_VENV") in ("1", "true"):
        return Path(sys.executable)
    if not venv_ok(ddir):
        return install(ddir)
    return venv_python(ddir / "venv")


def serve(ddir: Path) -> int:
    py = resolve_python(ddir)
    env = _server_env()
    env.setdefault("YTFEED_DATA_DIR", str(ddir))
    # no os.execv (Windows) — stdin/stdout/stderr are inherited
    return subprocess.call(_module_cmd(py, "ytfeed.server"), env=env, cwd=str(SERVER_DIR))


def auth(ddir: Path, client_secret: str | None) -> int:
    py = resolve_python(ddir)
    env = _server_env()
    if client_secret:
        env["YTFEED_GOOGLE_CLIENT_SECRET_FILE"] = client_secret
    return subprocess.call(_module_cmd(py, "ytfeed.auth"), env=env, cwd=str(SERVER_DIR))


def _is_store_alias(p: str) -> bool:
    return "windowsapps" in p.lower()


def doctor(ddir: Path) -> int:
    report: dict = {"plugin_root": str(PLUGIN_ROOT), "data_dir": str(ddir), "checks": [], "fixes": []}

    def check(name: str, ok: bool, detail: str = "", fix: str = "") -> None:
        report["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok and fix:
            report["fixes"].append(fix)

    exe = sys.executable
    check("python_version", sys.version_info >= (3, 10), sys.version.split()[0], "Install Python >= 3.10 from python.org")
    which = shutil.which("python") or ""
    check("python_not_store_alias", not _is_store_alias(which or exe), which or exe,
          "Disable the Microsoft Store alias (Settings → App execution aliases) or use `py -3` in hooks.json")
    check("venv", venv_ok(ddir), str(ddir / "venv"), f'python "{SERVER_DIR / "launch.py"}" install')

    secrets = _secrets_dir()
    token = secrets / "token.json"
    cs = _setting("google_client_secret_file") or str(secrets / "client_secret.json")
    mock = _setting("mock").lower() in ("1", "true", "yes", "on")
    report["settings"] = settings_report()
    _, id_src = _setting_source("google_client_id")
    _, sec_src = _setting_source("google_client_secret")
    if id_src and sec_src:
        detail = "environment variables / plugin options (client_id + client_secret)"
    elif id_src or sec_src:
        detail = "incomplete: only one of YTFEED_GOOGLE_CLIENT_ID / YTFEED_GOOGLE_CLIENT_SECRET is set"
    else:
        detail = "JSON file" if Path(cs).exists() else "missing"
    have_client = bool(id_src and sec_src) or Path(cs).exists()
    check("client_config_present", mock or have_client, "(mock)" if mock else detail,
          "Set YTFEED_GOOGLE_CLIENT_ID and YTFEED_GOOGLE_CLIENT_SECRET (see README) or YTFEED_GOOGLE_CLIENT_SECRET_FILE")
    report["warnings"] = []
    if Path(cs).exists() and id_src and sec_src:
        report["warnings"].append(f"The client config comes from variables, but the file {cs} still exists — delete the redundant secret copy.")
    for name in ("YTFEED_GOOGLE_CLIENT_SECRET", "YTFEED_STEAM_API_KEY"):
        if os.name == "nt" and _machine_env_has(name):
            report["warnings"].append(f"{name} is set at MACHINE level (visible to all accounts) — move it to user level.")
    check("token_present", mock or token.exists(), "(mock)" if mock else ("present" if token.exists() else "missing"),
          f'Run it yourself in a terminal: python "{SERVER_DIR / "launch.py"}" auth')
    if mock:
        check("token_scope_remote", True, "(mock — no network)")
    elif token.exists() and venv_ok(ddir):
        # §5a-9: actual token scopes per Google tokeninfo (GET), in the venv process (DPAPI, token refresh)
        try:
            r = subprocess.run(_module_cmd(venv_python(ddir / "venv"), "ytfeed.auth") + ["--status"], capture_output=True, timeout=60,
                               env=_server_env(), cwd=str(SERVER_DIR))
            st = json.loads(r.stdout.decode("utf-8") or "{}")
            ok = st.get("scope_verified_remote") is True
            check("token_scope_remote", ok, f"scopes per Google: {st.get('scope_remote') or st.get('remote_check') or st.get('hint')}",
                  f'Delete the token and authorize again: python "{SERVER_DIR / "launch.py"}" auth')
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            check("token_scope_remote", False, f"{type(e).__name__}: {str(e)[:120]}")

    ws = Path(_setting("workspace") or _env("CLAUDE_PROJECT_DIR") or os.getcwd())
    check("workspace_exists", ws.exists(), str(ws))
    tax = ws / "catalog" / "taxonomy.yaml"
    check("workspace_initialized", tax.exists(), str(tax), "Start the MCP server (e.g. /mcp) — it creates catalog/ from the templates")
    logs = ws / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        probe = logs / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        check("logs_writable", True, str(logs))
    except OSError as e:
        check("logs_writable", False, f"{logs}: {e}")
    if tax.exists():
        try:
            sys.path.insert(0, str(SERVER_DIR))
            from ytfeed.catalog import rules  # stdlib + PyYAML (if available)

            issues = rules.validate_workspace(ws)
            errs = [i for i in issues if i["level"] == "error"]
            check("catalog_valid", not errs, f"{len(errs)} errors, {len(issues) - len(errs)} warnings")
        except ImportError as e:
            check("catalog_valid", True, f"skipped (no PyYAML in this interpreter: {e})")
    report["ok"] = all(c["ok"] for c in report["checks"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _machine_env_has(name: str) -> bool:
    """Windows: whether the variable is defined in the MACHINE environment (HKLM) — secrets belong only in HKCU."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        return False


def _secrets_dir(os_name: str | None = None) -> Path:
    """Same rule as ytfeed.paths.secrets_dir(): YTFEED_SECRETS_DIR > %APPDATA% (Windows) > $XDG_CONFIG_HOME or ~/.config."""
    v = _env("YTFEED_SECRETS_DIR")
    if v:
        return Path(v)
    if (os_name or os.name) == "nt":
        return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / PLUGIN_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / PLUGIN_NAME


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # -I ignores PYTHONUTF8 — doctor JSON and logs are always UTF-8
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "install", "auth", "doctor"])
    ap.add_argument("--data-dir", help="overrides the data dir (default ${CLAUDE_PLUGIN_DATA})")
    ap.add_argument("--force", action="store_true", help="install: recreate the venv from scratch")
    ap.add_argument("--client-secret", help="auth: path to client_secret_*.json")
    a = ap.parse_args(argv)
    ddir = data_dir(a.data_dir)
    if a.command == "install":
        install(ddir, force=a.force)
        return 0
    if a.command == "auth":
        return auth(ddir, a.client_secret)
    if a.command == "doctor":
        return doctor(ddir)
    return serve(ddir)


if __name__ == "__main__":
    sys.exit(main())
