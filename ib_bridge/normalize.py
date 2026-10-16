"""Pure normalisation helpers - no IB connection needed, fully unit-testable.

These functions turn ib_async objects (or plain values) into the wire models.
Rule: if IB did not deliver a value, the output is None. No defaults, no
interpolation.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .models import ContractRef, Freshness, Quote

# ib_async uses float('nan') for "no value"; IB's wire protocol uses DBL_MAX
# (1.7976931348623157e+308, UNSET_DOUBLE) for unset fields such as realizedPnL on
# positions IB does not track. Both mean "not delivered".
UNSET_DOUBLE_THRESHOLD = 1e300


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f) or abs(f) >= UNSET_DOUBLE_THRESHOLD:
        return None
    return f


def iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def now_utc_iso() -> str:
    return iso_utc(datetime.now(timezone.utc)) or ""


FRESHNESS_BY_TYPE: dict[int, Freshness] = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed_frozen"}


def freshness_from_type(market_data_type: int | None, has_any_value: bool) -> Freshness:
    """IB market data type -> label. Unknown type or no values at all -> 'n/a'."""
    if not has_any_value:
        return "n/a"
    return FRESHNESS_BY_TYPE.get(int(market_data_type or 0), "n/a")


def mask_account(account_id: str | None) -> str:
    if not account_id:
        return ""
    return "*" * max(0, len(account_id) - 3) + account_id[-3:]


def contract_ref(c: Any) -> ContractRef:
    """ib_async.Contract -> ContractRef (works with any object exposing the same attrs)."""
    return ContractRef(
        con_id=int(getattr(c, "conId", 0) or 0),
        symbol=str(getattr(c, "symbol", "") or ""),
        local_symbol=(getattr(c, "localSymbol", None) or None),
        sec_type=str(getattr(c, "secType", "") or ""),
        exchange=(getattr(c, "exchange", None) or None),
        primary_exchange=(getattr(c, "primaryExchange", None) or None),
        currency=(getattr(c, "currency", None) or None),
        expiry=(getattr(c, "lastTradeDateOrContractMonth", None) or None),
        multiplier=to_float(getattr(c, "multiplier", None)),
        trading_class=(getattr(c, "tradingClass", None) or None),
        right=(getattr(c, "right", None) or None),
        strike=to_float(getattr(c, "strike", None)) if getattr(c, "strike", None) not in (None, 0, 0.0) else None,
        description=(getattr(c, "description", None) or None),
    )


def quote_from_ticker(t: Any, min_tick: float | None = None) -> Quote:
    """ib_async.Ticker -> Quote with freshness derived from ticker.marketDataType."""
    bid, ask, last, close = (to_float(getattr(t, n, None)) for n in ("bid", "ask", "last", "close"))
    volume = to_float(getattr(t, "volume", None))
    bid_size = to_float(getattr(t, "bidSize", None))
    ask_size = to_float(getattr(t, "askSize", None))
    # IB uses -1 for "no bid/ask available" on some feeds.
    bid = None if bid is not None and bid < 0 else bid
    ask = None if ask is not None and ask < 0 else ask
    has_any = any(v is not None for v in (bid, ask, last, close))
    mdt = getattr(t, "marketDataType", None)
    halted_raw = to_float(getattr(t, "halted", None))
    return Quote(
        con_id=int(getattr(getattr(t, "contract", None), "conId", 0) or 0),
        bid=bid,
        ask=ask,
        last=last,
        close=close,
        volume=volume,
        bid_size=bid_size,
        ask_size=ask_size,
        market_data_type=int(mdt) if mdt else None,
        freshness=freshness_from_type(int(mdt) if mdt else None, has_any),
        updated_utc=iso_utc(getattr(t, "time", None)),
        halted=(halted_raw is not None and halted_raw > 0) if halted_raw is not None else None,
        min_tick=min_tick,
    )


DERIVATIVE_SEC_TYPES = {"FUT", "FOP", "OPT"}


def avg_price_from_cost(avg_cost: float | None, multiplier: float | None, sec_type: str) -> tuple[float | None, str]:
    """IB reports avgCost per contract in currency for derivatives (price x multiplier)."""
    if avg_cost is None:
        return None, "unknown"
    if sec_type in DERIVATIVE_SEC_TYPES:
        if multiplier and multiplier > 0:
            return avg_cost / multiplier, "avg_cost_div_multiplier"
        return None, "unknown"
    return avg_cost, "avg_cost"


# Event-contract venues. Verified from IB's TWS API documentation (Event Trading page):
#   ForecastEx -> exchange FORECASTX, secType OPT, Call = YES, Put = NO, pays USD 1.00.
#   CME event contracts -> secType FOP on CME/CBOT/COMEX/NYMEX with trading class 'EC<future>', pays USD 100.
# Anything else (e.g. Kalshi routed through IB) is unverified until discovery confirms it.
FORECASTX = "FORECASTX"
CME_EVENT_EXCHANGES = {"CME", "CBOT", "COMEX", "NYMEX"}


def classify_event_contract(c: Any, extra_venues: Iterable[str] = ()) -> tuple[bool, str | None, str | None, float | None]:
    """-> (is_event, venue_code, side YES/NO, payout)."""
    exchange = (getattr(c, "exchange", "") or "").upper()
    primary = (getattr(c, "primaryExchange", "") or "").upper()
    sec_type = (getattr(c, "secType", "") or "").upper()
    trading_class = (getattr(c, "tradingClass", "") or "").upper()
    right = (getattr(c, "right", "") or "").upper()
    side = "YES" if right in ("C", "CALL") else "NO" if right in ("P", "PUT") else None
    if FORECASTX in (exchange, primary):
        return True, FORECASTX, side, 1.0
    if sec_type == "FOP" and trading_class.startswith("EC") and (exchange in CME_EVENT_EXCHANGES or primary in CME_EVENT_EXCHANGES):
        return True, "CME_EVENT", side, 100.0
    for v in extra_venues:
        if v and v.upper() in (exchange, primary):
            return True, v.upper(), side, None
    return False, None, None, None


def implied_probability(bid: float | None, ask: float | None, last: float | None, payout: float | None) -> tuple[float | None, str]:
    """Crowd-implied probability (0-100) of the contract's side from its book.

    ForecastEx has no trades/last, only 'highest bid' and 'buy now at' (ask);
    two-sided -> mid, else ask, else bid. Returns (None, 'n/a') when payout is
    unknown or nothing is quoted. Never extrapolates.
    """
    if not payout or payout <= 0:
        return None, "n/a"
    if bid is not None and ask is not None and 0 < bid <= payout and 0 < ask <= payout and bid <= ask:
        return round((bid + ask) / 2 / payout * 100, 2), "mid"
    if ask is not None and 0 < ask <= payout:
        return round(ask / payout * 100, 2), "ask"
    if bid is not None and 0 < bid <= payout:
        return round(bid / payout * 100, 2), "bid"
    if last is not None and 0 < last <= payout:
        return round(last / payout * 100, 2), "last"
    return None, "n/a"


def parse_ib_date(s: str | None) -> date | None:
    """'YYYYMMDD' or 'YYYYMM' -> date (first of month for YYYYMM). Also tolerates 'YYYYMMDD HH:MM:SS TZ'."""
    if not s:
        return None
    s = s.strip().split(" ")[0]
    try:
        if len(s) == 8:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        if len(s) == 6:
            return date(int(s[:4]), int(s[4:6]), 1)
    except ValueError:
        return None
    return None


def choose_front(details: list[Any], today: date, roll_window_days: int | None) -> tuple[Any | None, Any | None, str | None]:
    """Pick the front (nearest unexpired) and next contract from contract details.

    Returns (front_details, next_details, roll_note). The roll note is set when
    the front expires inside the roll window, so the desk can show both and
    prefer the more liquid one once volumes are observed. No volume guess here.
    """
    dated = []
    for d in details:
        c = getattr(d, "contract", d)
        exp = parse_ib_date(getattr(c, "lastTradeDateOrContractMonth", None))
        if exp is None or exp < today:
            continue
        dated.append((exp, d))
    dated.sort(key=lambda x: x[0])
    if not dated:
        return None, None, None
    front_exp, front = dated[0]
    nxt = dated[1][1] if len(dated) > 1 else None
    note = None
    if roll_window_days is not None and (front_exp - today).days <= roll_window_days:
        note = f"front expires {front_exp.isoformat()} ({(front_exp - today).days}d): inside the roll window - check volume before treating it as the liquid contract"
    return front, nxt, note
