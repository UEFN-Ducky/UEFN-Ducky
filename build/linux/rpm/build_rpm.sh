#!/usr/bin/env bash
# The .rpm from the one-dir build tarball:
#   build_rpm.sh <version> <uefn-ducky.tar> [out-dir]
# writes <out-dir>/uefn-ducky-<version>-1.<arch>.rpm for this machine's arch.
set -euo pipefail
version=$1 tarball=$(readlink -f "$2") out=$(mkdir -p "${3:-dist}" && cd "${3:-dist}" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
top=$(mktemp -d)
trap 'rm -rf "$top"' EXIT
rpmbuild -bb "$here/uefn-ducky.spec" \
  --define "_topdir $top" \
  --define "ducky_version $version" \
  --define "ducky_tar $tarball" \
  --define "ducky_packaging $(cd "$here/../../../packaging/linux" && pwd)"
cp "$top"/RPMS/*/uefn-ducky-"$version"-1.*.rpm "$out/"
