#!/usr/bin/env bash
# CI-only stand-in for `sign_linux.py repo`: signs with a throwaway gpg key made on first use
# and exports its public half to $GNUPGHOME/repo.asc / repo.gpg for the container to trust.
#   test_repo_signer.sh detach|clearsign <in> <out>
set -euo pipefail
export GNUPGHOME=${GNUPGHOME:-$HOME/.gnupg-ducky-ci}
mkdir -p -m 700 "$GNUPGHOME"
if ! gpg --batch --list-secret-keys ci@uefnducky.test >/dev/null 2>&1; then
  gpg --batch --passphrase '' --quick-gen-key "Ducky CI <ci@uefnducky.test>" rsa3072 sign never
  gpg --batch --armor --export ci@uefnducky.test > "$GNUPGHOME/repo.asc"
  gpg --batch --export ci@uefnducky.test > "$GNUPGHOME/repo.gpg"
fi
case $1 in
  detach) gpg --batch --yes --armor --detach-sign -o "$3" "$2" ;;
  clearsign) gpg --batch --yes --clearsign -o "$3" "$2" ;;
  *) echo "usage: $0 detach|clearsign <in> <out>" >&2; exit 2 ;;
esac
