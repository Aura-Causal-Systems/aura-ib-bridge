"""Platform helpers of the tray app: settings location and start-at-login on macOS."""

from __future__ import annotations

import plistlib
import sys

import pytest

from ib_bridge import config, tray

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS behaviour")


def test_applescript_string_escapes_quotes_and_backslashes():
    assert tray._applescript_string('say "hi" C:\\x') == '"say \\"hi\\" C:\\\\x"'


@mac_only
def test_settings_live_in_application_support(monkeypatch, tmp_path):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("IB_BRIDGE_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.config_dir() == tmp_path / "Library" / "Application Support" / config.CONFIG_DIRNAME


@mac_only
def test_launch_agent_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert not tray.autostart_enabled()

    tray.set_autostart(True)
    path = tmp_path / "Library" / "LaunchAgents" / f"{tray.AUTOSTART_LABEL}.plist"
    assert tray.autostart_enabled()
    plist = plistlib.loads(path.read_bytes())
    assert plist["Label"] == tray.AUTOSTART_LABEL
    assert plist["RunAtLoad"] is True
    assert plist["ProgramArguments"][-2:] == ["-m", "ib_bridge.tray"]  # from source, not frozen

    tray.set_autostart(False)
    assert not path.exists()
    assert not tray.autostart_enabled()


@mac_only
def test_menu_label_says_login_on_mac():
    assert tray.AUTOSTART_MENU_LABEL == "Start at login"
