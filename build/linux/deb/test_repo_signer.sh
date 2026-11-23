#!/usr/bin/env bash
# CI-only stand-in for `release/sign_linux.py --repo-meta FILE`: the same outputs
# (Release -> Release.gpg + InRelease, anything else -> FILE.asc) with a throwaway gpg key
# made on first use, whose public half lands in $GNUPGHOME/repo.asc / repo.gpg for the
# container to trust.
set -euo pipefail
[ "${1:-}" = --repo-meta ] && [ -n "${2:-}" ] || { echo "usage: $0 --repo-meta <file>" >&2; exit 2; }
file=$2
export GNUPGHOME=${GNUPGHOME:-$HOME/.gnupg-ducky-ci}
mkdir -p -m 700 "$GNUPGHOME"
if ! gpg --batch --list-secret-keys ci@uefnducky.test >/dev/null 2>&1; then
  gpg --batch --passphrase '' --quick-gen-key "Ducky CI <ci@uefnducky.test>" rsa3072 sign never
  gpg --batch --armor --export ci@uefnducky.test > "$GNUPGHOME/repo.asc"
  gpg --batch --export ci@uefnducky.test > "$GNUPGHOME/repo.gpg"
fi
if [ "$(basename "$file")" = Release ]; then
  gpg --batch --yes --armor --detach-sign -o "$(dirname "$file")/Release.gpg" "$file"
  gpg --batch --yes --clearsign -o "$(dirname "$file")/InRelease" "$file"
else
  gpg --batch --yes --armor --detach-sign -o "$file.asc" "$file"
fi
