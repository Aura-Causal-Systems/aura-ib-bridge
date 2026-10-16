# Developer console run of Aura IB Bridge on 127.0.0.1:7801 (no tray icon) using the repo's Python 3.12 env.
# Quit the installed tray app first (they share the port). Ctrl+C stops the bridge.
#
# TWS must be running with the API enabled (File > Global Configuration > API > Settings):
#   [x] Enable ActiveX and Socket Clients    [x] Read-Only API    Socket port 7496 (live) / 7497 (paper)
#   Trusted IPs: 127.0.0.1                   (leave "Allow connections from localhost only" checked)
param([switch]$NoClipboard)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Bridge environment missing. Run .\setup-bridge.ps1 first." -ForegroundColor Yellow
  exit 1
}
Set-Location $root

# Ensure config/token exist and tell the user where they are (token printed by the bridge only on first creation).
$token = & $py -c "from ib_bridge.config import load_config; print(load_config().token)" | Select-Object -Last 1
$info = & $py -c "from ib_bridge.config import load_config; c = load_config(); print(f'{c.path}|{c.tws_host}|{c.tws_port}|{c.bind_host}|{c.bind_port}')" | Select-Object -Last 1
$cfgPath, $twsHost, $twsPort, $bindHost, $bindPort = $info -split '\|'
if (-not $NoClipboard) { try { Set-Clipboard -Value $token; $clip = "token copied to clipboard" } catch { $clip = "token in $cfgPath" } } else { $clip = "token in $cfgPath" }

$twsUp = $false
try { $twsUp = (Test-NetConnection -ComputerName $twsHost -Port ([int]$twsPort) -InformationLevel Quiet -WarningAction SilentlyContinue) } catch { }

Write-Host ""
Write-Host "Aura IB Bridge (console)" -ForegroundColor Cyan
Write-Host "  listening : http://${bindHost}:${bindPort}   (loopback only, read-only, bearer token required)"
Write-Host "  TWS       : ${twsHost}:${twsPort} -> $(if ($twsUp) { 'port open' } else { 'NOT reachable yet - start TWS and enable the API; the bridge keeps retrying' })"
Write-Host "  config    : $cfgPath"
Write-Host "  auth      : $clip"
Write-Host ""

& $py -m uvicorn ib_bridge.main:create_app --factory --host $bindHost --port ([int]$bindPort) --log-level warning
