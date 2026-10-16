"""Manual smoke test against the running TWS: prints what the desk would see.

Usage (from the repo root):  .venv\\python.exe scripts\\smoke_tws.py [--port 7496] [--client-id 18]

Compare the output against the TWS Account window and Portfolio tab. Nothing
here is asserted automatically because the truth is on your screen.
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
from ib_bridge.instruments import find_instrument  # noqa: E402
from ib_bridge.service import BridgeService  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7496)
    ap.add_argument("--client-id", type=int, default=18)
    ap.add_argument("--resolve", default="ES,ZN,GC,CL,USDJPY", help="comma-separated instrument keys to resolve and quote")
    ap.add_argument("--probe-events", action="store_true", help="probe event-contract venues (ForecastEx / CME / Kalshi-via-IB)")
    args = ap.parse_args()

    cfg = BridgeConfig(tws_host=args.host, tws_port=args.port, client_id=args.client_id, token="smoke", allowed_origins=[])
    client = IBClient(cfg)
    svc = BridgeService(client)
    events = EventsService(client)
    client.ib.errorEvent += client._on_error
    print(f"connecting to {args.host}:{args.port} clientId={args.client_id} (readonly)...")
    try:
        await client._connect_once()
    except Exception as exc:  # noqa: BLE001
        print(f"CONNECT FAILED: {exc}")
        print("Check: TWS running? API enabled? port right (7496 live / 7497 paper)? 127.0.0.1 trusted?")
        return 2

    await asyncio.sleep(3)  # let account summary / positions / first ticks arrive
    print("\n== health ==")
    print(json.dumps((await svc.health()).model_dump(), indent=2))
    print("\n== account summary ==")
    print(json.dumps([a.model_dump() for a in await svc.account_summaries()], indent=2))
    await client.pin_book_positions()
    await asyncio.sleep(3)
    print("\n== positions ==")
    for p in svc.positions():
        print(json.dumps(p.model_dump(), indent=2))
    quotes, warnings = await svc.quotes([p.contract.con_id for p in svc.positions()])
    print("\n== quotes for book ==")
    for cid, q in quotes.items():
        print(cid, json.dumps(q.model_dump()))
    if warnings:
        print("warnings:", warnings)

    print("\n== resolve + quote front months ==")
    for key in [k.strip() for k in args.resolve.split(",") if k.strip()]:
        inst = find_instrument(key)
        if not inst:
            print(f"{key}: not in instruments.json")
            continue
        res = await svc.resolve(inst)
        print(f"{key}: front={res.front.local_symbol if res.front else None} next={res.next.local_symbol if res.next else None} roll_note={res.roll_note}")
        if res.front:
            q, w = await svc.quotes([res.front.con_id])
            await asyncio.sleep(2)
            q, w = await svc.quotes([res.front.con_id])
            print("   ", json.dumps(q.get(res.front.con_id).model_dump() if q else {"warnings": w}))

    print("\n== open orders ==")
    print(json.dumps([o.model_dump() for o in await svc.open_orders()], indent=2))
    print("\n== fills today ==")
    print(json.dumps([f.model_dump() for f in svc.fills_today()], indent=2))

    if args.probe_events:
        print("\n== event venues probe ==")
        for v in await events.probe_venues():
            print(json.dumps(v.model_dump()))

    print("\n== recent IB messages ==")
    for e in list(client.errors)[-15:]:
        print(f"reqId={e.req_id} code={e.code} {e.message}")
    client.ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
