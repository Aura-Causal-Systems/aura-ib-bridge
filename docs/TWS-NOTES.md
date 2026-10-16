# TWS API notes (verified against a live IB Canada account, TWS API server version 178)

## Connection and account

- `connectAsync(readonly=True)` works; ib_async skips the open-order fetch in read-only mode, so
  `/orders` calls `reqAllOpenOrdersAsync` itself.
- `accountSummary()` (sync) raises "This event loop is already running" inside FastAPI — use
  `accountSummaryAsync`. Never call ib_async accessors that spin the loop from route code.
- ib_async keys `reqCurrentTime` / `reqAllOpenOrders` futures by a fixed name: overlapping calls
  orphan the first future and the caller hangs forever. Every awaited IB request goes through
  `IBClient._req(awaitable, timeout, what)`; server time is a cached clock offset; open orders are
  locked and cached for 2 s (`tests/test_hangs.py`).
- `PnLSingle.realizedPnL` can arrive as `1.7976931348623157e+308` (IB `UNSET_DOUBLE`);
  `to_float` maps anything ≥ 1e300 to `null`.
- `Position.avgCost` is price × multiplier for FUT/FOP/OPT (`avg_price_basis` says how
  `avg_price` was derived).

## Virtual FX positions

TWS tracks currency conversions as **virtual FX positions** (e.g. `EUR.CAD`, `USD.CAD` on IDEALPRO).
They are a tracking view, not cash balances. Observed:

- With *API → Settings → Include virtual FX positions when sending portfolio* on, they appear in the
  positions feed with P&L; with it off, they do not.
- The account P&L feed (`reqPnL`) keeps including their P&L either way, while the account ledger
  (`reqAccountUpdates` → `UnrealizedPnL` / `RealizedPnL`, currency `BASE`) shows only what the
  account holds. Seen live: ledger unrealized 0.00 and 37 CAD net liquidation, `reqPnL` unrealized
  +34,945 CAD. The bridge exposes both (`unrealized_pnl` vs `ledger_unrealized_pnl`); desks must not
  present the P&L feed as account P&L when the two disagree.

## Market data

- Request type 3 (`reqMarketDataType(3)`): live where the user is subscribed, delayed elsewhere; each
  ticker reports its own type. Observed: IDEALPRO FX live; CME/CBOT/COMEX/NYMEX futures delayed
  (IB message 10167) without futures subscriptions.
- Weekends: data farms report "connection is broken" (2103/2105) and positions may be reported empty;
  the bridge passes IB's state through.

## Event contracts (IBKR Prediction Markets)

| Venue | TWS API modelling | Status |
|---|---|---|
| ForecastEx | `secType=OPT`, `exchange=FORECASTX`, underlier `IND` per product (`FF` = US Fed Funds Target Rate; also `FFDEC` Fed Decision, `FEDRC` Number of Fed Rate Cuts, ...). `right` C = YES, P = NO; `strike` = resolution threshold; `localSymbol` like `FF_091726_0.875_NO`; `realExpirationDate` set; multiplier 1; pays 1.00; quotes in 0.01 arrive **live** without a subscription. Books are one-sided (YES shows bid only, NO ask only) — `implied_probability` falls back to ask/bid. `reqSecDefOptParams` returns nothing for these; enumerate with `reqContractDetails(OPT, symbol, FORECASTX)` (118 contracts for FF). `reqMatchingSymbols` lists the underliers with `derivativeSecTypes=['EC']`. | verified |
| CME event contracts | `FOP ES@CME tradingClass=ECES` → no security definition. | unverified |
| Kalshi via IB | Exchange code `KALSHI` is recognised (error 200 "no security definition"), `KALSHIX`/`KLSHI`/`KLS` are "destination invalid"; no products found. Not offered to IB Canada clients. | not available (CA) |

IB Canada lists ForecastEx only (no election contracts). ForecastEx product codes for CPI/GDP are not
`CPI`/`GDP`; use `reqMatchingSymbols` on the product name.
