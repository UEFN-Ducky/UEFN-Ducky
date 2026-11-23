#!/usr/bin/env bash
# The .deb from the one-dir Linux build (dist/uefn-ducky, made by build_exes.py):
#   build_deb.sh <version> <amd64|arm64> <build-dir> [out-dir]
# writes <out-dir>/uefn-ducky_<version>_<arch>.deb (/opt/uefn-ducky + /usr/bin link,
# desktop file and hicolor icons; dpkg's triggers refresh the menu and icon caches).
set -euo pipefail
version=$1 arch=$2 build=$3 out=${4:-dist}
here=$(cd "$(dirname "$0")" && pwd)
pkg=$(cd "$here/../../../packaging/linux" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
chmod 755 "$stage"

mkdir -p "$stage/DEBIAN" "$stage/opt" "$stage/usr/bin" "$stage/usr/share/applications"
cp -a "$build" "$stage/opt/uefn-ducky"
ln -s /opt/uefn-ducky/uefn-ducky "$stage/usr/bin/uefn-ducky"
install -m644 "$pkg/uefn-ducky.desktop" "$stage/usr/share/applications/uefn-ducky.desktop"
cp -r "$pkg/icons" "$stage/usr/share/icons"

size=$(du -sk "$stage" | cut -f1)
sed -e "s/@VERSION@/$version/" -e "s/@ARCH@/$arch/" -e "s/@SIZE@/$size/" "$here/control" > "$stage/DEBIAN/control"
mkdir -p "$out"
dpkg-deb --root-owner-group -Zxz --build "$stage" "$out/uefn-ducky_${version}_${arch}.deb"
