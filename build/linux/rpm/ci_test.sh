#!/usr/bin/env bash
# CI, inside a fedora container as root: build the .rpm from the one-dir build tarball,
# put it in a signed dnf repo, `dnf install uefn-ducky` from it, start it under xvfb,
# then remove it and check nothing is left in /opt.
#   ci_test.sh <version> <uefn-ducky.tar>
set -euo pipefail
version=$1 tarball=$(readlink -f "$2")
here=$(cd "$(dirname "$0")" && pwd)
dnf install -y rpm-build createrepo_c gnupg2 xorg-x11-server-Xvfb xorg-x11-xauth python3 tar

work=$(mktemp -d)
bash "$here/build_rpm.sh" "$version" "$tarball" "$work/out"
DUCKY_REPO_SIGNER="bash $here/../deb/test_repo_signer.sh" GNUPGHOME="$work/gnupg" \
  bash "$here/build_dnf_repo.sh" "$work/repo" "$work"/out/uefn-ducky-"$version"-1.*.rpm
test -f "$work/repo/repodata/repomd.xml.asc"

sed -e "s|^baseurl=.*|baseurl=file://$work/repo|" -e "s|^gpgkey=.*|gpgkey=file://$work/gnupg/repo.asc|" \
  "$here/uefn-ducky.repo" > /etc/yum.repos.d/uefn-ducky.repo
rpm -qp --requires "$work"/out/uefn-ducky-*.rpm | grep -E '^(espeak-ng|portaudio)$'
dnf install -y uefn-ducky

test -x /usr/bin/uefn-ducky
test -f /usr/share/applications/uefn-ducky.desktop
test -f /usr/share/icons/hicolor/256x256/apps/uefn-ducky.png
xvfb-run -a uefn-ducky --runtime-smoke "$work/smoke.json"
python3 -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['ok'], r" "$work/smoke.json"

dnf remove -y uefn-ducky
test ! -e /opt/uefn-ducky && test ! -e /usr/bin/uefn-ducky
echo "rpm: install, start and remove OK"
