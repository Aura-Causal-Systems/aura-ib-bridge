# Aura IB Bridge

A small Windows helper that lets [Aura Causal Systems](https://auracausalsystems.com/) web desks
(IB Copilot, Bond Copilot) read a trader's own **Interactive Brokers TWS** on the same computer:
account values, positions, P&L, quotes, working orders, fills and IB event contracts (ForecastEx).

**It is read-only and cannot place, change or cancel orders.** That is enforced three times:

1. **TWS** — the trader enables the API with *Read-Only API* ticked, so TWS itself refuses any order.
2. **The connection** — the bridge connects with `readonly=True`.
3. **The code** — there is no write route and no order call anywhere; a test fails the build if one
   ever appears (`tests/test_routes.py::test_no_write_paths_exist`).

## For traders

1. Download **Aura-IB-Bridge-Setup.exe** from the [latest release](https://github.com/Aura-Causal-Systems/aura-ib-bridge/releases/latest)
   (or from your desk's *Setup* page). Chrome may ask you to **Keep** it and Windows may say
   *"Windows protected your PC"* → **More info → Run anyway**: both appear because the app is new and
   not yet code-signed.
2. Install with the defaults. A small icon appears next to the clock; the desk opens already connected.
3. In TWS, once: *File → Global Configuration → API → Settings* → tick **Enable ActiveX and Socket
   Clients** and **Read-Only API**, socket port **7496** (live) / **7497** (paper), Trusted IP **127.0.0.1**.

Tray menu: **Open IB Copilot / Open Bond Copilot** · Copy pairing link · TWS account (Live / Paper /
Gateway) · Start with Windows · View log · Quit. Blue icon = TWS connected, amber = waiting for TWS.

Uninstall from Windows *Settings → Apps*. Settings and the token live in `%APPDATA%\aura-ib-bridge\`.

## Security and privacy model

- Listens on **127.0.0.1:7801 only** (never reachable from the network, so no firewall prompt).
- Every request needs a **bearer token** generated on first run (`bridge.json`), compared in constant time.
- **CORS allow-list**: only the desk origins in `ib_bridge/branding.py` (`DESKS`) plus developer
  origins in `bridge.json` can call it. Chrome additionally asks the trader once per site to allow
  *local network access*.
- **Pairing**: *Open <desk>* opens `<origin><pair_path>#pair=<token>&bridge=http://localhost:7801`.
  The part after `#` is never sent to any web server; the desk stores it in the browser and removes it
  from the address bar. The tray only does this for desks that publish
  `<origin>/.well-known/aura-ib-bridge.json` with `{"pair_path": "/setup"}`; a remote file can never
  add origins to the allow-list.
- Your IB data goes from TWS → this helper → your own browser. The helper sends nothing anywhere else
  (its only outbound request is reading a desk's `.well-known` file when you click *Open <desk>*).
- No data is invented: anything IB did not deliver is `null`; every quote carries IB's market-data
  type (`live` / `delayed` / `frozen`).

## For desk developers

Base URL `http://localhost:7801`, header `Authorization: Bearer <token>`, all `GET`, JSON, UTC ISO times.

| Route | Returns |
|---|---|
| `/health` | `app_name`, `api_version`, TWS connection, mode (live/paper), masked accounts, lines in use |
| `/snapshot?conIds=` | one call: health, account summaries, positions, quotes (book + extra conIds), open orders, fills |
| `/account/summary` | NetLiq, cash, margin, buying power; `daily/unrealized/realized_pnl` (IB P&L feed) and `ledger_unrealized/realized_pnl` (account ledger) |
| `/positions`, `/orders`, `/fills?since=` | the book as IB reports it |
| `/quotes?conIds=` | streaming quotes with `freshness` |
| `/instruments` | the engine ↔ IB instrument map (`instruments.json`): `key`, `role` (primary / alternative / context / curve), engine symbol, IB spec, multiplier, tick, notation |
| `/contracts/resolve?key=ES[&micro=true]`, `/contracts/search?text=` | front month / roll note (futures) or the single contract (spot FX, ETF); symbol search. Keys: ES NQ VX GC SI CL BZ NG DXY · spot FX EURUSD USDJPY GBPUSD (IDEALPRO) · FX futures 6E 6J 6B · ZN ZB ZT ZF TN UB BUND · HYG |
| `/bars?conId=&duration=1 D&barSize=5 mins&what=TRADES` | historical bars, or `entitled:false` + IB's error |
| `/events/venues[?probe=true]`, `/events/products`, `/events/chain`, `/events/quotes` | IB event contracts with implied probability |

Check `api_version` (currently **2**; 2 added explicit instrument keys and spot FX) and ask the trader
to update when it is lower than you need.
To make a new desk pairable: add it to `DESKS`, ship a release, and publish the `.well-known` file.

Note on P&L: IB's P&L feed can include TWS **virtual FX positions** (currency-conversion tracking),
which are not cash balances or positions. Compare with the `ledger_*` fields before presenting P&L.

## Development

```powershell
.\setup-bridge.ps1                    # conda Python 3.12 env in .venv, deps, tests, token
.venv\python.exe -m pytest -q         # no TWS needed (fake IB in tests/conftest.py)
.venv\python.exe -m ib_bridge.tray    # run the tray app from source (quit the installed one first)
.\run-bridge.ps1                      # or a console server without tray
.venv\python.exe scripts\smoke_tws.py # against a running TWS: prints what desks would see
```

Release: bump `ib_bridge/__init__.py` and push, then on GitHub *Releases → Draft a new release* with
tag `v<version>` and **Publish** (no files needed). The `release` workflow
(`.github/workflows/release.yml`) runs the tests on GitHub's Windows machines, builds
`Aura-IB-Bridge-Setup.exe` from that exact tagged source and attaches it with `SHA256SUMS.txt`
(the job summary shows the commit and checksum). Desks link to
`https://github.com/Aura-Causal-Systems/aura-ib-bridge/releases/latest/download/Aura-IB-Bridge-Setup.exe`,
so the file name must stay the same. A local `.\packaging\build.ps1` (needs Inno Setup:
`winget install JRSoftware.InnoSetup`) produces the same files in `dist\release\` for testing.

Live findings against TWS (IB API quirks, event-contract modelling): [`docs/TWS-NOTES.md`](docs/TWS-NOTES.md).

© Aura Causal Systems. All rights reserved. The source is published so traders can verify what the
helper does; no license to reuse it is granted.
