"""Paths: workspace (catalog/, reports/, logs/), data dir (cache, quota), OAuth secrets.

Local server writes are allowed ONLY to the whitelist (`assert_writable`) — layer L2 §9.1.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

PLUGIN_NAME = "yt-media-feed-helper"
SERVER_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SERVER_DIR.parent
TEMPLATES = PLUGIN_ROOT / "templates"


def env(name: str) -> str:
    """Returns an environment variable; an unexpanded `${...}` (no userConfig value) counts as empty."""
    v = os.environ.get(name, "").strip()
    return "" if v.startswith("${") else v


# Settings: the user's environment variable (YTFEED_*) takes precedence over the plugin option
# (userConfig → CLAUDE_PLUGIN_OPTION_*; `sensitive` values are kept in the system keychain).
# The first non-empty name wins. Secret values are NEVER returned by tools or logged.
SETTINGS: dict[str, tuple[str, ...]] = {
    "workspace": ("YTFEED_WORKSPACE", "CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR"),
    "google_client_id": ("YTFEED_GOOGLE_CLIENT_ID", "CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_ID"),
    "google_client_secret": ("YTFEED_GOOGLE_CLIENT_SECRET", "CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET"),
    # YTFEED_CLIENT_SECRET = legacy name (file path), kept for compatibility
    "google_client_secret_file": ("YTFEED_GOOGLE_CLIENT_SECRET_FILE", "YTFEED_CLIENT_SECRET", "CLAUDE_PLUGIN_OPTION_GOOGLE_CLIENT_SECRET_PATH"),
    "mock": ("YTFEED_MOCK", "CLAUDE_PLUGIN_OPTION_MOCK_MODE"),
    "report_budget_units": ("YTFEED_REPORT_BUDGET_UNITS", "CLAUDE_PLUGIN_OPTION_REPORT_BUDGET_UNITS"),
    "strict_deps": ("YTFEED_STRICT_DEPS", "CLAUDE_PLUGIN_OPTION_STRICT_DEPS"),
    "steam_api_key": ("YTFEED_STEAM_API_KEY", "CLAUDE_PLUGIN_OPTION_STEAM_API_KEY"),
}


def setting_source(key: str) -> tuple[str, str]:
    """(value, name of the variable it came from) — ("", "") when unset."""
    for name in SETTINGS[key]:
        v = env(name)
        if v:
            return v, name
    return "", ""


def setting(key: str) -> str:
    return setting_source(key)[0]


def source_label(var_name: str) -> str:
    """Source description without the value: 'env:YTFEED_X' / 'plugin_option' / 'file' / ''."""
    if not var_name:
        return ""
    return "plugin_option" if var_name.startswith("CLAUDE_PLUGIN_OPTION_") else f"env:{var_name}"


def workspace_dir() -> Path:
    v = setting("workspace") or env("YTFEED_PROJECT_DIR") or env("CLAUDE_PROJECT_DIR")
    return Path(v).resolve() if v else Path.cwd().resolve()


def data_dir() -> Path:
    v = env("YTFEED_DATA_DIR") or env("CLAUDE_PLUGIN_DATA")
    if v:
        p = Path(v)
    else:
        try:
            import platformdirs

            p = Path(platformdirs.user_cache_dir(PLUGIN_NAME, appauthor=False))
        except ImportError:  # pragma: no cover
            p = Path.home() / f".{PLUGIN_NAME}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def secrets_dir(os_name: str | None = None) -> Path:
    """OAuth secrets dir — the same rule as `launch.py` and the hooks (`_common.secrets_dirs`):
    YTFEED_SECRETS_DIR overrides; Windows: %APPDATA%/yt-media-feed-helper;
    POSIX (Linux and macOS): $XDG_CONFIG_HOME/yt-media-feed-helper or ~/.config/yt-media-feed-helper."""
    v = env("YTFEED_SECRETS_DIR")
    if v:
        return Path(v)
    if (os_name or os.name) == "nt":
        return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / PLUGIN_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / PLUGIN_NAME


def token_path() -> Path:
    return secrets_dir() / "token.json"


def client_secret_path() -> Path:
    v = setting("google_client_secret_file")
    return Path(v) if v else secrets_dir() / "client_secret.json"


GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def client_config_from_env() -> tuple[dict, str]:
    """OAuth client config (Desktop app) from environment variables / plugin options — no JSON file.

    Returns ({"installed": {...}}, source label) or ({}, "") when the client_id + client_secret pair is missing.
    """
    cid, cid_src = setting_source("google_client_id")
    sec, sec_src = setting_source("google_client_secret")
    if not (cid and sec):
        return {}, ""
    labels = sorted({source_label(cid_src), source_label(sec_src)})
    return {"installed": {"client_id": cid, "client_secret": sec, "auth_uri": GOOGLE_AUTH_URI, "token_uri": GOOGLE_TOKEN_URI,
                          "redirect_uris": ["http://localhost"]}}, "+".join(labels)


def is_mock() -> bool:
    return setting("mock").lower() in ("1", "true", "yes", "on")


class Workspace:
    def __init__(self, root: Path | None = None):
        self.root = (root or workspace_dir()).resolve()
        self.catalog = self.root / "catalog"
        self.reports = self.root / "reports"
        self.logs = self.root / "logs"
        self.proposals = self.catalog / "proposals"
        self.taxonomy_file = self.catalog / "taxonomy.yaml"
        self.channels_file = self.catalog / "channels.yaml"
        self.config_file = self.catalog / "config.yaml"
        self.history_file = self.catalog / "history.jsonl"

    def ensure(self) -> "Workspace":
        """First-run initialization: copies templates, never overwrites existing files."""
        for d in (self.catalog, self.proposals, self.reports, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        if not self.taxonomy_file.exists():
            shutil.copyfile(TEMPLATES / "taxonomy.default.yaml", self.taxonomy_file)
        if not self.config_file.exists():
            shutil.copyfile(TEMPLATES / "config.default.yaml", self.config_file)
        if not self.channels_file.exists():
            atomic_write_text(self.channels_file, "# yt-media-feed-helper — channel catalog (editable; MCP writes atomically)\nschema_version: 1\nchannels: {}\n", self)
        return self

    def allowed_roots(self) -> list[Path]:
        return [self.catalog, self.reports, self.logs, data_dir().resolve()]

    def assert_writable(self, path: Path) -> Path:
        p = path.resolve()
        for root in self.allowed_roots():
            try:
                p.relative_to(root.resolve())
                return p
            except ValueError:
                continue
        raise PermissionError(f"Write outside the server's path whitelist: {p}")


def atomic_write_text(path: Path, text: str, ws: Workspace | None = None) -> None:
    """Atomic write: temp file in the same directory + os.replace."""
    if ws is not None:
        ws.assert_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
