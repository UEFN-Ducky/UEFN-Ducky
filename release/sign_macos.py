#!/usr/bin/env python3
"""Sign and notarize the macOS release (``UEFN Ducky.app``, its DMG).

Secrets come from CI only. With none set (today's default) the build is ad-hoc signed
(``codesign -s -``) and notarization is skipped, so every job stays green; adding all six
turns on Developer ID signing + notarization with no code change:
  MACOS_CERT_P12_B64   Developer ID Application certificate + key (.p12, base64)
  MACOS_CERT_PASSWORD  its password
  APPLE_TEAM_ID        the team id in the certificate's name, ``Developer ID Application: X (TEAMID)``
  ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_P8_B64   App Store Connect API key (notarytool)

Usage:
  python release/sign_macos.py sign "dist/UEFN Ducky.app"
  python release/sign_macos.py notarize "dist/UEFN Ducky.app" [--dmg UEFN-Ducky-<v>-macos-<arch>.dmg]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTITLEMENTS = ROOT / "build" / "macos" / "entitlements.plist"
SIGN_SECRETS = ("MACOS_CERT_P12_B64", "MACOS_CERT_PASSWORD", "APPLE_TEAM_ID")
NOTARY_SECRETS = ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_P8_B64")
# Mach-O and universal (fat) headers, both byte orders.
_MACHO_MAGIC = {bytes.fromhex(h) for h in ("feedface", "feedfacf", "cefaedfe", "cffaedfe", "cafebabe", "bebafeca")}


class SigningError(Exception):
    pass


def _have(env, names: tuple[str, ...]) -> bool:
    """All of ``names`` set; some but not all is a broken setup, never a silent ad-hoc build."""
    got = [n for n in names if env.get(n, "").strip()]
    if got and len(got) != len(names):
        missing = ", ".join(n for n in names if n not in got)
        raise SigningError(f"incomplete Apple secrets: {missing} not set")
    return bool(got)


def _is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) in _MACHO_MAGIC
    except OSError:
        return False


def code_files(app: Path) -> list[Path]:
    """Every Mach-O file in the bundle (``.so``, ``.dylib``, executables), deepest first:
    codesign seals inside-out, so nested code is signed before what contains it.
    ponytail: nested .framework bundles are signed file by file, not as bundles; enough for
    PyInstaller's flat Frameworks/ (sign them as bundles if one ever ships with Versions/)."""
    found = [p for p in (app / "Contents").rglob("*") if p.is_file() and not p.is_symlink() and _is_macho(p)]
    return sorted(found, key=lambda p: (-len(p.relative_to(app).parts), p.as_posix()))


def _ok(run, cmd: list[str], **kw):
    proc = run(cmd, **kw)
    if getattr(proc, "returncode", 1) != 0:
        err = getattr(proc, "stderr", "") or ""
        raise SigningError(f"{' '.join(cmd[:3])} failed: {str(err)[-400:]}")
    return proc


