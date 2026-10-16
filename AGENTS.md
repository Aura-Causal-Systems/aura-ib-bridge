# Aura IB Bridge — notes for agents and engineers

Public repository. Never commit tokens, `bridge.json`, account numbers (even masked), logs, or
internal infrastructure details (Cloudflare/D1 ids, private repo paths).

## Non-negotiables
- Read-only: no POST/PUT/PATCH/DELETE route, no IB order API call
  (`tests/test_routes.py::test_no_write_paths_exist` scans the package source).
- No fake or fallback data: missing = `None`; every quote carries IB's market-data type.
- Every awaited IB request goes through `IBClient._req` (hard timeout); see `docs/TWS-NOTES.md`.
- Loopback bind, bearer token, CORS = desk origins (`branding.DESKS`) + `allowed_origins`.
  Remote files (`.well-known`) may only enable pairing for an already-listed desk.
- Bump `branding.API_VERSION` on any incompatible HTTP API change; desks check it.

## Commands
- Setup: `.\setup-bridge.ps1` (conda Python 3.12 in `.venv`; system Python 3.9 is not supported).
- Tests: `.venv\python.exe -m pytest -q`.
- Tray from source: `.venv\python.exe -m ib_bridge.tray` (quit the installed app first: same port).
- Build: `.\packaging\build.ps1` (`-DebugConsole` shows startup tracebacks; `-SkipInstaller`).
- Manual TWS checks: `scripts\smoke_tws.py`, `scripts\discover_event_venues.py`.

## Packaging gotchas (conda + PyInstaller + Windows)
- Bundle `.venv\Library\bin` OpenSSL/ffi/sqlite DLLs (`--add-binary`) or `import ssl` fails.
- Entry point is `packaging/launcher.py` (package-relative imports in `tray.py`).
- `--windowed` has no stdout/stderr: `tray.ensure_std_streams()` redirects them; uvicorn gets
  `log_config=None`.
- `instruments.json` is read from `sys._MEIPASS` when frozen, from the repo root otherwise.
- Config written by PowerShell 5 / Notepad may carry a BOM: read with `utf-8-sig`.

## Release
`ib_bridge/__init__.py` version → build → GitHub Release `v<version>` with
`dist\release\Aura-IB-Bridge-Setup.exe` (stable name; desks link to `releases/latest/download/...`).
The installer replaces the pre-0.3 "IB Copilot Bridge"; the app migrates `%APPDATA%\ib-copilot`
config (token included) to `%APPDATA%\aura-ib-bridge` on first start.
