"""Wire models. Every field that IB did not deliver is None - never a default number.

Timestamps are ISO-8601 UTC strings. Freshness is derived from IB's market
data type (1 live, 2 frozen, 3 delayed, 4 delayed-frozen) and is attached to
every quote so the desk can label it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Freshness = Literal["live", "frozen", "delayed", "delayed_frozen", "n/a"]


class Health(BaseModel):
    app_name: str
    api_version: int  # desks check this and ask the trader to update an older helper
    connected: bool
    account_ids_masked: list[str]
    trading_mode: Literal["live", "paper", "unknown"]
    read_only: bool
    tws_server_version: int | None
    server_time_utc: str | None
    lines_in_use: int
    lines_cap: int
    bridge_version: str
    tws_host: str
    tws_port: int
    last_error: str | None = None


class ContractRef(BaseModel):
    con_id: int
    symbol: str
    local_symbol: str | None
    sec_type: str
    exchange: str | None
    primary_exchange: str | None = None
    currency: str | None
    expiry: str | None  # lastTradeDateOrContractMonth as IB returns it (YYYYMM or YYYYMMDD)
    multiplier: float | None
    trading_class: str | None
    right: str | None = None  # C / P for options and event contracts
    strike: float | None = None
    description: str | None = None


class Quote(BaseModel):
    con_id: int
    bid: float | None
    ask: float | None
    last: float | None
    close: float | None
    volume: float | None
    bid_size: float | None
    ask_size: float | None
    market_data_type: int | None
    freshness: Freshness
    updated_utc: str | None
    halted: bool | None
    min_tick: float | None = None


class Position(BaseModel):
    account_masked: str
    contract: ContractRef
    position: float
    # IB avgCost is per contract in currency (price x multiplier for FUT/FOP/OPT).
    avg_cost_raw: float
    avg_price: float | None
    avg_price_basis: Literal["avg_cost_div_multiplier", "avg_cost", "unknown"]
    market_price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    realized_pnl: float | None
    daily_pnl: float | None
    pnl_source: Literal["pnl_single", "portfolio", "none"]
    # Event-contract enrichment (ForecastEx / CME event contracts / other venues):
    is_event_contract: bool = False
    event_venue: str | None = None
    event_right: Literal["YES", "NO"] | None = None
    real_expiration_date: str | None = None


class AccountSummary(BaseModel):
    account_masked: str
    currency: str | None
    net_liquidation: float | None
    total_cash: float | None
    buying_power: float | None
    maint_margin_req: float | None
    init_margin_req: float | None
    excess_liquidity: float | None
    available_funds: float | None
    # IB P&L feed (reqPnL). It can include TWS "virtual FX positions" (currency-conversion
    # tracking), which are not cash balances or positions - compare with the ledger values.
    daily_pnl: float | None
    unrealized_pnl: float | None
    realized_pnl: float | None
    # Account ledger (reqAccountUpdates, BASE currency): what the account actually holds.
    ledger_unrealized_pnl: float | None = None
    ledger_realized_pnl: float | None = None
    updated_utc: str | None


class OpenOrder(BaseModel):
    order_id: int
    perm_id: int | None
    account_masked: str | None
    contract: ContractRef
    action: str
    order_type: str
    total_quantity: float
    lmt_price: float | None
    aux_price: float | None
    tif: str | None
    status: str | None
    filled: float | None
    remaining: float | None
    avg_fill_price: float | None


class Fill(BaseModel):
    exec_id: str
    time_utc: str | None
    account_masked: str | None
    contract: ContractRef
    side: str  # BOT / SLD
    shares: float
    price: float
    cum_qty: float | None
    avg_price: float | None
    exchange: str | None
    order_id: int | None
    perm_id: int | None
    commission: float | None
    commission_currency: str | None
    realized_pnl: float | None


class Bar(BaseModel):
    time_utc: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    bar_count: int | None


class BarsResponse(BaseModel):
    con_id: int
    duration: str
    bar_size: str
    what_to_show: str
    entitled: bool
    source: Literal["ib_historical"]
    bars: list[Bar] | None
    error: str | None = None


class ContractResolution(BaseModel):
    engine_symbol: str | None
    ib_spec: dict
    front: ContractRef | None
    next: ContractRef | None
    candidates: list[ContractRef]
    roll_note: str | None
    min_tick: float | None


class EventVenue(BaseModel):
    code: str
    label: str
    payout: float | None  # 1.00 ForecastEx, 100.00 CME event contracts, None when unverified
    sec_type: str
    verified: bool
    note: str | None = None


class EventProduct(BaseModel):
    venue: str
    symbol: str
    con_id: int | None
    long_name: str | None
    trading_class: str | None
    expirations: list[str]
    strikes: list[float]


class EventContract(BaseModel):
    contract: ContractRef
    venue: str
    side: Literal["YES", "NO"] | None
    real_expiration_date: str | None
    last_trade_time: str | None
    payout: float | None


class EventQuote(BaseModel):
    con_id: int
    quote: Quote
    payout: float | None
    implied_prob: float | None  # 0-100, None when not computable
    prob_basis: Literal["mid", "ask", "bid", "last", "n/a"]


class Snapshot(BaseModel):
    generated_utc: str
    health: Health
    account_summary: list[AccountSummary]
    positions: list[Position]
    quotes: dict[int, Quote]
    open_orders: list[OpenOrder]
    fills_today: list[Fill]
    warnings: list[str]
