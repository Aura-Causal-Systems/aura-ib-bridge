"""Composition layer: ib_async state -> wire models. Routes stay thin."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ib_async import Contract

from . import __version__
from .branding import API_VERSION, APP_NAME
from .ib_client import IBClient, LinesCapExceeded
from .instruments import Instrument, contract_for, spec_dict
from .models import (
    AccountSummary,
    Bar,
    BarsResponse,
    ContractRef,
    ContractResolution,
    Fill,
    Health,
    OpenOrder,
    Position,
    Quote,
    Snapshot,
)
from .normalize import (
    avg_price_from_cost,
    choose_front,
    classify_event_contract,
    contract_ref,
    iso_utc,
    now_utc_iso,
    quote_from_ticker,
    to_float,
)

log = logging.getLogger("ib_bridge.service")

SUMMARY_TAGS = {
    "NetLiquidation": "net_liquidation",
    "TotalCashValue": "total_cash",
    "BuyingPower": "buying_power",
    "MaintMarginReq": "maint_margin_req",
    "InitMarginReq": "init_margin_req",
    "ExcessLiquidity": "excess_liquidity",
    "AvailableFunds": "available_funds",
}


class BridgeService:
    def __init__(self, client: IBClient, event_venues: list[str] | None = None):
        self.client = client
        self.event_venues: list[str] = event_venues or []

    # ------------------------------------------------------------------ health
    async def health(self) -> Health:
        c = self.client
        st = await c.server_time()
        return Health(
            app_name=APP_NAME,
            api_version=API_VERSION,
            connected=c.connected,
            account_ids_masked=[c._mask(a) for a in (c.ib.managedAccounts() if c.connected else [])],
            trading_mode=c.trading_mode(),  # type: ignore[arg-type]
            read_only=True,  # bridge-level guarantee: no write route exists; connectAsync(readonly=True)
            tws_server_version=c.ib.client.serverVersion() if c.connected else None,
            server_time_utc=iso_utc(st),
            lines_in_use=c.lines_in_use(),
            lines_cap=c.cfg.lines_cap,
            bridge_version=__version__,
            tws_host=c.cfg.tws_host,
            tws_port=c.cfg.tws_port,
            last_error=c.last_error,
        )

    # ------------------------------------------------------------------ account
    async def account_summaries(self) -> list[AccountSummary]:
        c = self.client
        if not c.connected:
            return []
        out: list[AccountSummary] = []
        for acct in c.ib.managedAccounts():
            values: dict[str, float | None] = {v: None for v in SUMMARY_TAGS.values()}
            currency: str | None = None
            # accountSummary() is a sync wrapper that drives the loop itself and
            # raises "event loop is already running" under FastAPI; use the async one.
            rows = await c._req(c.ib.accountSummaryAsync(acct), 10, "accountSummary") or []
            for av in rows:
                key = SUMMARY_TAGS.get(av.tag)
                if key and av.currency not in ("", None) and (currency is None or av.currency == currency or av.currency == "BASE"):
                    values[key] = to_float(av.value)
                    if av.currency not in ("BASE",):
                        currency = av.currency
            pnl = c.account_pnl(acct)
            # Ledger values stream from reqAccountUpdates (subscribed at connect); BASE = account currency.
            ledger = {v.tag: to_float(v.value) for v in c.ib.accountValues(acct) if v.currency == "BASE" and v.tag in ("UnrealizedPnL", "RealizedPnL")}
            out.append(
                AccountSummary(
                    account_masked=c._mask(acct),
                    currency=currency,
                    daily_pnl=to_float(getattr(pnl, "dailyPnL", None)),
                    unrealized_pnl=to_float(getattr(pnl, "unrealizedPnL", None)),
                    realized_pnl=to_float(getattr(pnl, "realizedPnL", None)),
                    ledger_unrealized_pnl=ledger.get("UnrealizedPnL"),
                    ledger_realized_pnl=ledger.get("RealizedPnL"),
                    updated_utc=now_utc_iso(),
                    **values,
                )
            )
        return out

    # ------------------------------------------------------------------ positions
    def positions(self) -> list[Position]:
        c = self.client
        if not c.connected:
            return []
        portfolio = {item.contract.conId: item for item in c.ib.portfolio()}
        out: list[Position] = []
        for pos in c.ib.positions():
            if pos.position == 0:
                continue
            ref = contract_ref(pos.contract)
            cd = c.details_cache.get(ref.con_id)
            if cd and cd.contract:
                # positions() contracts lack exchange/multiplier detail; prefer the qualified one.
                ref = contract_ref(cd.contract)
            avg_price, basis = avg_price_from_cost(to_float(pos.avgCost), ref.multiplier, ref.sec_type)
            single = c.pnl_single(pos.account, ref.con_id)
            item = portfolio.get(ref.con_id)
            if single is not None and to_float(getattr(single, "unrealizedPnL", None)) is not None:
                unreal, real, daily = (to_float(getattr(single, n, None)) for n in ("unrealizedPnL", "realizedPnL", "dailyPnL"))
                mv = to_float(getattr(single, "value", None))
                src = "pnl_single"
            elif item is not None:
                unreal, real, daily, mv = to_float(item.unrealizedPNL), to_float(item.realizedPNL), None, to_float(item.marketValue)
                src = "portfolio"
            else:
                unreal = real = daily = mv = None
                src = "none"
            mkt_price = to_float(item.marketPrice) if item is not None else None
            is_event, venue, side, _payout = classify_event_contract(cd.contract if cd else pos.contract, self.event_venues)
            out.append(
                Position(
                    account_masked=c._mask(pos.account),
                    contract=ref,
                    position=float(pos.position),
                    avg_cost_raw=float(pos.avgCost),
                    avg_price=avg_price,
                    avg_price_basis=basis,  # type: ignore[arg-type]
                    market_price=mkt_price,
                    market_value=mv,
                    unrealized_pnl=unreal,
                    realized_pnl=real,
                    daily_pnl=daily,
                    pnl_source=src,  # type: ignore[arg-type]
                    is_event_contract=is_event,
                    event_venue=venue,
                    event_right=side,  # type: ignore[arg-type]
                    real_expiration_date=(getattr(cd, "realExpirationDate", None) or None) if cd else None,
                )
            )
        return out

    # ------------------------------------------------------------------ quotes
    async def quotes(self, con_ids: list[int], pin: bool = False) -> tuple[dict[int, Quote], list[str]]:
        c = self.client
        out: dict[int, Quote] = {}
        warnings: list[str] = []
        if not c.connected:
            return out, ["bridge not connected to TWS"]
        for cid in con_ids:
            try:
                entry = await c.ensure_ticker(cid, pinned=pin)
            except LinesCapExceeded as exc:
                warnings.append(str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"conId {cid}: {exc}")
                continue
            out[cid] = quote_from_ticker(entry.ticker, entry.min_tick)
        return out, warnings

    # ------------------------------------------------------------------ orders / fills
    async def open_orders(self) -> list[OpenOrder]:
        c = self.client
        if not c.connected:
            return []
        try:
            trades = await c.all_open_orders()
        except Exception as exc:  # noqa: BLE001
            log.warning("reqAllOpenOrders failed: %s", exc)
            trades = None
        if trades is None:
            # IB did not answer in time: fall back to what this client already knows.
            trades = c.ib.openTrades()
        out: list[OpenOrder] = []
        for t in trades:
            o, s = t.order, t.orderStatus
            out.append(
                OpenOrder(
                    order_id=int(o.orderId),
                    perm_id=int(o.permId) if o.permId else None,
                    account_masked=c._mask(o.account) if o.account else None,
                    contract=contract_ref(t.contract),
                    action=o.action,
                    order_type=o.orderType,
                    total_quantity=float(o.totalQuantity),
                    lmt_price=to_float(o.lmtPrice),
                    aux_price=to_float(o.auxPrice),
                    tif=o.tif or None,
                    status=s.status or None,
                    filled=to_float(s.filled),
                    remaining=to_float(s.remaining),
                    avg_fill_price=to_float(s.avgFillPrice),
                )
            )
        return out

    def fills_today(self) -> list[Fill]:
        c = self.client
        if not c.connected:
            return []
        out: list[Fill] = []
        for f in c.ib.fills():
            ex, cr = f.execution, f.commissionReport
            out.append(
                Fill(
                    exec_id=str(ex.execId),
                    time_utc=iso_utc(f.time),
                    account_masked=c._mask(ex.acctNumber) if ex.acctNumber else None,
                    contract=contract_ref(f.contract),
                    side=ex.side,
                    shares=float(ex.shares),
                    price=float(ex.price),
                    cum_qty=to_float(ex.cumQty),
                    avg_price=to_float(ex.avgPrice),
                    exchange=ex.exchange or None,
                    order_id=int(ex.orderId) if ex.orderId else None,
                    perm_id=int(ex.permId) if ex.permId else None,
                    commission=to_float(getattr(cr, "commission", None)) if cr and cr.execId else None,
                    commission_currency=(getattr(cr, "currency", None) or None) if cr and cr.execId else None,
                    realized_pnl=to_float(getattr(cr, "realizedPNL", None)) if cr and cr.execId else None,
                )
            )
        out.sort(key=lambda x: x.time_utc or "", reverse=True)
        return out

    # ------------------------------------------------------------------ snapshot
    async def snapshot(self, extra_con_ids: list[int]) -> Snapshot:
        warnings: list[str] = []
        health = await self.health()
        positions = self.positions()
        if self.client.connected:
            await self.client.pin_book_positions()
        wanted = list({p.contract.con_id for p in positions} | set(extra_con_ids))
        quotes, qwarn = await self.quotes(wanted)
        warnings.extend(qwarn)
        return Snapshot(
            generated_utc=now_utc_iso(),
            health=health,
            account_summary=await self.account_summaries(),
            positions=positions,
            quotes=quotes,
            open_orders=await self.open_orders(),
            fills_today=self.fills_today(),
            warnings=warnings,
        )

    # ------------------------------------------------------------------ contracts
    async def resolve(self, inst: Instrument, micro: bool = False) -> ContractResolution:
        c = self.client
        spec = spec_dict(inst, micro)
        if not c.connected:
            return ContractResolution(engine_symbol=inst.engine_symbol, ib_spec=spec, front=None, next=None, candidates=[], roll_note="bridge not connected", min_tick=None)
        probe = contract_for(inst, micro=micro)
        cds = await c.details(probe)
        if inst.ib.sec_type == "STK":
            cd = cds[0] if cds else None
            ref = contract_ref(cd.contract) if cd else None
            return ContractResolution(engine_symbol=inst.engine_symbol, ib_spec=spec, front=ref, next=None, candidates=[ref] if ref else [], roll_note=None, min_tick=(cd.minTick if cd else None))
        front, nxt, note = choose_front(cds, c.today_utc(), inst.roll_window_days)
        candidates = sorted((contract_ref(cd.contract) for cd in cds), key=lambda r: r.expiry or "")
        return ContractResolution(
            engine_symbol=inst.engine_symbol,
            ib_spec=spec,
            front=contract_ref(front.contract) if front else None,
            next=contract_ref(nxt.contract) if nxt else None,
            candidates=candidates[:8],
            roll_note=note,
            min_tick=(front.minTick if front else None),
        )

    async def search(self, text: str) -> list[ContractRef]:
        c = self.client
        if not c.connected:
            return []
        matches = await c._req(c.ib.reqMatchingSymbolsAsync(text), 10, f"matchingSymbols {text}")
        return [contract_ref(m.contract) for m in (matches or []) if m.contract]

    # ------------------------------------------------------------------ bars
    async def bars(self, con_id: int, duration: str, bar_size: str, what: str) -> BarsResponse:
        c = self.client
        cd = await c.details_for_conid(con_id)
        if cd is None or not cd.contract:
            return BarsResponse(con_id=con_id, duration=duration, bar_size=bar_size, what_to_show=what, entitled=False, source="ib_historical", bars=None, error="unknown conId")
        raw, errs, exc = await c.historical_bars(cd.contract, duration, bar_size, what)
        if exc is not None or (not raw and errs):
            msg = "; ".join(f"{e.code}: {e.message}" for e in errs) or (str(exc) if exc else "no data returned")
            # 162 = historical data service error (incl. no subscription), 354 = not subscribed, 10168/10167 delayed notices
            return BarsResponse(con_id=con_id, duration=duration, bar_size=bar_size, what_to_show=what, entitled=False, source="ib_historical", bars=None, error=msg)
        bars = [
            Bar(
                time_utc=iso_utc(b.date if isinstance(b.date, datetime) else datetime.combine(b.date, datetime.min.time(), tzinfo=timezone.utc)) or "",
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
                volume=to_float(b.volume),
                bar_count=int(b.barCount) if getattr(b, "barCount", None) not in (None, -1) else None,
            )
            for b in raw
        ]
        return BarsResponse(con_id=con_id, duration=duration, bar_size=bar_size, what_to_show=what, entitled=True, source="ib_historical", bars=bars, error=None)
