import json

from ib_bridge.branding import API_VERSION, DESKS
from ib_bridge.config import BridgeConfig, load_config
from ib_bridge.desks import discover_pair_path, valid_pair_path
from tests.conftest import seed_virtual_fx_only


# ------------------------------------------------------------------ desk discovery
def test_pair_path_validation():
    assert valid_pair_path("/setup") == "/setup"
    assert valid_pair_path("/copilot/connect") == "/copilot/connect"
    for bad in (None, 5, "", "setup", "/x#pair=evil", "/x?y=1", "/../etc", "//evil.com", "/" + "a" * 200):
        assert valid_pair_path(bad) is None, bad


def test_discover_pair_path_reads_well_known_and_fails_closed():
    calls = []

    def ok(url, timeout):
        calls.append(url)
        return json.dumps({"pair_path": "/setup"}).encode()

    assert discover_pair_path("https://desk.example/", fetch=ok) == "/setup"
    assert calls == ["https://desk.example/.well-known/aura-ib-bridge.json"]

    def boom(url, timeout):
        raise OSError("404")

    assert discover_pair_path("https://desk.example", fetch=boom) is None
    assert discover_pair_path("https://desk.example", fetch=lambda u, t: b"not json") is None
    assert discover_pair_path("https://desk.example", fetch=lambda u, t: b'{"pair_path": "https://evil"}') is None
    # plain-http non-loopback origins are never contacted
    assert discover_pair_path("http://desk.example", fetch=ok) is None


# ------------------------------------------------------------------ config: desks, CORS, migration
def test_desk_origins_are_always_cors_allowed():
    cfg = BridgeConfig(token="t", allowed_origins=["http://localhost:3000"])
    origins = cfg.effective_allowed_origins()
    assert "http://localhost:3000" in origins
    for d in DESKS:
        assert d["origin"] in origins


def test_pairing_url_carries_token_only_in_fragment():
    cfg = BridgeConfig(token="tok123")
    url = cfg.pairing_url({"origin": "https://desk.example/"}, "/setup")
    assert url == "https://desk.example/setup#pair=tok123&bridge=http://localhost:7801"


def test_legacy_config_is_migrated_with_its_token(tmp_path, monkeypatch):
    monkeypatch.delenv("IB_BRIDGE_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    legacy = tmp_path / "ib-copilot" / "bridge.json"
    legacy.parent.mkdir()
    # BOM + 0.2 schema (single desk_origin, old journal path)
    legacy.write_text(
        "\ufeff" + json.dumps({"token": "keep-me", "tws_port": 7497, "desk_origin": "https://custom.example", "journal_path": str(legacy.parent / "journal.db")}),
        encoding="utf-8",
    )
    cfg = load_config(announce=False)
    assert cfg.token == "keep-me"
    assert cfg.tws_port == 7497
    assert any(d["origin"] == "https://custom.example" for d in cfg.desks)
    assert (tmp_path / "aura-ib-bridge" / "bridge.json").exists()
    assert "aura-ib-bridge" in cfg.journal_path
    assert legacy.exists(), "the old file is left in place"
    saved = json.loads((tmp_path / "aura-ib-bridge" / "bridge.json").read_text(encoding="utf-8"))
    assert "desk_origin" not in saved and saved["token"] == "keep-me"


# ------------------------------------------------------------------ health + ledger P&L
def test_health_reports_app_and_api_version(client, auth):
    body = client.get("/health", headers=auth).json()
    assert body["api_version"] == API_VERSION
    assert body["app_name"] == "Aura IB Bridge"


def test_account_summary_exposes_ledger_next_to_pnl_feed(client, auth, fake_ib):
    seed_virtual_fx_only(fake_ib)
    acct = client.get("/account/summary", headers=auth).json()[0]
    assert acct["unrealized_pnl"] == 34945.0 and acct["daily_pnl"] == -1256.0  # reqPnL, passed through
    assert acct["ledger_unrealized_pnl"] == 0.0 and acct["ledger_realized_pnl"] == 0.0  # what the account holds
    assert acct["net_liquidation"] == 37.09
