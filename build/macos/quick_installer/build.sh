#!/bin/bash
# Builds dist/UEFN-Ducky-Installer-macos.zip: the universal quick installer (macOS only, needs
# Xcode's swiftc and Pillow for the icon). It always installs the Store's latest release, so
# it is rebuilt only when main.swift changes, not on every release.
#   bash build/macos/quick_installer/build.sh [icon.png]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
out="$root/dist/quick-installer"
app="$out/UEFN Ducky Installer.app"
zip="$root/dist/UEFN-Ducky-Installer-macos.zip"

rm -rf "$out" "$zip"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos11" -o "$out/installer-$arch" "$here/main.swift"
done
lipo -create -output "$app/Contents/MacOS/UEFN Ducky Installer" "$out/installer-arm64" "$out/installer-x86_64"
cp "$here/Info.plist" "$app/Contents/Info.plist"
python3 "$root/build/macos/make_icns.py" "${1:-$root/ducky_app/frontend/ui_web/web/public/OnlineMCPIcon.png}" \
  "$app/Contents/Resources/AppIcon.icns"
# Ad-hoc, like the app: unsigned for Apple, so the first open needs "Open Anyway".
codesign --force -s - "$app"
ditto -c -k --keepParent "$app" "$zip"
ls -l "$zip"
