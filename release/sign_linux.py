#!/usr/bin/env python3
"""Sign the Linux release files.

- Every artifact (AppImage, .zsync, .deb, .rpm, Flatpak bundle, tarball) gets ``<file>.minisig``:
  a prehashed minisign signature (what ``frontend/updater.verify_minisign`` checks before an
  AppImage swap). Written here with ``cryptography``'s Ed25519, so CI needs no minisign binary.
- apt/dnf repo metadata gets GPG: ``Release`` -> ``Release.gpg`` + ``InRelease``,
  anything else (``repomd.xml``) -> ``<file>.asc``.

Keys come from CI secrets only, never from a file in the repo:
  MINISIGN_SECRET_KEY        the minisign ``.key`` file's text (``minisign -G``; encrypted or ``-W``)
  MINISIGN_PASSWORD          its password when encrypted
  LINUX_REPO_GPG_PRIVATE_KEY armored secret key (``gpg --export-secret-keys --armor``)
  LINUX_REPO_GPG_PASSPHRASE  its passphrase, if any

Usage: python release/sign_linux.py [--repo-meta FILE ...] ARTIFACT ...
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_SK_LEN = 158  # Ed|kdf|B2|salt32|ops8|mem8|keynum_sk104


class SigningError(Exception):
    pass


def _pick_scrypt_params(opslimit: int, memlimit: int) -> tuple[int, int, int]:
    """libsodium's crypto_pwhash_scryptsalsa208sha256 parameter choice (minisign's KDF)."""
    opslimit = max(opslimit, 32768)
    r = 8
    if opslimit < memlimit // 32:
        p, max_n = 1, opslimit // (r * 4)
    else:
        max_n = memlimit // (r * 128)
    n_log2 = 1
    while n_log2 < 63 and (1 << n_log2) <= max_n // 2:
        n_log2 += 1
    if not opslimit < memlimit // 32:
        p = min((opslimit // 4) // (1 << n_log2), 0x3FFFFFFF) // r
    return 1 << n_log2, r, max(p, 1)


def _kdf_stream(password: bytes, salt: bytes, opslimit: int, memlimit: int) -> bytes:
    n, r, p = _pick_scrypt_params(opslimit, memlimit)
    return hashlib.scrypt(password, salt=salt, n=n, r=r, p=p, maxmem=256 * r * n * p + (64 << 20), dklen=104)


def _key_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def load_secret_key(text: str, password: str = "") -> tuple[bytes, bytes]:
    """(key_id, 32-byte Ed25519 seed) from a minisign secret key file's text."""
    try:
        raw = base64.b64decode(_key_lines(text)[-1], validate=True)
    except (IndexError, ValueError) as exc:
        raise SigningError("minisign secret key is not base64") from exc
    if len(raw) != _SK_LEN or raw[:2] != b"Ed" or raw[4:6] != b"B2":
        raise SigningError("not a minisign Ed25519 secret key")
    kdf, salt = raw[2:4], raw[6:38]
    ops, mem = int.from_bytes(raw[38:46], "little"), int.from_bytes(raw[46:54], "little")
    keynum = raw[54:]
    if kdf == b"Sc":
        if not password:
            raise SigningError("minisign secret key is encrypted: set MINISIGN_PASSWORD")
        stream = _kdf_stream(password.encode(), salt, ops, mem)
        keynum = bytes(a ^ b for a, b in zip(keynum, stream))
    elif kdf != b"\0\0":
        raise SigningError(f"unknown minisign KDF {kdf!r}")
    key_id, sk, checksum = keynum[:8], keynum[8:72], keynum[72:]
    if hashlib.blake2b(b"Ed" + key_id + sk, digest_size=32).digest() != checksum:
        raise SigningError("minisign secret key checksum mismatch (wrong password?)")
    seed, public = sk[:32], sk[32:]
    derived = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if derived != public:
        raise SigningError("minisign secret key is corrupt")
    return key_id, seed


def generate_keypair(password: str = "", *, opslimit: int = 33554432, memlimit: int = 1073741824) -> tuple[str, str]:
    """(public key file text, secret key file text), as ``minisign -G`` writes them.
    For tests and key rotation; the release only ever reads the secret from CI."""
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                              serialization.NoEncryption())
    public = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_id = os.urandom(8)
    sk = seed + public
    keynum = key_id + sk + hashlib.blake2b(b"Ed" + key_id + sk, digest_size=32).digest()
    salt = os.urandom(32)
    kdf = b"\0\0"
    if password:
        kdf = b"Sc"
        keynum = bytes(a ^ b for a, b in zip(keynum, _kdf_stream(password.encode(), salt, opslimit, memlimit)))
    raw = b"Ed" + kdf + b"B2" + salt + opslimit.to_bytes(8, "little") + memlimit.to_bytes(8, "little") + keynum
    kid = key_id[::-1].hex().upper()
    pub_text = f"untrusted comment: minisign public key {kid}\n{base64.b64encode(b'Ed' + key_id + public).decode()}\n"
    label = "encrypted secret key" if password else "secret key"
    sec_text = f"untrusted comment: minisign {label}\n{base64.b64encode(raw).decode()}\n"
    return pub_text, sec_text


