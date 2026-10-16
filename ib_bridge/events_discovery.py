"""Event-contract (prediction market) discovery through the TWS API.

Verified from IB's TWS API 'Event Trading' documentation:
  * ForecastEx: secType OPT, exchange FORECASTX, artificial IND underlier per
    product (e.g. FF = US Fed Funds Target Rate), Call = YES, Put = NO, pays
    USD 1.00, quoted in 0.01. No last/trade prints - bid/ask only.
  * CME event contracts: secType FOP on the future's exchange, trading class
    prefixed 'EC' + future symbol, pays USD 100.00, quoted in 1.00.
Kalshi contracts routed through IBKR Prediction Markets (May 2026) are NOT
documented for the TWS API at the time of writing: `probe_venues` tries a set
of candidate exchange codes and reports what TWS actually answers. A venue is
only marked verified when contract details come back.
"""

from __future__ import annotations

import logging
from typing import Any

from ib_async import Contract

from .ib_client import IBClient
from .models import EventContract, EventProduct, EventVenue, EventQuote
from .normalize import classify_event_contract, contract_ref, implied_probability, quote_from_ticker

log = logging.getLogger("ib_bridge.events")

KNOWN_VENUES: list[EventVenue] = [
    EventVenue(code="FORECASTX", label="ForecastEx (IBKR affiliate)", payout=1.0, sec_type="OPT", verified=False),
    EventVenue(code="CME_EVENT", label="CME Group event contracts", payout=100.0, sec_type="FOP", verified=False),
]

# Candidate exchange codes for Kalshi-via-IB. These are probes, not facts.
KALSHI_PROBE_CODES = ["KALSHI", "KALSHIX", "KLSHI", "KLS"]
# Products used for the ForecastEx / CME probes (documented product codes).
FORECASTX_PROBE_SYMBOL = "FF"      # US Fed Funds Target Rate
CME_PROBE_FUTURE = ("ES", "CME")   # E-mini S&P daily event contract


