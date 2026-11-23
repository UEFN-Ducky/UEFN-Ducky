#!/usr/bin/env bash
# CI, on an ubuntu runner: flatpak-builder the manifest from the one-dir build tarball,
# install it, `--self-test` (flatpak-spawn --host true), start it under xvfb, export
# UEFN-Ducky-<version>-x86_64.flatpak, then uninstall.
#   ci_test.sh <version> <uefn-ducky.tar> [out-dir]
set -euo pipefail
version=$1 tarball=$(readlink -f "$2") out=${3:-dist}
here=$(cd "$(dirname "$0")" && pwd)
app=online.uefnducky.Ducky
sudo apt-get update
sudo apt-get install -y flatpak flatpak-builder xvfb dbus-daemon
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo

work=$(mktemp -d)
cp "$tarball" "$here/uefn-ducky.tar"
trap 'rm -f "$here/uefn-ducky.tar"' EXIT
flatpak-builder --user --install-deps-from=flathub --install --force-clean --disable-rofiles-fuse \
  --repo="$work/repo" "$work/build" "$here/$app.yml"

flatpak info --show-permissions "$app" | grep -q org.freedesktop.Flatpak
dbus-run-session -- flatpak run "$app" --self-test
xvfb-run -a dbus-run-session -- flatpak run "$app" --runtime-smoke "$HOME/flatpak-smoke.json"
python3 -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['ok'], r" "$HOME/flatpak-smoke.json"

mkdir -p "$out"
flatpak build-bundle "$work/repo" "$out/UEFN-Ducky-$version-x86_64.flatpak" "$app"
flatpak uninstall --user -y "$app"
if flatpak info "$app" >/dev/null 2>&1; then echo "still installed" >&2; exit 1; fi
echo "flatpak: build, self-test, start and remove OK"
