"""IB connection wrapper around ib_async.IB.

Responsibilities:
  - connect to TWS with readonly=True and keep reconnecting (TWS restarts nightly);
  - request market data type 3 so TWS returns live where the user is entitled
    and delayed elsewhere, and record the per-ticker type for freshness labels;
  - hold a bounded set of streaming tickers (book positions pinned, others
    evicted when idle) so we stay under the account's market-data lines;
  - keep account/P&L subscriptions alive; capture IB error messages per reqId
    so routes can report entitlement failures honestly instead of returning
    empty data as if it were real.

The bridge never submits, modifies or cancels orders: there is no write path
(tests/test_routes.py scans this package's source for the IB order API names).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from ib_async import IB, Contract, ContractDetails, Ticker

from .config import BridgeConfig

log = logging.getLogger("ib_bridge.ib")


@dataclass
class TickerEntry:
    ticker: Ticker
    contract: Contract
    last_touch: float
    pinned: bool = False
    min_tick: float | None = None


@dataclass
class IbError:
    at: float
    req_id: int
    code: int
    message: str


class LinesCapExceeded(RuntimeError):
    pass


@dataclass
class IBClient:
    cfg: BridgeConfig
    ib: IB = field(default_factory=IB)
    tickers: dict[int, TickerEntry] = field(default_factory=dict)
    details_cache: dict[int, ContractDetails] = field(default_factory=dict)
    errors: deque[IbError] = field(default_factory=lambda: deque(maxlen=300))
    last_error: str | None = None
    connected_at: float | None = None
    _task: asyncio.Task | None = None
    _pnl_single_conids: set[int] = field(default_factory=set)
    _stopping: bool = False
    _time_offset: float | None = None
    _time_offset_at: float = 0.0
    _time_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _orders_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _open_orders_cache: list[Any] | None = None
    _open_orders_at: float = 0.0

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.ib.errorEvent += self._on_error
        self.ib.disconnectedEvent += self._on_disconnected
        self._task = asyncio.create_task(self._run(), name="ib-connect-loop")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self.ib.isConnected():
            self.ib.disconnect()

    async def _run(self) -> None:
        backoff = 2.0
        while not self._stopping:
            if not self.ib.isConnected():
                try:
                    await self._connect_once()
                    backoff = 2.0
                except Exception as exc:  # noqa: BLE001 - we want to keep the loop alive
                    self.last_error = f"connect failed: {exc}"
                    log.warning("TWS connect failed (%s); retrying in %.0fs", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.5, 30.0)
                    continue
            await asyncio.sleep(2.0)
            self._sweep_idle_tickers()

    async def _connect_once(self) -> None:
        log.info("connecting to TWS %s:%s clientId=%s (readonly)", self.cfg.tws_host, self.cfg.tws_port, self.cfg.client_id)
        await self.ib.connectAsync(
            self.cfg.tws_host,
            self.cfg.tws_port,
            clientId=self.cfg.client_id,
            timeout=15,
            readonly=True,
        )
        # 3 = delayed-if-not-entitled: TWS serves live where the user is
        # subscribed and delayed otherwise; each ticker reports which it got.
        self.ib.reqMarketDataType(3)
        await self._req(self.ib.reqAccountSummaryAsync(), 15, "reqAccountSummary")
        for acct in self.ib.managedAccounts():
            self.ib.reqPnL(acct)
        self.connected_at = time.time()
        self.last_error = None
        log.info("connected; accounts=%s serverVersion=%s", [self._mask(a) for a in self.ib.managedAccounts()], self.ib.client.serverVersion())
        await self.pin_book_positions()

    def _on_disconnected(self) -> None:
        log.warning("TWS disconnected; streaming state cleared")
        self.tickers.clear()
        self._pnl_single_conids.clear()
        self.connected_at = None
        self._time_offset = None
        self._open_orders_cache = None

    def _on_error(self, reqId: int, errorCode: int, errorString: str, contract: Any = None) -> None:
        self.errors.append(IbError(at=time.time(), req_id=int(reqId), code=int(errorCode), message=str(errorString)))
        # 2104/2106/2158 are "data farm connection OK" notices, not errors.
        if errorCode not in (2104, 2106, 2107, 2108, 2158, 2119):
            self.last_error = f"{errorCode}: {errorString}"

    def errors_for(self, req_id: int, since: float | None = None) -> list[IbError]:
        return [e for e in self.errors if e.req_id == req_id and (since is None or e.at >= since)]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _mask(account_id: str | None) -> str:
        if not account_id:
            return ""
        return "*" * max(0, len(account_id) - 3) + account_id[-3:]

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()

    def trading_mode(self) -> str:
        accts = self.ib.managedAccounts() if self.connected else []
        if accts:
            return "paper" if all(a.startswith(("DU", "DF")) for a in accts) else "live"
        if self.cfg.tws_port in (7497, 4002):
            return "paper"
        if self.cfg.tws_port in (7496, 4001):
            return "live"
        return "unknown"

    # ib_async keys some request futures by a fixed name ("currentTime", "openOrders"):
    # two overlapping callers make the first future orphaned and it never resolves.
    # Every awaited IB request therefore goes through `_req` (hard timeout) and the
    # keyed ones additionally through a lock so HTTP handlers can never hang.
    async def _req(self, awaitable, timeout: float, what: str):
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("IB request timed out after %.0fs: %s", timeout, what)
            self.last_error = f"timeout: {what}"
            return None

    async def server_time(self) -> datetime | None:
        """Server clock via a cached offset: one round-trip every 5 minutes, not one per poll."""
        if not self.connected:
            return None
        now = time.time()
        if self._time_offset is None or now - self._time_offset_at > 300:
            async with self._time_lock:
                if self._time_offset is None or now - self._time_offset_at > 300:
                    t = await self._req(self.ib.reqCurrentTimeAsync(), 5, "reqCurrentTime")
                    if t is not None:
                        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
                        self._time_offset = t.timestamp() - time.time()
                        self._time_offset_at = time.time()
        if self._time_offset is None:
            return None
        return datetime.fromtimestamp(time.time() + self._time_offset, tz=timezone.utc)

    async def all_open_orders(self) -> list[Any] | None:
        """Serialized + cached (2s) reqAllOpenOrders. None = IB did not answer in time."""
        now = time.time()
        if self._open_orders_cache is not None and now - self._open_orders_at < 2.0:
            return self._open_orders_cache
        async with self._orders_lock:
            now = time.time()
            if self._open_orders_cache is not None and now - self._open_orders_at < 2.0:
                return self._open_orders_cache
            trades = await self._req(self.ib.reqAllOpenOrdersAsync(), 8, "reqAllOpenOrders")
            if trades is None:
                return None
            self._open_orders_cache = list(trades)
            self._open_orders_at = time.time()
            return self._open_orders_cache

    # ------------------------------------------------------------------ contracts
    async def qualify(self, contract: Contract) -> Contract | None:
        if not self.connected:
            return None
        res = await self._req(self.ib.qualifyContractsAsync(contract), 10, f"qualify {contract.localSymbol or contract.symbol}")
        return res[0] if res else None

    async def details(self, contract: Contract) -> list[ContractDetails]:
        if not self.connected:
            return []
        cds = await self._req(self.ib.reqContractDetailsAsync(contract), 12, f"contractDetails {contract.localSymbol or contract.symbol or contract.conId}")
        if not cds:
            return []
        for cd in cds:
            if cd.contract and cd.contract.conId:
                self.details_cache[cd.contract.conId] = cd
        return cds

    async def details_for_conid(self, con_id: int) -> ContractDetails | None:
        if con_id in self.details_cache:
            return self.details_cache[con_id]
        cds = await self.details(Contract(conId=con_id))
        return cds[0] if cds else None

    # ------------------------------------------------------------------ tickers
    def lines_in_use(self) -> int:
        return len(self.tickers)

    async def ensure_ticker(self, con_id: int, pinned: bool = False) -> TickerEntry:
        """Stream market data for a conId (idempotent). Raises LinesCapExceeded when full."""
        entry = self.tickers.get(con_id)
        if entry:
            entry.last_touch = time.time()
            entry.pinned = entry.pinned or pinned
            return entry
        if not self.connected:
            raise ConnectionError("not connected to TWS")
        if len(self.tickers) >= self.cfg.lines_cap and not self._evict_one():
            raise LinesCapExceeded(f"market-data lines cap ({self.cfg.lines_cap}) reached; all lines pinned")
        cd = await self.details_for_conid(con_id)
        if cd is None or not cd.contract:
            raise LookupError(f"no contract details for conId {con_id}")
        contract = cd.contract
        ticker = self.ib.reqMktData(contract, "", False, False)
        entry = TickerEntry(ticker=ticker, contract=contract, last_touch=time.time(), pinned=pinned, min_tick=cd.minTick or None)
        self.tickers[con_id] = entry
        return entry

    def _evict_one(self) -> bool:
        victims = sorted((e for e in self.tickers.values() if not e.pinned), key=lambda e: e.last_touch)
        if not victims:
            return False
        v = victims[0]
        self._cancel(v)
        return True

    def _cancel(self, entry: TickerEntry) -> None:
        try:
            self.ib.cancelMktData(entry.contract)
        except Exception:  # noqa: BLE001
            pass
        self.tickers.pop(entry.contract.conId, None)

    def _sweep_idle_tickers(self) -> None:
        if not self.connected:
            return
        cutoff = time.time() - self.cfg.ticker_idle_seconds
        for e in list(self.tickers.values()):
            if not e.pinned and e.last_touch < cutoff:
                self._cancel(e)

    # ------------------------------------------------------------------ book
    async def pin_book_positions(self) -> None:
        """Stream quotes + per-position P&L for everything currently held."""
        if not self.connected:
            return
        for pos in self.ib.positions():
            con_id = pos.contract.conId
            if not con_id or pos.position == 0:
                continue
            try:
                await self.ensure_ticker(con_id, pinned=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not stream %s: %s", pos.contract.localSymbol, exc)
            if con_id not in self._pnl_single_conids:
                try:
                    self.ib.reqPnLSingle(pos.account, "", con_id)
                    self._pnl_single_conids.add(con_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("reqPnLSingle failed for %s: %s", con_id, exc)

    def pnl_single(self, account: str, con_id: int):
        # ib_async returns a list (possibly empty) of PnLSingle rows.
        try:
            rows = self.ib.pnlSingle(account, "", con_id)
            return rows[0] if rows else None
        except Exception:  # noqa: BLE001
            return None

    def account_pnl(self, account: str):
        try:
            rows = self.ib.pnl(account)
            return rows[0] if rows else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ history
    async def historical_bars(self, contract: Contract, duration: str, bar_size: str, what: str) -> tuple[list[Any], list[IbError], Exception | None]:
        """Returns (bars, errors_for_this_request, exception). Empty bars + an error means not entitled / no data."""
        if not self.connected:
            return [], [], ConnectionError("not connected to TWS")
        started = time.time()
        try:
            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what,
                useRTH=False,
                formatDate=2,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            return [], [e for e in self.errors if e.at >= started], exc
        req_id = getattr(bars, "reqId", None)
        errs = self.errors_for(req_id, since=started) if req_id is not None else []
        return list(bars), errs, None

    # ------------------------------------------------------------------ misc
    @staticmethod
    def today_utc() -> date:
        return datetime.now(timezone.utc).date()
