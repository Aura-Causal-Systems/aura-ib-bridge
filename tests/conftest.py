"""Test fixtures: a fake ib_async.IB with just enough surface for the service layer."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ib_bridge.config import BridgeConfig
from ib_bridge.ib_client import IBClient
from ib_bridge.main import create_app

NAN = float("nan")


def make_contract(**kw: Any) -> SimpleNamespace:
    base = dict(
        conId=0, symbol="", localSymbol="", secType="", exchange="", primaryExchange="", currency="USD",
        lastTradeDateOrContractMonth="", multiplier="", tradingClass="", right="", strike=0.0, description="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def make_ticker(contract: SimpleNamespace, **kw: Any) -> SimpleNamespace:
    base = dict(
        contract=contract, bid=NAN, ask=NAN, last=NAN, close=NAN, volume=NAN, bidSize=NAN, askSize=NAN,
        marketDataType=None, time=None, halted=NAN,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@dataclass
class FakeClientStub:
    server_version: int = 187

    def serverVersion(self) -> int:
        return self.server_version


@dataclass
class FakeIB:
    """Mimics the subset of ib_async.IB used by the bridge. Not connected by default."""

    _connected: bool = False
    accounts: list[str] = field(default_factory=lambda: ["U1234567"])
    _positions: list[Any] = field(default_factory=list)
    _portfolio: list[Any] = field(default_factory=list)
    _summary: dict[str, list[Any]] = field(default_factory=dict)
    _values: dict[str, list[Any]] = field(default_factory=dict)
    _fills: list[Any] = field(default_factory=list)
    _trades: list[Any] = field(default_factory=list)
    _details: dict[int, Any] = field(default_factory=dict)
    _tickers: dict[int, Any] = field(default_factory=dict)
    client: FakeClientStub = field(default_factory=FakeClientStub)
    errorEvent: list = field(default_factory=list)
    disconnectedEvent: list = field(default_factory=list)
    pnl_singles: dict[int, Any] = field(default_factory=dict)
    pnl_by_account: dict[str, Any] = field(default_factory=dict)
    market_data_type_requested: int | None = None
    place_order_calls: int = 0

    # connection
    def isConnected(self) -> bool:
        return self._connected

    def managedAccounts(self) -> list[str]:
        return list(self.accounts)

    def reqMarketDataType(self, t: int) -> None:
        self.market_data_type_requested = t

    async def reqCurrentTimeAsync(self):
        return datetime(2026, 9, 2, 14, 30, tzinfo=timezone.utc)

    def disconnect(self) -> None:
        self._connected = False

    # account/book
    def positions(self):
        return list(self._positions)

    def portfolio(self):
        return list(self._portfolio)

    def accountSummary(self, account: str = ""):
        # Mirrors ib_async 2.x: the sync accessor drives the loop itself and must never be
        # called from inside the running FastAPI loop.
        raise RuntimeError("This event loop is already running")

    async def accountSummaryAsync(self, account: str = ""):
        return list(self._summary.get(account, []))

    def accountValues(self, account: str = ""):
        return list(self._values.get(account, []))

    def pnl(self, account: str):
        p = self.pnl_by_account.get(account)
        return [p] if p else []

    def pnlSingle(self, account: str, model: str, con_id: int):
        p = self.pnl_singles.get(con_id)
        return [p] if p else []

    def reqPnL(self, account: str):
        return None

    def reqPnLSingle(self, account: str, model: str, con_id: int):
        return None

    def fills(self):
        return list(self._fills)

    async def reqAllOpenOrdersAsync(self):
        return list(self._trades)

    def openTrades(self):
        return list(self._trades)

    # contracts / data
    async def reqContractDetailsAsync(self, contract):
        cid = getattr(contract, "conId", 0)
        if cid and cid in self._details:
            return [self._details[cid]]
        sym = getattr(contract, "symbol", "")
        return [d for d in self._details.values() if d.contract.symbol == sym and (not getattr(contract, "tradingClass", "") or d.contract.tradingClass == contract.tradingClass)]

    async def qualifyContractsAsync(self, contract):
        return [contract]

    def reqMktData(self, contract, generic="", snapshot=False, regulatory=False):
        return self._tickers[contract.conId]

    def cancelMktData(self, contract):
        return None

    async def reqHistoricalDataAsync(self, *a, **k):
        return []

    async def reqMatchingSymbolsAsync(self, text):
        return []


@pytest.fixture
def cfg(tmp_path) -> BridgeConfig:
    return BridgeConfig(token="test-token", journal_path=str(tmp_path / "j.db"), allowed_origins=["https://desk.example"])


@pytest.fixture
def fake_ib() -> FakeIB:
    return FakeIB()


@pytest.fixture
def ib_client(cfg, fake_ib) -> IBClient:
    return IBClient(cfg=cfg, ib=fake_ib)  # type: ignore[arg-type]


@pytest.fixture
def app(cfg, ib_client):
    return create_app(cfg=cfg, client=ib_client, connect=False)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def seed_es_position(fake_ib: FakeIB, ib_client: IBClient) -> None:
    """One long ES position with a live quote and a PnLSingle row."""
    es = make_contract(conId=495512563, symbol="ES", localSymbol="ESZ6", secType="FUT", exchange="CME", currency="USD",
                       lastTradeDateOrContractMonth="20261218", multiplier="50", tradingClass="ES")
    fake_ib._connected = True
    fake_ib._positions = [SimpleNamespace(account="U1234567", contract=es, position=2.0, avgCost=50 * 5400.25)]
    fake_ib._details[es.conId] = SimpleNamespace(contract=es, minTick=0.25, realExpirationDate="", lastTradeTime="", longName="E-mini S&P 500")
    fake_ib._tickers[es.conId] = make_ticker(es, bid=5410.0, ask=5410.25, last=5410.25, close=5400.0, volume=120000, bidSize=12, askSize=9,
                                             marketDataType=1, time=datetime(2026, 9, 2, 14, 29, 58, tzinfo=timezone.utc))
    fake_ib.pnl_singles[es.conId] = SimpleNamespace(unrealizedPnL=1000.0, realizedPnL=0.0, dailyPnL=1025.0, value=541025.0)
    fake_ib.pnl_by_account["U1234567"] = SimpleNamespace(dailyPnL=1025.0, unrealizedPnL=1000.0, realizedPnL=0.0)
    fake_ib._summary["U1234567"] = [
        SimpleNamespace(tag="NetLiquidation", value="250000.50", currency="USD"),
        SimpleNamespace(tag="BuyingPower", value="900000", currency="USD"),
        SimpleNamespace(tag="MaintMarginReq", value="27000", currency="USD"),
    ]
    fake_ib._values["U1234567"] = [
        SimpleNamespace(tag="UnrealizedPnL", value="1000.00", currency="BASE"),
        SimpleNamespace(tag="UnrealizedPnL", value="1000.00", currency="USD"),
        SimpleNamespace(tag="RealizedPnL", value="0.00", currency="BASE"),
    ]
    ib_client.details_cache[es.conId] = fake_ib._details[es.conId]


def seed_virtual_fx_only(fake_ib: FakeIB) -> None:
    """Live case seen 2026-10-16: no positions, ledger P&L 0, but reqPnL reports virtual-FX P&L."""
    fake_ib._connected = True
    fake_ib.pnl_by_account["U1234567"] = SimpleNamespace(dailyPnL=-1256.0, unrealizedPnL=34945.0, realizedPnL=0.0)
    fake_ib._summary["U1234567"] = [SimpleNamespace(tag="NetLiquidation", value="37.09", currency="CAD")]
    fake_ib._values["U1234567"] = [
        SimpleNamespace(tag="UnrealizedPnL", value="0.00", currency="BASE"),
        SimpleNamespace(tag="RealizedPnL", value="0.00", currency="BASE"),
    ]
