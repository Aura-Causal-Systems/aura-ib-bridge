#!/usr/bin/env bash
# Builds the macOS menu-bar app and a drag-to-Applications disk image.
#   packaging/build_mac.sh          -> dist/Aura IB Bridge.app
#                                      dist/release/Aura-IB-Bridge-mac-<arch>.dmg (+ SHA256SUMS-mac-<arch>.txt)
#   packaging/build_mac.sh --app    -> .app only (no disk image)
# <arch> is AppleSilicon or Intel, from the machine (or Python) doing the build: PyInstaller cannot
# cross-compile, so build each one on its own kind of Mac (the release workflow does both).
# Requires Python 3.12 (python.org or Homebrew). Uses .venv if present, otherwise creates it.
# Not code-signed with an Apple Developer ID: the app is ad-hoc signed, so the first open needs
# System Settings -> Privacy & Security -> "Open Anyway" (see README).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
APP_ONLY=0
[[ "${1:-}" == "--app" ]] && APP_ONLY=1

PY_BOOT="${PYTHON:-python3.12}"
if [[ ! -x .venv/bin/python ]]; then
  "$PY_BOOT" -m venv .venv
fi
PY=.venv/bin/python
"$PY" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'

VERSION="$("$PY" -c 'import ib_bridge; print(ib_bridge.__version__)')"
APP_NAME="$("$PY" -c 'from ib_bridge.branding import APP_NAME; print(APP_NAME)')"
case "$("$PY" -c 'import platform; print(platform.machine())')" in
  arm64) ARCH=AppleSilicon ;;
  x86_64) ARCH=Intel ;;
  *) echo "unsupported architecture" >&2; exit 1 ;;
esac
echo "Building $APP_NAME v$VERSION for macOS ($ARCH)"

"$PY" -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
"$PY" packaging/make_icon.py
"$PY" -m pytest -q

rm -rf build dist
"$PY" -m PyInstaller \
  --noconfirm --clean --windowed --onedir \
  --name "$APP_NAME" \
  --icon packaging/bridge.icns \
  --osx-bundle-identifier com.auracausalsystems.ib-bridge \
  --add-data "instruments.json:." \
  --collect-submodules uvicorn \
  --collect-submodules ib_bridge \
  --hidden-import pystray._darwin \
  --hidden-import PyObjCTools.AppHelper \
  --exclude-module tkinter \
  --paths "$ROOT" \
  packaging/launcher.py

APP="dist/$APP_NAME.app"
PLIST="$APP/Contents/Info.plist"
# Menu-bar only (no Dock icon), proper version strings, and the minimum macOS the Python build supports.
plutil -replace LSUIElement -bool YES "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace NSHumanReadableCopyright -string "Aura Causal Systems" "$PLIST"
# Editing Info.plist invalidates PyInstaller's signature; re-sign ad hoc (required on Apple Silicon).
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
echo "app: $ROOT/$APP"

[[ $APP_ONLY -eq 1 ]] && exit 0

mkdir -p dist/release dist/dmg
cp -R "$APP" dist/dmg/
ln -s /Applications dist/dmg/Applications
DMG="dist/release/Aura-IB-Bridge-mac-$ARCH.dmg"
hdiutil create -volname "$APP_NAME" -srcfolder dist/dmg -ov -format UDZO "$DMG"
rm -rf dist/dmg
(cd dist/release && shasum -a 256 "$(basename "$DMG")" > "SHA256SUMS-mac-$ARCH.txt")
echo "disk image: $ROOT/$DMG"
cat "dist/release/SHA256SUMS-mac-$ARCH.txt"
