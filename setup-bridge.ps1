# Developer setup for Aura IB Bridge on Windows (one-time, safe to re-run). End users use the installer instead.
#   1. finds conda (Anaconda/Miniconda) and creates .venv with Python 3.12 if missing
#   2. installs the pinned Python dependencies
#   3. runs the test suite (no TWS needed)
#   4. creates the bridge config + bearer token if missing and copies the token to the clipboard
# Then: .\run-bridge.ps1 (console server) or .venv\python.exe -m ib_bridge.tray (tray app), or build with packaging\build.ps1.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "python.exe"

function Find-Conda {
  $c = Get-Command conda -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @("$env:USERPROFILE\anaconda3\Scripts\conda.exe", "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
                   "$env:LOCALAPPDATA\anaconda3\Scripts\conda.exe", "$env:LOCALAPPDATA\miniconda3\Scripts\conda.exe",
                   "C:\ProgramData\anaconda3\Scripts\conda.exe", "C:\ProgramData\miniconda3\Scripts\conda.exe")) {
    if (Test-Path $p) { return $p }
  }
  return $null
}

Write-Host ""
Write-Host "Aura IB Bridge - developer setup" -ForegroundColor Cyan
Write-Host "--------------------------------"

if (-not (Test-Path $py)) {
  $conda = Find-Conda
  if (-not $conda) {
    Write-Host "Python 3.12 is required and no conda was found." -ForegroundColor Yellow
    Write-Host "Install Miniconda (https://docs.conda.io/en/latest/miniconda.html) or Python 3.12 from python.org,"
    Write-Host "then re-run this script. With python.org Python: py -3.12 -m venv .venv"
    exit 1
  }
  Write-Host "[1/4] creating Python 3.12 environment in $venv (this downloads ~40 MB once)..."
  & $conda create -p $venv python=3.12 -y | Out-Null
} else {
  Write-Host "[1/4] Python environment present: $(& $py --version)"
}

Write-Host "[2/4] installing dependencies..."
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements.txt")

Write-Host "[3/4] running the bridge test suite..."
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "Tests failed - stop here and report the output." -ForegroundColor Red; exit 1 }

Write-Host "[4/4] creating config + token if missing..."
$token = & $py -c "from ib_bridge.config import load_config; c = load_config(); print(c.token)" | Select-Object -Last 1
$cfgPath = & $py -c "from ib_bridge.config import config_path; print(config_path())" | Select-Object -Last 1
try { Set-Clipboard -Value $token; $clip = "copied to your clipboard" } catch { $clip = "not copied (clipboard unavailable)" }

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  config : $cfgPath"
Write-Host "  token  : $clip - paste it into the desk's Setup page"
Write-Host ""
Write-Host "Next: start TWS, enable the API (Setup page explains the exact settings), then run .\run-bridge.ps1"
