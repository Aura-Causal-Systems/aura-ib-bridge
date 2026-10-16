"""Aura IB Bridge - Windows tray application.

Wraps the read-only bridge (main.create_app) so a trader never sees a console:
a tray icon shows whether TWS is connected, and the menu opens each configured
web desk with a pairing link carrying the bearer token in the URL fragment
(never sent to any server) - only for desks that advertise pairing support
(see desks.py). Runs uvicorn in a background thread; the tray owns the main
thread. Logs to %APPDATA%\\aura-ib-bridge\\bridge.log.

Entry point for the PyInstaller build (packaging/build.ps1) and for
`python -m ib_bridge.tray` during development.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any

import uvicorn
from PIL import Image, ImageDraw

from . import __version__
from .branding import APP_ID, APP_NAME, LEGACY_APP_IDS
from .config import BridgeConfig, load_config, log_path, save_config
from .desks import discover_pair_path
from .ib_client import IBClient
from .main import create_app

log = logging.getLogger("ib_bridge.tray")

# Tray colours follow the desk's semantics: primary (blue) = good, amber = attention, grey = starting.
COLOR_CONNECTED = (56, 189, 248)
COLOR_WAITING = (245, 158, 11)
COLOR_STARTING = (120, 120, 130)
COLOR_BG = (14, 18, 26)


# ----------------------------------------------------------------------------- helpers
def ensure_std_streams() -> None:
    """A --windowed PyInstaller app has sys.stdout/stderr == None; libraries that print or probe
    isatty() then crash. Point both at a console log file next to bridge.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    path = log_path().with_name("bridge.console.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives for the process
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def setup_logging(level: str) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
    logging.getLogger("ib_async").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def message_box(text: str, title: str = APP_NAME, icon: int = 0x40) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, icon)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        print(f"{title}: {text}")


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def render_icon(color: tuple[int, int, int], size: int = 64) -> Image.Image:
    """Rounded dark square with the brand's crossed strokes and a status dot."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 10
    d.rounded_rectangle((pad, pad, size - pad, size - pad), radius=size // 5, fill=COLOR_BG + (255,), outline=(70, 80, 100, 255), width=2)
    w = max(2, size // 14)
    d.line((size * 0.3, size * 0.72, size * 0.7, size * 0.28), fill=color + (255,), width=w)
    d.line((size * 0.3, size * 0.28, size * 0.7, size * 0.72), fill=(90, 100, 120, 255), width=w)
    r = size // 7
    d.ellipse((size - pad - 2 * r, size - pad - 2 * r, size - pad, size - pad), fill=color + (255,), outline=COLOR_BG + (255,), width=2)
    return img


# ----------------------------------------------------------------------------- autostart (HKCU Run key)
def autostart_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, APP_ID)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            exe = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" -m ib_bridge.tray'
            winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, f'"{exe}"' if getattr(sys, "frozen", False) else exe)
        else:
            try:
                winreg.DeleteValue(k, APP_ID)
            except OSError:
                pass


def migrate_legacy_autostart() -> None:
    """The pre-0.3 'IB Copilot Bridge' registered its own Run value; replace it with ours."""
    import winreg

    found = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
            for legacy in LEGACY_APP_IDS:
                try:
                    winreg.QueryValueEx(k, legacy)
                    winreg.DeleteValue(k, legacy)
                    found = True
                except OSError:
                    pass
    except OSError:
        return
    if found and getattr(sys, "frozen", False) and not autostart_enabled():
        set_autostart(True)
        log.info("replaced legacy autostart entry")


# ----------------------------------------------------------------------------- server thread
class BridgeRuntime:
    """Owns the FastAPI app + uvicorn server running on a background thread."""

    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.client = IBClient(cfg)
        self.app = create_app(cfg=cfg, client=self.client, connect=True)
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        config = uvicorn.Config(
            self.app,
            host=self.cfg.bind_host,
            port=self.cfg.bind_port,
            log_level="warning",
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="on",
            access_log=False,
            log_config=None,  # we configure logging ourselves (file handler); uvicorn's default probes a TTY
        )
        self.server = uvicorn.Server(config)

        def quiet_resets(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            # Windows Proactor logs a traceback each time a browser aborts a request (page
            # navigation, our own fetch timeouts). Harmless; keep the log readable.
            if isinstance(context.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
                return
            loop.default_exception_handler(context)

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(quiet_resets)
            self.loop = loop
            try:
                loop.run_until_complete(self.server.serve())  # type: ignore[union-attr]
            finally:
                loop.close()

        self.thread = threading.Thread(target=run, name="bridge-server", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=8)

    def reconnect_with_port(self, port: int) -> None:
        """Switch TWS port (live/paper). Persists config; drops the IB connection so the loop reconnects."""
        self.cfg.tws_port = port
        save_config(self.cfg)
        if self.loop and self.client.connected:
            self.loop.call_soon_threadsafe(self.client.ib.disconnect)

    # status snapshot for the tray (plain attribute reads; safe across threads)
    def status(self) -> dict[str, Any]:
        c = self.client
        connected = c.connected
        accts = c.ib.managedAccounts() if connected else []
        return {
            "connected": connected,
            "mode": c.trading_mode(),
            "accounts": [c._mask(a) for a in accts],
            "lines": c.lines_in_use(),
            "last_error": c.last_error,
            "tws_port_open": port_in_use(self.cfg.tws_host, self.cfg.tws_port),
        }


# ----------------------------------------------------------------------------- tray
def run_tray() -> int:
    import pystray

    ensure_std_streams()
    cfg = load_config(announce=False)
    setup_logging(cfg.log_level)
    log.info(
        "%s v%s starting (bind %s, TWS %s:%s, desks %s)",
        APP_NAME, __version__, cfg.bridge_url, cfg.tws_host, cfg.tws_port, ", ".join(d.get("origin", "?") for d in cfg.desks),
    )
    migrate_legacy_autostart()

    if port_in_use(cfg.bind_host, cfg.bind_port):
        message_box(
            f"{APP_NAME} (or an older IB Copilot Bridge) is already running: port {cfg.bind_port} is in use.\n\n"
            "Look for its icon next to the clock, or quit it there and start this one again.",
            icon=0x30,
        )
        return 1

    runtime = BridgeRuntime(cfg)
    runtime.start()

    state = {"color": COLOR_STARTING, "text": "starting…"}

    def open_desk(desk: dict[str, str]) -> None:
        """Open a desk, paired when the desk advertises pairing support. Runs off the menu thread."""

        def work() -> None:
            pair_path = discover_pair_path(desk["origin"])
            if pair_path:
                webbrowser.open(cfg.pairing_url(desk, pair_path))
            else:
                log.info("%s does not advertise helper pairing; opening without token", desk.get("name"))
                webbrowser.open(desk["origin"].rstrip("/") + "/")

        threading.Thread(target=work, name="open-desk", daemon=True).start()

    def copy_pairing_link(desk: dict[str, str]) -> None:
        def work() -> None:
            pair_path = discover_pair_path(desk["origin"])
            if not pair_path:
                message_box(f"{desk.get('name', 'This desk')} does not support connecting to {APP_NAME} yet.", icon=0x40)
                return
            url = cfg.pairing_url(desk, pair_path)
            try:
                subprocess.run(["clip"], input=url.encode("utf-16le"), check=True, creationflags=0x08000000)
                log.info("pairing link for %s copied to clipboard", desk.get("name"))
            except Exception as exc:  # noqa: BLE001
                message_box(f"Could not copy: {exc}", icon=0x30)

        threading.Thread(target=work, name="copy-pairing", daemon=True).start()

    def open_log(_icon: Any = None, _item: Any = None) -> None:
        os.startfile(str(log_path()))  # type: ignore[attr-defined]

    def open_config_folder(_icon: Any = None, _item: Any = None) -> None:
        os.startfile(str(log_path().parent))  # type: ignore[attr-defined]

    def set_port(port: int):
        def _handler(_icon: Any = None, _item: Any = None) -> None:
            if cfg.tws_port != port:
                runtime.reconnect_with_port(port)
                log.info("TWS port switched to %s", port)

        return _handler

    def toggle_autostart(_icon: Any = None, _item: Any = None) -> None:
        set_autostart(not autostart_enabled())

    def quit_app(icon: Any, _item: Any = None) -> None:
        log.info("quit requested")
        runtime.stop()
        icon.stop()

    def status_text(_item: Any = None) -> str:
        return state["text"]

    def desk_action(fn: Any, desk: dict[str, str]) -> Any:
        return lambda _icon=None, _item=None: fn(desk)

    open_items = [
        pystray.MenuItem(f"Open {d.get('name', d['origin'])}", desk_action(open_desk, d), default=(i == 0)) for i, d in enumerate(cfg.desks)
    ]
    copy_items = [pystray.MenuItem(d.get("name", d["origin"]), desk_action(copy_pairing_link, d)) for d in cfg.desks]

    menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        *open_items,
        pystray.MenuItem("Copy pairing link", pystray.Menu(*copy_items)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "TWS account",
            pystray.Menu(
                pystray.MenuItem("Live (port 7496)", set_port(7496), checked=lambda _i: cfg.tws_port == 7496, radio=True),
                pystray.MenuItem("Paper (port 7497)", set_port(7497), checked=lambda _i: cfg.tws_port == 7497, radio=True),
                pystray.MenuItem("IB Gateway live (4001)", set_port(4001), checked=lambda _i: cfg.tws_port == 4001, radio=True),
                pystray.MenuItem("IB Gateway paper (4002)", set_port(4002), checked=lambda _i: cfg.tws_port == 4002, radio=True),
            ),
        ),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda _i: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("View log", open_log),
        pystray.MenuItem("Open settings folder", open_config_folder),
        pystray.MenuItem(f"{APP_NAME} v{__version__}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )

    icon = pystray.Icon(APP_ID, render_icon(COLOR_STARTING), APP_NAME, menu)

    def watcher() -> None:
        last_connected: bool | None = None
        while icon.visible or last_connected is None:
            try:
                st = runtime.status()
                if st["connected"]:
                    mode = st["mode"].upper()
                    accts = ", ".join(st["accounts"]) or "?"
                    text = f"TWS connected · {mode} · {accts} · {st['lines']} lines"
                    color = COLOR_CONNECTED
                elif st["tws_port_open"]:
                    text = f"TWS port {cfg.tws_port} open · waiting for API acceptance"
                    color = COLOR_WAITING
                else:
                    text = f"TWS not detected on port {cfg.tws_port} · start TWS and enable the API"
                    color = COLOR_WAITING
                if text != state["text"]:
                    state["text"] = text
                    icon.title = f"{APP_NAME}: {text}"
                if color != state["color"]:
                    state["color"] = color
                    icon.icon = render_icon(color)
                if last_connected is not None and st["connected"] != last_connected:
                    log.info("TWS %s", "connected" if st["connected"] else "disconnected")
                last_connected = st["connected"]
            except Exception as exc:  # noqa: BLE001
                log.warning("status watcher: %s", exc)
            time.sleep(2)

    def on_ready(icon_: Any) -> None:
        icon_.visible = True
        threading.Thread(target=watcher, name="tray-watcher", daemon=True).start()
        # --open-desk opens the first desk; --open-desk=<id> a specific one (installer uses the former).
        for arg in sys.argv[1:]:
            if arg == "--open-desk" or arg.startswith("--open-desk="):
                wanted = arg.partition("=")[2]
                desk = next((d for d in cfg.desks if d.get("id") == wanted), cfg.desks[0] if cfg.desks else None)
                if desk:
                    open_desk(desk)

    icon.run(setup=on_ready)
    log.info("exited")
    return 0


def main() -> int:
    if sys.platform != "win32":
        print("The tray app is Windows-only; use run-bridge.ps1 / uvicorn directly elsewhere.")
        return 1
    try:
        return run_tray()
    except Exception as exc:  # noqa: BLE001
        ensure_std_streams()
        if not logging.getLogger().handlers:
            setup_logging("INFO")
        logging.getLogger("ib_bridge.tray").exception("fatal")
        message_box(f"{APP_NAME} could not start:\n\n{exc}\n\nSee the log at {log_path()}", icon=0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