def sign_app(app: Path, env=None, run=subprocess.run, entitlements: Path = ENTITLEMENTS) -> str:
    """Sign ``app`` inside-out; returns ``"developer-id"`` or ``"adhoc"``."""
    env = os.environ if env is None else env
    app = Path(app)
    if not (app / "Contents" / "Info.plist").is_file():
        raise SigningError(f"{app} is not an .app bundle")
    files = code_files(app)
    if not _have(env, SIGN_SECRETS):
        print("unsigned macOS build (ad-hoc)")
        base = ["codesign", "--force", "-s", "-", "--entitlements", str(entitlements)]
        for f in files:
            _ok(run, base + [str(f)])
        _ok(run, base + [str(app)])
        return "adhoc"
    with tempfile.TemporaryDirectory(prefix="ducky-sign-") as tmp:
        keychain, p12 = Path(tmp) / "ducky-sign.keychain-db", Path(tmp) / "cert.p12"
        password = secrets.token_hex(16)
        p12.write_bytes(base64.b64decode(env["MACOS_CERT_P12_B64"]))
        try:
            for cmd in (
                ["security", "create-keychain", "-p", password, str(keychain)],
                ["security", "set-keychain-settings", "-lut", "21600", str(keychain)],
                ["security", "unlock-keychain", "-p", password, str(keychain)],
                ["security", "import", str(p12), "-k", str(keychain), "-P", env["MACOS_CERT_PASSWORD"],
                 "-T", "/usr/bin/codesign"],
                ["security", "set-key-partition-list", "-S", "apple-tool:,apple:", "-s", "-k", password, str(keychain)],
            ):
                _ok(run, cmd, capture_output=True, text=True)
            p12.unlink()
            # The team id is in the certificate's common name and the keychain holds just this one.
            base = ["codesign", "--force", "--options", "runtime", "--timestamp", "--entitlements",
                    str(entitlements), "--keychain", str(keychain), "-s", env["APPLE_TEAM_ID"].strip()]
            for f in files:
                _ok(run, base + [str(f)])
            _ok(run, base + [str(app)])  # never --deep for signing: it skips entitlements on nested code
            _ok(run, ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
        finally:
            run(["security", "delete-keychain", str(keychain)], capture_output=True)
    print(f"signed {app.name} (Developer ID, hardened runtime)")
    return "developer-id"


def notarize(app: Path, dmg: Path | None = None, env=None, run=subprocess.run) -> bool:
    """Notarize and staple ``app`` (and ``dmg``). Without Apple credentials: skipped, logged, False."""
    env = os.environ if env is None else env
    signed, notary = _have(env, SIGN_SECRETS), _have(env, NOTARY_SECRETS)
    if not (signed and notary):
        print("notarize: skipped (no Apple credentials, ad-hoc build)")
        return False
    app = Path(app)
    with tempfile.TemporaryDirectory(prefix="ducky-notary-") as tmp:
        key = Path(tmp) / f"AuthKey_{env['ASC_KEY_ID'].strip()}.p8"
        key.write_bytes(base64.b64decode(env["ASC_KEY_P8_B64"]))
        auth = ["--key", str(key), "--key-id", env["ASC_KEY_ID"].strip(), "--issuer", env["ASC_ISSUER_ID"].strip()]
        archive = Path(tmp) / f"{app.stem}.zip"
        _ok(run, ["ditto", "-c", "-k", "--keepParent", str(app), str(archive)])
        targets = [(archive, app)] + ([(Path(dmg), Path(dmg))] if dmg else [])
        for upload, staple in targets:
            proc = _ok(run, ["xcrun", "notarytool", "submit", str(upload), *auth, "--wait", "--output-format", "json"],
                       capture_output=True, text=True)
            try:
                result = json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                result = {}
            if result.get("status") != "Accepted":
                sub = str(result.get("id") or "")
                if sub:
                    log = run(["xcrun", "notarytool", "log", sub, *auth], capture_output=True, text=True)
                    print(getattr(log, "stdout", "") or "", file=sys.stderr)
                raise SigningError(f"notarization of {upload.name} was {result.get('status') or 'not accepted'}")
            print(f"notarize: {upload.name} accepted ({result.get('id')})")
            _ok(run, ["xcrun", "stapler", "staple", str(staple)])
    return True


def main(argv: list[str] | None = None, env=None, run=subprocess.run) -> int:
    p = argparse.ArgumentParser(description="Sign / notarize the macOS app (ad-hoc without Apple secrets)")
    p.add_argument("action", choices=("sign", "notarize"))
    p.add_argument("app", type=Path)
    p.add_argument("--dmg", type=Path)
    args = p.parse_args(argv)
    try:
        if args.action == "sign":
            sign_app(args.app, env, run)
        else:
            notarize(args.app, args.dmg, env, run)
    except (SigningError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
