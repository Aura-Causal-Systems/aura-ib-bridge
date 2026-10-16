"""Empirical discovery of how IBKR Prediction Markets contracts appear in the TWS API.

What is documented (IB Campus, TWS API 'Event Trading'): ForecastEx = OPT on
FORECASTX (Call=YES, Put=NO, pays 1.00); CME event contracts = FOP with trading
class EC<future> (pays 100). What is NOT documented: how Kalshi contracts routed
through IB are modelled. This script asks TWS and prints exactly what comes
back, so the desk only ever renders verified venues.

Usage (from the repo root):  .venv\\python.exe scripts\\discover_event_venues.py [--port 7496] [--symbols FF,CPI,GDP]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ib_bridge.config import BridgeConfig  # noqa: E402
from ib_bridge.events_discovery import EventsService  # noqa: E402
from ib_bridge.ib_client import IBClient  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7496)
    ap.add_argument("--client-id", type=int, default=19)
    ap.add_argument("--symbols", default="FF,CPI,GDP,NFP", help="ForecastEx product codes to enumerate")
    ap.add_argument("--matching", default="Kalshi,FED,CPI", help="reqMatchingSymbols patterns to inspect for unknown venues")
    args = ap.parse_args()

    cfg = BridgeConfig(tws_host=args.host, tws_port=args.port, client_id=args.client_id, token="probe", allowed_origins=[])
    client = IBClient(cfg)
    client.ib.errorEvent += client._on_error
    events = EventsService(client)
    try:
        await client._connect_once()
    except Exception as exc:  # noqa: BLE001
        print(f"CONNECT FAILED: {exc}")
        return 2

    print("== venue probes ==")
    for v in await events.probe_venues(deep=True):
        print(json.dumps(v.model_dump()))

    print("\n== ForecastEx products ==")
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        try:
            prods = await events.products("FORECASTX", sym)
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: error {exc}")
            continue
        for p in prods:
            print(json.dumps(p.model_dump()))
        if prods:
            chain = await events.chain("FORECASTX", sym)
            print(f"  {sym}: {len(chain)} contracts; first 6:")
            for ec in chain[:6]:
                print("   ", json.dumps(ec.model_dump()))
            if chain:
                quotes, warnings = await events.quotes([ec.contract.con_id for ec in chain[:4]])
                await asyncio.sleep(3)
                quotes, warnings = await events.quotes([ec.contract.con_id for ec in chain[:4]])
                for q in quotes:
                    print("    quote", json.dumps(q.model_dump()))
                if warnings:
                    print("    warnings", warnings)

    print("\n== CME event contract chain probe (ES) ==")
    try:
        chain = await events.chain("CME_EVENT", "ES")
        print(f"  {len(chain)} contracts; first 4:")
        for ec in chain[:4]:
            print("   ", json.dumps(ec.model_dump()))
    except Exception as exc:  # noqa: BLE001
        print("  error", exc)

    print("\n== reqMatchingSymbols: look for unknown exchanges (Kalshi-via-IB) ==")
    seen_exchanges: set[str] = set()
    for pat in [s.strip() for s in args.matching.split(",") if s.strip()]:
        try:
            matches = await client.ib.reqMatchingSymbolsAsync(pat)
        except Exception as exc:  # noqa: BLE001
            print(f"{pat}: error {exc}")
            continue
        for m in matches or []:
            c = m.contract
            seen_exchanges.add(f"{c.secType}@{c.primaryExchange or c.exchange}")
            print(f"{pat}: {c.symbol} {c.secType} {c.primaryExchange or c.exchange} {c.currency} derivs={list(m.derivativeSecTypes or [])} desc={getattr(c, 'description', '')}")
    print("exchanges seen:", sorted(seen_exchanges))

    print("\n== recent IB messages ==")
    for e in list(client.errors)[-25:]:
        print(f"reqId={e.req_id} code={e.code} {e.message}")
    client.ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