class EventsService:
    def __init__(self, client: IBClient):
        self.client = client
        self.venues: list[EventVenue] = [v.model_copy() for v in KNOWN_VENUES]

    def verified_codes(self) -> list[str]:
        return [v.code for v in self.venues if v.verified and v.code not in ("FORECASTX", "CME_EVENT")]

    # ------------------------------------------------------------------ probes
    async def probe_venues(self, deep: bool = False) -> list[EventVenue]:
        """Verify the documented venues against TWS. `deep` also tries the Kalshi-via-IB exchange-code
        guesses (slow, several seconds of IB round-trips) - used by scripts/discover_event_venues.py only."""
        c = self.client
        if not c.connected:
            return self.venues
        # ForecastEx
        fx = next(v for v in self.venues if v.code == "FORECASTX")
        try:
            cds = await c.details(Contract(symbol=FORECASTX_PROBE_SYMBOL, secType="IND", exchange="FORECASTX", currency="USD"))
            fx.verified = len(cds) > 0
            fx.note = f"IND {FORECASTX_PROBE_SYMBOL}@FORECASTX -> {len(cds)} detail(s)"
        except Exception as exc:  # noqa: BLE001
            fx.verified, fx.note = False, f"probe failed: {exc}"
        # CME event contracts
        cme = next(v for v in self.venues if v.code == "CME_EVENT")
        sym, exch = CME_PROBE_FUTURE
        try:
            cds = await c.details(Contract(symbol=sym, secType="FOP", exchange=exch, currency="USD", tradingClass=f"EC{sym}"))
            cme.verified = len(cds) > 0
            cme.note = f"FOP {sym}@{exch} tradingClass EC{sym} -> {len(cds)} detail(s)"
        except Exception as exc:  # noqa: BLE001
            cme.verified, cme.note = False, f"probe failed: {exc}"
        # Kalshi-via-IB probes (honest: unverified unless TWS answers)
        for code in KALSHI_PROBE_CODES if deep else []:
            existing = next((v for v in self.venues if v.code == code), None)
            try:
                cds = await c.details(Contract(secType="OPT", exchange=code, currency="USD", symbol=FORECASTX_PROBE_SYMBOL))
                ok = len(cds) > 0
                note = f"OPT @{code} -> {len(cds)} detail(s)"
            except Exception as exc:  # noqa: BLE001
                ok, note = False, f"probe failed: {exc}"
            venue = EventVenue(code=code, label=f"Kalshi via IB (probe {code})", payout=None, sec_type="OPT", verified=ok, note=note)
            if existing:
                existing.verified, existing.note = ok, note
            elif ok:
                self.venues.append(venue)
            else:
                log.info("kalshi probe %s: %s", code, note)
        return self.venues

    # ------------------------------------------------------------------ products / chains
    async def products(self, venue: str, symbol: str | None) -> list[EventProduct]:
        """ForecastEx products = artificial IND underliers. A symbol filter is required by TWS for IND lookups."""
        c = self.client
        if not c.connected:
            return []
        if venue.upper() != "FORECASTX":
            return []
        if not symbol:
            raise ValueError("TWS needs a product symbol for ForecastEx underlier lookups (e.g. FF, CPI, GDP)")
        cds = await c.details(Contract(symbol=symbol.upper(), secType="IND", exchange="FORECASTX", currency="USD"))
        out: list[EventProduct] = []
        for cd in cds:
            und = cd.contract
            expirations: set[str] = set()
            strikes: set[float] = set()
            try:
                chains = await c._req(c.ib.reqSecDefOptParamsAsync(und.symbol, "", "IND", und.conId), 10, f"secDefOptParams {und.symbol}") or []
                for ch in chains:
                    if ch.exchange and ch.exchange.upper() != "FORECASTX":
                        continue
                    expirations.update(ch.expirations or [])
                    strikes.update(float(s) for s in (ch.strikes or []))
            except Exception as exc:  # noqa: BLE001
                log.warning("reqSecDefOptParams failed for %s: %s", und.symbol, exc)
            out.append(
                EventProduct(
                    venue="FORECASTX",
                    symbol=und.symbol,
                    con_id=und.conId or None,
                    long_name=cd.longName or None,
                    trading_class=und.tradingClass or None,
                    expirations=sorted(expirations),
                    strikes=sorted(strikes),
                )
            )
        return out

    async def chain(self, venue: str, symbol: str, expiry: str = "") -> list[EventContract]:
        c = self.client
        if not c.connected:
            return []
        v = venue.upper()
        if v == "FORECASTX":
            probe = Contract(symbol=symbol.upper(), secType="OPT", exchange="FORECASTX", currency="USD", lastTradeDateOrContractMonth=expiry)
        elif v == "CME_EVENT":
            # symbol = the future (ES, CL, GC...). The exchange is the future's listing venue.
            exch = {"ES": "CME", "NQ": "CME", "CL": "NYMEX", "NG": "NYMEX", "GC": "COMEX", "SI": "COMEX", "ZN": "CBOT", "ZB": "CBOT", "EUR": "CME"}.get(symbol.upper(), "CME")
            probe = Contract(symbol=symbol.upper(), secType="FOP", exchange=exch, currency="USD", tradingClass=f"EC{symbol.upper()}", lastTradeDateOrContractMonth=expiry)
        else:
            probe = Contract(symbol=symbol.upper(), secType="OPT", exchange=v, currency="USD", lastTradeDateOrContractMonth=expiry)
        cds = await c.details(probe)
        out: list[EventContract] = []
        for cd in cds:
            is_event, venue_code, side, payout = classify_event_contract(cd.contract, self.verified_codes())
            out.append(
                EventContract(
                    contract=contract_ref(cd.contract),
                    venue=venue_code or v,
                    side=side,  # type: ignore[arg-type]
                    real_expiration_date=cd.realExpirationDate or None,
                    last_trade_time=cd.lastTradeTime or None,
                    payout=payout if is_event else None,
                )
            )
        out.sort(key=lambda e: ((e.contract.expiry or ""), e.contract.strike or 0.0, e.side or ""))
        return out

    async def quotes(self, con_ids: list[int]) -> tuple[list[EventQuote], list[str]]:
        c = self.client
        out: list[EventQuote] = []
        warnings: list[str] = []
        if not c.connected:
            return out, ["bridge not connected to TWS"]
        for cid in con_ids:
            try:
                entry = await c.ensure_ticker(cid)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"conId {cid}: {exc}")
                continue
            q = quote_from_ticker(entry.ticker, entry.min_tick)
            _is_event, _venue, _side, payout = classify_event_contract(entry.contract, self.verified_codes())
            prob, basis = implied_probability(q.bid, q.ask, q.last, payout)
            out.append(EventQuote(con_id=cid, quote=q, payout=payout, implied_prob=prob, prob_basis=basis))  # type: ignore[arg-type]
        return out, warnings

    @staticmethod
    def describe(obj: Any) -> str:
        return repr(obj)
