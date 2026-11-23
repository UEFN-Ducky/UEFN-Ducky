#!/usr/bin/env bash
# CI, inside an ubuntu container as root: build the .deb from the one-dir build tarball,
# put it in a signed apt repo, add the repo, `apt install uefn-ducky`, start it under xvfb,
# then remove it and check nothing is left in /opt.
#   ci_test.sh <version> <uefn-ducky.tar>
set -euo pipefail
version=$1 tarball=$(readlink -f "$2")
here=$(cd "$(dirname "$0")" && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends dpkg-dev apt-utils gnupg xvfb xauth ca-certificates python3

work=$(mktemp -d)
tar -C "$work" -xf "$tarball"
arch=$(dpkg --print-architecture)
bash "$here/build_deb.sh" "$version" "$arch" "$work/uefn-ducky" "$work/out"
DUCKY_REPO_SIGNER="bash $here/test_repo_signer.sh" GNUPGHOME="$work/gnupg" \
  bash "$here/build_apt_repo.sh" "$work/repo" "$work/out/uefn-ducky_${version}_${arch}.deb"

install -m644 "$work/gnupg/repo.gpg" /usr/share/keyrings/uefn-ducky.gpg
chmod -R a+rX "$work"
echo "deb [signed-by=/usr/share/keyrings/uefn-ducky.gpg] file://$work/repo stable main" \
  > /etc/apt/sources.list.d/uefn-ducky.list
apt-get update
apt-cache show uefn-ducky | grep -E '^Depends:.*espeak-ng.*libportaudio2'
apt-get install -y uefn-ducky

test -x /usr/bin/uefn-ducky
test -f /usr/share/applications/uefn-ducky.desktop
test -f /usr/share/icons/hicolor/256x256/apps/uefn-ducky.png
xvfb-run -a uefn-ducky --runtime-smoke "$work/smoke.json"
python3 -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['ok'], r" "$work/smoke.json"

apt-get remove -y --purge uefn-ducky
test ! -e /opt/uefn-ducky && test ! -e /usr/bin/uefn-ducky
echo "deb: install, start and remove OK"
