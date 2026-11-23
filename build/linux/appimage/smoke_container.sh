#!/bin/bash
# Start the AppImage on a clean distro (CI: docker run <image> bash smoke_container.sh <AppImage>).
# Installs only a virtual display, then runs the frozen app's --runtime-smoke (imports, MCP,
# every bundled panel asset) through the AppImage runtime without FUSE, checks the embedded
# update info, and checks the menu integration installs and uninstalls cleanly.
set -euo pipefail
app="$(readlink -f "$1")"
want_update="${2:-}"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq xvfb xauth python3 >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q xorg-x11-server-Xvfb xorg-x11-xauth python3 which findutils >/dev/null
elif command -v pacman >/dev/null; then
  pacman -Sy --noconfirm --needed xorg-server-xvfb xorg-xauth python >/dev/null
fi
. /etc/os-release && echo "== $PRETTY_NAME ($(uname -m))"
chmod +x "$app"
export APPIMAGE_EXTRACT_AND_RUN=1
if [ -n "$want_update" ]; then
  got="$("$app" --appimage-updateinformation)"
  [ "$got" = "$want_update" ] || { echo "update info: $got != $want_update"; exit 1; }
fi
report="$(mktemp -d)/smoke.json"
xvfb-run -a "$app" --runtime-smoke "$report"
python3 - "$report" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r.get("ok") and r.get("panel", {}).get("all_assets_verified"), r
print("runtime-smoke ok:", r.get("python"), r.get("versions"))
PY
# Menu integration round trip in a throwaway HOME: user data stays, everything else goes.
export HOME="$(mktemp -d)"
unset XDG_DATA_HOME XDG_CONFIG_HOME XDG_CACHE_HOME
mkdir -p "$HOME/.local/share/UEFN-Ducky/chats" "$HOME/.config/autostart"
echo '{}' > "$HOME/.local/share/UEFN-Ducky/chats/keep.json"
printf '[Desktop Entry]\nType=Application\nName=UEFN Ducky\nExec=%s\n' "$app" > "$HOME/.config/autostart/uefn-ducky.desktop"
"$app" --install-integration
[ -f "$HOME/.local/share/applications/uefn-ducky.desktop" ]
"$app" --uninstall
left="$(cd "$HOME" && find . -mindepth 1 | sort | tr '\n' ' ')"
[ "$left" = "./.config ./.local ./.local/share ./.local/share/UEFN-Ducky ./.local/share/UEFN-Ducky/chats ./.local/share/UEFN-Ducky/chats/keep.json " ] \
  || { echo "uninstall left: $left"; exit 1; }
echo "AppImage OK on $PRETTY_NAME"
