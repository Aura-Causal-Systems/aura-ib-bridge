import inspect

import ib_bridge.events_discovery as events_mod
import ib_bridge.ib_client as ib_client_mod
import ib_bridge.routes as routes_mod
import ib_bridge.service as service_mod
from tests.conftest import seed_es_position


def test_requires_bearer_token(client):
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer test-token"}).status_code == 200


def test_health_when_disconnected_is_honest(client, auth):
    body = client.get("/health", headers=auth).json()
    assert body["connected"] is False
    assert body["account_ids_masked"] == []
    assert body["tws_server_version"] is None
    assert body["read_only"] is True
    assert body["trading_mode"] == "live"  # inferred from port 7496 in the default config


def test_snapshot_disconnected_has_no_numbers(client, auth):
    body = client.get("/snapshot", headers=auth).json()
    assert body["positions"] == []
    assert body["account_summary"] == []
    assert body["quotes"] == {}
    assert "bridge not connected" in " ".join(body["warnings"])


def test_snapshot_with_live_es_position(client, auth, fake_ib, ib_client):
    seed_es_position(fake_ib, ib_client)
    body = client.get("/snapshot", headers=auth).json()
    assert fake_ib.market_data_type_requested is None  # only set on real connect
    assert body["health"]["connected"] is True
    assert body["health"]["account_ids_masked"] == ["*****567"]
    pos = body["positions"][0]
    assert pos["contract"]["local_symbol"] == "ESZ6"
    assert pos["position"] == 2.0
    assert pos["avg_price"] == 5400.25 and pos["avg_price_basis"] == "avg_cost_div_multiplier"
    assert pos["unrealized_pnl"] == 1000.0 and pos["pnl_source"] == "pnl_single"
    assert pos["is_event_contract"] is False
    q = body["quotes"]["495512563"]
    assert q["last"] == 5410.25 and q["freshness"] == "live" and q["min_tick"] == 0.25
    acct = body["account_summary"][0]
    assert acct["net_liquidation"] == 250000.5 and acct["buying_power"] == 900000.0
    assert acct["total_cash"] is None  # not delivered -> null, not zero
    assert acct["daily_pnl"] == 1025.0


def test_quotes_bad_conid_is_400(client, auth):
    assert client.get("/quotes?conIds=abc", headers=auth).status_code == 400


def test_resolve_unknown_instrument_404(client, auth):
    assert client.get("/contracts/resolve?key=NOPE", headers=auth).status_code == 404


def test_resolve_micro_without_verified_micro_404(client, auth):
    r = client.get("/contracts/resolve?key=NG&micro=true", headers=auth)
    assert r.status_code == 404
    assert "no verified micro" in r.json()["detail"]


def test_instruments_endpoint_lists_map(client, auth):
    rows = client.get("/instruments", headers=auth).json()
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)), "instrument keys must be unique"
    assert {"ES", "ZN", "USDJPY", "6J", "BUND", "EURUSD", "6E"} <= set(keys)
    # FX ideas trade spot on IDEALPRO; the futures are alternatives for the same engine view
    jpy_spot = next(r for r in rows if r["key"] == "USDJPY")
    assert jpy_spot["ib"]["sec_type"] == "CASH" and jpy_spot["ib"]["exchange"] == "IDEALPRO"
    assert jpy_spot["direction_sign"] == 1 and jpy_spot["role"] == "primary"
    assert jpy_spot["ib"]["tick_size"] is None  # taken from IB minTick at runtime, never guessed
    jpy_fut = next(r for r in rows if r["key"] == "6J")
    assert jpy_fut["direction_sign"] == -1 and jpy_fut["ib"]["trading_class"] == "6J" and jpy_fut["role"] == "alternative"
    assert jpy_fut["engine_symbol"] == "USDJPY"
    zn = next(r for r in rows if r["key"] == "ZN")
    assert zn["notation"] == "32nds"
    # exactly one primary instrument per engine symbol
    primaries = [r["engine_symbol"] for r in rows if r["role"] == "primary" and r["engine_symbol"]]
    assert len(primaries) == len(set(primaries))


def test_find_instrument_prefers_keys_then_primary():
    from ib_bridge.instruments import contract_for, find_instrument

    assert find_instrument("EURUSD").ib.sec_type == "CASH"
    assert find_instrument("6E").ib.trading_class == "6E"
    assert find_instrument("eurusd").key == "EURUSD"
    assert find_instrument("FGBL").key == "BUND"  # trading class still works
    fx = contract_for(find_instrument("USDJPY"))
    assert (fx.secType, fx.symbol, fx.currency, fx.exchange) == ("CASH", "USD", "JPY", "IDEALPRO")


def test_resolve_spot_fx_returns_the_single_contract(client, auth, fake_ib):
    from types import SimpleNamespace

    from tests.conftest import make_contract

    fake_ib._connected = True
    eur = make_contract(conId=12087792, symbol="EUR", localSymbol="EUR.USD", secType="CASH", exchange="IDEALPRO", currency="USD", tradingClass="EUR.USD")
    fake_ib._details[eur.conId] = SimpleNamespace(contract=eur, minTick=5e-05, realExpirationDate="", lastTradeTime="", longName="European Monetary Union Euro")
    body = client.get("/contracts/resolve?key=EURUSD", headers=auth).json()
    assert body["front"]["con_id"] == 12087792 and body["front"]["sec_type"] == "CASH"
    assert body["next"] is None and body["roll_note"] is None
    assert body["min_tick"] == 5e-05


def test_cors_preflight_carries_private_network_header(client):
    r = client.options(
        "/snapshot",
        headers={
            "Origin": "https://desk.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://desk.example"
    assert r.headers.get("access-control-allow-private-network") == "true"


def _walk_routes(routes):
    """FastAPI >= 0.140 nests included routers; flatten to concrete routes."""
    for r in routes:
        inner = getattr(r, "original_router", None) or getattr(r, "router", None)
        if inner is not None and getattr(inner, "routes", None):
            yield from _walk_routes(inner.routes)
        elif getattr(r, "methods", None):
            yield r


def test_no_write_paths_exist(app):
    """The bridge is read-only by construction: no POST/PUT/DELETE routes, no order calls anywhere."""
    concrete = list(_walk_routes(app.routes))
    assert len(concrete) >= 10, "route walk found nothing - the assertion below would be vacuous"
    for route in concrete:
        assert not (set(route.methods) & {"POST", "PUT", "PATCH", "DELETE"}), f"write method on {route.path}"
    assert {"/health", "/snapshot", "/positions", "/events/quotes"} <= {r.path for r in concrete}
    forbidden = ("placeOrder", "cancelOrder", "reqGlobalCancel", "whatIfOrder", "exerciseOptions", "modifyOrder")
    for mod in (ib_client_mod, service_mod, routes_mod, events_mod):
        src = inspect.getsource(mod)
        for name in forbidden:
            assert name not in src, f"{name} referenced in {mod.__name__}"
