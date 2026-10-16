"""Bridge configuration: %APPDATA%\\aura-ib-bridge\\bridge.json (override with IB_BRIDGE_CONFIG).

On first run the file is created with a fresh bearer token. The token is the
only credential a desk needs. The tray app hands it to a desk through a pairing
link (URL fragment, never sent to a server); the console build prints it once
at creation. It is never logged.

Upgrading from the pre-0.3 "IB Copilot Bridge": its %APPDATA%\\ib-copilot\\bridge.json
is copied (token included, so existing pairings keep working). The old file is
left in place.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .branding import DESKS

DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
CONFIG_DIRNAME = "aura-ib-bridge"
LEGACY_CONFIG_DIRNAMES = ("ib-copilot",)


@dataclass
class BridgeConfig:
    tws_host: str = "127.0.0.1"
    tws_port: int = 7496  # live TWS; 7497 paper; 4001/4002 Gateway
    client_id: int = 17
    token: str = ""
    bind_host: str = "127.0.0.1"
    bind_port: int = 7801
    # Extra origins allowed to call the helper (developer desks). Desk origins are always allowed.
    allowed_origins: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_ORIGINS))
    # Web desks served by this helper: [{"id", "name", "origin"}]. Shown in the tray menu.
    desks: list[dict[str, str]] = field(default_factory=lambda: [dict(d) for d in DESKS])
    # Market-data lines the bridge may hold at once (IB default allowance is 100 per user;
    # TWS itself uses some). Book positions are pinned and never evicted.
    lines_cap: int = 60
    # Seconds a non-pinned ticker may go unrequested before it is cancelled.
    ticker_idle_seconds: int = 300
    journal_path: str = ""
    log_level: str = "INFO"
    path: str = ""  # where this config was loaded from (not persisted)

    def account_mask(self, account_id: str | None) -> str | None:
        if not account_id:
            return None
        return "*" * max(0, len(account_id) - 3) + account_id[-3:]

    @property
    def bridge_url(self) -> str:
        host = "localhost" if self.bind_host in ("127.0.0.1", "0.0.0.0") else self.bind_host
        return f"http://{host}:{self.bind_port}"

    def effective_allowed_origins(self) -> list[str]:
        origins = [o.rstrip("/") for o in self.allowed_origins]
        for d in self.desks:
            o = (d.get("origin") or "").rstrip("/")
            if o and o not in origins:
                origins.append(o)
        return origins

    def pairing_url(self, desk: dict[str, str], pair_path: str) -> str:
        """Desk URL that stores the token client-side. The fragment (#...) is never sent to the web server."""
        base = (desk.get("origin") or "").rstrip("/")
        return f"{base}{pair_path}#pair={self.token}&bridge={self.bridge_url}"


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or str(Path.home()))


def config_dir() -> Path:
    env = os.environ.get("IB_BRIDGE_CONFIG")
    if env:
        return Path(env).expanduser().parent
    return _appdata() / CONFIG_DIRNAME


def config_path() -> Path:
    env = os.environ.get("IB_BRIDGE_CONFIG")
    if env:
        return Path(env).expanduser()
    return config_dir() / "bridge.json"


def log_path() -> Path:
    return config_dir() / "bridge.log"


def _legacy_config_path() -> Path | None:
    if os.environ.get("IB_BRIDGE_CONFIG"):
        return None
    for name in LEGACY_CONFIG_DIRNAMES:
        p = _appdata() / name / "bridge.json"
        if p.exists():
            return p
    return None


def save_config(cfg: BridgeConfig) -> None:
    path = Path(cfg.path) if cfg.path else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    data.pop("path", None)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _from_raw(raw: dict) -> BridgeConfig:
    cfg = BridgeConfig(**{k: v for k, v in raw.items() if k in BridgeConfig.__dataclass_fields__ and k != "path"})
    # 0.2 configs had a single "desk_origin"; keep it as a desk if it is not a known one.
    legacy_origin = (raw.get("desk_origin") or "").rstrip("/")
    known = {(d.get("origin") or "").rstrip("/") for d in cfg.desks}
    if legacy_origin and legacy_origin not in known and not legacy_origin.startswith("http://localhost"):
        cfg.desks.append({"id": "desk", "name": "Desk", "origin": legacy_origin})
    cfg.desks = [d for d in cfg.desks if isinstance(d, dict) and d.get("origin")]
    return cfg


def load_config(create: bool = True, announce: bool = True) -> BridgeConfig:
    path = config_path()
    created = False
    resave = False
    if path.exists():
        # utf-8-sig: Notepad / PowerShell 5 write a BOM; a hand-edited config must still load.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        cfg = _from_raw(raw)
        # Older files lack newer keys (or carry removed ones): write back so the file stays current.
        resave = set(raw) != {k for k in BridgeConfig.__dataclass_fields__ if k != "path"}
    else:
        legacy = _legacy_config_path()
        if legacy is not None:
            cfg = _from_raw(json.loads(legacy.read_text(encoding="utf-8-sig")))
            if cfg.journal_path and Path(cfg.journal_path).parent == legacy.parent:
                cfg.journal_path = ""
            resave = True
        elif not create:
            raise FileNotFoundError(f"Bridge config not found at {path}")
        else:
            cfg = BridgeConfig()
            created = True
    if not cfg.token:
        cfg.token = secrets.token_urlsafe(32)
        created = True
    if not cfg.journal_path:
        cfg.journal_path = str(path.parent / "journal.db")
        resave = True
    cfg.path = str(path)
    if created or resave:
        save_config(cfg)
        if created and announce:
            # Printed exactly once, at creation, so a console user can paste it into a desk's Settings.
            print(f"[aura-ib-bridge] created config at {path}")
            print(f"[aura-ib-bridge] bearer token (paste into the desk Settings page): {cfg.token}")
    return cfg
