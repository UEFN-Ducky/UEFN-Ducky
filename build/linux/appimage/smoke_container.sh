#!/bin/bash
# Start the AppImage on a clean distro (CI: docker run <image> bash smoke_container.sh <AppImage>).
# Installs a virtual display and the host's WebKitGTK 4.1 (the AppImage bundles PyGObject and the
# GI typelibs, never the GTK/WebKit stack: unified.spec LINUX_HOST_LIBS), then runs the frozen app's --runtime-smoke (imports, MCP,
# every bundled panel asset) through the AppImage runtime without FUSE, checks the embedded
# update info, and checks the menu integration installs and uninstalls cleanly.
set -euo pipefail
app="$(readlink -f "$1")"
want_update="${2:-}"
# "fuse": start it the way a desktop does, mounted through FUSE (docker run --device /dev/fuse
# --cap-add SYS_ADMIN), with fusermount3 as on stock Ubuntu 24.04 and no libfuse2 anywhere.
mode="${3:-extract}"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq xvfb xauth python3 libwebkit2gtk-4.1-0 >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q xorg-x11-server-Xvfb xorg-x11-xauth python3 which findutils webkit2gtk4.1 >/dev/null
elif command -v pacman >/dev/null; then
  # -Syu, not -Sy: a partial upgrade conflicts with the image's own packages (openssl).
  pacman -Syu --noconfirm --needed xorg-server-xvfb xorg-xauth python webkit2gtk-4.1 >/dev/null
fi
. /etc/os-release && echo "== $PRETTY_NAME ($(uname -m))"
chmod +x "$app"
if [ "$mode" = fuse ]; then
  apt-get install -y -qq fuse3 >/dev/null
  if ldconfig -p | grep -q 'libfuse\.so\.2'; then echo "libfuse2 is installed: not a stock system"; exit 1; fi
  echo "no libfuse.so.2 here; $(fusermount3 --version 2>&1)"
  "$app" --appimage-version
else
  export APPIMAGE_EXTRACT_AND_RUN=1
fi
if [ -n "$want_update" ]; then
  # Read the runtime's .upd_info ELF section: with APPIMAGE_EXTRACT_AND_RUN the runtime hands
  # --appimage-updateinformation to the app instead of answering it.
  got="$(python3 - "$app" <<'PY'
import struct, sys
d = open(sys.argv[1], "rb").read()
shoff, = struct.unpack_from("<Q", d, 0x28)
shentsize, shnum, shstrndx = struct.unpack_from("<HHH", d, 0x3A)
sec = [struct.unpack_from("<IIQQQQ", d, shoff + i * shentsize) for i in range(shnum)]
strtab = sec[shstrndx][4]
for name, _t, _f, _a, off, size in sec:
    if d[strtab + name:d.index(b"\0", strtab + name)] == b".upd_info":
        print(d[off:off + size].split(b"\0")[0].decode())
PY
)"
  [ "$got" = "$want_update" ] || { echo "update info: $got != $want_update"; exit 1; }
fi
report="$(mktemp -d)/smoke.json"
# A container has no user namespaces for WebKit's bubblewrap sandbox; a desktop does.
export WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1
xvfb-run -a "$app" --runtime-smoke "$report" 2>&1 | tee "$report.log" \
  || { echo "runtime-smoke failed:"; cat "$report" 2>/dev/null; exit 1; }
! grep -q "GTK cannot be loaded" "$report.log" || { echo "pywebview could not load GTK"; exit 1; }
python3 - "$report" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r.get("ok") and r.get("panel", {}).get("all_assets_verified") and r.get("window"), r
print("runtime-smoke ok:", r.get("python"), r.get("versions"), r["window"])
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
