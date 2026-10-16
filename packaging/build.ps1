# Builds the Windows tray app and (if Inno Setup is installed) the installer.
#   .\packaging\build.ps1                 -> dist\Aura IB Bridge\Aura IB Bridge.exe
#                                            dist\Aura-IB-Bridge-Setup-<ver>.exe   (versioned, keep)
#                                            dist\release\Aura-IB-Bridge-Setup.exe (stable name: attach to the GitHub Release)
#   .\packaging\build.ps1 -SkipInstaller  -> exe only
#   .\packaging\build.ps1 -DebugConsole   -> exe with a console window, to see startup tracebacks (never ship)
# Requires .venv (see setup-bridge.ps1). Inno Setup 6: winget install JRSoftware.InnoSetup
param([switch]$SkipInstaller, [switch]$DebugConsole)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $root ".venv\python.exe"
if (-not (Test-Path $py)) { Write-Error "Missing $py - run setup-bridge.ps1 first" }
Set-Location $root

$version = (& $py -c "import ib_bridge; print(ib_bridge.__version__)" | Select-Object -Last 1).Trim()
$appName = (& $py -c "from ib_bridge.branding import APP_NAME; print(APP_NAME)" | Select-Object -Last 1).Trim()
Write-Host "Building $appName v$version" -ForegroundColor Cyan

& $py -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements.txt") pyinstaller
& $py (Join-Path $root "packaging\make_icon.py")

& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Error "tests failed - not building" }

Remove-Item -Recurse -Force (Join-Path $root "build"), (Join-Path $root "dist") -ErrorAction SilentlyContinue

# Conda-built CPython keeps OpenSSL/ffi/sqlite/lzma/bz2/zlib DLLs in Library\bin, which PyInstaller
# does not discover from the .pyd imports; bundle them next to the exe or `import ssl` fails.
$libBin = Join-Path $root ".venv\Library\bin"
$binaryArgs = @()
foreach ($pattern in @("libssl-*.dll", "libcrypto-*.dll", "ffi*.dll", "sqlite3.dll", "liblzma.dll", "libbz2.dll", "zlib.dll", "libexpat.dll")) {
  Get-ChildItem (Join-Path $libBin $pattern) -ErrorAction SilentlyContinue | ForEach-Object { $binaryArgs += @("--add-binary", "$($_.FullName);.") }
}

$windowMode = if ($DebugConsole) { "--console" } else { "--windowed" }
& $py -m PyInstaller `
  --noconfirm --clean $windowMode --onedir `
  --name $appName `
  --icon (Join-Path $root "packaging\bridge.ico") `
  --add-data "$(Join-Path $root 'instruments.json');." `
  @binaryArgs `
  --collect-submodules uvicorn `
  --collect-submodules ib_bridge `
  --hidden-import pystray._win32 `
  --exclude-module tkinter `
  --paths $root `
  (Join-Path $root "packaging\launcher.py")
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed" }
Write-Host "exe: $(Join-Path $root "dist\$appName\$appName.exe")" -ForegroundColor Green

if ($SkipInstaller -or $DebugConsole) { exit 0 }

$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
  Write-Host "Inno Setup not found - exe built, installer skipped. Install with: winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
  exit 0
}
& $iscc "/DAppVersion=$version" (Join-Path $root "packaging\installer.iss")
if ($LASTEXITCODE -ne 0) { Write-Error "Inno Setup failed" }
$installer = Join-Path $root "dist\Aura-IB-Bridge-Setup-$version.exe"

# Stable file name for GitHub Releases: desks link to .../releases/latest/download/Aura-IB-Bridge-Setup.exe
$release = Join-Path $root "dist\release"
New-Item -ItemType Directory -Force -Path $release | Out-Null
Copy-Item $installer (Join-Path $release "Aura-IB-Bridge-Setup.exe") -Force
$hash = (Get-FileHash (Join-Path $release "Aura-IB-Bridge-Setup.exe") -Algorithm SHA256).Hash
Set-Content (Join-Path $release "SHA256SUMS.txt") "$hash  Aura-IB-Bridge-Setup.exe"
Write-Host "installer: $installer" -ForegroundColor Green
Write-Host "release asset: $release\Aura-IB-Bridge-Setup.exe  (sha256 $hash)" -ForegroundColor Green
Write-Host "Next: GitHub -> Releases -> Draft a new release -> tag v$version -> attach both files in dist\release -> Publish."
