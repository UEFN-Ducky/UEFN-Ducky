#!/usr/bin/env python3
"""Stage the built phone panel into the UEFN Ducky site plugin.

Direct (tunnel-free) Remote View serves the panel from the tenant's own
platform: the bundle ships inside `plugin-uefn-ducky` and DuckyOS delivers it
same-origin from that tenant's storage at

    https://<tenant>/static/plugins/uefn-ducky/panel/

so there is no CDN, no second domain, and no per-user DNS. This script copies
`web/dist` into the plugin's `assets/panel/`; the plugin release then packages
and uploads it like any other plugin asset.

Usage (from repo root):
  py release/publish_panel.py                 # build dist, stage into the plugin
  py release/publish_panel.py --no-build      # stage the dist already on disk
  py release/publish_panel.py --plugin-dir D  # plugin checkout elsewhere

After staging, release the plugin from the DuckyOS repo:
  bash plugins/tooling/scripts/release.sh plugins/plugin-uefn-ducky --docker --upload --approve
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "ducky_app" / "frontend" / "ui_web" / "web"
DIST = WEB / "dist"
INIT = ROOT / "ducky_app" / "frontend" / "__init__.py"
DEFAULT_PLUGIN_DIR = ROOT.parent / "DuckyOS" / "plugins" / "plugin-uefn-ducky"
# Kept in sync with `plugin-uefn-ducky/src/direct.rs::DEFAULT_PANEL_BASE`.
PANEL_SUBDIR = "panel"


def version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', INIT.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("version not found in frontend/__init__.py")
    return m.group(1)


def build() -> None:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    print("$", npm, "run build", flush=True)
    subprocess.run([npm, "run", "build"], cwd=str(WEB), check=True)


# The marketplace's artifact scan (duckyos-security-kernel `is_hidden_char`) refuses any
# text file with these; it names no file, so find them here before the upload.
_SCANNED = {".js", ".mjs", ".cjs", ".ts", ".css", ".json", ".html", ".htm", ".md"}
_HIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‍‪-‮⁦-⁩﻿]")


def hidden_chars(root: Path) -> list[str]:
    """`file:line U+XXXX` for each scanned file holding a character the scan refuses."""
    out = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in _SCANNED:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # the scan skips non-UTF-8 files too
        hit = _HIDDEN.search(text)
        if hit:
            line = text.count("\n", 0, hit.start()) + 1
            near = text[max(0, hit.start() - 24):hit.end() + 24]
            out.append(f"{f.relative_to(root)}:{line} U+{ord(hit.group()):04X} near {near!r}")
    return out


def stage(plugin_dir: Path, ver: str) -> int:
    if not (DIST / "index.html").is_file():
        raise SystemExit(f"{DIST}/index.html missing — run the panel build first")
    if not (DIST / "sw.js").is_file():
        raise SystemExit(f"{DIST}/sw.js missing — public/sw.js should be copied by Vite")
    assets = plugin_dir / "assets"
    if not assets.is_dir():
        raise SystemExit(f"plugin assets dir not found: {assets}")
    target = assets / PANEL_SUBDIR
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(DIST, target)
    # The panel reports the desktop build it was made from, so a mismatch with
    # the connected desktop is visible rather than guessed at.
    (target / "panel-version.json").write_text(
        json.dumps({"version": ver}, indent=2) + "\n", encoding="utf-8"
    )
    total = 0
    count = 0
    unservable: list[str] = []
    servable = {
        ".js", ".mjs", ".css", ".map", ".json", ".html",
        ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
        ".wasm", ".ttf", ".woff", ".woff2",
    }
    for f in target.rglob("*"):
        if not f.is_file():
            continue
        count += 1
        total += f.stat().st_size
        if f.suffix.lower() not in servable:
            unservable.append(str(f.relative_to(target)))
    print(f"panel: staged {count} files ({total // 1024} KB) into {target}")
    if unservable:
        # Core only delivers known asset types; anything else is dropped at
        # upload and 404s at runtime, so fail loudly here instead.
        print("panel: these files will NOT be delivered by core:", file=sys.stderr)
        for row in unservable[:20]:
            print(f"  {row}", file=sys.stderr)
        return 1
    hidden = hidden_chars(target)
    if hidden:
        print("panel: the marketplace scan will refuse these files (hidden characters):", file=sys.stderr)
        for row in hidden[:20]:
            print(f"  {row}", file=sys.stderr)
        return 1
    print(f"panel: {ver} ready — release the plugin to publish it:")
    print("  bash plugins/tooling/scripts/release.sh plugins/plugin-uefn-ducky --docker --upload --approve")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--plugin-dir", default=str(DEFAULT_PLUGIN_DIR))
    args = ap.parse_args()
    plugin_dir = Path(args.plugin_dir).resolve()
    if not plugin_dir.is_dir():
        print(
            f"panel: plugin checkout not found at {plugin_dir} — pass --plugin-dir",
            file=sys.stderr,
        )
        return 1
    if not args.no_build:
        build()
    return stage(plugin_dir, version())


if __name__ == "__main__":
    sys.exit(main())
