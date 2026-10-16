"""Read-only HTTP routes. There is intentionally no order/write route anywhere in this package."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..instruments import find_instrument, load_instruments, spec_dict
from ..models import (
    AccountSummary,
    BarsResponse,
    ContractRef,
    ContractResolution,
    EventContract,
    EventProduct,
    EventQuote,
    EventVenue,
    Fill,
    Health,
    OpenOrder,
    Position,
    Quote,
    Snapshot,
)

router = APIRouter()


def _svc(request: Request):
    return request.app.state.service


def _events(request: Request):
    return request.app.state.events


def _parse_conids(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"bad conId '{part}'") from exc
    return out[:200]


# ---------------------------------------------------------------- health / account
@router.get("/health", response_model=Health)
async def health(request: Request):
    return await _svc(request).health()


@router.get("/account/summary", response_model=list[AccountSummary])
async def account_summary(request: Request):
    return await _svc(request).account_summaries()


# ---------------------------------------------------------------- book
@router.get("/positions", response_model=list[Position])
async def positions(request: Request):
    svc = _svc(request)
    if svc.client.connected:
        await svc.client.pin_book_positions()
    return svc.positions()


@router.get("/orders", response_model=list[OpenOrder])
async def orders(request: Request):
    return await _svc(request).open_orders()


@router.get("/fills", response_model=list[Fill])
async def fills(request: Request, since: str | None = Query(default=None, description="ISO-8601 UTC lower bound")):
    rows = _svc(request).fills_today()
    if since:
        rows = [f for f in rows if (f.time_utc or "") >= since]
    return rows


# ---------------------------------------------------------------- quotes / snapshot
@router.get("/quotes", response_model=dict[int, Quote])
async def quotes(request: Request, conIds: str = Query(..., description="comma-separated conIds")):
    out, warnings = await _svc(request).quotes(_parse_conids(conIds))
    if warnings and not out:
        raise HTTPException(status_code=503, detail="; ".join(warnings))
    return out


@router.get("/snapshot", response_model=Snapshot)
async def snapshot(request: Request, conIds: str | None = Query(default=None, description="extra conIds to quote (watchlist)")):
    return await _svc(request).snapshot(_parse_conids(conIds))


# ---------------------------------------------------------------- contracts
@router.get("/instruments")
async def instruments():
    return [
        {
            "key": i.key,
            "engine_symbol": i.engine_symbol,
            "engine_desk": i.engine_desk,
            "display_name": i.display_name,
            "ib": spec_dict(i),
            "micro": spec_dict(i, micro=True) if i.micro else None,
            "notation": i.notation,
            "tick_fraction_of_32nd": i.tick_fraction_of_32nd,
            "direction_sign": i.direction_sign,
            "data_bundle": i.data_bundle,
            "notes": i.notes,
        }
        for i in load_instruments()
    ]


@router.get("/contracts/resolve", response_model=ContractResolution)
async def resolve(request: Request, key: str = Query(..., description="engine symbol, IB trading class or IB symbol"), micro: bool = False):
    inst = find_instrument(key)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument '{key}'")
    if micro and not inst.micro:
        raise HTTPException(status_code=404, detail=f"{inst.key} has no verified micro contract in instruments.json")
    return await _svc(request).resolve(inst, micro=micro)


@router.get("/contracts/search", response_model=list[ContractRef])
async def search(request: Request, text: str = Query(..., min_length=1, max_length=30)):
    return await _svc(request).search(text)


@router.get("/bars", response_model=BarsResponse)
async def bars(
    request: Request,
    conId: int,
    duration: str = Query(default="1 D", pattern=r"^\d+ [SDWMY]$"),
    barSize: str = Query(default="5 mins"),
    what: str = Query(default="TRADES", pattern=r"^(TRADES|MIDPOINT|BID|ASK|BID_ASK)$"),
):
    return await _svc(request).bars(conId, duration, barSize, what)


# ---------------------------------------------------------------- event contracts
@router.get("/events/venues", response_model=list[EventVenue])
async def event_venues(request: Request, probe: bool = False):
    ev = _events(request)
    if probe:
        return await ev.probe_venues()
    return ev.venues


@router.get("/events/products", response_model=list[EventProduct])
async def event_products(request: Request, venue: str = "FORECASTX", symbol: str | None = None):
    try:
        return await _events(request).products(venue, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/events/chain", response_model=list[EventContract])
async def event_chain(request: Request, venue: str, symbol: str, expiry: str = ""):
    return await _events(request).chain(venue, symbol, expiry)


@router.get("/events/quotes", response_model=list[EventQuote])
async def event_quotes(request: Request, conIds: str = Query(...)):
    out, warnings = await _events(request).quotes(_parse_conids(conIds))
    if warnings and not out:
        raise HTTPException(status_code=503, detail="; ".join(warnings))
    return out
