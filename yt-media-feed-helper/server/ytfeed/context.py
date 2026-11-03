"""AppContext: workspace, config, cache, evidence registry, quota ledger, sources. Process singleton."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .auth import AuthManager
from .cache import Store
from .config import Config
from .evidence import EvidenceRegistry
from .paths import Workspace, data_dir, is_mock
from .quota import QuotaLedger
from .sources.base import SourceRegistry
from .sources.mock import MockTransport
from .sources.steam import SteamSource
from .sources.youtube import YouTubeSource

log = logging.getLogger("ytfeed")

_CURRENT: "AppContext | None" = None


@dataclass
class AppContext:
    ws: Workspace
    cfg: Config
    store: Store
    evidence: EvidenceRegistry
    ledger: QuotaLedger
    auth: AuthManager
    transport: Any
    youtube: YouTubeSource
    steam: SteamSource
    sources: SourceRegistry
    mock: bool
    _cfg_mtime: float = field(default=0.0)

    @classmethod
    def create(cls) -> "AppContext":
        global _CURRENT
        ws = Workspace().ensure()
        cfg = Config.load(ws.config_file)
        ddir = data_dir()
        store = Store(ddir / "cache.sqlite", int(cfg.get("cache.ttl_days_max", 30)))
        evidence = EvidenceRegistry(store)
        ledger = QuotaLedger(ddir, cfg)
        mock = is_mock()
        auth = AuthManager(mock=mock)
        if mock:
            transport: Any = MockTransport()
        else:
            from .sources.http import HttpTransport

            transport = HttpTransport(auth.access_token, float(cfg.get("steam.min_interval_s", 1.0)))
        yt = YouTubeSource(transport, store, ledger, evidence, cfg, auth)
        st = SteamSource(transport, store, evidence)
        reg = SourceRegistry()
        reg.register(yt)
        reg.register(st)
        ctx = cls(ws, cfg, store, evidence, ledger, auth, transport, yt, st, reg, mock,
                  ws.config_file.stat().st_mtime if ws.config_file.exists() else 0.0)
        _CURRENT = ctx
        log.info("ytfeed: workspace=%s data=%s mock=%s", ws.root, ddir, mock)
        return ctx

    def refresh_config(self) -> None:
        """Reload catalog/config.yaml after a manual edit (mtime comparison)."""
        try:
            m = self.ws.config_file.stat().st_mtime
        except OSError:
            return
        if m != self._cfg_mtime:
            self.cfg = Config.load(self.ws.config_file)
            self.ledger.cfg = self.cfg
            self.youtube.cfg = self.cfg
            self._cfg_mtime = m

    def close(self) -> None:
        global _CURRENT
        try:
            self.store.conn.close()
        finally:
            if hasattr(self.transport, "close"):
                self.transport.close()
            if _CURRENT is self:
                _CURRENT = None


def get_ctx() -> AppContext:
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = AppContext.create()
    _CURRENT.refresh_config()
    return _CURRENT