def _blake2b_file(path: Path) -> bytes:
    h = hashlib.blake2b(digest_size=64)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.digest()


def sign_file(path: Path, key_id: bytes, seed: bytes, *, now: float | None = None) -> Path:
    """Write ``<path>.minisig`` (prehashed, minisign 0.8+ format) and return it."""
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    sig = priv.sign(_blake2b_file(path))
    trusted = f"timestamp:{int(now if now is not None else time.time())}\tfile:{path.name}\thashed"
    global_sig = priv.sign(sig + trusted.encode())
    out = path.with_name(path.name + ".minisig")
    out.write_text(
        "untrusted comment: signature from UEFN Ducky release key\n"
        f"{base64.b64encode(b'ED' + key_id + sig).decode()}\n"
        f"trusted comment: {trusted}\n"
        f"{base64.b64encode(global_sig).decode()}\n",
        encoding="utf-8",
    )
    return out


def verify_file(path: Path, signature: str, public_key: str) -> bool:
    """Check ``<path>.minisig`` text against a minisign public key file's text."""
    try:
        key = base64.b64decode(_key_lines(public_key)[-1])
        lines = _key_lines(signature)
        sig, trusted, global_sig = base64.b64decode(lines[1]), lines[2], base64.b64decode(lines[3])
        if len(key) != 42 or sig[:2] != b"ED" or sig[2:10] != key[2:10]:
            return False
        if not trusted.startswith("trusted comment: "):
            return False
        pub = Ed25519PublicKey.from_public_bytes(key[10:])
        pub.verify(sig[10:], _blake2b_file(path))
        pub.verify(global_sig, sig[10:] + trusted[len("trusted comment: "):].encode())
        return True
    except (IndexError, ValueError, InvalidSignature, OSError):
        return False


def gpg_sign(files: list[Path], key_armored: str, passphrase: str = "", *, gpg: str | None = None) -> list[Path]:
    """GPG-sign apt/dnf metadata in a throwaway keyring (the runner's own keyring is never used)."""
    gpg = gpg or shutil.which("gpg")
    if not gpg:
        raise SigningError("gpg not found on PATH")
    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="ducky-gpg-") as home:
        os.chmod(home, 0o700)
        base = [gpg, "--homedir", home, "--batch", "--yes", "--pinentry-mode", "loopback",
                "--passphrase-fd", "0"]
        pw = (passphrase + "\n").encode()

        def run(args: list[str], data: bytes = pw) -> None:
            proc = subprocess.run(base + args, input=data, capture_output=True)
            if proc.returncode != 0:
                raise SigningError(f"gpg {args[0]} failed: {proc.stderr.decode(errors='replace')[-400:]}")

        key_file = Path(home) / "key.asc"
        key_file.write_text(key_armored, encoding="utf-8")
        run(["--import", str(key_file)])
        key_file.unlink()
        for f in files:
            if f.name == "Release":
                targets = [(["--armor", "--detach-sign", "-o"], f.with_name("Release.gpg")),
                           (["--clearsign", "-o"], f.with_name("InRelease"))]
            else:
                targets = [(["--armor", "--detach-sign", "-o"], f.with_name(f.name + ".asc"))]
            for args, out in targets:
                run(args + [str(out), str(f)])
                written.append(out)
    return written


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    p = argparse.ArgumentParser(description="Sign Linux release files (minisign + GPG repo metadata)")
    p.add_argument("artifacts", nargs="*", type=Path)
    p.add_argument("--repo-meta", nargs="*", type=Path, default=[], help="apt Release / dnf repomd.xml")
    args = p.parse_args(argv)
    try:
        if args.artifacts:
            secret = env.get("MINISIGN_SECRET_KEY", "")
            if not secret:
                raise SigningError("MINISIGN_SECRET_KEY is not set: refusing to ship unsigned files")
            key_id, seed = load_secret_key(secret, env.get("MINISIGN_PASSWORD", ""))
            for f in args.artifacts:
                print(f"minisign: {sign_file(f, key_id, seed)}")
        if args.repo_meta:
            key = env.get("LINUX_REPO_GPG_PRIVATE_KEY", "")
            if not key:
                raise SigningError("LINUX_REPO_GPG_PRIVATE_KEY is not set")
            for out in gpg_sign(args.repo_meta, key, env.get("LINUX_REPO_GPG_PASSPHRASE", "")):
                print(f"gpg: {out}")
    except (SigningError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
