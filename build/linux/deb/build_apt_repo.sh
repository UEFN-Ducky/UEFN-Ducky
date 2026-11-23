#!/usr/bin/env bash
# A signed apt repo (suite "stable", component "main") from .deb files:
#   build_apt_repo.sh <repo-dir> <file.deb>...
# Release is signed through release/sign_linux.py (InRelease + Release.gpg);
# DUCKY_REPO_SIGNER replaces that command (same "detach|clearsign <in> <out>" arguments).
set -euo pipefail
repo=$1; shift
root=$(cd "$(dirname "$0")/../../.." && pwd)
read -r -a signer <<< "${DUCKY_REPO_SIGNER:-python3 $root/release/sign_linux.py repo}"

mkdir -p "$repo/pool/main"
cp "$@" "$repo/pool/main/"
cd "$repo"
for arch in amd64 arm64; do
  dir=dists/stable/main/binary-$arch
  mkdir -p "$dir"
  apt-ftparchive --arch "$arch" packages pool > "$dir/Packages"
  gzip -9kf "$dir/Packages"
done
apt-ftparchive \
  -o APT::FTPArchive::Release::Origin="UEFN Ducky" \
  -o APT::FTPArchive::Release::Label="UEFN Ducky" \
  -o APT::FTPArchive::Release::Suite=stable \
  -o APT::FTPArchive::Release::Codename=stable \
  -o APT::FTPArchive::Release::Architectures="amd64 arm64" \
  -o APT::FTPArchive::Release::Components=main \
  release dists/stable > dists/stable/Release.tmp
mv dists/stable/Release.tmp dists/stable/Release
"${signer[@]}" clearsign dists/stable/Release dists/stable/InRelease
"${signer[@]}" detach dists/stable/Release dists/stable/Release.gpg
