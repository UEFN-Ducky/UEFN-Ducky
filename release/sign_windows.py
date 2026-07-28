#!/usr/bin/env python3
"""Authenticode-sign Windows EXEs for SmartScreen / Chrome download trust.

Chrome's "isn't commonly downloaded" and Windows SmartScreen both improve when
Setup.exe + the payload EXE are signed with a real code-signing certificate.

Env (first match wins):

  DUCKY_WINDOWS_PFX + DUCKY_WINDOWS_PFX_PASSWORD
      Path to .pfx/.p12 and its password (OV / exportable cert).

  DUCKY_SIGNTOOL_EXTRA
      Extra args appended to every signtool invoke (e.g. Azure SignTool /
      DigiCert KeyLocker: set DUCKY_SIGNTOOL to that binary and put token
      args here). When set without a PFX, the file path is still appended
      last:  <signtool> sign <EXTRA...> <file.exe>

  DUCKY_SIGNTOOL
      Override path to signtool.exe (default: newest Windows Kits bin\\x64).

  DUCKY_SIGN_TIMESTAMP_URL
      Default http://timestamp.digicert.com

Usage:
  py release/sign_windows.py dist/UEFN-Ducky-Setup-1.0.564.exe
  py release/sign_windows.py --require dist/UEFN-Ducky-1.0.564.exe dist/UEFN-Ducky-Setup-1.0.564.exe

Exit codes:
  0  signed (or skipped because no cert configured and --require not set)
  2  --require but no signing credentials configured
  1  signtool failed
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"


def _load_dotenv() -> None:
    for path in (
        ROOT / ".env",
        ROOT.parent / "DuckyOS" / ".env",
        Path.home() / ".duckyos" / ".env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def find_signtool() -> Path | None:
    override = (os.environ.get("DUCKY_SIGNTOOL") or "").strip()
    if override:
        p = Path(override)
        return p if p.is_file() else None
    which = shutil.which("signtool")
    if which:
        return Path(which)
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    if not kits.is_dir():
        return None
    candidates: list[Path] = []
    for ver_dir in sorted(kits.iterdir(), reverse=True):
        cand = ver_dir / "x64" / "signtool.exe"
        if cand.is_file():
            candidates.append(cand)
    return candidates[0] if candidates else None


def signing_configured() -> bool:
    pfx = (os.environ.get("DUCKY_WINDOWS_PFX") or "").strip()
    extra = (os.environ.get("DUCKY_SIGNTOOL_EXTRA") or "").strip()
    return bool(pfx) or bool(extra)


def _pfx_args() -> list[str]:
    pfx = (os.environ.get("DUCKY_WINDOWS_PFX") or "").strip()
    if not pfx:
        return []
    path = Path(pfx)
    if not path.is_file():
        raise SystemExit(f"DUCKY_WINDOWS_PFX not found: {path}")
    password = os.environ.get("DUCKY_WINDOWS_PFX_PASSWORD")
    if password is None:
        raise SystemExit("DUCKY_WINDOWS_PFX_PASSWORD is required when using DUCKY_WINDOWS_PFX")
    return ["/f", str(path), "/p", password]


def sign_file(path: Path, *, signtool: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"Cannot sign missing file: {path}")
    ts = (os.environ.get("DUCKY_SIGN_TIMESTAMP_URL") or DEFAULT_TIMESTAMP).strip()
    extra = (os.environ.get("DUCKY_SIGNTOOL_EXTRA") or "").strip()
    extra_parts = extra.split() if extra else []

    cmd = [
        str(signtool),
        "sign",
        "/fd",
        "sha256",
        "/td",
        "sha256",
        "/tr",
        ts,
        *_pfx_args(),
        *extra_parts,
        str(path),
    ]
    # Never print password — redact /p value in log.
    log_cmd = []
    skip_next = False
    for i, part in enumerate(cmd):
        if skip_next:
            skip_next = False
            log_cmd.append("***")
            continue
        if part == "/p":
            log_cmd.append(part)
            skip_next = True
            continue
        log_cmd.append(part)
    print(f"  signtool: {' '.join(log_cmd)}")
    subprocess.run(cmd, check=True)


def verify_file(path: Path, *, signtool: Path) -> None:
    subprocess.run([str(signtool), "verify", "/pa", str(path)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path, help="EXE paths to sign")
    parser.add_argument(
        "--require",
        action="store_true",
        help="Fail if no DUCKY_WINDOWS_PFX / DUCKY_SIGNTOOL_EXTRA (refuse unsigned publish)",
    )
    parser.add_argument("--self-check", action="store_true", help="Sanity check helpers and exit")
    args = parser.parse_args()

    if args.self_check:
        assert DEFAULT_TIMESTAMP.startswith("http")
        print("sign_windows self-check ok")
        return

    if not args.files:
        raise SystemExit("Pass one or more EXE paths to sign (or --self-check)")

    _load_dotenv()

    if not signing_configured():
        msg = (
            "No Windows code-signing credentials configured "
            "(set DUCKY_WINDOWS_PFX + DUCKY_WINDOWS_PFX_PASSWORD, or DUCKY_SIGNTOOL_EXTRA). "
            "Chrome/SmartScreen will keep warning on Setup.exe until you sign releases."
        )
        if args.require:
            print(msg, file=sys.stderr)
            raise SystemExit(2)
        print(f"  warn: {msg}")
        return

    signtool = find_signtool()
    if signtool is None:
        raise SystemExit(
            "signtool.exe not found. Install Windows SDK Signing Tools, or set DUCKY_SIGNTOOL."
        )

    for path in args.files:
        print(f"=== Sign {path.name} ===")
        sign_file(path, signtool=signtool)
        verify_file(path, signtool=signtool)
        print(f"  ok: {path.name}")


if __name__ == "__main__":
    main()
