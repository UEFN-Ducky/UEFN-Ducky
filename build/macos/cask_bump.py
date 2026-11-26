#!/usr/bin/env python3
"""Point the Homebrew cask at a release: rewrite its version and per-arch sha256, print the diff.

Usage: python build/macos/cask_bump.py <version> <sha256-arm64> <sha256-x64> [--cask PATH]
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

CASK = Path(__file__).resolve().parent / "cask" / "uefn-ducky.rb"
_VERSION = re.compile(r'^(\s*version ")[^"]*(")', re.M)
_SHA = {arch: re.compile(rf'^(\s*(?:sha256 )?{arch}:\s*")[0-9a-f]{{64}}(")', re.M) for arch in ("arm", "intel")}


def bump(text: str, version: str, sha_arm: str, sha_intel: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"version must be MAJOR.MINOR.PATCH, got {version!r}")
    for sha in (sha_arm, sha_intel):
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"not a sha256: {sha!r}")
    out = text
    for pattern, value in ((_VERSION, version), (_SHA["arm"], sha_arm), (_SHA["intel"], sha_intel)):
        out, n = pattern.subn(lambda m: f"{m.group(1)}{value}{m.group(2)}", out, count=1)
        if n != 1:
            raise ValueError(f"cask has no line matching {pattern.pattern!r}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bump the uefn-ducky Homebrew cask")
    p.add_argument("version")
    p.add_argument("sha_arm64")
    p.add_argument("sha_x64")
    p.add_argument("--cask", type=Path, default=CASK)
    args = p.parse_args(argv)
    old = args.cask.read_text(encoding="utf-8")
    try:
        new = bump(old, args.version, args.sha_arm64.lower(), args.sha_x64.lower())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    args.cask.write_text(new, encoding="utf-8", newline="\n")
    sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                               str(args.cask.name), str(args.cask.name)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
