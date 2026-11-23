#!/usr/bin/env bash
# CI, inside archlinux:base-devel as root: .SRCINFO matches the PKGBUILD, then
# `makepkg -si` from the release AppImage (as a build user), start it under xvfb,
# remove it and check nothing is left in /opt.
#   ci_test.sh <UEFN-Ducky-<ver>-x86_64.AppImage>
set -euo pipefail
appimage=$(readlink -f "$1")
here=$(cd "$(dirname "$0")" && pwd)
version=$(basename "$appimage" | sed -E 's/^UEFN-Ducky-(.+)-x86_64\.AppImage$/\1/')
pacman -Syu --noconfirm --needed xorg-server-xvfb xorg-xauth python sudo

id builder >/dev/null 2>&1 || useradd -m builder
echo 'builder ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/builder
pkg=/home/builder/pkg
rm -rf "$pkg" && mkdir -p "$pkg"
cp "$here/PKGBUILD" "$here/.SRCINFO" "$pkg/"
chown -R builder: "$pkg"
sudo -u builder bash -c "cd $pkg && makepkg --printsrcinfo | diff -u .SRCINFO -"

sed -i "s/^pkgver=.*/pkgver=$version/" "$pkg/PKGBUILD"
cp "$appimage" "$pkg/UEFN-Ducky-$version-x86_64.AppImage"
chown -R builder: "$pkg"
sudo -u builder bash -c "cd $pkg && makepkg -si --noconfirm"

test -x /usr/bin/uefn-ducky
test -f /usr/share/applications/uefn-ducky.desktop
test -f /usr/share/icons/hicolor/256x256/apps/uefn-ducky.png
xvfb-run -a uefn-ducky --runtime-smoke /tmp/aur-smoke.json
python -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['ok'], r" /tmp/aur-smoke.json

pacman -Rns --noconfirm uefn-ducky-bin
test ! -e /opt/uefn-ducky && test ! -e /usr/bin/uefn-ducky
echo "aur: makepkg -si, start and remove OK"
