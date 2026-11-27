#!/usr/bin/env python3
"""Package ``UEFN Ducky.app``: a DMG for download, an update zip for the in-app updater.

Both go through Apple's own tools (``ditto``, ``hdiutil``), which keep code signatures,
symlinks and extended attributes; Python's zipfile drops them.

Usage: python build/macos/package.py "dist/UEFN Ducky.app" <version> [--arch arm64|x64] [--out dist]
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

VOLUME = "UEFN Ducky"


def mac_arch(machine: str | None = None) -> str:
    m = (machine or platform.machine()).lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def artifact_names(version: str, arch: str) -> tuple[str, str]:
    """(dmg, updater zip) file names, e.g. ``UEFN-Ducky-1.3.0-macos-x64.dmg`` and
    ``uefn-ducky-update-1.3.0-macos-x64.zip``. The Store scans only ``uefn-ducky-update-*.zip``
    as a release pack (zip structure); any other .zip gets the plugin-source text rules,
    which refuse the form feeds in stock Tcl/Tk files."""
    return f"UEFN-Ducky-{version}-macos-{arch}.dmg", f"uefn-ducky-update-{version}-macos-{arch}.zip"


def _ok(run, cmd: list[str]) -> None:
    if getattr(run(cmd), "returncode", 1) != 0:
        raise RuntimeError(f"{cmd[0]} failed: {' '.join(cmd)}")


def make_dmg(app: Path, out: Path, run=subprocess.run) -> Path:
    """Drag-to-install DMG: the app beside an ``/Applications`` link."""
    app, out = Path(app), Path(out)
    with tempfile.TemporaryDirectory(prefix="ducky-dmg-") as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        _ok(run, ["ditto", str(app), str(stage / app.name)])
        _ok(run, ["ln", "-s", "/Applications", str(stage / "Applications")])
        _ok(run, ["hdiutil", "create", "-volname", VOLUME, "-srcfolder", str(stage), "-format", "UDZO", "-ov", str(out)])
    return out


def make_zip(app: Path, out: Path, run=subprocess.run) -> Path:
    """The updater's zip: unpacks (``ditto -x -k``) to ``UEFN Ducky.app`` next to the running one."""
    app, out = Path(app), Path(out)
    _ok(run, ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(out)])
    return out


def main(argv: list[str] | None = None, run=subprocess.run) -> int:
    p = argparse.ArgumentParser(description="DMG + updater zip for the macOS app")
    p.add_argument("app", type=Path)
    p.add_argument("version")
    p.add_argument("--arch", default=mac_arch(), choices=("arm64", "x64"))
    p.add_argument("--out", type=Path, default=Path("dist"))
    args = p.parse_args(argv)
    if not (args.app / "Contents" / "Info.plist").is_file():
        print(f"ERROR: {args.app} is not an .app bundle", file=sys.stderr)
        return 1
    dmg, zipname = artifact_names(args.version, args.arch)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        print(make_dmg(args.app, args.out / dmg, run))
        print(make_zip(args.app, args.out / zipname, run))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
