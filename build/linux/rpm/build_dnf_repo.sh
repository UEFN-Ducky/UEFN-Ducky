#!/usr/bin/env bash
# A signed dnf repo from .rpm files:
#   build_dnf_repo.sh <repo-dir> <file.rpm>...
# createrepo_c writes repodata/; repomd.xml is signed by `release/sign_linux.py --repo-meta`
# (repomd.xml.asc, checked by repo_gpgcheck=1 in uefn-ducky.repo). DUCKY_REPO_SIGNER
# replaces that command (same "--repo-meta <file>" arguments).
set -euo pipefail
repo=$1; shift
root=$(cd "$(dirname "$0")/../../.." && pwd)
read -r -a signer <<< "${DUCKY_REPO_SIGNER:-python3 $root/release/sign_linux.py}"

mkdir -p "$repo"
cp "$@" "$repo/"
createrepo_c --update "$repo"
"${signer[@]}" --repo-meta "$repo/repodata/repomd.xml"
test -s "$repo/repodata/repomd.xml.asc"
